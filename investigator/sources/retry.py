"""Bounded retry policy shared by the live and the sample Fetcher (SPEC.md 7.2, amended 2026-10-23). Owner: B1.

call_with_retries() is the only retry loop for source requests. LiveFetcher runs it over real HTTP attempts;
FixtureFetcher runs it over replayed attempts, where a recorded fault script can make an attempt fail
(investigator/sources/transient.py, fixtures/transient/README.md). The policy decides the same way in both modes:

- only transient failures are retried: HTTP 429 and 5xx, timeouts, connection errors (reset, refused, DNS); any
  other 4xx, a body over the size limit and every other error stop at the first attempt;
- at most `max_retries` retries (RunConfig.fetch_retries, default 2, `run.py --fetch-retries N`), so a request
  makes at most 1 + max_retries attempts;
- the backoff before retry n is backoff_ms[n - 1] (500 ms, then 1500 ms; the last value repeats), fixed so that a
  log of retries is reproducible;
- `sleep` waits the backoff out: time.sleep in live mode, SampleClock.sleep in sample mode (the deterministic
  clock advances by the backoff and nothing waits), so both modes spend it from budget.max_seconds;
- a backoff that does not fit in what is left of budget.max_seconds (time_left_ms) is not taken: the request gives
  up at once with reason `deadline`;
- on_retry gets one dict per retry, before the backoff; the Collector logs it as a `retry` event.

The outcome is (result, attempts) or a GaveUp exception carrying the last failure, the number of attempts made and
why the loop stopped (`permanent`, `max_attempts` or `deadline`).
"""
from __future__ import annotations

import http
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_MS: tuple[int, ...] = (500, 1500)
MAX_RETRIES_CAP = 5


def is_transient_http(code: Optional[int]) -> bool:
    """429 Too Many Requests and every 5xx are worth another attempt; any other status is an answer."""
    return code is not None and (code == 429 or 500 <= code <= 599)


def http_phrase(code: int) -> str:
    try:
        return http.HTTPStatus(code).phrase
    except ValueError:
        return ""


class AttemptFailed(Exception):
    """One failed attempt. `kind` is http | timeout | connection | other; `transient` decides whether to retry."""

    def __init__(self, reason: str, *, kind: str, transient: bool, http: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.transient = transient
        self.http = http


def http_failure(code: int, phrase: Optional[str] = None) -> AttemptFailed:
    """The failure for an HTTP status, classified by is_transient_http (the same text in both modes)."""
    text = (phrase or http_phrase(code)).strip()
    reason = f"http {code} {text}" if text else f"http {code}"
    return AttemptFailed(reason, kind="http", transient=is_transient_http(code), http=code)


def timeout_failure() -> AttemptFailed:
    return AttemptFailed("timeout", kind="timeout", transient=True)


def connection_failure(detail: str) -> AttemptFailed:
    return AttemptFailed(detail, kind="connection", transient=True)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_ms: tuple[int, ...] = DEFAULT_BACKOFF_MS

    @classmethod
    def from_retries(cls, max_retries: Optional[int]) -> "RetryPolicy":
        value = DEFAULT_MAX_RETRIES if max_retries is None else int(max_retries)
        return cls(max_retries=max(0, min(value, MAX_RETRIES_CAP)))

    @property
    def max_attempts(self) -> int:
        return 1 + max(0, self.max_retries)

    def backoff_for(self, failed_attempt: int) -> int:
        """Milliseconds to wait after attempt `failed_attempt` (1-based) failed."""
        if not self.backoff_ms:
            return 0
        return int(self.backoff_ms[min(failed_attempt, len(self.backoff_ms)) - 1])


class GaveUp(Exception):
    """The loop stopped without a result: `why` is permanent | max_attempts | deadline."""

    def __init__(self, failure: AttemptFailed, attempts: int, why: str, backoff_ms: int = 0) -> None:
        self.failure = failure
        self.attempts = attempts
        self.why = why
        self.backoff_ms = backoff_ms
        super().__init__(self.message)

    @property
    def http(self) -> Optional[int]:
        return self.failure.http

    @property
    def message(self) -> str:
        if self.why == "permanent":
            return f"{self.failure.reason} (not retried: not a transient failure)"
        if self.why == "deadline":
            return (f"{self.failure.reason} on attempt {self.attempts}; not retried: the {self.backoff_ms} ms backoff "
                    "does not fit in what is left of budget.max_seconds")
        return f"{self.failure.reason} after {self.attempts} attempt{'' if self.attempts == 1 else 's'}"


DEFAULT_POLICY = RetryPolicy()


def call_with_retries(attempt: Callable[[int], T], *, policy: RetryPolicy, target: str, url: str, method: str,
                      sleep: Callable[[float], None], on_retry: Optional[Callable[[dict[str, Any]], None]] = None,
                      time_left_ms: Optional[Callable[[], Optional[int]]] = None, replayed: bool = False,
                      extra: Optional[dict[str, Any]] = None) -> tuple[T, int]:
    """Run attempt(1), attempt(2), ... until one returns, a failure is permanent, the attempts run out or the next
    backoff would pass the deadline. Returns (result, attempts used); raises GaveUp otherwise. Only AttemptFailed
    is handled here: any other exception (the sample-mode network guard included) propagates untouched."""
    for number in range(1, policy.max_attempts + 1):
        try:
            return attempt(number), number
        except AttemptFailed as failure:
            if not failure.transient:
                raise GaveUp(failure, number, "permanent") from failure
            if number >= policy.max_attempts:
                raise GaveUp(failure, number, "max_attempts") from failure
            backoff = policy.backoff_for(number)
            left = time_left_ms() if time_left_ms is not None else None
            if left is not None and backoff >= left:
                raise GaveUp(failure, number, "deadline", backoff_ms=backoff) from failure
            if on_retry is not None:
                info: dict[str, Any] = {"target": target, "url": url, "method": method, "attempt": number,
                                        "next_attempt": number + 1, "max_attempts": policy.max_attempts,
                                        "reason": failure.reason, "kind": failure.kind, "http": failure.http,
                                        "backoff_ms": backoff, "replayed": replayed}
                if extra:
                    info.update(extra)
                on_retry(info)
            sleep(backoff / 1000.0)
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover
