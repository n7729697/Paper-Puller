from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any


@dataclass(slots=True)
class Paper:
    article_id: str
    title: str
    authors: list[str]
    publication_year: int | None
    journal: str
    volume: str | None
    issue: str | None
    pages: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    published_date: str | None
    entry_type: str = "article"
    venue_field: str = "journal"
    id_label: str | None = None

    @classmethod
    def from_ieee(cls, payload: dict[str, Any]) -> "Paper":
        article_number = str(
            payload.get("article_number")
            or payload.get("articleNumber")
            or payload.get("document_id")
            or payload.get("id")
            or ""
        ).strip()

        title = str(payload.get("title") or "Untitled").strip()
        publication_year = _safe_int(payload.get("publication_year") or payload.get("publicationYear"))
        journal = str(
            payload.get("publication_title")
            or payload.get("publicationTitle")
            or "IEEE Transactions on Robotics"
        ).strip()

        authors = _extract_authors(payload)
        pages = _extract_pages(payload)

        doi = str(payload.get("doi") or "").strip() or None
        html_url = str(payload.get("html_url") or payload.get("pdf_url") or "").strip() or None
        abstract = str(payload.get("abstract") or "").strip() or None
        volume = str(payload.get("volume") or "").strip() or None
        issue = str(payload.get("issue") or "").strip() or None
        published_date = str(
            payload.get("publication_date") or payload.get("publicationDate") or ""
        ).strip() or None

        if not article_number:
            fallback = doi or title
            article_number = _slug(fallback)[:32] or datetime.utcnow().strftime("%Y%m%d%H%M%S")

        return cls(
            article_id=article_number,
            title=title,
            authors=authors,
            publication_year=publication_year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            doi=doi,
            url=html_url,
            abstract=abstract,
            published_date=published_date,
        )

    @classmethod
    def from_crossref(cls, payload: dict[str, Any]) -> "Paper":
        doi = str(payload.get("DOI") or payload.get("doi") or "").strip() or None
        article_id = str(payload.get("id") or doi or "").strip()

        title = _first_text(payload.get("title")) or "Untitled"
        journal = _first_text(payload.get("container-title")) or "IEEE Transactions on Robotics"
        authors = _extract_crossref_authors(payload)
        publication_year = _extract_crossref_year(payload)
        publication_year = _normalize_conference_year_from_doi(
            publication_year=publication_year,
            doi=doi,
            journal=journal,
        )
        volume = str(payload.get("volume") or "").strip() or None
        issue = str(payload.get("issue") or payload.get("journal-issue") or "").strip() or None
        pages = str(payload.get("page") or "").strip() or None
        url = str(payload.get("URL") or payload.get("resource", {}).get("primary") if isinstance(payload.get("resource"), dict) else "").strip() or None
        abstract = str(payload.get("abstract") or "").strip() or None
        published_date = _format_crossref_date(payload)

        if not article_id:
            fallback = doi or title
            article_id = _slug(fallback)[:32] or datetime.utcnow().strftime("%Y%m%d%H%M%S")

        return cls(
            article_id=article_id,
            title=title,
            authors=authors,
            publication_year=publication_year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            doi=doi,
            url=url,
            abstract=abstract,
            published_date=published_date,
        )

    @classmethod
    def from_dblp(cls, payload: dict[str, Any], *, default_venue: str) -> "Paper":
        info = payload.get("info")
        if not isinstance(info, dict):
            info = {}

        key = str(info.get("key") or "").strip()
        title = str(info.get("title") or "Untitled").strip()
        title = title[:-1] if title.endswith(".") else title
        venue = str(info.get("venue") or default_venue).strip() or default_venue
        authors = _extract_dblp_authors(info)
        publication_year = _safe_int(info.get("year"))
        pages = str(info.get("pages") or "").strip() or None
        doi = str(info.get("doi") or "").strip() or None
        url = str(info.get("ee") or info.get("url") or "").strip() or None

        if not key:
            fallback = doi or title
            key = _slug(fallback)[:32] or datetime.utcnow().strftime("%Y%m%d%H%M%S")

        return cls(
            article_id=key,
            title=title,
            authors=authors,
            publication_year=publication_year,
            journal=venue,
            volume=None,
            issue=None,
            pages=pages,
            doi=doi,
            url=url,
            abstract=None,
            published_date=str(publication_year) if publication_year is not None else None,
            entry_type="inproceedings",
            venue_field="booktitle",
        )

    @classmethod
    def from_s2(cls, payload: dict[str, Any], *, default_venue: str) -> "Paper":
        paper_id = str(payload.get("paperId") or "").strip()
        title = str(payload.get("title") or "Untitled").strip().rstrip(".") or "Untitled"
        external = payload.get("externalIds") if isinstance(payload.get("externalIds"), dict) else {}
        doi = str(external.get("DOI") or "").strip() or None
        journal = payload.get("journal") if isinstance(payload.get("journal"), dict) else {}
        authors = [str(a.get("name") or "").strip() for a in payload.get("authors") or []
                   if isinstance(a, dict) and str(a.get("name") or "").strip()]
        year = _safe_int(payload.get("year"))
        date = str(payload.get("publicationDate") or "").strip() or None
        if date and year is not None and not date.startswith(str(year)):
            date = None  # online-first dates can precede the proceedings year
        url = f"https://doi.org/{doi}" if doi else (str(payload.get("url") or "").strip() or None)
        if not paper_id:
            paper_id = _slug(doi or title)[:32] or datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return cls(
            article_id=paper_id,
            title=title,
            authors=authors,
            publication_year=year,
            journal=str(payload.get("venue") or default_venue).strip() or default_venue,
            volume=None,
            issue=None,
            pages=str(journal.get("pages") or "").strip() or None,
            doi=doi,
            url=url,
            abstract=str(payload.get("abstract") or "").strip() or None,
            published_date=date or (str(year) if year is not None else None),
            entry_type="inproceedings",
            venue_field="booktitle",
            id_label="Semantic Scholar ID",
        )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_authors(payload: dict[str, Any]) -> list[str]:
    authors_block = payload.get("authors")
    names: list[str] = []

    if isinstance(authors_block, dict):
        author_items = authors_block.get("authors") or authors_block.get("author") or []
        if isinstance(author_items, list):
            for entry in author_items:
                if isinstance(entry, dict):
                    name = str(entry.get("full_name") or entry.get("name") or "").strip()
                    if name:
                        names.append(name)

    if not names:
        raw = str(payload.get("authors") or payload.get("author") or "").strip()
        if raw:
            names = [name.strip() for name in raw.replace(";", ",").split(",") if name.strip()]

    return names


def _extract_pages(payload: dict[str, Any]) -> str | None:
    pages = str(payload.get("start_page") or "").strip()
    end_page = str(payload.get("end_page") or "").strip()
    if pages and end_page and end_page != pages:
        return f"{pages}--{end_page}"
    if pages:
        return pages
    article_number = str(payload.get("article_number") or "").strip()
    return article_number or None


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    elif isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def _extract_crossref_authors(payload: dict[str, Any]) -> list[str]:
    authors = payload.get("author")
    names: list[str] = []
    if isinstance(authors, list):
        for author in authors:
            if not isinstance(author, dict):
                continue
            given = str(author.get("given") or "").strip()
            family = str(author.get("family") or "").strip()
            name = " ".join(part for part in [given, family] if part)
            if name:
                names.append(name)
    return names


def _extract_dblp_authors(payload: dict[str, Any]) -> list[str]:
    authors = payload.get("authors")
    if not isinstance(authors, dict):
        return []

    author_items = authors.get("author")
    if isinstance(author_items, dict):
        author_items = [author_items]
    if not isinstance(author_items, list):
        return []

    names: list[str] = []
    for author in author_items:
        if isinstance(author, dict):
            name = str(author.get("text") or "").strip()
        else:
            name = str(author or "").strip()
        if name:
            names.append(name)
    return names


def _extract_crossref_year(payload: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "created", "issued"):
        block = payload.get(key)
        if isinstance(block, dict):
            date_parts = block.get("date-parts")
            if isinstance(date_parts, list) and date_parts:
                first = date_parts[0]
                if isinstance(first, list) and first:
                    year = _safe_int(first[0])
                    if year is not None:
                        return year
    return None


def _normalize_conference_year_from_doi(
    *,
    publication_year: int | None,
    doi: str | None,
    journal: str,
) -> int | None:
    if not doi:
        return publication_year

    journal_lower = journal.lower()
    if "conference" not in journal_lower and "workshop" not in journal_lower:
        return publication_year

    match = re.search(r"/(?:iros|icra|robot)\.(\d{4})\.", doi.lower())
    if not match:
        return publication_year

    doi_year = int(match.group(1))
    if 1980 <= doi_year <= 2100:
        return doi_year
    return publication_year


def _format_crossref_date(payload: dict[str, Any]) -> str | None:
    for key in ("published-print", "published-online", "created", "issued"):
        block = payload.get(key)
        if isinstance(block, dict):
            date_parts = block.get("date-parts")
            if isinstance(date_parts, list) and date_parts:
                first = date_parts[0]
                if isinstance(first, list) and first:
                    year = _safe_int(first[0]) if len(first) >= 1 else None
                    if year is None:
                        continue
                    parts = [f"{year:04d}"]
                    if len(first) >= 2:
                        month = _safe_int(first[1])
                        if month is not None:
                            parts.append(f"{month:02d}")
                    if len(first) >= 3:
                        day = _safe_int(first[2])
                        if day is not None:
                            parts.append(f"{day:02d}")
                    return "-".join(parts)
    return None


def _slug(text: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in text)
    return "-".join(part for part in cleaned.split("-") if part)
