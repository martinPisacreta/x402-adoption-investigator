"""Clock protocol: SystemClock for live runs, SampleClock for deterministic sample runs (SPEC.md section 3).

SampleClock starts at a fixed instant (MANIFEST.fetchedAt) and advances exactly 1 ms per call, so every `at`
field in the logs of a sample run is reproducible byte for byte. sleep() is how a retry backoff is spent: the
SystemClock really waits, the SampleClock moves forward by the backoff without waiting (sources/retry.py).
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Protocol

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


def iso_from_ms(ms: int) -> str:
    """Epoch milliseconds -> "YYYY-MM-DDTHH:MM:SS.mmmZ" (UTC)."""
    base = _dt.datetime.fromtimestamp(ms // 1000, tz=_dt.timezone.utc)
    return f"{base.strftime(_ISO_FORMAT)}.{ms % 1000:03d}Z"


def parse_iso_ms(iso: str) -> int:
    """Parse "YYYY-MM-DDTHH:MM:SS[.fff]Z" (or +00:00) into epoch milliseconds."""
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return int(round(parsed.timestamp() * 1000))


class Clock(Protocol):
    def now_iso(self) -> str: ...
    def now_ms(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Wall clock, UTC."""

    name = "system"

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def now_iso(self) -> str:
        return iso_from_ms(self.now_ms())

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


class SampleClock:
    """Fixed start + 1 ms per call. Deterministic by construction."""

    name = "sample"

    def __init__(self, start_iso: str) -> None:
        self.start_iso = start_iso
        self._start_ms = parse_iso_ms(start_iso)
        self._ticks = 0

    def now_ms(self) -> int:
        value = self._start_ms + self._ticks
        self._ticks += 1
        return value

    def now_iso(self) -> str:
        return iso_from_ms(self.now_ms())

    def sleep(self, seconds: float) -> None:
        """Advance the clock by `seconds` (rounded to ms) without waiting: a replayed backoff is deterministic."""
        self._ticks += max(0, int(round(seconds * 1000)))
