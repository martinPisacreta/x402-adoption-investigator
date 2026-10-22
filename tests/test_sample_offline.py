"""Sample mode is provably offline: the guard is installed first and any socket use fails (SPEC.md I4, 7.4). Owner: B1."""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import guard  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402


class NetworkGuard(unittest.TestCase):
    def tearDown(self) -> None:
        guard.uninstall_network_guard()

    def test_guard_blocks_urlopen_and_sockets(self) -> None:
        patched = guard.install_network_guard()
        self.assertIn("urllib.request.urlopen", patched)
        with self.assertRaises(guard.NetworkDisabledError):
            urllib.request.urlopen("https://tienda.textokit.store/receipts.json", timeout=1)
        with self.assertRaises(guard.NetworkDisabledError):
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        with self.assertRaises(guard.NetworkDisabledError):
            socket.getaddrinfo("example.com", 443)
        with socket.socket() as sock, self.assertRaises(guard.NetworkDisabledError):
            sock.connect(("127.0.0.1", 9))

    def test_sample_run_installs_the_guard_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            payload = json.loads((REPO_ROOT / "examples" / "ex1.json").read_text(encoding="utf-8"))
            envelope = run_investigation(payload, config, echo_stdout=False)
            self.assertEqual(envelope["status"], "ok", envelope.get("error"))
            first = json.loads((Path(tmp) / "ex01-seller-audit" / "run.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["type"], "guard")
            self.assertTrue(guard.is_installed())
            with self.assertRaises(guard.NetworkDisabledError):
                urllib.request.urlopen("https://mainnet.base.org", timeout=1)

    def test_full_sample_pipeline_never_touches_a_socket(self) -> None:
        """All three examples run with the guard installed first; every source comes from a fixture (replayed),
        every fetch event says so, and any socket use would have surfaced as E_INTERNAL (NetworkDisabledError)."""
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            for example in ("ex1.json", "ex2.json", "ex3.json"):
                payload = json.loads((REPO_ROOT / "examples" / example).read_text(encoding="utf-8"))
                envelope = run_investigation(payload, config, echo_stdout=False)
                self.assertEqual(envelope["status"], "ok", (example, envelope.get("error")))
                output = envelope["output"]
                self.assertEqual(output["mounts"]["fetcher"], "fixture")
                self.assertIn("sample_mode_notice", output)
                sources = output["sources"]
                self.assertTrue(sources, example)
                # Every source is a replay: a fixture hit or an honest fixture_miss (the Verifier's RPC elements for
                # hashes the fixtures do not cover). Nothing is stale, nothing came from a socket.
                self.assertTrue(all(s["replayed"] and s["status"] in ("ok", "fixture_miss") and not s["stale"] for s in sources), example)
                self.assertTrue(all(s["sha256"] and s["fixture_file"] for s in sources if s["status"] == "ok"), example)
                self.assertTrue(any(s["status"] == "ok" for s in sources), example)
                lines = [json.loads(l) for l in (Path(tmp) / envelope["run_id"] / "run.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(lines[0]["type"], "guard")
                fetches = [l for l in lines if l["type"] == "fetch"]
                self.assertEqual(len(fetches), len(sources))
                self.assertTrue(all(f["payload"]["replayed"] for f in fetches))
                self.assertTrue(guard.is_installed())
                with self.assertRaises(guard.NetworkDisabledError):
                    socket.create_connection(("127.0.0.1", 9), timeout=1)


if __name__ == "__main__":
    unittest.main()
