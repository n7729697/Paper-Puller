"""Time-bounded API requests and short-lived checkpoints for interrupted fetches."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


class FetchPolicy:
    def __init__(self, *, seconds: float | None = None, cache_dir: Path | None = None,
                 min_interval: float = 0.0):
        self.deadline = time.monotonic() + seconds if seconds else None
        self.cache_dir = cache_dir
        self.next_request = 0.0
        # Minimum spacing between request starts, shared by all worker threads.
        self.min_interval = min_interval
        self._lock = threading.Lock()

    def timeout(self, default: float) -> float:
        if self.deadline is None:
            return default
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('Fetch time budget exceeded; rerun to resume cached pages, or increase --max-seconds')
        return min(default, remaining)

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.deadline is not None and seconds >= self.deadline - time.monotonic():
            raise RuntimeError('Fetch time budget exceeded during backoff; rerun later to resume cached pages')
        time.sleep(seconds)

    def wait(self) -> None:
        """Reserve the next request slot; safe to call from several threads."""
        self.timeout(20)
        with self._lock:
            start = max(time.monotonic(), self.next_request)
            self.next_request = start + self.min_interval
        self.sleep(start - time.monotonic())

    def backoff(self, seconds: float) -> None:
        """Delay every thread's next request (e.g. after HTTP 429)."""
        with self._lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)

    def observe(self, headers) -> None:
        retry_after = headers.get('Retry-After')
        if retry_after:
            try:
                delay = float(retry_after)
            except (ValueError, TypeError):
                try:
                    delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError):
                    delay = 0
            self.backoff(max(0, delay))
        try:
            limit = float(headers.get('x-rate-limit-limit', 0))
            interval = float(headers.get('x-rate-limit-interval', '1s').rstrip('s'))
            if limit > 0:
                with self._lock:
                    self.min_interval = max(self.min_interval, interval / limit)
        except (ValueError, TypeError):
            pass

    def _path(self, params: dict) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f'{digest}.json'

    def load(self, params: dict, *, ttl: float = 240) -> dict | None:
        self.timeout(20)
        path = self._path(params)
        if path is None or not path.exists():
            return None
        try:
            cached = json.loads(path.read_text())
            # Crossref cursors expire: do not resume a stale cursor chain.
            if time.time() - cached['saved'] < ttl and isinstance(cached['payload'], dict):
                return cached['payload']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def save(self, params: dict, payload: dict) -> None:
        path = self._path(params)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'saved': time.time(), 'payload': payload}))
        temporary.replace(path)

    def clear(self) -> None:
        if self.cache_dir and self.cache_dir.exists():
            for path in self.cache_dir.glob('*.json'):
                path.unlink(missing_ok=True)
