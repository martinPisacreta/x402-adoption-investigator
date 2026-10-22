#!/usr/bin/env bash
# The clean-clone command for judges: no dependencies, no network, no env file (sample mode).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
"$PY" --version
"$PY" run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
echo
echo "Logs: logs/ex01-seller-audit/ (run.jsonl mirrored above, output.json, report.md)"
if "$PY" -c "import pytest" 2>/dev/null; then "$PY" -m pytest -q; else "$PY" -m unittest discover -s tests -q; fi
