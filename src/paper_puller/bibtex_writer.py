from __future__ import annotations

from pathlib import Path

from .models import Paper


def append_entries(path: Path, papers: list[Paper]) -> int:
    if not papers:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    has_content = path.exists() and path.stat().st_size > 0
    ends_with_newline = True
    if has_content:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            ends_with_newline = fh.read(1) == b"\n"

    rendered = "\n\n".join(_render_entry(paper) for paper in papers)
    with path.open("a", encoding="utf-8") as fh:
        if has_content and not ends_with_newline:
            fh.write("\n")
        if has_content:
            fh.write("\n")
        fh.write(rendered)
        fh.write("\n")

    return len(papers)


def _render_entry(paper: Paper) -> str:
    citation_key = build_citation_key(paper)
    fields: list[tuple[str, str]] = [
        ("title", paper.title),
        ("author", _join_authors(paper.authors)),
        (paper.venue_field, paper.journal),
    ]

    if paper.publication_year is not None:
        fields.append(("year", str(paper.publication_year)))
    if paper.published_date:
        fields.append(("date", paper.published_date))
    if paper.volume:
        fields.append(("volume", paper.volume))
    if paper.issue:
        fields.append(("number", paper.issue))
    if paper.pages:
        fields.append(("pages", paper.pages))
    if paper.doi:
        fields.append(("doi", paper.doi))
    if paper.url:
        fields.append(("url", paper.url))
    if paper.abstract:
        fields.append(("abstract", paper.abstract))

    if paper.id_label:
        fields.append(("note", f"{paper.id_label}: {paper.article_id}"))
    elif paper.entry_type == "article":
        fields.append(("note", f"IEEE article id: {paper.article_id}"))
    else:
        fields.append(("note", f"DBLP key: {paper.article_id}"))

    rendered_fields = ",\n".join(
        f"  {name} = {{{_sanitize(value)}}}" for name, value in fields if value.strip()
    )
    return f"@{paper.entry_type}{{{citation_key},\n{rendered_fields}\n}}"


def build_citation_key(paper: Paper) -> str:
    first_author = paper.authors[0] if paper.authors else "unknown"
    surname = first_author.split()[-1] if first_author.split() else "unknown"
    year = str(paper.publication_year or "noyear")
    tail = paper.article_id[-6:] if len(paper.article_id) > 6 else paper.article_id
    return _slug(f"{surname}-{year}-{tail}")


def _join_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown"
    return " and ".join(authors)


def _slug(text: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in text)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts)


def _sanitize(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace("{", "\\{").replace("}", "\\}")
    return " ".join(escaped.split())
