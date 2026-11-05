from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .tro_query import build_tro_query

IEEE_ENDPOINT = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


@dataclass(slots=True)
class FetchResult:
    articles: list[dict[str, Any]]
    total_records: int


class IeeeXploreClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 20.0,
        retry_attempts: int = 4,
        backoff_seconds: float = 1.5,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self.backoff_seconds = backoff_seconds
        self.session = requests.Session()

    def fetch_tro_history(
        self,
        *,
        page_size: int = 200,
        start_year: int | None = None,
        max_pages: int | None = None,
    ) -> FetchResult:
        start_record = 1
        pages = 0
        total_records = 0
        all_articles: list[dict[str, Any]] = []

        while True:
            params = build_tro_query(
                start_record=start_record,
                max_records=page_size,
                start_year=start_year,
            )
            payload = self._request(params)

            records = payload.get("articles") or []
            if not isinstance(records, list):
                raise RuntimeError("Unexpected IEEE payload: 'articles' is not a list")

            total_records = int(payload.get("total_records") or payload.get("totalRecords") or 0)
            if not records:
                break

            all_articles.extend(records)
            pages += 1
            start_record += len(records)

            if max_pages is not None and pages >= max_pages:
                break
            if total_records and len(all_articles) >= total_records:
                break

        return FetchResult(articles=all_articles, total_records=total_records)

    def _request(self, params: dict[str, str | int]) -> dict[str, Any]:
        query_params = {"apikey": self.api_key, **params}

        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = self.session.get(
                    IEEE_ENDPOINT,
                    params=query_params,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429:
                    raise RuntimeError("IEEE API rate-limited request (HTTP 429)")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Unexpected IEEE payload shape")
                return payload
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                if attempt >= self.retry_attempts:
                    raise RuntimeError(f"IEEE API request failed after retries: {exc}") from exc
                sleep_for = self.backoff_seconds * attempt
                time.sleep(sleep_for)

        raise RuntimeError("Unreachable retry state in IEEE API request")
