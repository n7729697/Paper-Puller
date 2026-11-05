import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_puller import cli


class EnvTests(unittest.TestCase):
    def run_with_env(self, contents, argv, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if contents is not None:
                (root / '.env').write_text(contents)
            with patch.dict(os.environ, environment or {}, clear=True), patch.object(Path, 'cwd', return_value=root):
                results = []
                def sync(args, *, parser, profile):
                    results.append((args.mailto, cli._resolve_source(args, profile),
                                    args.api_key or os.getenv('IEEE_API_KEY', '')))
                with patch.object(cli, '_sync_profile', side_effect=sync):
                    self.assertEqual(cli.main(argv), 0)
                return results[0]

    def test_file_loaded_with_quotes_comments_and_export(self):
        result = self.run_with_env('CROSSREF_MAILTO="test@example.com" # contact\nexport IEEE_API_KEY="test-key"\n', ['tro'])
        self.assertEqual(result, ('test@example.com', 'ieee', 'test-key'))

    def test_environment_overrides_file(self):
        result = self.run_with_env('CROSSREF_MAILTO=file@example.com\nIEEE_API_KEY=file-key\n', ['tro'],
                                   {'CROSSREF_MAILTO': 'env@example.com', 'IEEE_API_KEY': 'env-key'})
        self.assertEqual(result, ('env@example.com', 'ieee', 'env-key'))

    def test_flags_override_environment_and_file(self):
        result = self.run_with_env('CROSSREF_MAILTO=file@example.com\nIEEE_API_KEY=file-key\n',
                                   ['tro', '--mailto', 'flag@example.com', '--api-key', 'flag-key'],
                                   {'CROSSREF_MAILTO': 'env@example.com', 'IEEE_API_KEY': 'env-key'})
        self.assertEqual(result, ('flag@example.com', 'ieee', 'flag-key'))

    def test_missing_or_blank_file_keeps_crossref(self):
        for contents in [None, 'CROSSREF_MAILTO=""\nIEEE_API_KEY=""\n']:
            self.assertEqual(self.run_with_env(contents, ['tro']), ('', 'crossref', ''))

    def test_values_are_literal(self):
        result = self.run_with_env('IEEE_API_KEY="literal-${OTHER}"\n', ['tro'], {'OTHER': 'expanded'})
        self.assertEqual(result[2], 'literal-${OTHER}')
