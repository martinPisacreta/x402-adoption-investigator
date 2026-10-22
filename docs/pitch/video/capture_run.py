"""Run a command and record every stdout line with its arrival time, for the recorded-run part of demo.mp4.

Usage (what produced run-capture.json; the image was built from a fresh clone of this repository):
    git clone https://github.com/martinPisacreta/x402-adoption-investigator r && cd r && docker build -t x402-demo .
    python docs/pitch/video/capture_run.py --out run-capture.json --meta commit=<sha> -- \
        docker run --rm --network none x402-demo --sample --input examples/ex4.json --print-output

Each line keeps its exact text; "t" is seconds since the command was launched. Standard library only.
"""
import argparse
import datetime
import json
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", action="append", default=[], help="key=value stored in the capture header")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command given")
    meta = dict(kv.split("=", 1) for kv in a.meta)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = []
    for raw in proc.stdout:
        lines.append({"t": round(time.perf_counter() - t0, 4), "line": raw.decode("utf-8").rstrip("\r\n")})
    err = proc.stderr.read().decode("utf-8")
    rc = proc.wait()
    total = round(time.perf_counter() - t0, 4)
    doc = {"cmd": cmd, "meta": meta, "started_utc": started, "rc": rc, "total_s": total, "stderr": err,
           "lines": lines}
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"rc {rc}, {len(lines)} stdout lines, {total} s, stderr {len(err)} bytes", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
