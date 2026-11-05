"""Semantic Scholar bulk search: 1,000 records (with abstracts) per request.

Docs: https://www.semanticscholar.org/product/api/tutorial
A personal key (header ``x-api-key``) gets 1 request/second across all
endpoints. Without a key, requests share an anonymous pool that frequently
answers HTTP 429, so the client spaces requests further apart and backs off.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Callable

import requests

from .crossref_client import JournalProfile
from .fetch_policy import FetchPolicy

S2_BULK_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
S2_FIELDS = "title,abstract,authors,year,publicationDate,venue,publicationVenue,externalIds,url,journal"

ProgressCallback = Callable[[str, int, int], None]


@dataclass(slots=True)
class S2FetchResult:
    articles: list[dict[str, Any]]
    total_records: int


def year_chunks(from_year: int, to_year: int, *, recent_years: int = 6, old_span: int = 5) -> list[tuple[int, int]]:
    """Split a year range into independent query chains.

    Recent years hold most papers, so each gets its own chain; older years are
    grouped. Chains run in parallel and share one rate limiter, which keeps the
    limiter busy while individual responses (~2 s each) are in flight.
    """
    chunks: list[tuple[int, int]] = []
    split = max(from_year, to_year - recent_years + 1)
    start = from_year
    while start < split:
        end = min(start + old_span - 1, split - 1)
        chunks.append((start, end))
        start = end + 1
    chunks.extend((year, year) for year in range(split, to_year + 1))
    # Largest (most recent) chunks first so the slowest chains start earliest.
    return sorted(chunks, key=lambda chunk: -chunk[1])


class SemanticScholarClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        retry_count: int = 6,
        workers: int = 3,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.workers = workers
        self.session = requests.Session()
        self.policy = FetchPolicy()
        self.policy.min_interval = 1.05 if self.api_key else 3.0

    def fetch_venue_history(
        self,
        *,
        profile: JournalProfile,
        start_year: int | None = None,
        end_year: int | None = None,
        max_pages: int | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> S2FetchResult:
        if not profile.s2_venues:
            raise RuntimeError(f"{profile.key} does not define a Semantic Scholar venue")
        from_year = int(start_year) if start_year is not None else profile.default_start_year
        to_year = int(end_year) if end_year is not None else datetime.now().year
        if from_year > to_year:
            raise RuntimeError(f"Invalid year range: {from_year} is after {to_year}")
        if not self.policy.min_interval:
            self.policy.min_interval = 1.05 if self.api_key else 3.0

        chunks = year_chunks(from_year, to_year)
        if max_pages is not None:
            # Trial runs: a single chain over the whole range, page-limited.
            chunks = [(from_year, to_year)]
        workers = 1 if len(chunks) == 1 else min(self.workers, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda chunk: self._fetch_chain(profile, chunk, max_pages, progress_callback), chunks))

        articles: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        for chain_articles, chain_total in results:
            total += chain_total
            for item in chain_articles:
                paper_id = str(item.get("paperId") or "")
                if paper_id and paper_id in seen:
                    continue
                seen.add(paper_id)
                articles.append(item)
        return S2FetchResult(articles=articles, total_records=total)

    def _fetch_chain(
        self, profile: JournalProfile, chunk: tuple[int, int], max_pages: int | None,
        progress_callback: ProgressCallback | None,
    ) -> tuple[list[dict[str, Any]], int]:
        first, last = chunk
        label = str(first) if first == last else f"{first}-{last}"
        params: dict[str, str] = {
            "venue": ",".join(profile.s2_venues),
            "year": label,
            "fields": S2_FIELDS,
        }
        accepted = {name.casefold() for name in profile.s2_venues}
        articles: list[dict[str, Any]] = []
        total = pages = 0
        while True:
            payload = self._request(params)
            total = max(total, int(payload.get("total") or 0))
            data = payload.get("data") or []
            if not isinstance(data, list):
                raise RuntimeError("Unexpected Semantic Scholar payload: data is not a list")
            articles.extend(item for item in data if isinstance(item, dict) and _matches_venue(item, accepted))
            pages += 1
            if progress_callback is not None:
                progress_callback(label, pages, len(articles))
            token = payload.get("token")
            if not token or not data or (max_pages is not None and pages >= max_pages):
                break
            params = {**params, "token": str(token)}
        return articles, total

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        cached = self.policy.load(params)
        if cached is not None:
            return cached
        headers = {"User-Agent": "paper-puller/0.1.0"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                self.policy.wait()
                response = self.session.get(
                    S2_BULK_ENDPOINT, params=params, headers=headers,
                    timeout=self.policy.timeout(self.timeout_seconds),
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"Semantic Scholar returned HTTP {response.status_code}", response=response)
                if response.status_code in {401, 403}:
                    raise RuntimeError("Semantic Scholar rejected the API key (HTTP "
                                       f"{response.status_code}); check S2_API_KEY")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Unexpected Semantic Scholar payload shape")
                self.policy.save(params, payload)
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise RuntimeError(f"Semantic Scholar rejected the request (HTTP {status})") from exc
                if attempt >= self.retry_count:
                    break
                delay = min(2.0 ** attempt, 20.0)
                if status == 429 and not self.api_key and attempt == 1:
                    logging.warning("Semantic Scholar anonymous pool is busy (HTTP 429); "
                                    "set S2_API_KEY for a dedicated 1 request/second limit")
                logging.debug("Semantic Scholar attempt %s/%s failed: %s; retrying in %.0fs",
                              attempt, self.retry_count, exc, delay)
                self.policy.backoff(delay)
        raise RuntimeError(f"Semantic Scholar request failed after {self.retry_count} attempts: {last_error}")


def _matches_venue(item: dict[str, Any], accepted: set[str]) -> bool:
    names = [str(item.get("venue") or "")]
    venue = item.get("publicationVenue")
    if isinstance(venue, dict):
        names.append(str(venue.get("name") or ""))
        names.extend(str(alt) for alt in venue.get("alternate_names") or [])
    return any(name.strip().casefold() in accepted for name in names if name)
