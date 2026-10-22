# The clean-clone command for judges (PowerShell): no dependencies, no network, no env file (sample mode).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python --version
python run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
$logs = if ($env:LOG_DIR) { $env:LOG_DIR } else { "logs" }
Write-Host "Logs: $logs/ex01-seller-audit/ (run.jsonl mirrored above, output.json, report.md)"
python -c "import pytest" 2>$null
if ($LASTEXITCODE -eq 0) { python -m pytest -q } else { python -m unittest discover -s tests -q }
