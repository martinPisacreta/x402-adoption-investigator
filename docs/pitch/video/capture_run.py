"""Run a command and record every stdout line with its arrival time, for the captured parts of demo.mp4.

Each line keeps its exact text; "t" is seconds since the command was launched. Standard library only.

What produced run-capture.json on 2026-10-24, from the repository root (the capture of 22 Oct ran the same command
inside `docker run --rm --network none x402-demo`, an image built from a fresh clone):
    python docs/pitch/video/capture_run.py --out docs/pitch/video/run-capture.json --meta commit=<sha> -- \
        python run.py --sample --input examples/ex4.json --print-output

--env KEY=VALUE sets a variable for the command and stores it in the capture header (only non-secret values belong
there); an argument list starting with `python` runs with the capturing interpreter, whose version the header
records. What produced live-capture-run.json (a live run through Meterless Nightshift, model pinned to Nemotron):
    python docs/pitch/video/capture_run.py --out docs/pitch/video/live-capture-run.json --meta commit=<sha> \
        --env METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b -- \
        python run.py --mode live --require-generator --input examples/ex1.json --run-id live-video-ex01-20261024

--cmdline passes one string to the OS as the exact command line instead of an argument list (on Windows the string
goes to CreateProcess unchanged, so Git's grep sees single-quoted JSON patterns as a shell would). What produced
live-capture-local.json and zetaris-capture.json, from the repository root in Git Bash on Windows:
    python docs/pitch/video/capture_run.py --out docs/pitch/video/live-capture-local.json --cmdline "grep -m 1 '\"type\":\"generate\"' examples/live-trace-nightshift/nemotron-local/nemotron-local-ex01-20261024/run.jsonl"
    python docs/pitch/video/capture_run.py --out docs/pitch/video/zetaris-capture.json --cmdline "grep -E '\"type\":\"(run_start|sql)\"' examples/live-trace/live-ex01-20261022/run.jsonl"
(live-capture-cloud.json, a grep over the committed log of nemotron-cloud-ex01-20261024 used by the first 24 Oct
rebuild of the video, was replaced by live-capture-run.json.)
What produced ledger-capture.json (the check of that live run against Nightshift's own signed receipt ledger; the
sha256 of the script goes in the header so the build can refuse a capture made with another version of it):
    python docs/pitch/video/capture_run.py --out docs/pitch/video/ledger-capture.json \
        --meta script_sha256=<sha256 of scripts/nightshift_ledger.py> --meta "ledger=..." -- \
        python scripts/nightshift_ledger.py examples/live-trace-nightshift/nemotron-cloud/live-video-ex01-20261024
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", action="append", default=[], help="key=value stored in the capture header")
    ap.add_argument("--cmdline", help="one command line string handed to the OS as is")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE for the command, stored in the header")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if a.cmdline:
        if cmd:
            ap.error("give --cmdline or an argument list, not both")
        cmd = a.cmdline
    if not cmd:
        ap.error("no command given")
    meta = dict(kv.split("=", 1) for kv in a.meta)
    env_set = dict(kv.split("=", 1) for kv in a.env)
    child_env = {**os.environ, **env_set}
    launch = cmd
    if isinstance(cmd, list) and cmd[0] == "python":
        launch = [sys.executable] + cmd[1:]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()
    proc = subprocess.Popen(launch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env)
    lines = []
    for raw in proc.stdout:
        lines.append({"t": round(time.perf_counter() - t0, 4), "line": raw.decode("utf-8").rstrip("\r\n")})
    err = proc.stderr.read().decode("utf-8")
    rc = proc.wait()
    total = round(time.perf_counter() - t0, 4)
    doc = {("cmdline" if isinstance(cmd, str) else "cmd"): cmd, "meta": meta, "started_utc": started, "rc": rc, "total_s": total, "stderr": err,
           "lines": lines}
    if env_set:
        doc["env"] = env_set
    if launch is not cmd:
        doc["python"] = f"python = the capturing interpreter, Python {sys.version.split()[0]}"
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"rc {rc}, {len(lines)} stdout lines, {total} s, stderr {len(err)} bytes", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
