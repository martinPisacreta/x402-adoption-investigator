"""Request keys, fixture resolution for every fixture, live retries, fallback stale flag, RPC over fixtures
(SPEC.md section 7). Owner: B1."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.chain.base_rpc import BaseRpcClient, result_of, settlement_batch  # noqa: E402
from investigator.clock import SampleClock  # noqa: E402
from investigator.contracts import to_json  # noqa: E402
from investigator.sources.fetcher import FallbackFetcher, FetchError, FixtureFetcher, LiveFetcher, slug_of  # noqa: E402
from investigator.sources.manifest import load_manifest  # noqa: E402
from investigator.sources.registry import (SNAPSHOT_KEY, canonical_request_key, canonical_url,  # noqa: E402
                                           default_fetch_list, family_of_key, request_for, rpc_request_key)

FIXTURES = REPO_ROOT / "fixtures" / "raw"
MANIFEST = load_manifest(FIXTURES / "MANIFEST.json")


# ----------------------------------------------------------------------------------------------------------
# Test doubles for urllib (no socket is ever opened)
# ----------------------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/x", code, "err", {}, io.BytesIO(b""))


class ScriptedOpener:
    """Each call pops the next scripted outcome: a FakeResponse to return or an Exception to raise."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float = 0.0) -> FakeResponse:
        self.calls.append({"url": request.full_url, "method": request.get_method(), "timeout": timeout,
                           "headers": dict(request.header_items()), "data": request.data})
        outcome = self.script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def live(script: list[Any], **kwargs: Any) -> tuple[LiveFetcher, ScriptedOpener, list[float], list[dict[str, Any]]]:
    opener = ScriptedOpener(script)
    sleeps: list[float] = []
    retries: list[dict[str, Any]] = []
    fetcher = LiveFetcher(SampleClock(MANIFEST.fetched_at), opener=opener, sleep=sleeps.append,
                          on_retry=retries.append, **kwargs)
    return fetcher, opener, sleeps, retries


# ----------------------------------------------------------------------------------------------------------

class RequestKeys(unittest.TestCase):
    def test_canonical_url_sorts_query_and_lowercases_host(self) -> None:
        self.assertEqual(canonical_url("HTTPS://Api.CDP.coinbase.com/platform/v2/x402/discovery/resources?offset=0&limit=100#x"),
                         "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=0")
        self.assertEqual(canonical_url("https://mainnet.base.org/"), "https://mainnet.base.org")
        self.assertEqual(canonical_request_key("get", "https://tienda.textokit.store/receipts.json"),
                         "GET https://tienda.textokit.store/receipts.json")

    def test_post_body_changes_the_key(self) -> None:
        a = canonical_request_key("POST", "https://x402-trust.com/v1/semantic-search", {"query": "a", "limit": 10})
        b = canonical_request_key("POST", "https://x402-trust.com/v1/semantic-search", {"query": "b", "limit": 10})
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("POST https://x402-trust.com/v1/semantic-search "))
        self.assertEqual(canonical_request_key("GET", "https://a.b/c", {"ignored": 1}), "GET https://a.b/c")

    def test_rpc_key_is_stable(self) -> None:
        a = rpc_request_key("eth_getTransactionByHash", ["0xabc"])
        self.assertEqual(a, rpc_request_key("eth_getTransactionByHash", ["0xabc"]))
        self.assertTrue(a.startswith("RPC eth_getTransactionByHash "))
        self.assertEqual(len(a.split(" ")[-1]), 16)

    def test_request_for_builds_canonical_items(self) -> None:
        item = request_for("cdp_search", "cdp_search_host", host="Tienda.Textokit.Store")
        self.assertEqual(item["name"], "cdp_search_host")
        self.assertEqual(item["request_key"],
                         "GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?limit=20&urlSubstring=tienda.textokit.store")
        page = request_for("cdp_bazaar", "cdp_page", limit=100, offset=100)
        self.assertEqual((page["name"], page["page"]), ("cdp_page_1", 1))
        route = request_for("agent402", "agent402_route", query="x402 adoption")
        self.assertIn("q=x402%20adoption", route["url"])
        self.assertIn("q=x402+adoption", route["request_key"])
        snapshot = request_for("cdp_catalog_snapshot", "cdp_catalog_snapshot")
        self.assertEqual(snapshot["request_key"], SNAPSHOT_KEY)
        rpc = request_for("base_rpc", "base_rpc_batch")
        self.assertTrue(rpc["deferred"])
        default_store = request_for("receipts", "store_receipts")                       # the Planner's call shape
        self.assertEqual(default_store["url"], "https://tienda.textokit.store/receipts.json")
        other_store = request_for("store_openapi", "store_openapi", host="Other.Example")
        self.assertEqual(other_store["request_key"], "GET https://other.example/openapi.json")
        with self.assertRaises(ValueError):
            request_for("vet402", "vet402_resolve")            # host missing for a host-parametrised template
        with self.assertRaises(ValueError):
            request_for("nope", "x")

    def test_family_of_key_uses_host_and_path(self) -> None:
        self.assertEqual(family_of_key("GET https://x402-trust.com/openapi.json"), "x402_trust")
        self.assertEqual(family_of_key("GET https://tienda.textokit.store/openapi.json"), "store_openapi")
        self.assertEqual(family_of_key("GET https://tienda.textokit.store/receipts.json"), "receipts")
        self.assertEqual(family_of_key("GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?limit=20&urlSubstring=x"), "cdp_search")
        self.assertEqual(family_of_key("GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=0"), "cdp_bazaar")
        self.assertEqual(family_of_key(SNAPSHOT_KEY), "cdp_catalog_snapshot")
        self.assertEqual(family_of_key("RPC eth_chainId 0000000000000000"), "base_rpc")

    def test_default_fetch_lists_are_fixture_covered(self) -> None:
        """Every non-deferred key the default lists build for the committed examples has a fixture."""
        lists = [
            default_fetch_list("seller_profile", {"host": "tienda.textokit.store"}, fixture_mode=True),
            default_fetch_list("catalog_concentration", {"query": "cdp bazaar"}, fixture_mode=True),
            default_fetch_list("payer_dossier", {"host": "tienda.textokit.store", "wallet": "0x" + "0" * 40}, fixture_mode=True),
        ]
        for items in lists:
            self.assertTrue(any(not it.get("optional", True) for it in items), "each playbook has a mandatory source")
            for item in items:
                if item.get("deferred"):
                    continue
                self.assertIn(item["request_key"], MANIFEST.requests, item["name"])
        pages = [it for it in lists[1] if it["family"] == "cdp_bazaar"]
        self.assertEqual([p["name"] for p in pages], ["cdp_page_0", "cdp_page_1"])
        self.assertTrue(all(p["limit"] == 100 for p in pages), "fixture mode replays the committed limit=100 pages")
        live_pages = [it for it in default_fetch_list("catalog_concentration", {}, catalog="pages", bazaar_pages=3)
                      if it["family"] == "cdp_bazaar"]
        self.assertEqual([p["offset"] for p in live_pages], [0, 1000, 2000])

    def test_slug_is_filesystem_safe(self) -> None:
        slug = slug_of("GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?limit=20&urlSubstring=x")
        self.assertNotIn("/", slug)
        self.assertNotIn(":", slug)
        self.assertTrue(slug.startswith("get-api.cdp.coinbase.com"))


class FixtureResolution(unittest.TestCase):
    def test_manifest_and_disk_name_the_same_fixture_files(self) -> None:
        """Every file the manifest names exists, and every response file on disk is named by the manifest
        (26 responses + MANIFEST.json on 2026-10-22; the count is read, never typed)."""
        named = set(MANIFEST.files())
        on_disk = {p.name for p in FIXTURES.iterdir() if p.is_file() and p.name != "MANIFEST.json"}
        self.assertEqual(named, on_disk)
        self.assertGreater(len(named), 0)
        self.assertEqual(set(MANIFEST.request_files()), on_disk, "MANIFEST.requests covers every file on disk")

    def test_fixture_miss_is_visible_never_fabricated(self) -> None:
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        obs = fetcher.fetch("GET https://example.invalid/nothing", "https://example.invalid/nothing")
        self.assertEqual(obs.status, "fixture_miss")
        self.assertTrue(obs.replayed)
        self.assertIsNone(obs.payload)
        self.assertIsNone(obs.bytes_sha256)

    def test_every_request_key_resolves_to_its_fixture(self) -> None:
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        self.assertGreater(len(MANIFEST.requests), 0, "run scripts/build_manifest_requests.py")
        for key, entry in MANIFEST.requests.items():
            obs = fetcher.fetch(key, entry.url or "", entry.method)
            self.assertEqual(obs.status, "ok", key)
            self.assertTrue(obs.replayed)
            self.assertFalse(obs.stale)
            self.assertEqual(obs.bytes_sha256, entry.sha256, key)
            self.assertEqual(obs.bytes_sha256, hashlib.sha256((FIXTURES / entry.file).read_bytes()).hexdigest())
            self.assertEqual(obs.captured_at, entry.captured_at)
            self.assertEqual(obs.fixture_file, f"fixtures/raw/{entry.file}")
            self.assertIsNotNone(obs.payload)
            self.assertEqual(obs.latency_ms, 0)
            self.assertEqual(obs.http, entry.http)
            self.assertEqual(fetcher.raw_bytes(key), (FIXTURES / entry.file).read_bytes())

    def test_batch_fixture_elements_are_split_by_request_key(self) -> None:
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        block = fetcher.fetch(rpc_request_key("eth_blockNumber", []), "https://mainnet.base.org", "POST")
        chain = fetcher.fetch(rpc_request_key("eth_chainId", []), "https://mainnet.base.org", "POST")
        self.assertIsInstance(block.payload, dict)
        self.assertEqual(chain.payload.get("result"), "0x2105")
        self.assertNotEqual(block.payload.get("result"), chain.payload.get("result"))
        self.assertEqual(block.fixture_file, chain.fixture_file)

    def test_snapshot_is_decompressed_and_hashed_as_bytes(self) -> None:
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        obs = fetcher.fetch(SNAPSHOT_KEY, "https://api.cdp.coinbase.com/x")
        entry = MANIFEST.requests[SNAPSHOT_KEY]
        self.assertEqual(obs.status, "ok")
        self.assertIsInstance(obs.payload, dict)
        self.assertEqual(len(obs.payload["items"]), len(json.loads(gzip.decompress((FIXTURES / entry.file).read_bytes()))["items"]))
        self.assertEqual(obs.bytes_sha256, entry.sha256)
        self.assertEqual(obs.captured_at, obs.payload["fetchedAt"])


class LiveRetries(unittest.TestCase):
    def test_retries_on_timeout_and_connection_error_then_succeeds(self) -> None:
        body = b'{"ok": true, "n": 3}'
        fetcher, opener, sleeps, retries = live([socket.timeout("t"), urllib.error.URLError("refused"), FakeResponse(body)])
        obs = fetcher.fetch("GET https://example.test/a", "https://example.test/a", timeout=2.5)
        self.assertEqual(obs.status, "ok")
        self.assertEqual(obs.http, 200)
        self.assertEqual(obs.payload, {"ok": True, "n": 3})
        self.assertEqual(obs.bytes_sha256, hashlib.sha256(body).hexdigest())
        self.assertFalse(obs.replayed)
        self.assertEqual(sleeps, [0.5, 1.5])
        self.assertEqual([r["attempt"] for r in retries], [1, 2])
        self.assertEqual([r["backoff_ms"] for r in retries], [500, 1500])
        self.assertEqual(retries[0]["max_attempts"], 3)
        self.assertEqual(retries[0]["target"], "GET https://example.test/a")
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(opener.calls[0]["timeout"], 2.5)
        self.assertEqual(fetcher.raw_bytes("GET https://example.test/a"), body)

    def test_5xx_is_retried_then_raises_fetch_error(self) -> None:
        fetcher, opener, sleeps, retries = live([http_error(503), http_error(502), http_error(500)])
        with self.assertRaises(FetchError) as ctx:
            fetcher.fetch("GET https://example.test/b", "https://example.test/b")
        self.assertEqual(ctx.exception.http, 500)
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(len(retries), 2)
        self.assertEqual(len(opener.calls), 3)

    def test_4xx_fails_fast_without_retry(self) -> None:
        fetcher, opener, sleeps, retries = live([http_error(404)])
        with self.assertRaises(FetchError) as ctx:
            fetcher.fetch("GET https://example.test/c", "https://example.test/c")
        self.assertEqual(ctx.exception.http, 404)
        self.assertEqual(sleeps, [])
        self.assertEqual(retries, [])
        self.assertEqual(len(opener.calls), 1)

    def test_gzip_body_is_decoded_and_hashed_decoded(self) -> None:
        body = b'[{"a": 1}]'
        fetcher, _, _, _ = live([FakeResponse(gzip.compress(body), headers={"Content-Type": "application/json",
                                                                             "Content-Encoding": "gzip"})])
        obs = fetcher.fetch("GET https://example.test/d", "https://example.test/d")
        self.assertEqual(obs.payload, [{"a": 1}])
        self.assertEqual(obs.bytes_sha256, hashlib.sha256(body).hexdigest())

    def test_text_body_is_kept_as_text(self) -> None:
        fetcher, _, _, _ = live([FakeResponse(b"# hello\n", headers={"Content-Type": "text/plain"})])
        obs = fetcher.fetch("GET https://example.test/llms.txt", "https://example.test/llms.txt")
        self.assertEqual(obs.payload, "# hello\n")

    def test_identifying_user_agent_and_no_headers_in_observation(self) -> None:
        fetcher, opener, _, _ = live([FakeResponse(b"{}")])
        obs = fetcher.fetch("POST https://example.test/rpc", "https://example.test/rpc", "POST", {"jsonrpc": "2.0"})
        sent = {k.lower(): v for k, v in opener.calls[0]["headers"].items()}
        self.assertTrue(sent["user-agent"].startswith("x402-adoption-investigator/"))
        self.assertEqual(sent["content-type"], "application/json")
        self.assertEqual(opener.calls[0]["method"], "POST")
        self.assertEqual(json.loads(opener.calls[0]["data"]), {"jsonrpc": "2.0"})
        serialised = json.dumps(to_json(obs)).lower()
        self.assertNotIn("user-agent", serialised)
        self.assertNotIn("authorization", serialised)

    def test_record_dir_caches_raw_bytes_with_a_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher, _, _, _ = live([FakeResponse(b'{"x": 1}')], record_dir=Path(tmp), raw_dir=Path(tmp) / "raw")
            obs = fetcher.fetch("GET https://example.test/e?b=2&a=1", "https://example.test/e?b=2&a=1")
            files = sorted(p.name for p in Path(tmp).iterdir())
            metas = [p for p in Path(tmp).glob("*.meta.json")]
            self.assertEqual(len(metas), 1)
            meta = json.loads(metas[0].read_text(encoding="utf-8"))
            self.assertEqual(meta["request_key"], "GET https://example.test/e?b=2&a=1")
            self.assertEqual(meta["sha256"], obs.bytes_sha256)
            self.assertIn(meta["file"], files)
            self.assertEqual((Path(tmp) / meta["file"]).read_bytes(), b'{"x": 1}')
            self.assertTrue(any((Path(tmp) / "raw").iterdir()))


class Fallback(unittest.TestCase):
    def test_fallback_marks_stale(self) -> None:
        fetcher_live, opener, _, _ = live([http_error(503), http_error(503), http_error(503)])
        fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        fallback = FallbackFetcher(fetcher_live, fixture)
        self.assertEqual(fallback.mode, "live+fixture-fallback")
        key = "GET https://tienda.textokit.store/receipts.json"
        obs = fallback.fetch(key, "https://tienda.textokit.store/receipts.json")
        self.assertEqual(obs.status, "ok")
        self.assertTrue(obs.stale)
        self.assertTrue(obs.replayed)
        self.assertIn("live fetch failed", obs.error or "")
        self.assertEqual(obs.bytes_sha256, MANIFEST.requests[key].sha256)
        self.assertEqual(obs.captured_at, MANIFEST.requests[key].captured_at)
        self.assertEqual(len(fallback.fallbacks), 1)
        self.assertEqual(fallback.raw_bytes(key), (FIXTURES / "store-receipts.json").read_bytes())

    def test_fallback_without_fixture_is_unavailable(self) -> None:
        fetcher_live, _, _, _ = live([http_error(404)])
        fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        fallback = FallbackFetcher(fetcher_live, fixture)
        obs = fallback.fetch("GET https://example.invalid/none", "https://example.invalid/none")
        self.assertEqual(obs.status, "unavailable")
        self.assertFalse(obs.stale)
        self.assertFalse(obs.replayed)
        self.assertIsNone(obs.payload)
        self.assertIn("http 404", obs.error or "")

    def test_live_success_is_not_marked_stale(self) -> None:
        fetcher_live, _, _, _ = live([FakeResponse(b'{"sales": []}')])
        fallback = FallbackFetcher(fetcher_live, FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at)))
        obs = fallback.fetch("GET https://tienda.textokit.store/receipts.json", "https://tienda.textokit.store/receipts.json")
        self.assertEqual((obs.status, obs.stale, obs.replayed), ("ok", False, False))


class RpcOverFixtures(unittest.TestCase):
    TX = "0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3"
    PAYER = "0x097e3F339D0b023605e12A6B81E2d6Cb7571475a"

    def test_settlement_batch_is_served_element_by_element(self) -> None:
        fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        seen: list[str] = []
        client = BaseRpcClient(fixture, observer=lambda o: seen.append(o.name))
        unknown = "0x" + "ab" * 32
        observations = client.batch(settlement_batch([self.TX, unknown], self.PAYER))
        by_name = {o.name: o for o in observations}
        self.assertEqual(len(observations), 8)
        self.assertEqual(seen, [o.name for o in observations])
        self.assertEqual(by_name[f"rpc_receipt_{self.TX}"].status, "ok")
        self.assertEqual(by_name[f"rpc_receipt_{unknown}"].status, "fixture_miss")
        self.assertEqual(by_name[f"rpc_tx_{unknown}"].status, "fixture_miss")
        receipt = result_of(by_name[f"rpc_receipt_{self.TX}"])
        self.assertEqual(receipt["transactionHash"], self.TX)
        self.assertTrue(all(o.replayed for o in observations))
        self.assertTrue(all(o.family == "base_rpc" and o.method == "POST" for o in observations))
        self.assertIsNone(result_of(by_name[f"rpc_tx_{unknown}"]))

    def test_convenience_calls_decode_hex(self) -> None:
        fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        client = BaseRpcClient(fixture)
        balance_fixture = json.loads((FIXTURES / "base-rpc-eth_call-usdc-balanceOf-payer.json").read_text(encoding="utf-8"))
        nonce_fixture = json.loads((FIXTURES / "base-rpc-eth_getTransactionCount-payer.json").read_text(encoding="utf-8"))
        self.assertEqual(client.usdc_balance(self.PAYER), int(balance_fixture["result"], 16))
        self.assertEqual(client.nonce(self.PAYER), int(nonce_fixture["result"], 16))
        self.assertEqual(client.chain_id(), 8453)
        self.assertIsNone(client.receipt("0x" + "cd" * 32))

    def test_budget_callback_stops_fetching(self) -> None:
        fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at))
        allowance = [1]

        def budget() -> bool:
            if allowance[0] <= 0:
                return False
            allowance[0] -= 1
            return True

        client = BaseRpcClient(fixture, budget=budget)
        observations = client.batch(settlement_batch([self.TX], None, with_chain_header=False))
        self.assertEqual([o.status for o in observations], ["ok", "unavailable"])
        self.assertIn("budget_exhausted", observations[1].error or "")


if __name__ == "__main__":
    unittest.main()
