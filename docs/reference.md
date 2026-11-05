# Reference

## Fetch options

```bash
# --since YEAR          Fetch from this year onward (also backfills older years into an existing .bib)
# --full                Scan complete history (or the --since window); skip existing entries
# --mode MODE           incremental (default) or full; legacy spelling
# --journal VENUE ...   Legacy alternative to positional venue keys
# --source SOURCE       auto (default), s2, crossref, dblp, or ieee
# --enrich-abstracts    Look up missing abstracts on newly fetched Crossref records
# --no-enrich-abstracts Skip extra lookups (default); source abstracts are retained
# --max-seconds N       Network budget per venue (Semantic Scholar/Crossref/DBLP); default 60
# --page-size N         Crossref/DBLP default 1000; IEEE capped at 200
# --max-pages N         Partial trial fetch, not a complete sync
# --dry-run            Fetch without writing bibliographies or sync state
# --output PATH        Custom .bib file (single venue only)
# --state-file PATH    Custom sync-state file (single venue only)
# --mailto EMAIL       Overrides CROSSREF_MAILTO
# --api-key KEY        Overrides IEEE_API_KEY
# --s2-api-key KEY     Overrides S2_API_KEY
# --verbose            Detailed page progress
# --help               Usage
```

Sources chosen by `--source auto`:

- **Journals** use Crossref with exact ISSN filters. An IEEE key switches TRO to IEEE Xplore; `--source crossref` overrides that.
- **AAAI, ICML, NeurIPS, ICLR, CoRL, AAMAS** use the Semantic Scholar bulk search (`/graph/v1/paper/search/bulk`), filtered by venue and year. It returns 1,000 papers with abstracts per request. A personal key (`S2_API_KEY`, sent as `x-api-key`) allows 1 request/second; without one the client spaces requests 3 s apart and retries HTTP 429 from the shared anonymous pool. Year ranges are split into chains (one per recent year, five-year groups before that) that run in parallel through one shared rate limiter.
- **ICRA, IROS** use Semantic Scholar when `S2_API_KEY` is set, otherwise Crossref.
- **DBLP** is available with `--source dblp` (for example for pre-2010 AAAI). Its search API returns at most 10,000 hits per query, has no abstracts, and often answers automated clients with a bot-check page.

Semantic Scholar's index differs from DBLP's: for some venues and years it holds fewer papers (for example ICML 2025). Switching sources only adds papers that are missing; records already in the `.bib` are matched by DOI, then by normalized title, and skipped.

With `CROSSREF_MAILTO` set, Crossref year ranges run 3 at a time (Crossref's advertised polite-pool concurrency); without it, requests run one at a time.

Crossref groups journal years only when the ISSNs stay unchanged, preserving predecessor-title boundaries. Conferences retain their per-year matching rules. Pagination counts all returned records before venue filtering, avoiding unnecessary pages. An unexpectedly truncated cursor response fails instead of being accepted as a complete result.

Crossref/DBLP pages from interrupted fetches are cached under `.state/fetch/` for four minutes. Rerun the same command promptly to resume. Completed Crossref ranges are cached for 24 hours during an interrupted job, so retries can skip them even after individual cursors expire. After expiry the tool fetches fresh data; this avoids following stale Crossref cursors. Completed syncs and successful dry runs clear the checkpoint cache. Interrupted fetches leave existing bibliographies and sync-state files unchanged. Source access/rate-limit failures return a nonzero exit status; other venues continue. DBLP access blocks skip the remaining DBLP venues in that invocation.

Incremental mode starts at the latest date/year in the local bibliography. An explicit `--since YEAR` replaces that start, so it can backfill older years. This can miss late-indexed papers with earlier publication dates. Use `--full` periodically for a complete rescan. Full rescans add missing records; they do not overwrite or enrich records already present. Older years are never backfilled unless you pass `--since`.

## Search, graph, and report options

```bash
paper-puller search --help
paper-puller graph --help
paper-puller report --help
```

- Search: positional FTS5 queries and/or `--queries FILE` (JSON string array), `--since YEAR`, repeated `--venue`, `--limit`, `--root`, `--output`, `--rebuild`.
- Graph: input `.bib`, `--output-prefix`, `--min-edge-score`, `--max-degree`, `--large-graph-limit`, `--allow-large`, and `--no-plot` for data-only output. Default input: `interested/search/selected.bib`. HTML is self-contained after the first Cytoscape.js download; no Graphviz install is needed for HTML.
- Report: `--root`, `--output`, `--rebuild`. Default destination: `reports/`.

The index reads `article` and `inproceedings` entries in the tool's one-field-per-line braced BibTeX format. It is not a general-purpose parser for macros, string concatenation, or arbitrary hand-written BibTeX.

Search is lexical, not embedding-based. Titles receive more weight than abstracts. Multiple query rankings are combined by reciprocal rank fusion. DOI/title duplicates are merged and query provenance retained; the limit applies before deduplication. Exported citation-key collisions are disambiguated without modifying source files.

Graph links combine TF-IDF metadata similarity with heuristic shared topic/platform labels. They are not evidence that one paper cites another. Missing abstracts and domain-specific labeling rules affect graph quality. Corpus reports count stored records, including duplicates across venues, and do not measure scientific impact.

## AI and legacy analyses

Example instruction for an AI assistant:

> Review candidates.json alongside selected.bib and corpus_summary.json. Group papers by research question and method, justify inclusion/exclusion, and cite each claim with export_key and DOI. Distinguish title-only evidence from abstract-supported claims. Do not invent findings or missing citations. Identify papers needing full-text reading, propose additional FTS5 queries, and write a draft review under reports/. Leave the generated evidence files unchanged.

The older `scripts/analyze_bib.py` and `scripts/extract_annual_candidates.py` generate specialized robotics CSVs and annual candidate JSON under `reports/data/`. They use nine predefined venues and a fixed 1984–2026 window, do not use the shared search index, and use keyword heuristics rather than citation measurements. Topic-specific scripts previously stored in `interested/` are local artifacts, not prerequisites for the supported CLI workflow.

## Development

Python dependencies are declared in `pyproject.toml`. SQLite FTS5 must be enabled in the Python installation. No C++ compiler is required.

```bash
conda activate paper_puller
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
```

Configuration precedence: flags → exported environment variables → `.env` in the working directory. Do not put real keys in `.env.example` or tracked query files.
