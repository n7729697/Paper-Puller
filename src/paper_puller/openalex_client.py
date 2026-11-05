from __future__ import annotations

from typing import Any
from typing import Callable

import requests

OPENALEX_ENDPOINT = "https://api.openalex.org/works"

BatchProgressCallback = Callable[[int, int, int], None]


class OpenAlexClient:
    def __init__(self, *, mailto: str | None = None, timeout_seconds: float = 20.0) -> None:
        self.mailto = (mailto or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def fetch_abstracts_by_doi(
        self,
        dois: list[str],
        *,
        batch_size: int = 50,
        progress_callback: BatchProgressCallback | None = None,
    ) -> dict[str, str]:
        normalized = [normalize_doi(doi) for doi in dois]
        normalized = [doi for doi in normalized if doi]
        unique_dois = sorted(set(normalized))
        if not unique_dois:
            return {}

        results: dict[str, str] = {}
        total_batches = (len(unique_dois) + batch_size - 1) // batch_size

        for idx in range(total_batches):
            start = idx * batch_size
            batch = unique_dois[start : start + batch_size]
            payload = self._request(batch)
            works = payload.get("results") or []
            if isinstance(works, list):
                for work in works:
                    if not isinstance(work, dict):
                        continue
                    doi = normalize_doi(str(work.get("doi") or ""))
                    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
                    if doi and abstract:
                        results[doi] = abstract

            if progress_callback is not None:
                progress_callback(idx + 1, total_batches, len(results))

        return results

    def _request(self, dois: list[str]) -> dict[str, Any]:
        headers = {"User-Agent": "paper-puller/0.1.0"}
        if self.mailto:
            headers["User-Agent"] = f"paper-puller/0.1.0 (mailto:{self.mailto})"

        filter_value = "doi:" + "|".join(dois)
        response = self.session.get(
            OPENALEX_ENDPOINT,
            params={"filter": filter_value, "per-page": len(dois), "select": "doi,abstract_inverted_index"},
            timeout=self.timeout_seconds,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected OpenAlex payload shape")
        return payload


def reconstruct_abstract(inverted_index: object) -> str | None:
    if not isinstance(inverted_index, dict):
        return None

    max_pos = -1
    for positions in inverted_index.values():
        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int) and pos > max_pos:
                    max_pos = pos

    if max_pos < 0:
        return None

    words = [""] * (max_pos + 1)
    for token, positions in inverted_index.items():
        if not isinstance(token, str) or not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < len(words):
                words[pos] = token

    abstract = " ".join(part for part in words if part)
    return abstract.strip() or None


def normalize_doi(doi: str) -> str:
    value = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.strip()
