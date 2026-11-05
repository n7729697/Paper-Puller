import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_puller.catalog import Catalog
from paper_puller.cli import main


def entry(key='one', title='Robot cooperation', doi='10.1/one', year=2025, kind='article'):
    return f'@{kind}{{{key},\n  title = {{{title}}},\n  year = {{{year}}},\n  doi = {{{doi}}},\n  abstract = {{Multi agent learning}}\n}}\n'


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'tro_history.bib'
        self.source.write_text(entry())

    def test_reuses_index_and_refreshes_modifications_and_deletions(self):
        second = self.root / 'icml_history.bib'
        second.write_text(entry('two', 'Robot planning', '10.1/two', kind='inproceedings'))
        with Catalog(self.root) as catalog:
            self.assertEqual(catalog.refresh(), (2, 2))
            with patch('paper_puller.catalog._iter_entries', side_effect=AssertionError('should not reparse')):
                self.assertEqual(catalog.refresh(), (0, 2))
            self.source.write_text(entry(title='Navigation'))
            self.assertEqual(catalog.refresh(), (1, 2))
            self.assertEqual(len(catalog.search(['cooperation'])[0]), 0)
            second.unlink()
            self.assertEqual(catalog.refresh(), (0, 1))
            self.assertEqual(len(catalog.search(['planning'])[0]), 0)
            self.assertEqual(catalog.refresh(rebuild=True), (1, 1))

    def test_batch_deduplicates_and_retains_query_provenance(self):
        (self.root / 'icml_history.bib').write_text(entry('two', doi='https://doi.org/10.1/ONE', kind='inproceedings'))
        with Catalog(self.root) as catalog:
            catalog.refresh()
            records, counts = catalog.search(['robot', 'cooperation'])
            self.assertEqual(len(records), 1)
            self.assertEqual(set(records[0]['sources']), {'icml_history.bib', 'tro_history.bib'})
            self.assertEqual({m['query'] for m in records[0]['matches']}, {'robot', 'cooperation'})
            self.assertEqual(counts, {'robot': 2, 'cooperation': 2})
            self.assertEqual(catalog.search(['robot'], since=2026)[0], [])
            self.assertEqual(catalog.search(['robot'], venues=['icra'])[0], [])
            self.assertEqual(len(catalog.search(['robot'], venues=['tro'])[0]), 1)
            with self.assertRaises(ValueError):
                catalog.search(['"unfinished'])

    def test_refresh_failure_rolls_back(self):
        with Catalog(self.root) as catalog:
            catalog.refresh()
            self.source.write_text(entry(title='Navigation'))
            with patch('paper_puller.catalog._iter_entries', side_effect=ValueError('bad input')):
                with self.assertRaises(ValueError):
                    catalog.refresh()
            self.assertEqual(len(catalog.search(['cooperation'])[0]), 1)

    def test_cli_exports_queries_report_and_collision_safe_bib(self):
        self.source.write_text(entry() + entry('one', 'Robot planning', '10.1/two'))
        queries = self.root / 'queries.json'
        queries.write_text(json.dumps(['robot', 'planning']))
        out = self.root / 'interested' / 'test'
        self.assertEqual(main(['search', '--queries', str(queries), '--root', str(self.root), '--output', str(out)]), 0)
        payload = json.loads((out / 'candidates.json').read_text())
        self.assertEqual(len(payload['records']), 2)
        keys = {r['export_key'] for r in payload['records']}
        self.assertEqual(keys, {'one', 'one-2'})
        self.assertTrue((out / 'selected.bib').exists())
        self.assertEqual(main(['report', '--root', str(self.root)]), 0)
        summary = json.loads((self.root / 'reports' / 'corpus_summary.json').read_text())
        self.assertEqual(summary['records'], 2)
        self.assertEqual(main(['search', 'nonexistent', '--root', str(self.root), '--output', str(out)]), 0)
        self.assertEqual((out / 'selected.bib').read_text(), '')

    def test_invalid_query_preserves_previous_exports(self):
        out = self.root / 'interested'
        out.mkdir()
        (out / 'selected.bib').write_text('keep me')
        with self.assertRaises(SystemExit):
            main(['search', '"unfinished', '--root', str(self.root), '--output', str(out)])
        self.assertEqual((out / 'selected.bib').read_text(), 'keep me')


if __name__ == '__main__':
    unittest.main()
