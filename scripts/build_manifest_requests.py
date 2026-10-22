#!/usr/bin/env python3
"""Generate MANIFEST.requests (request key -> {file, capturedAt, sha256, http, contentType, url, method,
element?}) from the fixture files. Owner: B1.

Never written by hand: every request key is produced by the SAME builders the runtime uses
(investigator.sources.registry.request_for / canonical_request_key, investigator.chain.base_rpc call builders
+ rpc_request_key), fed with parameters read from the fixture payloads themselves (page limit/offset, echoed
search queries, the vet402/x402-trust query, the Agent402 origin) or from the MANIFEST "sources" capture
record (host, base_rpc sample tx/payer). The two values that no payload echoes (the CDP query-search term and
the store llms.txt path) are declared in CAPTURE_PARAMS below and printed by --explain. Raw responses cached by
LiveFetcher(record_dir=...) come with a <slug>.meta.json sidecar and are indexed as they are.

sha256 is computed over the file bytes on disk (what the FixtureFetcher hashes at replay). Every file on disk
must be covered or the script fails, so a new fixture cannot be forgotten. The existing "sources" section is
kept verbatim as the human capture record.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.chain.base_rpc import (call_balance_of, call_block_number, call_chain_id, call_nonce,  # noqa: E402
                                         call_receipt, call_transaction)
from investigator.sources.manifest import Manifest, ManifestEntry, load_manifest, parse_requests, write_requests  # noqa: E402
from investigator.sources.registry import (SNAPSHOT_FILE, SNAPSHOT_KEY, SNAPSHOT_SOURCE_URL, family_of_key,  # noqa: E402
                                           request_for, rpc_request_key)

CAPTURE_PARAMS: dict[str, Any] = {
    # not echoed by the payload: the term used for the CDP "query=" hybrid search capture
    "cdp_search_query": "textokit",
}
CONTENT_TYPES = {".json": "application/json", ".txt": "text/plain", ".gz": "application/gzip"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_type_of(file: str) -> str:
    return CONTENT_TYPES.get(Path(file).suffix, "application/octet-stream")


def read_json(path: Path) -> Any:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def host_from_sources(sources: dict[str, Any]) -> str:
    """The store host under investigation, read from the vet402 capture URL (?q=<host>)."""
    url = (sources.get("vet402") or {}).get("url") or ""
    host = (parse_qs(urlsplit(url).query).get("q") or [""])[0]
    if not host:
        raise SystemExit("MANIFEST.sources.vet402.url must carry ?q=<host>")
    return host.lower()


def http_of(sources: dict[str, Any], source_name: str) -> int:
    return int((sources.get(source_name) or {}).get("http") or 200)


class Builder:
    def __init__(self, manifest: Manifest, fixtures_dir: Path) -> None:
        self.manifest = manifest
        self.fixtures_dir = fixtures_dir
        self.entries: dict[str, ManifestEntry] = {}
        self.explain: list[tuple[str, str, str]] = []     # (file, request_key, how the key was derived)

    def add(self, file: str, request_key: str, *, url: str, method: str, source_name: str, how: str,
            captured_at: Optional[str] = None, element: Optional[int] = None) -> None:
        path = self.fixtures_dir / file
        if not path.is_file():
            raise SystemExit(f"fixture named by the derivation is missing on disk: {file}")
        if request_key in self.entries:
            raise SystemExit(f"duplicate request key {request_key!r} ({file} and {self.entries[request_key].file})")
        self.entries[request_key] = ManifestEntry(
            request_key=request_key, file=file, captured_at=captured_at or self.manifest.fetched_at,
            sha256=sha256_file(path), http=http_of(self.manifest.sources, source_name),
            content_type=content_type_of(file), url=url, method=method, element=element,
            family=family_of_key(request_key))
        self.explain.append((file, request_key, how))

    def add_item(self, file: str, item: dict[str, Any], source_name: str, how: str,
                 captured_at: Optional[str] = None) -> None:
        self.add(file, item["request_key"], url=item["url"], method=item["method"], source_name=source_name,
                 how=how, captured_at=captured_at)

    def add_rpc(self, file: str, call: dict[str, Any], how: str, element: Optional[int] = None) -> None:
        key = rpc_request_key(call["method"], call["params"])
        self.add(file, key, url="https://mainnet.base.org", method="POST", source_name="base_rpc", how=how,
                 element=element)


def derive(manifest: Manifest, fixtures_dir: Path) -> Builder:
    b = Builder(manifest, fixtures_dir)
    sources = manifest.sources
    host = host_from_sources(sources)

    # store
    b.add_item("store-receipts.json", request_for("receipts", "store_receipts", host=host), "store_receipts",
               "receipts template + host from sources.vet402.url")
    b.add_item("store-openapi.json", request_for("store_openapi", "store_openapi", host=host), "store_openapi",
               "openapi template + host")
    b.add_item("store-llms.txt", request_for("store_openapi", "store_llms", host=host), "store_openapi",
               "llms template + host")

    # CDP discovery pages: limit/offset read from each file's pagination object
    for path in sorted(fixtures_dir.glob("cdp-discovery-resources-p*.json")):
        pagination = read_json(path).get("pagination") or {}
        item = request_for("cdp_bazaar", "cdp_page", limit=int(pagination["limit"]), offset=int(pagination["offset"]))
        b.add_item(path.name, item, "cdp_bazaar_discovery_resources",
                   f"pagination.limit={pagination['limit']} offset={pagination['offset']} read from the file")
    snapshot = read_json(fixtures_dir / SNAPSHOT_FILE)
    b.add(SNAPSHOT_FILE, SNAPSHOT_KEY, url=str(snapshot.get("source") or SNAPSHOT_SOURCE_URL), method="GET",
          source_name="cdp_bazaar_discovery_resources", how="SNAPSHOT_KEY; capturedAt = snapshot.fetchedAt",
          captured_at=snapshot.get("fetchedAt"))

    # CDP search
    b.add_item("cdp-discovery-search-urlSubstring-tienda.json", request_for("cdp_search", "cdp_search_host", host=host),
               "cdp_bazaar_discovery_search", "search urlSubstring template + host")
    b.add_item("cdp-discovery-search-query-textokit.json",
               request_for("cdp_search", "cdp_search_query", query=CAPTURE_PARAMS["cdp_search_query"]),
               "cdp_bazaar_discovery_search", f"search query template + CAPTURE_PARAMS[cdp_search_query]={CAPTURE_PARAMS['cdp_search_query']!r}")

    # Agent402
    index = read_json(fixtures_dir / "agent402-index-seller-tienda.json")
    origin_host = (urlsplit(str(index.get("origin") or "")).hostname or host).lower()
    b.add_item("agent402-index-seller-tienda.json", request_for("agent402", "agent402_index", host=origin_host),
               "agent402", "index template + host of payload.origin")
    route = read_json(fixtures_dir / "agent402-route-x402-adoption.json")
    b.add_item("agent402-route-x402-adoption.json", request_for("agent402", "agent402_route", query=str(route["query"])),
               "agent402", "route template + payload.query")

    # x402-trust
    search = read_json(fixtures_dir / "x402-trust-search-tienda.json")
    b.add_item("x402-trust-search-tienda.json", request_for("x402_trust", "trust_search", host=str(search["query"])),
               "x402_trust", "trust search template + payload.query (limit/offset echoed: "
                             f"{search.get('limit')}/{search.get('offset')})")
    for file, name in (("x402-trust-stats.json", "trust_stats"), ("x402-trust-verified-p0.json", "trust_verified"),
                       ("x402-trust-facilitators.json", "trust_facilitators"), ("x402-trust-report.json", "trust_report"),
                       ("x402-trust-leaderboard-p0.json", "trust_leaderboard"), ("x402-trust-activity.json", "trust_activity"),
                       ("x402-trust-openapi.json", "trust_openapi"), ("x402-trust-llms.txt", "trust_llms")):
        b.add_item(file, request_for("x402_trust", name), "x402_trust", f"{name} template (fixed URL)")
    semantic = read_json(fixtures_dir / "x402-trust-semantic-search-argentina-tax-id.json")
    body = {"query": str(semantic["query"]), "limit": int(semantic.get("count") or 10)}
    b.add_item("x402-trust-semantic-search-argentina-tax-id.json",
               request_for("x402_trust", "trust_semantic_search", body=body), "x402_trust",
               "semantic-search POST + body {query, limit} from payload.query / payload.count (never fetched in a run)")

    # vet402
    resolve = read_json(fixtures_dir / "vet402-resolve-tienda.json")
    vet_host = str(((resolve.get("query") or {}).get("value")) or host)
    b.add_item("vet402-resolve-tienda.json", request_for("vet402", "vet402_resolve", host=vet_host), "vet402",
               "resolve template + payload.query.value")

    # Base RPC: keys from the chain call builders over the sample in the capture record
    sample = (sources.get("base_rpc") or {}).get("sample") or {}
    tx, payer, usdc = sample["tx"], sample["payer"], sample.get("usdc")
    b.add_rpc("base-rpc-eth_getTransactionReceipt.json", call_receipt(tx), "call_receipt(sources.base_rpc.sample.tx)")
    b.add_rpc("base-rpc-eth_getTransactionByHash.json", call_transaction(tx), "call_transaction(sample.tx)")
    b.add_rpc("base-rpc-eth_call-usdc-balanceOf-payer.json", call_balance_of(payer, usdc) if usdc else call_balance_of(payer),
              "call_balance_of(sample.payer, sample.usdc)")
    b.add_rpc("base-rpc-eth_getTransactionCount-payer.json", call_nonce(payer), "call_nonce(sample.payer)")
    b.add_rpc("base-rpc-batch-blockNumber-chainId.json", call_block_number(), "call_block_number(); element 0", element=0)
    b.add_rpc("base-rpc-batch-blockNumber-chainId.json", call_chain_id(), "call_chain_id(); element 1", element=1)

    # Raw responses cached by LiveFetcher(record_dir=fixtures/raw): indexed from their sidecar
    for meta_path in sorted(fixtures_dir.glob("*.meta.json")):
        meta = read_json(meta_path)
        if meta.get("request_key") in b.entries or not (fixtures_dir / meta["file"]).is_file():
            continue
        b.add(meta["file"], meta["request_key"], url=meta["url"], method=meta.get("method", "GET"),
              source_name="recorded", how=f"sidecar {meta_path.name}", captured_at=meta.get("capturedAt"))
    return b


def uncovered_files(builder: Builder, fixtures_dir: Path) -> list[str]:
    covered = {e.file for e in builder.entries.values()}
    skip = {"MANIFEST.json"}
    return sorted(p.name for p in fixtures_dir.iterdir()
                  if p.is_file() and p.name not in skip and not p.name.endswith(".meta.json") and p.name not in covered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(REPO_ROOT / "fixtures" / "raw" / "MANIFEST.json"))
    parser.add_argument("--check", action="store_true", help="exit 1 when the generated map differs from the file")
    parser.add_argument("--explain", action="store_true", help="print how every request key was derived")
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)
    fixtures_dir = manifest_path.parent
    manifest = load_manifest(manifest_path)
    builder = derive(manifest, fixtures_dir)
    missing = uncovered_files(builder, fixtures_dir)
    if missing:
        print("fixture files on disk with no derived request key (register them in derive()):", file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        return 2
    generated = {k: v.to_json() for k, v in builder.entries.items()}
    if args.explain:
        for file, key, how in builder.explain:
            print(f"{file}\n    {key}\n    <- {how}")
    if args.check:
        current = {k: v.to_json() for k, v in parse_requests(json.loads(manifest_path.read_text(encoding="utf-8"))).items()}
        if current != generated:
            added = sorted(set(generated) - set(current))
            removed = sorted(set(current) - set(generated))
            changed = sorted(k for k in set(current) & set(generated) if current[k] != generated[k])
            print(f"MANIFEST.requests is stale: +{len(added)} -{len(removed)} ~{len(changed)}", file=sys.stderr)
            for k in added:
                print(f"  + {k}", file=sys.stderr)
            for k in removed:
                print(f"  - {k}", file=sys.stderr)
            for k in changed:
                print(f"  ~ {k}", file=sys.stderr)
            return 1
        print(f"MANIFEST.requests is current: {len(generated)} request keys over {len({e['file'] for e in generated.values()})} files")
        return 0
    write_requests(manifest_path, builder.entries)
    print(f"wrote {len(generated)} request keys over {len({e['file'] for e in generated.values()})} files to {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
