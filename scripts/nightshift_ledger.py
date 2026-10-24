#!/usr/bin/env python3
"""Check a run's model requests against Meterless Nightshift's own signed receipt ledger.

    python scripts/nightshift_ledger.py <run dir | transcripts folder> [...] [--ledger FILE] [--copy-to FILE]

Nightshift keeps every compute receipt it issues, Ed25519-signed by its daemon, in <stateDir>/receipts.jsonl
(stateDir = METERLESS_STATE_DIR or the OS default the official ns CLI uses, investigator.config.meterless_state_dir).
For every `generate` event of <run dir>/run.jsonl that names a receipt (or every transcript of a folder such as
fixtures/transcripts/, searched recursively), this finds that receipt in the ledger (or in --ledger, e.g. the
committed excerpt examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl), verifies its signature
(Ed25519 over the compact JSON of the signed body, with the public key the envelope carries; RFC 8032 verification
in pure Python) and prints one JSON line per request from the ledger's side: receipt id, when Nightshift issued it,
the app header it saw, the model its router picked, the route kind, where the prompt went, tokens, latency, the
signer and whether the signature holds, next to the run's seq, agent and prompt id. The last line says how many
receipts were found, verified and routed to the model run.jsonl names. Exit 0 only when all of them are. The
receipt's prompt preview is never printed. --copy-to writes the matching ledger lines, unedited, to FILE.
Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import meterless_state_dir  # noqa: E402

# ---------------------------------------------------------------- Ed25519 verification (RFC 8032, 5.1.7)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if xx == 0:
        return None if sign else 0
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - xx) % _P:
        return None
    if x & 1 != sign:
        x = _P - x
    return x


def _add(a: tuple, b: tuple) -> tuple:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    pa, pb = (y1 - x1) * (y2 - x2) % _P, (y1 + x1) * (y2 + x2) % _P
    pc, pd = t1 * 2 * _D * t2 % _P, z1 * 2 * z2 % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s: int, pt: tuple) -> tuple:
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _decode(raw: bytes) -> Optional[tuple]:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _equal(a: tuple, b: tuple) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    a, r = _decode(public_key), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


def signed_bytes(body: dict) -> bytes:
    """What the daemon signs: the body as compact JSON, keys in their order, UTF-8 (checked on the 24 Oct ledger)."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_envelope(envelope: dict) -> bool:
    try:
        return ed25519_verify(base64.b64decode(envelope["pubkey_b64"]), signed_bytes(envelope["body"]),
                              base64.b64decode(envelope["sig_b64"]))
    except (KeyError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------- ledger and run

def default_ledger() -> Optional[Path]:
    state_dir, _ = meterless_state_dir(dict(os.environ))
    return Path(state_dir) / "receipts.jsonl" if state_dir else None


def read_ledger(path: Path, wanted: set) -> dict:
    """receipt_id -> (the ledger line as written, the parsed envelope), for the wanted ids only."""
    found: dict = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for raw in fh:
            raw = raw.rstrip("\r\n")
            if not any(w in raw for w in wanted):
                continue
            envelope = json.loads(raw)
            rid = ((envelope.get("body") or {}).get("receipt") or {}).get("receipt_id")
            if rid in wanted:
                found[rid] = (raw, envelope)
    return found


def run_requests(run_dir: Path) -> list:
    """The receipts a run names: one per `generate` event of its run.jsonl (a replayed one included)."""
    out = []
    for raw in (run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(raw)
        routing = (event.get("payload") or {}).get("routing") or {}
        if event.get("type") == "generate" and routing.get("receipt_id"):
            out.append({"seq": event["seq"], "agent": event["agent"], "prompt_id": event["payload"].get("prompt_id"),
                        "receipt_id": routing["receipt_id"], "routed_model": routing.get("routed_model")})
    return out


def transcript_requests(folder: Path) -> list:
    """The receipts the transcripts under a folder carry (fixtures/transcripts/<example>/<prompt_id>-<sha12>.json)."""
    out = []
    for path in sorted(folder.rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        routing = record.get("routing") if isinstance(record, dict) else None
        if isinstance(routing, dict) and routing.get("receipt_id"):
            out.append({"transcript": path.relative_to(folder).as_posix(), "prompt_id": record.get("prompt_id"),
                        "receipt_id": routing["receipt_id"], "routed_model": routing.get("routed_model")})
    return out


def requests_of(path: Path) -> list:
    return run_requests(path) if (path / "run.jsonl").is_file() else transcript_requests(path)


def iso_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    stamp = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ledger_line(req: dict, envelope: dict, valid: bool) -> dict:
    body = envelope["body"]
    rc = body["receipt"]
    route = rc.get("winning_route") or {}
    usage = rc.get("usage") or {}
    winner = (rc.get("routing") or {}).get("winner")
    return {"receipt_id": rc.get("receipt_id"), "issued_at": iso_ms(rc.get("created_at")), "app": rc.get("app"),
            "status": rc.get("status"), "winner": winner, "kind": route.get("kind"), "provider": route.get("provider"),
            "egress": (rc.get("egress") or {}).get("destination"), "tokens_in": usage.get("input_tokens"),
            "tokens_out": usage.get("output_tokens"), "latency_ms": usage.get("latency_ms"),
            "signer": body.get("signer"), "ed25519": "valid" if valid else "INVALID",
            "run": {**{k: v for k, v in req.items() if k not in ("receipt_id", "routed_model")},
                    "same_model": winner == route.get("model") == req["routed_model"]}}


def compact(obj: dict) -> str:
    """One output line, compact like the run's own JSONL."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sources", type=Path, nargs="+",
                    help="run folders with run.jsonl (logs/<run_id> or a committed trace) or transcript folders")
    ap.add_argument("--ledger", type=Path, help="receipts.jsonl to read (default: <stateDir>/receipts.jsonl)")
    ap.add_argument("--copy-to", type=Path, help="write the matching ledger lines, unedited, to this file")
    a = ap.parse_args(argv)
    ledger = a.ledger or default_ledger()
    if ledger is None or not ledger.is_file():
        print(json.dumps({"error": "no Nightshift receipt ledger found (pass --ledger)"}))
        return 1
    requests = [req for source in a.sources for req in requests_of(source)]
    found = read_ledger(ledger, {r["receipt_id"] for r in requests})
    present = valid = same = 0
    copied = []
    for req in requests:
        hit = found.get(req["receipt_id"])
        if not hit:
            print(compact({"receipt_id": req["receipt_id"], "ledger": "missing",
                           "run": {k: v for k, v in req.items() if k not in ("receipt_id", "routed_model")}}))
            continue
        raw, envelope = hit
        ok = verify_envelope(envelope)
        line = ledger_line(req, envelope, ok)
        present += 1
        valid += ok
        same += line["run"]["same_model"]
        if raw not in copied:
            copied.append(raw)
        print(compact(line), flush=True)
    n = len(requests)
    print(compact({"requests": n, "found_in_nightshift_ledger": present, "signatures_valid": valid,
                   "winner_is_run_routed_model": same, "routed_models": sorted({r["routed_model"] for r in requests})}))
    if a.copy_to:
        a.copy_to.write_text("".join(r + "\n" for r in copied), encoding="utf-8", newline="\n")
    return 0 if n and present == valid == same == n else 1


if __name__ == "__main__":
    sys.exit(main())
