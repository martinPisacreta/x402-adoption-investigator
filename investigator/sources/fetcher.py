"""Fetcher implementations (SPEC.md section 7.2). Owner: B1.

- LiveFetcher: urllib, per-request timeout, 2 retries (0.5 s / 1.5 s) on timeout / connection errors / 5xx /
  429, gzip accepted, sha256 of the raw body bytes, identifying User-Agent; never stores or logs request
  headers. A failed request after the last attempt raises FetchError (a 4xx other than 429 fails at once).
  Optional `record_dir` caches every successful raw response as <slug>.<ext> + <slug>.meta.json (how new
  fixtures are captured from a live run; scripts/build_manifest_requests.py then indexes them).
- FixtureFetcher: request_key -> fixtures/raw/<file> via MANIFEST.requests; replayed=True; captured_at from the
  manifest; latency_ms=0; a key with no fixture -> status fixture_miss (nothing fabricated). A manifest entry
  with `element` picks one element of a JSON array file (the 2-element JSON-RPC batch capture).
- FallbackFetcher: LiveFetcher then FixtureFetcher with stale=True on failure; mode "live+fixture-fallback".

All three expose raw_bytes(request_key) so the Collector can write logs/<run_id>/raw/<name>.<ext> without the
Observation contract carrying bytes.
"""
from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from .. import USER_AGENT
from ..clock import Clock
from ..config import RunConfig
from ..contracts import Fetcher, Observation
from .manifest import Manifest
from .registry import family_of_key

RETRY_BACKOFF_S: tuple[float, ...] = (0.5, 1.5)
RETRYABLE_HTTP: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_BODY_BYTES = 64 * 1024 * 1024
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class FetchError(Exception):
    """A live fetch failed after retries (timeout, connection error, 5xx) or at once (other 4xx)."""

    def __init__(self, message: str, http: Optional[int] = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.http = http
        self.attempts = attempts


class _Retryable(Exception):
    def __init__(self, reason: str, http: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http = http


def slug_of(request_key: str) -> str:
    """Filesystem-safe name for a request key (used for raw caches)."""
    text = request_key.strip()
    text = re.sub(r"^(GET|POST|RPC|SNAPSHOT)\s+", lambda m: m.group(1).lower() + "-", text)
    text = text.replace("https://", "").replace("http://", "")
    return _SLUG_RE.sub("-", text).strip("-")[:120] or "request"


def _family_of(request_key: str) -> str:
    return family_of_key(request_key)


def _parse_payload(data: bytes, content_type: Optional[str]) -> Any:
    stripped = data.lstrip()
    looks_json = stripped.startswith((b"{", b"["))
    if content_type and "json" not in content_type and not looks_json:
        return data.decode("utf-8", errors="replace")
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return data.decode("utf-8", errors="replace")


def _ext_for(payload: Any, file: Optional[str] = None) -> str:
    if file:
        if file.endswith(".json.gz"):
            return ".json.gz"
        suffix = Path(file).suffix
        if suffix:
            return suffix
    return ".json" if isinstance(payload, (dict, list)) else ".txt"


def _observation(request_key: str, url: str, method: str, *, status: str, clock: Clock, http: Optional[int] = None,
                 sha256: Optional[str] = None, latency_ms: int = 0, captured_at: Optional[str] = None,
                 replayed: bool = False, stale: bool = False, error: Optional[str] = None, payload: Any = None,
                 fixture_file: Optional[str] = None) -> Observation:
    name = request_key.split(" ", 1)[-1]
    return Observation(name=name, family=_family_of(request_key), request_key=request_key, url=url, method=method,
                       http=http, bytes_sha256=sha256, latency_ms=latency_ms,
                       captured_at=captured_at or clock.now_iso(), replayed=replayed, stale=stale,
                       status=status, error=error, payload=payload, fixture_file=fixture_file)  # type: ignore[arg-type]


class LiveFetcher:
    mode = "live"

    def __init__(self, clock: Clock, raw_dir: Optional[Path] = None, timeout: float = 10.0,
                 user_agent: str = USER_AGENT, *, opener: Optional[Callable[..., Any]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 on_retry: Optional[Callable[[dict[str, Any]], None]] = None,
                 record_dir: Optional[Path] = None, backoff_s: tuple[float, ...] = RETRY_BACKOFF_S) -> None:
        self.clock = clock
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self.timeout = timeout
        self.user_agent = user_agent
        self._opener = opener
        self._sleep = sleep or time.sleep
        self.on_retry = on_retry
        self.record_dir = Path(record_dir) if record_dir else None
        self.backoff_s = tuple(backoff_s)
        self._raw: dict[str, bytes] = {}

    # -- one attempt ---------------------------------------------------------------------------------------
    def _request_once(self, url: str, method: str, body: Any, timeout: float) -> tuple[bytes, int, Optional[str]]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
                   "Accept-Encoding": "gzip"}
        data: Optional[bytes] = None
        if body is not None and method.upper() != "GET":
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        opener = self._opener or urllib.request.urlopen     # looked up at call time: the sample guard patches it
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
                status = int(getattr(response, "status", None) or response.getcode())
                content_type = response.headers.get("Content-Type") if getattr(response, "headers", None) else None
                encoding = response.headers.get("Content-Encoding", "") if getattr(response, "headers", None) else ""
        except urllib.error.HTTPError as exc:
            if exc.code in RETRYABLE_HTTP:
                raise _Retryable(f"http {exc.code}", exc.code) from exc
            raise FetchError(f"http {exc.code} {exc.reason}", http=exc.code) from exc
        except urllib.error.URLError as exc:
            raise _Retryable(f"url error: {exc.reason}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise _Retryable("timeout") from exc
        except (http.client.HTTPException, ConnectionError, OSError) as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        if len(raw) > MAX_BODY_BYTES:
            raise FetchError(f"body larger than {MAX_BODY_BYTES} bytes", http=status)
        if encoding and "gzip" in encoding.lower():
            raw = gzip.decompress(raw)
        if status >= 400:
            if status in RETRYABLE_HTTP:
                raise _Retryable(f"http {status}", status)
            raise FetchError(f"http {status}", http=status)
        return raw, status, content_type

    # -- public ----------------------------------------------------------------------------------------------
    def fetch(self, request_key: str, url: str, method: str = "GET", body: Any = None,
              timeout: float = 10.0) -> Observation:
        """urllib request with retries; raises FetchError after the last attempt; caches the raw bytes."""
        timeout = float(timeout or self.timeout)
        max_attempts = 1 + len(self.backoff_s)
        started = time.perf_counter()
        last_reason = "unknown"
        last_http: Optional[int] = None
        for attempt in range(1, max_attempts + 1):
            try:
                raw, status, content_type = self._request_once(url, method, body, timeout)
                break
            except _Retryable as exc:
                last_reason, last_http = exc.reason, exc.http
                if attempt == max_attempts:
                    raise FetchError(f"{exc.reason} after {attempt} attempts", http=exc.http, attempts=attempt) from exc
                backoff = self.backoff_s[attempt - 1]
                if self.on_retry:
                    self.on_retry({"target": request_key, "attempt": attempt, "max_attempts": max_attempts,
                                   "reason": exc.reason, "backoff_ms": int(backoff * 1000)})
                self._sleep(backoff)
        else:  # pragma: no cover - loop always breaks or raises
            raise FetchError(f"{last_reason} after {max_attempts} attempts", http=last_http, attempts=max_attempts)
        latency_ms = int((time.perf_counter() - started) * 1000)
        digest = hashlib.sha256(raw).hexdigest()
        payload = _parse_payload(raw, content_type)
        self._raw[request_key] = raw
        captured_at = self.clock.now_iso()
        self._cache(request_key, url, method, raw, payload, status, content_type, captured_at, digest)
        return _observation(request_key, url, method, status="ok", clock=self.clock, http=status, sha256=digest,
                            latency_ms=latency_ms, captured_at=captured_at, payload=payload)

    def raw_bytes(self, request_key: str) -> Optional[bytes]:
        return self._raw.get(request_key)

    def _cache(self, request_key: str, url: str, method: str, raw: bytes, payload: Any, status: int,
               content_type: Optional[str], captured_at: str, digest: str) -> None:
        slug = slug_of(request_key)
        ext = _ext_for(payload)
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            (self.raw_dir / f"{slug}{ext}").write_bytes(raw)
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / f"{slug}{ext}").write_bytes(raw)
            meta = {"request_key": request_key, "url": url, "method": method, "capturedAt": captured_at,
                    "sha256": digest, "http": status, "contentType": content_type, "file": f"{slug}{ext}"}
            (self.record_dir / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")


class FixtureFetcher:
    mode = "fixture"

    def __init__(self, manifest: Optional[Manifest], fixtures_dir: Path, clock: Clock) -> None:
        self.manifest = manifest
        self.fixtures_dir = Path(fixtures_dir)
        self.clock = clock
        self._raw: dict[str, bytes] = {}

    def fetch(self, request_key: str, url: str, method: str = "GET", body: Any = None,
              timeout: float = 10.0) -> Observation:
        entry = self.manifest.requests.get(request_key) if self.manifest else None
        if entry is None:
            reason = "no fixture for this request key" if self.manifest and self.manifest.requests else (
                "MANIFEST.requests is empty: run scripts/build_manifest_requests.py")
            return _observation(request_key, url, method, status="fixture_miss", clock=self.clock, replayed=True,
                                error=reason)
        path = self.fixtures_dir / entry.file
        if not path.is_file():
            return _observation(request_key, url, method, status="fixture_miss", clock=self.clock, replayed=True,
                                error=f"fixture file missing on disk: fixtures/raw/{entry.file}",
                                fixture_file=f"fixtures/raw/{entry.file}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self._raw[request_key] = data
        body_bytes = gzip.decompress(data) if entry.file.endswith(".gz") else data
        payload = _parse_payload(body_bytes, entry.content_type)
        if entry.element is not None:
            if isinstance(payload, list) and 0 <= entry.element < len(payload):
                payload = payload[entry.element]
            else:
                return _observation(request_key, url, method, status="fixture_miss", clock=self.clock, replayed=True,
                                    error=f"fixture element {entry.element} not present in fixtures/raw/{entry.file}",
                                    fixture_file=f"fixtures/raw/{entry.file}")
        return _observation(request_key, url, method, status="ok", clock=self.clock, http=entry.http, sha256=digest,
                            latency_ms=0, captured_at=entry.captured_at, replayed=True, payload=payload,
                            fixture_file=f"fixtures/raw/{entry.file}")

    def raw_bytes(self, request_key: str) -> Optional[bytes]:
        return self._raw.get(request_key)


class FallbackFetcher:
    mode = "live+fixture-fallback"

    def __init__(self, live: LiveFetcher, fixture: FixtureFetcher) -> None:
        self.live = live
        self.fixture = fixture
        self.fallbacks: list[dict[str, Any]] = []

    @property
    def on_retry(self) -> Optional[Callable[[dict[str, Any]], None]]:
        return self.live.on_retry

    @on_retry.setter
    def on_retry(self, callback: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self.live.on_retry = callback

    def fetch(self, request_key: str, url: str, method: str = "GET", body: Any = None,
              timeout: float = 10.0) -> Observation:
        try:
            return self.live.fetch(request_key, url, method, body, timeout)
        except FetchError as exc:
            obs = self.fixture.fetch(request_key, url, method, body, timeout)
            live_error = f"live fetch failed: {exc}"
            if obs.status == "ok":
                obs.stale = True
                obs.error = live_error
                self.fallbacks.append({"request_key": request_key, "reason": str(exc), "http": exc.http})
            else:
                obs.status = "unavailable"
                obs.stale = False
                obs.replayed = False
                obs.error = f"{live_error}; {obs.error}"
            return obs

    def raw_bytes(self, request_key: str) -> Optional[bytes]:
        return self.live.raw_bytes(request_key) or self.fixture.raw_bytes(request_key)


def build_fetcher(config: RunConfig, manifest: Optional[Manifest], clock: Clock, run_dir: Path) -> Fetcher:
    fixture = FixtureFetcher(manifest, config.fixtures_dir, clock)
    if config.is_sample():
        return fixture
    return FallbackFetcher(LiveFetcher(clock), fixture)


def fixture_fetcher_for(fetcher: Any) -> Optional[FixtureFetcher]:
    """The FixtureFetcher behind any mounted fetcher (the snapshot is served from fixtures in every mode)."""
    if isinstance(fetcher, FixtureFetcher):
        return fetcher
    inner = getattr(fetcher, "fixture", None)
    return inner if isinstance(inner, FixtureFetcher) else None
