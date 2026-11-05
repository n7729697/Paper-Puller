"""Local, incrementally refreshed FTS index for paper-puller's BibTeX output."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .bibtex_reader import _entry_fields, _entry_key, _iter_entries, _parse_year, normalize_bibtex_doi


class Catalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        cache = self.root / '.state' / 'catalog.sqlite3'
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS files (
                    source TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER
                );
                CREATE TABLE IF NOT EXISTS papers (
                    id INTEGER PRIMARY KEY, source TEXT, venue TEXT, entry_key TEXT,
                    year INTEGER, title TEXT, author TEXT, doi TEXT, url TEXT,
                    abstract TEXT, raw TEXT
                );
                CREATE INDEX IF NOT EXISTS papers_source ON papers(source);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(title, abstract);
            ''')
        except sqlite3.OperationalError as exc:
            self.db.close()
            raise RuntimeError('Cannot open search index; Python needs SQLite with FTS5 enabled') from exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def _remove(self, source: str) -> None:
        self.db.execute('DELETE FROM search WHERE rowid IN (SELECT id FROM papers WHERE source=?)', (source,))
        self.db.execute('DELETE FROM papers WHERE source=?', (source,))
        self.db.execute('DELETE FROM files WHERE source=?', (source,))

    def refresh(self, *, rebuild: bool = False) -> tuple[int, int]:
        """Parse changed files only. A failed refresh rolls back the entire batch."""
        paths = sorted(self.root.glob('*_history.bib'))
        if not paths:
            raise ValueError(f'No *_history.bib files in {self.root}; run paper-puller first')
        known = {row['source']: row for row in self.db.execute('SELECT * FROM files')}
        current = {p.name for p in paths}
        changed = 0
        with self.db:
            for source in known.keys() - current:
                self._remove(source)
            for path in paths:
                stat = path.stat()
                previous = known.get(path.name)
                if not rebuild and previous and (previous['mtime_ns'], previous['size']) == (stat.st_mtime_ns, stat.st_size):
                    continue
                self._remove(path.name)
                for raw in _iter_entries(path.read_text(encoding='utf-8')):
                    if not re.match(r'@(article|inproceedings)\s*\{', raw, re.I):
                        continue
                    fields = _entry_fields(raw)
                    title = fields.get('title', '')
                    abstract = fields.get('abstract', '')
                    cursor = self.db.execute(
                        'INSERT INTO papers(source,venue,entry_key,year,title,author,doi,url,abstract,raw) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (path.name, path.stem.removesuffix('_history'), _entry_key(raw),
                         _parse_year(fields.get('year')), title, fields.get('author', ''),
                         normalize_bibtex_doi(fields.get('doi', '')), fields.get('url', ''), abstract, raw),
                    )
                    self.db.execute('INSERT INTO search(rowid,title,abstract) VALUES (?,?,?)',
                                    (cursor.lastrowid, title, abstract))
                after = path.stat()
                if (stat.st_mtime_ns, stat.st_size) != (after.st_mtime_ns, after.st_size):
                    raise RuntimeError(f'{path.name} changed while indexing; retry after syncing finishes')
                self.db.execute('INSERT INTO files VALUES (?,?,?)', (path.name, stat.st_mtime_ns, stat.st_size))
                changed += 1
        total = self.db.execute('SELECT count(*) FROM papers').fetchone()[0]
        return changed, total

    def search(self, queries: list[str], *, limit: int = 50, since: int | None = None,
               venues: list[str] | None = None) -> tuple[list[dict], dict[str, int]]:
        """Union per-query ranked candidates; merge DOI/title duplicates with provenance."""
        selected: dict[str, dict] = {}
        counts = {}
        for query in dict.fromkeys(queries):
            clauses = ['search MATCH ?']
            params: list = [query]
            if since is not None:
                clauses.append('p.year >= ?')
                params.append(since)
            if venues:
                clauses.append('p.venue IN (' + ','.join('?' for _ in venues) + ')')
                params.extend(venues)
            params.append(limit)
            sql = ('SELECT p.*, bm25(search, 5.0, 1.0) AS score FROM search '
                   'JOIN papers p ON p.id=search.rowid WHERE ' + ' AND '.join(clauses)
                   + ' ORDER BY score, p.id LIMIT ?')
            try:
                rows = self.db.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                raise ValueError(f'Invalid search query {query!r}: {exc}') from exc
            counts[query] = len(rows)
            for rank, row in enumerate(rows, 1):
                item = dict(row)
                identity = ('doi:' + item['doi']) if item['doi'] else 'title:' + re.sub(r'\W+', ' ', item['title'].casefold()).strip()
                if identity == 'title:':
                    identity = f"record:{item['id']}"
                if identity not in selected:
                    item['matches'] = []
                    item['sources'] = []
                    selected[identity] = item
                match = selected[identity]
                if item['source'] not in match['sources']:
                    match['sources'].append(item['source'])
                match['matches'].append({'query': query, 'rank': rank, 'score': item['score']})
        # Rank fusion avoids comparing BM25 magnitudes across different queries.
        for item in selected.values():
            best_ranks: dict[str, int] = {}
            for match in item['matches']:
                q = match['query']
                best_ranks[q] = min(best_ranks.get(q, match['rank']), match['rank'])
            item['fusion_score'] = sum(1 / (60 + rank) for rank in best_ranks.values())
        return sorted(selected.values(), key=lambda item: (-item['fusion_score'], item['id'])), counts
