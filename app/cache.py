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
        self._store[key] = (datetime.now(timezone.utc), value)