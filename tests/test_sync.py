import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from paper_puller import cli
from paper_puller.bibtex_writer import append_entries
from paper_puller.dblp_client import DblpClient, DblpUnavailable
from paper_puller.models import Paper


class CliTests(unittest.TestCase):
    def parse(self, argv):
        parser = cli._build_parser()
        args = parser.parse_args(argv)
        return args, cli._selected_profiles(args, parser)

    def test_simple_defaults(self):
        args, profiles = self.parse([])
        self.assertEqual(len(profiles), 18)
        self.assertEqual(args.mode, 'incremental')
        self.assertFalse(args.enrich_abstracts)
        self.assertIsNone(args.page_size)

    def test_selection_and_legacy(self):
        for argv in [['tro', 'icra', 'tro'], ['--journal', 'tro', 'icra']]:
            _, profiles = self.parse(argv)
            self.assertEqual([p.key for p in profiles], ['tro', 'icra'])
        args, profiles = self.parse(['tro', '--full'])
        self.assertEqual(args.mode, 'full')
        self.assertEqual(len(profiles), 1)
        args, profiles = self.parse(['--full'])
        self.assertEqual(args.mode, 'full')
        self.assertEqual(len(profiles), 18)

    def test_invalid_options(self):
        for argv in [['unknown'], ['tro', '--journal', 'icra'], ['all', 'tro'],
                     ['--page-size', '0'], ['--max-pages', '-1'],
                     ['tro', 'icra', '--output', 'x.bib']]:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.parse(argv)

    def test_failure_does_not_stop_other_venues(self):
        with patch.object(cli, '_sync_profile', side_effect=[RuntimeError('offline'), None]) as sync:
            self.assertEqual(cli.main(['tro', 'ijrr']), 1)
            self.assertEqual(sync.call_count, 2)

    def test_blocked_dblp_skips_other_dblp_venues(self):
        with patch.object(cli, '_sync_profile', side_effect=[DblpUnavailable('blocked')]) as sync:
            self.assertEqual(cli.main(['icml', 'aaai', '--source', 'dblp']), 1)
            self.assertEqual([c.kwargs['profile'].key for c in sync.call_args_list], ['icml'])

    def test_auto_source_routing(self):
        with patch.dict('os.environ', {'S2_API_KEY': '', 'SEMANTIC_SCHOLAR_API_KEY': '', 'IEEE_API_KEY': ''}):
            args, _ = self.parse([])
            sources = {p.key: cli._resolve_source(args, p) for p in cli.PROFILES_BY_KEY.values()}
            self.assertEqual(sources['aaai'], 's2')
            self.assertEqual(sources['icml'], 's2')
            self.assertEqual(sources['icra'], 'crossref')
            self.assertEqual(sources['tro'], 'crossref')
            args, _ = self.parse(['--s2-api-key', 'k'])
            self.assertEqual(cli._resolve_source(args, cli.PROFILES_BY_KEY['icra']), 's2')
            self.assertEqual(cli._resolve_source(args, cli.PROFILES_BY_KEY['tro']), 'crossref')
        with self.assertRaises(SystemExit):
            self.parse(['tro', '--source', 's2'])

    def test_since_backfills_before_existing_records_and_skips_duplicates(self):
        existing = Paper.from_s2({'paperId': 'old', 'title': 'A sufficiently long title about multi robot planning',
                                  'year': 2025, 'externalIds': {}}, default_venue='AAAI')
        same_title = {'paperId': 'other-id', 'title': 'A Sufficiently Long Title About Multi-Robot Planning.',
                      'year': 2025, 'externalIds': {}, 'venue': 'AAAI Conference on Artificial Intelligence'}
        older = {'paperId': 'p2010', 'title': 'Old paper', 'year': 2010, 'abstract': 'Line one.\nLine two.',
                 'externalIds': {'DOI': '10.1609/x'}, 'authors': [{'name': 'Ada Lovelace'}],
                 'venue': 'AAAI Conference on Artificial Intelligence'}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'aaai.bib'
            append_entries(output, [existing])
            argv = ['aaai', '--since', '2007', '--output', str(output),
                    '--state-file', str(Path(directory) / 's.json')]
            with patch.object(cli.SemanticScholarClient, 'fetch_venue_history',
                              return_value=SimpleNamespace(articles=[same_title, older])) as fetch:
                self.assertEqual(cli.main(argv), 0)
            self.assertEqual(fetch.call_args.kwargs['start_year'], 2007)
            text = output.read_text()
            self.assertEqual(text.count('@inproceedings'), 2)
            self.assertIn('abstract = {Line one. Line two.}', text)
            self.assertIn('note = {Semantic Scholar ID: p2010}', text)

    def test_full_rescan_deduplicates_and_dry_run_preserves_files(self):
        article = {'DOI': '10.1/test', 'title': ['Example'], 'issued': {'date-parts': [[2026]]}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tro.bib'
            state = Path(directory) / 'state.json'
            argv = ['tro', '--full', '--source', 'crossref', '--output', str(output), '--state-file', str(state)]
            with patch.object(cli.CrossrefClient, 'fetch_tro_history', return_value=SimpleNamespace(articles=[article])) as fetch:
                with patch.object(cli, '_enrich_missing_abstracts') as enrich:
                    self.assertEqual(cli.main(argv), 0)
                    self.assertEqual(fetch.call_args.kwargs['page_size'], 1000)
                    enrich.assert_not_called()
                first = output.read_bytes()
                self.assertEqual(cli.main(argv), 0)
                self.assertEqual(output.read_bytes(), first)
                state_before = state.read_bytes()
                self.assertEqual(cli.main(argv + ['--dry-run']), 0)
                self.assertEqual(output.read_bytes(), first)
                self.assertEqual(state.read_bytes(), state_before)

    def test_failed_fetch_does_not_touch_output_or_state(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tro.bib'
            state = Path(directory) / 'state.json'
            output.write_text('% existing bibliography\n')
            state.write_text('{}\n')
            with patch.object(cli.CrossrefClient, 'fetch_tro_history', side_effect=RuntimeError('offline')):
                self.assertEqual(cli.main(['tro', '--source', 'crossref', '--output', str(output),
                                           '--state-file', str(state)]), 1)
            self.assertEqual(output.read_text(), '% existing bibliography\n')
            self.assertEqual(state.read_text(), '{}\n')

    def test_ieee_page_size_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli.IeeeXploreClient, 'fetch_tro_history', return_value=SimpleNamespace(articles=[])) as fetch:
                result = cli.main(['tro', '--source', 'ieee', '--api-key', 'test', '--page-size', '1000',
                                   '--output', str(Path(directory) / 'tro.bib'), '--dry-run'])
                self.assertEqual(result, 0)
                self.assertEqual(fetch.call_args.kwargs['page_size'], 200)


class DblpTests(unittest.TestCase):
    def test_access_blocks_are_not_retried(self):
        for status, content_type in [(202, 'text/html'), (200, 'text/html'), (403, ''), (429, '')]:
            client = DblpClient()
            client.session = Mock()
            client.session.get.return_value = Mock(status_code=status, headers={'Content-Type': content_type})
            with self.subTest(status=status), patch('paper_puller.dblp_client.time.sleep') as sleep:
                with self.assertRaises(DblpUnavailable):
                    client._request({})
                self.assertEqual(client.session.get.call_count, 1)
                sleep.assert_not_called()

    def test_transient_failures_retry(self):
        client = DblpClient(retry_delay_seconds=0)
        client.session = Mock()
        response = Mock(status_code=200, headers={'Content-Type': 'application/json'})
        response.json.return_value = {'result': {}}
        import requests
        client.session.get.side_effect = [requests.Timeout('timeout'), response]
        with patch('paper_puller.dblp_client.time.sleep'):
            self.assertEqual(client._request({}), {'result': {}})
        self.assertEqual(client.session.get.call_count, 2)


class SemanticScholarTests(unittest.TestCase):
    def test_year_chunks_cover_range_once(self):
        from paper_puller.semantic_scholar_client import year_chunks
        chunks = year_chunks(2007, 2026)
        years = sorted(y for a, b in chunks for y in range(a, b + 1))
        self.assertEqual(years, list(range(2007, 2027)))
        self.assertEqual(chunks[0], (2026, 2026))
        self.assertEqual(year_chunks(2026, 2026), [(2026, 2026)])

    def test_fetch_follows_tokens_filters_venue_and_retries_429(self):
        from paper_puller.semantic_scholar_client import SemanticScholarClient
        from paper_puller.crossref_client import AAAI_PROFILE
        def response(status, payload=None):
            r = Mock(status_code=status, headers={})
            r.json.return_value = payload
            r.raise_for_status = Mock()
            return r
        page1 = {'total': 3, 'token': 't2', 'data': [
            {'paperId': 'a', 'venue': 'AAAI Conference on Artificial Intelligence'},
            {'paperId': 'x', 'venue': 'AAAI Conference on Human Computation and Crowdsourcing'}]}
        page2 = {'total': 3, 'data': [{'paperId': 'b', 'venue': '',
                                       'publicationVenue': {'name': 'x', 'alternate_names': ['AAAI']}}]}
        client = SemanticScholarClient(api_key='k')
        client.policy.min_interval = 0
        client.session = Mock()
        client.session.get.side_effect = [response(429), response(200, page1), response(200, page2)]
        with patch('paper_puller.fetch_policy.time.sleep'):
            result = client.fetch_venue_history(profile=AAAI_PROFILE, start_year=2026, end_year=2026)
        self.assertEqual([a['paperId'] for a in result.articles], ['a', 'b'])
        calls = client.session.get.call_args_list
        self.assertEqual(calls[0].kwargs['headers']['x-api-key'], 'k')
        self.assertEqual(calls[1].kwargs['params']['year'], '2026')
        self.assertEqual(calls[2].kwargs['params']['token'], 't2')


class WriterTests(unittest.TestCase):
    def test_append_preserves_existing_content(self):
        paper = Paper.from_crossref({'DOI': '10.1/test', 'title': ['Example']})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.bib'
            for content in ['', '% existing', '% existing\n']:
                path.write_text(content)
                self.assertEqual(append_entries(path, [paper]), 1)
                self.assertTrue(path.read_text().startswith(content))
                self.assertIn('\n@article', '\n' + path.read_text())


if __name__ == '__main__':
    unittest.main()
