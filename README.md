# Paper-Puller

Pull journal and conference papers (title, authors, year, venue, DOI, abstract) into local `.bib` files, search them offline, explore topics as an interactive network, and hand the results to an AI assistant for review.

```
fetch  →  <venue>_history.bib  →  search  →  selected.bib + candidates.json  →  graph (HTML)  →  AI review
```

## 1. Install

```bash
git clone https://github.com/n7729697/Paper-Puller.git
cd Paper-Puller
conda create -n paper_puller python=3.11 pip sqlite -y
conda activate paper_puller
python -m pip install -e .
cp -n .env.example .env
```

Put your keys in `.env`. It is read from the directory you run the command in and is ignored by Git:

```dotenv
S2_API_KEY="..."            # Semantic Scholar key: fast conference fetching (recommended)
CROSSREF_MAILTO="you@example.com"  # optional: 3 parallel Crossref requests instead of 1
IEEE_API_KEY=""             # optional: use IEEE Xplore for TRO
```

Request a free Semantic Scholar key at <https://www.semanticscholar.org/product/api#api-key>. Without one, conferences still work, but they share a busy anonymous pool and run several times slower.

## 2. Fetch papers

Run `paper-puller VENUE`. The first run creates `<venue>_history.bib`. Every later run is **incremental**: it fetches only the newest publication year onward and skips papers already in the file.

```bash
# First fetch: the last 20 years of one journal and one conference
paper-puller tro  --since 2007        # → tro_history.bib
paper-puller aaai --since 2007        # → aaai_history.bib

# Incremental update: append only papers not yet in the .bib
paper-puller tro aaai

# Update every venue
paper-puller

# Backfill older years into an existing .bib (existing papers are skipped)
paper-puller aaai --since 2000

# Preview without writing anything
paper-puller aaai --dry-run
```

Papers already in the file are matched by DOI or title, so overlapping or repeated runs never create duplicates.

| Venue keys | Source | Speed |
| --- | --- | --- |
| Journals: `tro ijrr tmech scirobotics cybernetics rcim tase ral jfr tfr` | Crossref (exact ISSN filter) | 1,000 papers per request; a few seconds per venue |
| Conferences: `aaai icml neurips iclr corl aamas` | Semantic Scholar bulk search (includes abstracts) | 1,000 papers per request at 1 request/s with `S2_API_KEY` |
| Conferences: `icra iros` | Semantic Scholar if `S2_API_KEY` is set, otherwise Crossref | Crossref path is slower: one text query per year |

Each venue has a 60-second network budget. If a large first fetch runs out of time, rerun the same command within 4 minutes: the pages already downloaded are cached, so it resumes where it stopped. Raise the budget with `--max-seconds 180`.

Useful options: `--since YEAR`, `--dry-run`, `--full` (rescan the whole history and add anything missing), `--source s2|crossref|dblp|ieee`, `--verbose`. See `paper-puller --help` and [docs/reference.md](docs/reference.md).

The tool stores metadata and abstracts only; it never downloads PDFs.

## 3. Search the collection

Searches run offline over titles and abstracts in every `*_history.bib`, using [SQLite FTS5 syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax) (`AND`, `OR`, `NOT`, `"exact phrase"`, `prefix*`).

```bash
# Quick search
paper-puller search '"partner selection"' 'cooperation AND learning' \
  --venue tro --venue icra --since 2018 --limit 50 --output interested/my-topic

# Several related queries from a file (a JSON array of strings)
paper-puller search --queries examples/cooperation-queries.json \
  --since 2018 --output interested/cooperation
```

Each output directory contains:

| File | Contents |
| --- | --- |
| `selected.bib` | Deduplicated candidate bibliography |
| `candidates.json` | Metadata, abstracts, matched queries and ranks, `export_key`, source file |
| `summary.md` | Readable list of the candidates |

The first search builds an index in `.state/`; later searches re-read only `.bib` files that changed. Reusing an output directory overwrites its files.

## 4. Interactive paper network (HTML)

```bash
paper-puller graph interested/cooperation/selected.bib
xdg-open interested/cooperation/selected_graph.html     # macOS: open …
```

This writes `selected_graph.html` next to the input, along with `_graph_nodes.csv`, `_graph_edges.csv` and `_graph.dot`. The HTML file is self-contained: it works offline and can be emailed or committed as-is. The first run downloads Cytoscape.js once into `.state/assets/`.

In the browser you can:

- **Drag** nodes, **scroll** to zoom, and drag the background to pan.
- **Click a paper** to show its details (authors, venue, year, theme, method, tags, DOI link) and highlight its links.
- **Search** by title, author, theme or tag to filter what's shown.
- **Filter by theme** by clicking legend entries (*All* / *None* toggle every theme).
- **Copy visible BibTeX** for the papers currently shown.
- **Download visible graph PNG** for slides or papers.

Links mean **similar topic** (shared terms and labels), **not citations**.

To tune the network:

```bash
# Fewer, stronger links (default 0.30), and at most ~5 links per paper (default 8)
paper-puller graph interested/cooperation/selected.bib --min-edge-score 0.40 --max-degree 5
```

Build graphs from curated searches rather than whole venues: graphs over 2,000 papers are refused unless you pass `--allow-large`.

## 5. AI-assisted review

The tool never calls an AI model itself. It prepares the evidence files for one. A ready-made prompt is in [examples/ai-review-prompt.md](examples/ai-review-prompt.md).

```bash
# 1. Search your topic
paper-puller search --queries examples/cooperation-queries.json \
  --since 2018 --output interested/cooperation

# 2. Describe what the corpus covers (venues, years, abstract coverage)
paper-puller report                     # → reports/corpus_summary.json

# 3. Optional: the network view, for your own reading
paper-puller graph interested/cooperation/selected.bib
```

4. Open your AI assistant and paste in the prompt from [examples/ai-review-prompt.md](examples/ai-review-prompt.md), filling in your topic. Attach:
   - `interested/cooperation/candidates.json`
   - `interested/cooperation/selected.bib`
   - `reports/corpus_summary.json`

   With Claude Code you can skip the attachments and ask: *"Follow examples/ai-review-prompt.md for topic X using interested/cooperation/ and reports/corpus_summary.json; write interested/cooperation/review.md."*
5. Save the suggested follow-up queries as `examples/<topic>-queries.json`, rerun step 1 with them, and repeat until no new relevant papers appear.

The prompt requires the assistant to cite only the supplied papers by `export_key` and DOI, to mark title-only evidence, and not to treat search rank as quality. Check the cited papers yourself before using the review.

## 6. Corpus report

```bash
paper-puller report
```

Writes `reports/corpus_counts.csv` (papers per venue and year, with DOI and abstract coverage) plus `reports/corpus_summary.json` and `reports/corpus_summary.md`.

## More

- All options and source details: [docs/reference.md](docs/reference.md)
- Tests: `python -m pip install -e '.[dev]' && python -m pytest`
