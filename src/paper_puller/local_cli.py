"""Offline search exports and corpus reports for human/AI literature review."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .catalog import Catalog
from .crossref_client import PROFILES_BY_KEY


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main(command: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f'paper-puller {command}')
    parser.add_argument('--root', type=Path, default=Path('.'), help='directory containing *_history.bib')
    parser.add_argument('--output', type=Path, help='output directory (replaces generated files with the same names)')
    parser.add_argument('--rebuild', action='store_true', help='reparse all source files')
    if command == 'search':
        parser.add_argument('query_terms', nargs='*', metavar='QUERY', help='quoted SQLite FTS5 queries')
        parser.add_argument('--queries', type=Path, help='JSON array of query strings')
        parser.add_argument('--since', type=int, help='minimum publication year')
        parser.add_argument('--venue', action='append', choices=list(PROFILES_BY_KEY), help='repeat to select venues')
        parser.add_argument('--limit', type=positive_int, default=50, help='maximum raw matches per query (default 50)')
    args = parser.parse_args(argv)
    try:
        queries = []
        if command == 'search':
            queries = list(args.query_terms)
            if args.queries is not None:
                saved = json.loads(args.queries.read_text(encoding='utf-8'))
                if not isinstance(saved, list) or not all(isinstance(q, str) and q.strip() for q in saved):
                    raise ValueError('--queries must contain a JSON array of nonempty strings')
                queries.extend(saved)
            if not queries or not all(q.strip() for q in queries):
                raise ValueError('provide at least one query or --queries FILE')
        with Catalog(args.root) as catalog:
            changed, total = catalog.refresh(rebuild=args.rebuild)
            print(f'Indexed {total} records; reparsed {changed} file(s).')
            if command == 'search':
                rows, counts = catalog.search(queries, limit=args.limit, since=args.since, venues=args.venue)
                output = args.output or args.root / 'interested' / 'search'
                export_search(output, rows, counts, args, catalog)
                print(f'Exported {len(rows)} unique candidates to {output}')
            else:
                output = args.output or args.root / 'reports'
                export_report(output, catalog)
                print(f'Wrote corpus summary to {output}')
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f'error: {exc}\n')
    return 0


def export_search(output: Path, rows: list[dict], counts: dict[str, int], args, catalog: Catalog) -> None:
    output.mkdir(parents=True, exist_ok=True)
    # Existing source keys may collide across venues; disambiguate exported keys only.
    used: set[str] = set()
    bibs = []
    records = []
    for row in rows:
        key = original = row['entry_key']
        suffix = 2
        while key in used:
            key = f'{original}-{suffix}'
            suffix += 1
        used.add(key)
        raw = row['raw']
        if key != original:
            start = raw.index('{') + 1
            end = raw.index(',', start)
            raw = raw[:start] + key + raw[end:]
        bibs.append(raw)
        records.append({k: v for k, v in row.items() if k not in {'raw', 'id', 'score'}} | {'export_key': key})
    (output / 'selected.bib').write_text('\n\n'.join(bibs) + ('\n' if bibs else ''), encoding='utf-8')
    payload = {
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'root': str(catalog.root), 'query_counts': counts, 'limit_per_query': args.limit,
        'since': args.since, 'venues': args.venue,
        'source_files': [dict(row) for row in catalog.db.execute('SELECT * FROM files ORDER BY source')],
        'records': records,
    }
    (output / 'candidates.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# Search candidates', '', f'Unique candidates: {len(rows)}', '',
             'Lexical matches in titles and available abstracts; not a relevance or quality assessment.',
             'Each query is capped before DOI/title deduplication. Ranking combines per-query ranks.', '',
             '## Queries', '']
    lines.extend(f'- {query}: {count} raw matches returned' for query, count in counts.items())
    lines.extend(['', '## Candidates', ''])
    for row, record in zip(rows, records):
        lines.append(f"- [{record['export_key']}] {row['year'] or 'n/a'}: {row['title']} (DOI: {row['doi'] or 'missing'})")
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def export_report(output: Path, catalog: Catalog) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows = [dict(row) for row in catalog.db.execute('''
        SELECT venue, year, count(*) AS records,
               sum(CASE WHEN abstract != '' THEN 1 ELSE 0 END) AS with_abstract,
               sum(CASE WHEN doi != '' THEN 1 ELSE 0 END) AS with_doi
        FROM papers GROUP BY venue, year ORDER BY venue, year
    ''')]
    with (output / 'corpus_counts.csv').open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=['venue', 'year', 'records', 'with_abstract', 'with_doi'])
        writer.writeheader()
        writer.writerows(rows)
    total = sum(row['records'] for row in rows)
    abstracts = sum(row['with_abstract'] for row in rows)
    summary = {'records': total, 'with_abstract': abstracts,
               'generated_utc': datetime.now(timezone.utc).isoformat(),
               'source_files': [dict(row) for row in catalog.db.execute('SELECT * FROM files ORDER BY source')],
               'counts': rows}
    (output / 'corpus_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    (output / 'corpus_summary.md').write_text(
        f'# Local corpus\n\nRecords: {total}\n\nWith abstracts: {abstracts}\n\n'
        'Counts are stored entries, not unique papers across venues. Missing abstracts limit topic recall.\n'
        'See corpus_counts.csv for venue/year counts. These are metadata counts, not impact or quality scores.\n',
        encoding='utf-8',
    )
