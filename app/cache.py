from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar


T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self._store: dict[str, tuple[datetime, T]] = {}

    def get(self, key: str) -> T | None:
        row = self._store.get(key)
        if row is None:
            return None
        created_at, value = row
        if datetime.now(timezone.utc) - created_at > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T) -> None:
        now = datetime.now(timezone.utc)
        # Keys that are never read again (e.g. per-query ids) would otherwise
        # accumulate forever; get() only evicts the key it is asked for.
        expired = [k for k, (created_at, _) in self._store.items() if now - created_at > self.ttl]
        for k in expired:
            self._store.pop(k, None)
        self._store[key] = (now, value)