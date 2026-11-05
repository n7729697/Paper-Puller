from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any
from typing import Callable

import requests

from .crossref_client import JournalProfile
from .fetch_policy import FetchPolicy

DBLP_ENDPOINT = "https://dblp.org/search/publ/api"

ProgressCallback = Callable[[int, int, int], None]


@dataclass(slots=True)
class DblpFetchResult:
    articles: list[dict[str, Any]]
    total_records: int


class DblpUnavailable(Exception):
    """Access is blocked; retrying other streams would only add pressure."""


class DblpClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout_seconds: float = 20.0,
        retry_count: int = 3,
        retry_delay_seconds: float = 2.0,
        page_delay_seconds: float = 2.0,
    ) -> None:
        self.mailto = (mailto or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_delay_seconds = retry_delay_seconds
        self.page_delay_seconds = page_delay_seconds
        self.session = requests.Session()
        self.policy = FetchPolicy()

    def fetch_conference_history(
        self,
        *,
        profile: JournalProfile,
        page_size: int = 1000,
        start_year: int | None = None,
        max_pages: int | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DblpFetchResult:
        if not profile.dblp_stream or not profile.dblp_venue:
            raise RuntimeError(f"{profile.key} does not define a DBLP stream")

        current_year = datetime.now().year
        from_year = int(start_year) if start_year is not None else profile.default_start_year
        query = f"stream:{profile.dblp_stream}:"
        offset = 0
        pages = 0
        total_records = 0
        articles: list[dict[str, Any]] = []

        # DBLP's search API has no server-side date filter, so a plain "stream:"
        # query always walks from offset 0. When this is a real incremental
        # sync (from_year is a checkpoint later than the profile's own
        # earliest year, not a full-history build), DBLP returns each stream
        # newest-first, so we can stop as soon as we've paged past the
        # checkpoint instead of re-fetching the entire history every run.
        # This is what previously walked ~85+ pages of a 17k-record stream on
        # every "icml" incremental sync and eventually triggered DBLP
        # rate-limiting (429s / connection resets) partway through.
        incremental_early_stop = from_year > profile.default_start_year
        stale_pages = 0
        STALE_PAGE_LIMIT = 2

        while True:
            payload = self._request(
                {
                    "q": query,
                    "h": page_size,
                    "f": offset,
                    "format": "json",
                }
            )
            hits_block = payload.get("result", {}).get("hits", {})
            if not isinstance(hits_block, dict):
                raise RuntimeError("Unexpected DBLP payload: missing hits object")

            total_records = int(hits_block.get("@total") or 0)
            raw_hits = hits_block.get("hit") or []
            if isinstance(raw_hits, dict):
                raw_hits = [raw_hits]
            if not isinstance(raw_hits, list) or not raw_hits:
                break

            kept = [
                hit
                for hit in raw_hits
                if isinstance(hit, dict)
                and _matches_profile(hit, profile)
                and _published_in_range(hit, from_year=from_year, to_year=current_year)
            ]
            articles.extend(kept)
            pages += 1

            if progress_callback is not None:
                progress_callback(pages, len(articles), total_records)

            if incremental_early_stop:
                page_years = [
                    year
                    for hit in raw_hits
                    if isinstance(hit, dict) and (year := _hit_year(hit)) is not None
                ]
                if page_years and max(page_years) < from_year:
                    stale_pages += 1
                    if stale_pages >= STALE_PAGE_LIMIT:
                        logging.info(
                            "dblp incremental early-stop: %s consecutive pages older than %s, "
                            "stopping after page %s instead of walking the full %s-record stream",
                            stale_pages,
                            from_year,
                            pages,
                            total_records,
                        )
                        break
                else:
                    stale_pages = 0

            offset += len(raw_hits)
            if max_pages is not None and pages >= max_pages:
                break
            if offset >= total_records:
                break
            if self.page_delay_seconds > 0:
                self.policy.sleep(self.page_delay_seconds)

        return DblpFetchResult(articles=articles, total_records=total_records)

    def _request(self, params: dict[str, str | int]) -> dict[str, Any]:
        cached = self.policy.load(params)
        if cached is not None:
            return cached
        headers = {"User-Agent": "paper-puller/0.1.0"}
        if self.mailto:
            headers["User-Agent"] = f"paper-puller/0.1.0 (mailto:{self.mailto})"

        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                response = self.session.get(
                    DBLP_ENDPOINT,
                    params=params,
                    timeout=self.policy.timeout(self.timeout_seconds),
                    headers=headers,
                )
                if response.status_code in {403, 429}:
                    raise DblpUnavailable(f"DBLP refused access (HTTP {response.status_code}); retry later")
                if response.status_code == 202 or "text/html" in response.headers.get("Content-Type", "").lower():
                    raise DblpUnavailable("DBLP returned an HTML/bot-check page instead of API data; retry later")
                if response.status_code in {500, 502, 503, 504}:
                    raise requests.HTTPError(f"DBLP returned HTTP {response.status_code}", response=response)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Unexpected DBLP payload shape")
                self.policy.save(params, payload)
                return payload
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    break
                sleep_for = self.retry_delay_seconds * attempt
                logging.warning("DBLP request failed on attempt %s/%s: %s", attempt, self.retry_count, exc)
                self.policy.sleep(sleep_for)

        raise RuntimeError(f"DBLP request failed after {self.retry_count} attempts: {last_error}")


def _matches_profile(hit: dict[str, Any], profile: JournalProfile) -> bool:
    info = hit.get("info")
    if not isinstance(info, dict):
        return False
    venue = str(info.get("venue") or "").strip()
    return venue == profile.dblp_venue


def _published_in_range(hit: dict[str, Any], *, from_year: int, to_year: int) -> bool:
    year = _hit_year(hit)
    if year is None:
        return False
    return from_year <= year <= to_year


def _hit_year(hit: dict[str, Any]) -> int | None:
    info = hit.get("info")
    if not isinstance(info, dict):
        return None
    try:
        year = int(info.get("year") or 0)
    except (TypeError, ValueError):
        return None
    return year or None
