#!/usr/bin/env bash
# The clean-clone command for judges: no dependencies, no network, no env file (sample mode).
# Runs example 01, then the test suite (pytest when installed, otherwise the standard library's unittest).
set -euo pipefail
cd "$(dirname "$0")/.."
# The first of python3 / python that really runs and is 3.11 or newer. A name on PATH is not enough: on Windows
# "python3" is often the Microsoft Store stub, which exits non-zero without running anything.
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found (tried python3, python)." >&2
  exit 1
fi
"$PY" --version
"$PY" run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
echo
echo "Logs: ${LOG_DIR:-logs}/ex01-seller-audit/ (run.jsonl mirrored above, output.json, report.md)"
if "$PY" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("pytest") else 1)' >/dev/null 2>&1; then
  "$PY" -m pytest -q
else
  echo "pytest not installed: running the suite with $PY -m unittest discover -s tests"
  "$PY" -m unittest discover -s tests -q
fi
