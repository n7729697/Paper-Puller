from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from requests import RequestException

from .bibtex_writer import append_entries
from .bibtex_writer import build_citation_key
from .bibtex_reader import BibtexSnapshot
from .bibtex_reader import inspect_bibtex
from .bibtex_reader import normalize_bibtex_doi
from .bibtex_reader import title_identity
from .crossref_client import CrossrefClient
from .crossref_client import JournalProfile
from .crossref_client import PROFILES_BY_KEY
from .crossref_client import TRO_PROFILE
from .dblp_client import DblpClient, DblpUnavailable
from .ieee_client import IeeeXploreClient
from .models import Paper
from .fetch_policy import FetchPolicy
from .openalex_client import OpenAlexClient
from .semantic_scholar_client import SemanticScholarClient
from .openalex_client import normalize_doi
from .state_store import JsonStateStore


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "graph":
        from .graph import main as graph_main
        return graph_main(argv[1:])
    if argv and argv[0] in {"search", "report"}:
        from .local_cli import main as local_main
        return local_main(argv[0], argv[1:])
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    profiles = _selected_profiles(args, parser)
    failed: list[str] = []
    dblp_unavailable = False
    for profile in profiles:
        if dblp_unavailable and _resolve_source(args, profile) == "dblp":
            logging.error("%s skipped: DBLP is unavailable; retry later", profile.key)
            failed.append(profile.key)
            continue
        try:
            _sync_profile(args, parser=parser, profile=profile)
        except DblpUnavailable as exc:
            dblp_unavailable = True
            failed.append(profile.key)
            logging.error("%s: %s", profile.key, exc)
        except (RequestException, RuntimeError, ValueError, OSError) as exc:
            failed.append(profile.key)
            logging.error("%s: %s", profile.key, exc)
    if failed:
        logging.error("Incomplete sync for %s. Rerun the same command to preserve its date range and output paths.", ", ".join(failed))
        return 1
    return 0


def _selected_profiles(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[JournalProfile]:
    if args.venues and args.journal:
        parser.error("use positional venues or --journal, not both")
    journal_keys = list(args.venues or args.journal or ["all"])
    unknown = set(journal_keys) - set(PROFILES_BY_KEY) - {"all"}
    if unknown:
        parser.error("unknown venue(s): " + ", ".join(sorted(unknown)))
    journal_keys = list(dict.fromkeys(journal_keys))
    if "all" in journal_keys:
        if len(journal_keys) > 1:
            parser.error("--journal all cannot be combined with specific journal keys")
        journal_keys = list(PROFILES_BY_KEY)

    if len(journal_keys) > 1:
        if args.output is not None:
            parser.error("--output can be used only when syncing a single journal")
        if args.state_file is not None:
            parser.error("--state-file can be used only when syncing a single journal")
        if args.source == "ieee" and any(key != TRO_PROFILE.key for key in journal_keys):
            parser.error("--source ieee supports only --journal tro")

    selected = [PROFILES_BY_KEY[key] for key in journal_keys]
    if args.source == "crossref" and any(profile.dblp_stream for profile in selected):
        parser.error("--source crossref is not supported for DBLP-only conference profiles")
    if args.source == "dblp" and any(not profile.dblp_stream for profile in selected):
        parser.error("--source dblp is supported only for DBLP conference profiles")
    if args.source == "s2" and any(not profile.s2_venues for profile in selected):
        parser.error("--source s2 is supported only for conferences: "
                     + ", ".join(p.key for p in PROFILES_BY_KEY.values() if p.s2_venues))

    return selected


def _sync_profile(args: argparse.Namespace, *, parser: argparse.ArgumentParser, profile: JournalProfile) -> None:
    output_path = args.output or f"{profile.key}_history.bib"
    state_path = args.state_file or f".state/{profile.key}_sync_state.json"
    state_store = JsonStateStore(Path(state_path))
    state = state_store.load()

    existing = inspect_bibtex(Path(output_path))
    start_year = None
    start_date = None
    if args.mode == "incremental":
        start_date = existing.latest_date
        if start_date is not None:
            start_year = start_date.year
            logging.info("%s incremental sync starts from BibTeX date %s", profile.key, start_date.isoformat())
        elif existing.latest_year is not None:
            start_year = existing.latest_year
            logging.info("%s incremental sync starts from BibTeX year %s", profile.key, start_year)

    if args.since is not None:
        # An explicit --since always defines the window, so it can backfill
        # older years; records already in the .bib are skipped below.
        start_year = args.since
        start_date = None
    source = _resolve_source(args, profile)
    policy = FetchPolicy(seconds=args.max_seconds, cache_dir=Path('.state/fetch') / source / profile.key)

    if source == "ieee":
        if profile.key != TRO_PROFILE.key:
            parser.error("IEEE source currently supports only --journal tro. Use --source crossref for other journals.")
        api_key = (args.api_key or os.getenv("IEEE_API_KEY") or "").strip()
        if not api_key:
            parser.error("IEEE API key is required via --api-key or IEEE_API_KEY environment variable")
        client = IeeeXploreClient(api_key=api_key)
        result = client.fetch_tro_history(
            page_size=min(args.page_size or 200, 200),
            start_year=start_year,
            max_pages=args.max_pages,
        )
        papers = [Paper.from_ieee(article) for article in result.articles]
    elif source == "s2":
        client = SemanticScholarClient(api_key=_s2_api_key(args))
        client.policy = policy
        if client.api_key is None:
            logging.info("%s via Semantic Scholar without S2_API_KEY: shared pool, expect slower retries", profile.key)
        else:
            logging.info("Syncing %s via Semantic Scholar", profile.key)
        result = client.fetch_venue_history(
            profile=profile,
            start_year=start_year,
            max_pages=args.max_pages,
            progress_callback=_s2_progress,
        )
        papers = [Paper.from_s2(article, default_venue=profile.display_name) for article in result.articles]
    elif source == "crossref":
        client = CrossrefClient(mailto=args.mailto)
        client.policy = policy
        logging.info("Syncing %s via Crossref", profile.key)
        result = client.fetch_tro_history(
            profile=profile,
            page_size=args.page_size or 1000,
            start_year=start_year,
            start_date=start_date,
            max_pages=args.max_pages,
            progress_callback=_crossref_progress,
            page_progress_callback=_crossref_page_progress,
        )
        papers = [Paper.from_crossref(article) for article in result.articles]
    else:
        client = DblpClient(mailto=args.mailto)
        client.policy = policy
        logging.info("DBLP stream fetch started for %s.", profile.display_name)
        result = client.fetch_conference_history(
            profile=profile,
            page_size=args.page_size or 1000,
            start_year=start_year,
            max_pages=args.max_pages,
            progress_callback=_dblp_progress,
        )
        papers = [Paper.from_dblp(article, default_venue=profile.display_name) for article in result.articles]

    papers = _filter_non_scholarly_records(papers)
    if existing is not None:
        before = len(papers)
        papers = _skip_existing_papers(papers, existing)
        skipped = before - len(papers)
        if skipped:
            logging.info("%s incremental sync skipped %s existing BibTeX records", profile.key, skipped)

    if source == "crossref":
        if args.enrich_abstracts:
            _enrich_missing_abstracts(papers, mailto=args.mailto)

    papers.sort(key=lambda paper: (paper.publication_year or 0, paper.title.lower()))

    if args.dry_run:
        policy.clear()
        _print_summary(papers, dry_run=True)
        return

    written = append_entries(Path(output_path), papers)
    latest_year = max((paper.publication_year or 0 for paper in papers), default=0) or None
    state_store.save(state_store.mark_run(state, written, latest_year))

    policy.clear()
    _print_summary(papers, dry_run=False)
    logging.info("wrote %s entries into %s", written, output_path)


def _resolve_source(args: argparse.Namespace, profile: JournalProfile) -> str:
    if args.source != "auto":
        return str(args.source)
    if profile.dblp_stream:
        # DBLP's search API caps results at 10,000, has no abstracts, and is
        # often behind a bot check; Semantic Scholar works without a key.
        return "s2"
    if profile.s2_venues and _s2_api_key(args):
        return "s2"
    has_ieee_key = bool((args.api_key or os.getenv("IEEE_API_KEY") or "").strip())
    if profile.key == TRO_PROFILE.key and has_ieee_key:
        return "ieee"
    return "crossref"


def _s2_api_key(args: argparse.Namespace) -> str:
    return (getattr(args, "s2_api_key", "") or os.getenv("S2_API_KEY")
            or os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-puller",
        usage="%(prog)s [VENUE ...] [--full] [--dry-run] [options]",
        description="Update local BibTeX histories. With no venues, update all.",
        epilog="Local workflows: paper-puller search --help; paper-puller report --help; paper-puller graph --help. Venues: " + ", ".join(PROFILES_BY_KEY),
    )
    parser.add_argument("venues", nargs="*", metavar="VENUE", help="e.g. tro icra; defaults to all")
    parser.add_argument("--full", dest="mode", action="store_const", const="full",
                        help="scan the full history (may take a long time)")
    parser.add_argument("--enrich-abstracts", action=argparse.BooleanOptionalAction,
                        default=False, help="look up missing abstracts for new records (slower)")
    parser.add_argument("--dry-run", action="store_true", help="fetch without writing files")
    parser.add_argument("--verbose", action="store_true", help="show detailed fetch progress")
    advanced = parser.add_argument_group("advanced / compatibility options")
    advanced.add_argument("--journal", choices=["all", *PROFILES_BY_KEY], nargs="+", metavar="VENUE",
                          help="legacy alternative to positional venues")
    advanced.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    advanced.add_argument("--source", choices=["auto", "s2", "crossref", "dblp", "ieee"], default="auto",
                          help="auto: Semantic Scholar for conferences, Crossref for journals")
    advanced.add_argument("--api-key", default="", help="defaults to IEEE_API_KEY")
    advanced.add_argument("--s2-api-key", default="", help="defaults to S2_API_KEY")
    advanced.add_argument("--mailto", default=os.getenv("CROSSREF_MAILTO", ""))
    advanced.add_argument("--output", help="custom BibTeX path (one venue only)")
    advanced.add_argument("--state-file", help="custom state path (one venue only)")
    advanced.add_argument("--page-size", type=_positive_int,
                          help="default: 1000 for Crossref/DBLP, 200 for IEEE (capped at 200)")
    advanced.add_argument("--max-pages", type=_positive_int, help="limit fetching for a trial run")
    advanced.add_argument("--since", type=_positive_int, metavar="YEAR", help="fetch publications from this year onward")
    advanced.add_argument("--max-seconds", type=_positive_int, default=60,
                          help="network budget per venue (default 60); interrupted pages are cached briefly")
    parser.set_defaults(mode="incremental")
    return parser


def _print_summary(papers: list[Paper], *, dry_run: bool) -> None:
    years = [paper.publication_year for paper in papers if paper.publication_year is not None]
    min_year = min(years) if years else "n/a"
    max_year = max(years) if years else "n/a"
    mode = "dry-run" if dry_run else "sync"
    logging.info("%s fetched %s papers spanning %s to %s", mode, len(papers), min_year, max_year)


def _skip_existing_papers(papers: list[Paper], existing: BibtexSnapshot) -> list[Paper]:
    kept: list[Paper] = []
    seen_dois = set(existing.dois)
    seen_keys = set(existing.citation_keys)
    seen_titles = set(existing.titles)

    for paper in papers:
        # Title identity catches records already stored from another source
        # (e.g. DBLP entries without DOIs when a venue moves to Semantic Scholar).
        title_key = title_identity(paper.title, paper.publication_year)
        if title_key and title_key in seen_titles:
            continue
        doi = normalize_bibtex_doi(paper.doi or "")
        if doi:
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
            seen_titles.add(title_key)
            kept.append(paper)
            continue

        citation_key = build_citation_key(paper)
        if citation_key in seen_keys or paper.article_id in seen_keys:
            continue
        seen_keys.add(citation_key)
        seen_titles.add(title_key)
        kept.append(paper)

    return kept


def _crossref_progress(done_years: int, total_years: int, current_year: int, paper_count: int) -> None:
    percentage = (done_years / total_years) * 100 if total_years else 100.0
    logging.debug(
        "crossref progress %.1f%% (%s/%s years, current year %s, papers %s)",
        percentage,
        done_years,
        total_years,
        current_year,
        paper_count,
    )


def _crossref_page_progress(year: int, page: int, year_papers: int, year_total: int) -> None:
    if page == 1 or page % 5 == 0 or (year_total > 0 and year_papers >= year_total):
        if year_total > 0:
            pct = (year_papers / year_total) * 100
            logging.debug(
                "crossref year %s page %s: %s/%s papers (%.1f%%)",
                year,
                page,
                year_papers,
                year_total,
                pct,
            )
        else:
            logging.debug(
                "crossref year %s page %s: %s papers",
                year,
                page,
                year_papers,
            )


def _s2_progress(chain: str, page: int, paper_count: int) -> None:
    logging.debug("s2 %s page %s: %s matching papers", chain, page, paper_count)


def _dblp_progress(page: int, paper_count: int, total_records: int) -> None:
    logging.debug(
        "dblp page %s: kept %s matching papers (stream total %s)",
        page,
        paper_count,
        total_records,
    )


def _enrich_missing_abstracts(papers: list[Paper], *, mailto: str) -> None:
    candidates: list[str] = []
    for paper in papers:
        if paper.abstract:
            continue
        doi = normalize_doi(paper.doi or "")
        if doi:
            candidates.append(doi)

    unique_candidates = sorted(set(candidates))
    if not unique_candidates:
        logging.info("abstract enrichment skipped: no DOI candidates with missing abstracts")
        return

    logging.info("abstract enrichment started for %s DOI candidates", len(unique_candidates))
    client = OpenAlexClient(mailto=mailto)
    try:
        abstracts_by_doi = client.fetch_abstracts_by_doi(
            unique_candidates,
            batch_size=50,
            progress_callback=_openalex_progress,
        )
    except (RequestException, RuntimeError, ValueError) as exc:
        logging.warning("abstract enrichment skipped after OpenAlex request failure: %s", exc)
        return

    enriched = 0
    for paper in papers:
        if paper.abstract:
            continue
        doi = normalize_doi(paper.doi or "")
        abstract = abstracts_by_doi.get(doi)
        if abstract:
            paper.abstract = abstract
            enriched += 1

    logging.info("abstract enrichment completed: %s papers enriched", enriched)


def _openalex_progress(batch: int, total_batches: int, found_abstracts: int) -> None:
    percentage = (batch / total_batches) * 100 if total_batches else 100.0
    logging.debug(
        "openalex progress %.1f%% (%s/%s batches, abstracts found %s)",
        percentage,
        batch,
        total_batches,
        found_abstracts,
    )


def _filter_non_scholarly_records(papers: list[Paper]) -> list[Paper]:
    filtered: list[Paper] = []
    skipped = 0
    for paper in papers:
        if _is_non_scholarly_record(paper):
            skipped += 1
            continue
        filtered.append(paper)

    if skipped:
        logging.info("filtered out %s non-scholarly records (blank/cover placeholders)", skipped)
    return filtered


def _is_non_scholarly_record(paper: Paper) -> bool:
    title = (paper.title or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "", title)
    if normalized in {"blankpage", "[blankpage]", "cover", "frontcover", "backcover"}:
        return True

    if not paper.authors and paper.pages:
        compact_pages = paper.pages.replace(" ", "")
        if re.fullmatch(r"[A-Z]?\d+--?[A-Z]?\d+", compact_pages) and title in {"blank page", "[blank page]"}:
            return True

    return False
