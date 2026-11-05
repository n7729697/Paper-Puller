import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paper_puller.crossref_client import CrossrefClient, TRO_PROFILE, ICRA_PROFILE, TMECH_PROFILE
from paper_puller.fetch_policy import FetchPolicy


class FetchTests(unittest.TestCase):
    def test_groups_journal_years_but_respects_lineage(self):
        client = CrossrefClient()
        with patch.object(client, '_fetch_year', return_value=([], 0, 1)) as fetch:
            client.fetch_tro_history(profile=TRO_PROFILE, start_year=1985, end_year=2026)
            self.assertEqual([(c.kwargs['year'], c.kwargs['until_year']) for c in fetch.call_args_list],
                             [(1985, 1988), (1989, 2003), (2004, 2026)])
        with patch.object(client, '_fetch_year', return_value=([], 0, 1)) as fetch:
            client.fetch_tro_history(profile=ICRA_PROFILE, start_year=2024, end_year=2026)
            self.assertEqual(fetch.call_count, 3)

    def test_twenty_year_filter_and_complete_pagination(self):
        client = CrossrefClient()
        page = {'message': {'items': [{'DOI': '10.1/a'}, {'DOI': '10.1/b'}], 'total-results': 3, 'next-cursor': 'next'}}
        end = {'message': {'items': [{'DOI': '10.1/c'}], 'total-results': 3}}
        with patch.object(client, '_request', side_effect=[page, end]) as request:
            result = client.fetch_tro_history(profile=TMECH_PROFILE, start_year=2007, end_year=2026, page_size=2)
            self.assertEqual(len(result.articles), 3)
            self.assertEqual(request.call_count, 2)
            self.assertIn('from-pub-date:2007-01-01', request.call_args_list[0].args[0]['filter'])
            self.assertIn('until-pub-date:2026-12-31', request.call_args_list[0].args[0]['filter'])

    def test_pagination_counts_raw_results_before_venue_filter(self):
        client = CrossrefClient()
        payload = {'message': {'items': [{'DOI': '10.1/other'}, {'DOI': '10.1109/ICRA.2026.1'}],
                               'total-results': 2, 'next-cursor': 'unneeded'}}
        with patch.object(client, '_request', return_value=payload) as request:
            result = client.fetch_tro_history(profile=ICRA_PROFILE, start_year=2026, end_year=2026, page_size=2)
            self.assertEqual(len(result.articles), 1)
            self.assertEqual(request.call_count, 1)

    def test_incomplete_cursor_is_not_reported_complete(self):
        client = CrossrefClient()
        payload = {'message': {'items': [{'DOI': '10.1/a'}], 'total-results': 2}}
        with patch.object(client, '_request', return_value=payload):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete Crossref'):
                client.fetch_tro_history(profile=TMECH_PROFILE, start_year=2026, end_year=2026)

    def test_page_limit_applies_across_years(self):
        client = CrossrefClient()
        with patch.object(client, '_fetch_year', return_value=([], 0, 1)) as fetch:
            client.fetch_tro_history(profile=TRO_PROFILE, start_year=1985, end_year=2026, max_pages=1)
            self.assertEqual(fetch.call_count, 1)

    def test_timeout_does_not_wait_or_request(self):
        with patch('paper_puller.fetch_policy.time.monotonic', return_value=10):
            policy = FetchPolicy(seconds=1)
        with patch('paper_puller.fetch_policy.time.monotonic', return_value=12):
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                policy.timeout(20)
        with patch('paper_puller.fetch_policy.time.monotonic', return_value=10):
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                policy.sleep(2)

    def test_checkpoint_reuses_pages_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            params = {'filter': 'test', 'cursor': '*'}
            payload = {'message': {'items': [], 'total-results': 0}}
            client = CrossrefClient()
            client.policy = FetchPolicy(cache_dir=Path(directory))
            response = Mock(status_code=200, headers={})
            response.json.return_value = payload
            client.session = Mock()
            client.session.get.return_value = response
            self.assertEqual(client._request(params), payload)
            self.assertEqual(client._request(params), payload)
            self.assertEqual(client.session.get.call_count, 1)
            with patch('paper_puller.fetch_policy.time.time', return_value=10**12):
                self.assertIsNone(client.policy.load(params))
            client.policy.clear()
            self.assertFalse(list(Path(directory).glob('*.json')))

    def test_retry_after_and_rate_limit(self):
        policy = FetchPolicy()
        with patch('paper_puller.fetch_policy.time.monotonic', return_value=10):
            policy.observe({'Retry-After': '5', 'x-rate-limit-limit': '1', 'x-rate-limit-interval': '1s'})
        self.assertEqual(policy.next_request, 15)
        self.assertEqual(policy.min_interval, 1)

    def test_interrupted_fetch_resumes_and_completed_range_outlives_cursor(self):
        import requests
        import time
        first = {'message': {'items': [{'DOI': '10.1/a'}, {'DOI': '10.1/b'}],
                             'total-results': 3, 'next-cursor': 'next'}}
        last = {'message': {'items': [{'DOI': '10.1/c'}], 'total-results': 3}}
        def response(payload):
            result = Mock(status_code=200, headers={})
            result.json.return_value = payload
            return result
        with tempfile.TemporaryDirectory() as directory:
            first_client = CrossrefClient(retry_count=1)
            first_client.policy = FetchPolicy(cache_dir=Path(directory))
            first_client.session = Mock()
            first_client.session.get.side_effect = [response(first), requests.Timeout('offline')]
            with self.assertRaises(RuntimeError):
                first_client.fetch_tro_history(profile=TMECH_PROFILE, start_year=2007, end_year=2026, page_size=2)
            resumed = CrossrefClient()
            resumed.policy = FetchPolicy(cache_dir=Path(directory))
            resumed.session = Mock()
            resumed.session.get.return_value = response(last)
            result = resumed.fetch_tro_history(profile=TMECH_PROFILE, start_year=2007, end_year=2026, page_size=2)
            self.assertEqual(len(result.articles), 3)
            self.assertEqual(resumed.session.get.call_count, 1)
            self.assertEqual(resumed.session.get.call_args.kwargs['params']['cursor'], 'next')
            now = time.time()
            with patch('paper_puller.fetch_policy.time.time', return_value=now + 300), patch.object(resumed, '_request', side_effect=AssertionError('should reuse completed range')):
                result = resumed.fetch_tro_history(profile=TMECH_PROFILE, start_year=2007, end_year=2026, page_size=2)
                self.assertEqual(len(result.articles), 3)
