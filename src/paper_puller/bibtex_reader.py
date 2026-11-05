from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class BibtexSnapshot:
    citation_keys: set[str]
    dois: set[str]
    latest_date: date | None
    latest_year: int | None
    titles: frozenset[str] = frozenset()


def inspect_bibtex(path: Path) -> BibtexSnapshot:
    if not path.exists():
        return BibtexSnapshot(citation_keys=set(), dois=set(), latest_date=None, latest_year=None)

    citation_keys: set[str] = set()
    dois: set[str] = set()
    titles: set[str] = set()
    dates: list[date] = []
    years: list[int] = []

    for entry in _iter_entries(path.read_text(encoding="utf-8")):
        key = _entry_key(entry)
        if key:
            citation_keys.add(key)

        fields = _entry_fields(entry)
        doi = fields.get("doi")
        if doi:
            dois.add(normalize_bibtex_doi(doi))
        title_key = title_identity(fields.get("title", ""), _parse_year(fields.get("year")))
        if title_key:
            titles.add(title_key)

        parsed_date = _parse_date(fields.get("date"))
        if parsed_date is not None:
            dates.append(parsed_date)

        year = _parse_year(fields.get("year"))
        if year is not None:
            years.append(year)

    return BibtexSnapshot(
        citation_keys=citation_keys,
        dois={doi for doi in dois if doi},
        latest_date=max(dates) if dates else None,
        latest_year=max(years) if years else None,
        titles=frozenset(titles),
    )


def title_identity(title: str, year: int | None) -> str:
    """Source-independent identity for records without a shared DOI.

    Long titles are distinctive on their own (sources may disagree on the year
    by one); short ones such as "Editorial" are qualified by year.
    """
    normalized = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    if not normalized or normalized == "untitled":
        return ""
    if len(normalized) >= 40:
        return normalized
    return f"{normalized}|{year or ''}"


def normalize_bibtex_doi(value: str) -> str:
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.strip()


def _iter_entries(text: str) -> list[str]:
    starts = [match.start() for match in re.finditer(r"@\w+\s*\{", text)]
    entries: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        entries.append(text[start:end].strip())
    return entries


def _entry_key(entry: str) -> str | None:
    match = re.match(r"@\w+\s*\{\s*([^,\s]+)", entry)
    if not match:
        return None
    return match.group(1).strip() or None


def _entry_fields(entry: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*\{(.*)\},?\s*$", entry):
        fields[match.group(1).lower()] = _unescape(match.group(2).strip())
    return fields


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None

    parts = value.strip().split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d{4}", value)
    if not match:
        return None
    return int(match.group(0))


def _unescape(value: str) -> str:
    return value.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
