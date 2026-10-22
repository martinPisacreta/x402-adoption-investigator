"""Compact catalog snapshot loader (gz) and the live Bazaar page crawler (<= budget.bazaar_pages pages). Owner: B1.

options.catalog = "snapshot" loads fixtures/raw/cdp-discovery-catalog-compact.json.gz (coverage basis
"compact snapshot <fetchedAt date>", replayed=True even in live mode); "pages" crawls limit=1000 pages live
(coverage basis "live pages (N x 1000)"). Sample mode always uses the snapshot for catalog metrics and the
committed p0/p1 pages (limit=100) for page-level views. Nothing here computes a metric: the summaries only
read the headers the sources already carry (item counts, reported totals) so coverage can be stated.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from ..clock import Clock
from ..contracts import Fetcher, Observation
from .fetcher import FixtureFetcher
from .manifest import Manifest
from .registry import CDP_PAGE_LIMIT_LIVE, SNAPSHOT_KEY, SNAPSHOT_SOURCE_URL, request_for

SNAPSHOT_BASIS_PREFIX = "compact snapshot"
SNAPSHOT_BASIS = "compact snapshot 2026-10-22"   # the committed snapshot's basis; snapshot_basis(payload) derives it from fetchedAt
SNAPSHOT_NAME = "cdp_catalog_snapshot"


def load_snapshot(path: Path) -> Any:
    """Decompress and parse the compact catalog snapshot."""
    with gzip.open(Path(path), "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def snapshot_basis(payload: Any) -> str:
    fetched = (payload or {}).get("fetchedAt") if isinstance(payload, dict) else None
    return f"{SNAPSHOT_BASIS_PREFIX} {str(fetched)[:10]}" if fetched else SNAPSHOT_BASIS_PREFIX


def pages_basis(pages: int, limit: int) -> str:
    return f"live pages ({pages} x {limit})"


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if items is None:
            items = payload.get("resources")
        return list(items or [])
    return []


def snapshot_summary(payload: Any) -> dict[str, Any]:
    """{items_seen, total_reported, share, hosts, fetched_at, basis} read from the snapshot (no metric here)."""
    items = _items(payload)
    pagination = payload.get("pagination") if isinstance(payload, dict) else None
    total = None
    if isinstance(pagination, dict):
        total = pagination.get("totalReportedByApi", pagination.get("total"))
    hosts = {host_of(str(item.get("resource", ""))) for item in items}
    hosts.discard("")
    share = round(len(items) / total, 6) if isinstance(total, int) and total > 0 else None
    return {"items_seen": len(items), "total_reported": total, "share": share, "hosts": len(hosts),
            "fetched_at": payload.get("fetchedAt") if isinstance(payload, dict) else None,
            "source": payload.get("source") if isinstance(payload, dict) else None,
            "basis": snapshot_basis(payload)}


def load_snapshot_observation(manifest: Optional[Manifest], fixtures_dir: Path, clock: Clock,
                              url: str = SNAPSHOT_SOURCE_URL) -> Observation:
    """The snapshot as an Observation (family cdp_catalog_snapshot, replayed=True in every mode)."""
    fetcher = FixtureFetcher(manifest, fixtures_dir, clock)
    obs = fetcher.fetch(SNAPSHOT_KEY, url, "GET")
    obs.name = SNAPSHOT_NAME
    obs.family = "cdp_catalog_snapshot"
    obs.replayed = True
    if obs.status == "ok" and isinstance(obs.payload, dict):
        obs.url = str(obs.payload.get("source") or url)
        fetched = obs.payload.get("fetchedAt")
        if isinstance(fetched, str) and fetched:
            obs.captured_at = fetched
    return obs


def crawl_pages(fetcher: Fetcher, pages: int, limit: int = CDP_PAGE_LIMIT_LIVE, start_offset: int = 0,
                timeout: float = 10.0, on_page: Optional[Callable[[Observation], None]] = None) -> list[Observation]:
    """Fetch discovery/resources pages offset start, start+limit, ... (<= pages) via the fetcher.

    Stops early when a page comes back empty, unavailable, or past the reported total (the API returns
    items=[] for offset >= total). Every page Observation is named cdp_page_<n>."""
    observations: list[Observation] = []
    total: Optional[int] = None
    for index in range(max(0, int(pages))):
        offset = start_offset + index * limit
        if total is not None and offset >= total:
            break
        item = request_for("cdp_bazaar", "cdp_page", limit=limit, offset=offset)
        obs = fetcher.fetch(item["request_key"], item["url"], item["method"], None, timeout)
        obs.name = item["name"]
        obs.family = "cdp_bazaar"
        observations.append(obs)
        if on_page:
            on_page(obs)
        if obs.status != "ok":
            break
        pagination = obs.payload.get("pagination") if isinstance(obs.payload, dict) else None
        if isinstance(pagination, dict) and isinstance(pagination.get("total"), int):
            total = pagination["total"]
        if not _items(obs.payload):
            break
    return observations


def pages_coverage(observations: list[Observation]) -> dict[str, Any]:
    """Coverage of a page crawl: items seen (deduplicated by resource), max reported total, share, basis."""
    seen: set[str] = set()
    total: Optional[int] = None
    limit: Optional[int] = None
    pages_ok = 0
    for obs in observations:
        if obs.status != "ok" or not isinstance(obs.payload, dict):
            continue
        pages_ok += 1
        for item in _items(obs.payload):
            seen.add(str(item.get("resource", "")))
        pagination = obs.payload.get("pagination") or {}
        if isinstance(pagination.get("total"), int):
            total = max(total or 0, pagination["total"])
        if isinstance(pagination.get("limit"), int):
            limit = pagination["limit"]
    seen.discard("")
    share = round(len(seen) / total, 6) if total else None
    return {"items_seen": len(seen), "total_reported": total, "share": share,
            "basis": pages_basis(pages_ok, limit or CDP_PAGE_LIMIT_LIVE), "pages": pages_ok}


def merge_pages(observations: list[Observation]) -> dict[str, Any]:
    """Pages -> one catalog-shaped document {source, fetchedAt, note, pagination, coverage, items} with items
    deduplicated by resource (first occurrence wins). Same top-level shape as the compact snapshot so the
    catalog view can be built from either; `note` says which one it is."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    fetched_at: Optional[str] = None
    for obs in observations:
        if obs.status != "ok" or not isinstance(obs.payload, dict):
            continue
        fetched_at = fetched_at or obs.captured_at
        for item in _items(obs.payload):
            key = str(item.get("resource", ""))
            if key and key not in seen:
                seen.add(key)
                items.append(item)
    coverage = pages_coverage(observations)
    return {"source": SNAPSHOT_SOURCE_URL, "fetchedAt": fetched_at,
            "note": f"Live crawl of {coverage['pages']} discovery page(s), deduplicated by resource; not the compact snapshot.",
            "pagination": {"limit": None, "pages": coverage["pages"], "totalReportedByApi": coverage["total_reported"]},
            "coverage": coverage, "items": items}
