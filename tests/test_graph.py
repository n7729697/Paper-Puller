import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_puller.cli import main
from paper_puller.graph import parse_entries, build_nodes, build_edges


BIB = '''@article{a,
  title = {Multi-robot task allocation},
  author = {Alice Smith},
  year = {2025},
  doi = {10.1/a}
}
@article{b,
  title = {Multi-robot task allocation with learning},
  author = {Bob Smith},
  year = {2026},
  doi = {10.1/b}
}
'''


class GraphTests(unittest.TestCase):
    def test_graph_command_writes_portable_html_and_edge_data(self):
        with tempfile.TemporaryDirectory() as directory:
            bib = Path(directory) / 'selected.bib'
            bib.write_text(BIB)
            with patch('paper_puller.graph.load_cytoscape_js', return_value='/* offline fixture */'):
                self.assertEqual(main(['graph', str(bib)]), 0)
            html = bib.with_name('selected_graph.html').read_text()
            self.assertIn('offline fixture', html)
            self.assertIn('Multi-robot task allocation', html)
            self.assertNotIn('__GRAPH_DATA__', html)
            self.assertTrue(bib.with_name('selected_graph_edges.csv').exists())
            self.assertIn('topic_similarity', bib.with_name('selected_graph_edges.csv').read_text())

    def test_links_have_valid_nodes_and_no_self_edges(self):
        entries = parse_entries(BIB)
        nodes = build_nodes(entries)
        edges = build_edges(entries, nodes, .1, 8)
        ids = {node['id'] for node in nodes}
        self.assertEqual(len(edges), 1)
        for edge in edges:
            self.assertIn(edge['source'], ids)
            self.assertIn(edge['target'], ids)
            self.assertNotEqual(edge['source'], edge['target'])
