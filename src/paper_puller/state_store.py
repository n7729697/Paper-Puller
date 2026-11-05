from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class SyncState:
    last_publication_year: int | None = None
    last_run_utc: str | None = None
    total_entries_written: int = 0


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SyncState:
        if not self.path.exists():
            return SyncState()

        with self.path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        return SyncState(
            last_publication_year=_safe_int(raw.get("last_publication_year")),
            last_run_utc=str(raw.get("last_run_utc") or "").strip() or None,
            total_entries_written=_safe_int(raw.get("total_entries_written")) or 0,
        )

    def save(self, state: SyncState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_publication_year": state.last_publication_year,
            "last_run_utc": state.last_run_utc,
            "total_entries_written": state.total_entries_written,
        }
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")

    @staticmethod
    def mark_run(state: SyncState, new_entries_count: int, latest_year: int | None) -> SyncState:
        state.last_run_utc = datetime.now(tz=timezone.utc).isoformat()
        state.total_entries_written += new_entries_count
        if latest_year is not None:
            state.last_publication_year = latest_year
        return state


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
