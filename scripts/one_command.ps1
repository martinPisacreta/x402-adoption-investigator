# The clean-clone command for judges (Windows PowerShell 5.1 or PowerShell 7): no dependencies, no network, no env
# file (sample mode). Runs example 01, then the test suite: with pytest when it is installed, otherwise with the
# standard library's unittest. A missing optional tool never stops the script; only a failed run or test does.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

# The first interpreter that really runs and is Python 3.11 or newer (skips the Microsoft Store "python" stub).
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
$py = $null
foreach ($candidate in @(@("python"), @("py", "-3"), @("python3"))) {
  if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
  $pre = @($candidate | Select-Object -Skip 1)
  try { & $candidate[0] @pre -c $check *> $null } catch { continue }
  if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
}
if (-not $py) {
  Write-Host "Python 3.11 or newer was not found (tried python, py -3, python3)."
  exit 1
}
$exe = $py[0]
$pre = @($py | Select-Object -Skip 1)

& $exe @pre --version
& $exe @pre run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
$logs = if ($env:LOG_DIR) { $env:LOG_DIR } else { "logs" }
Write-Host "Logs: $logs/ex01-seller-audit/ (run.jsonl mirrored above, output.json, report.md)"

# pytest is optional: find_spec answers without importing it and without writing to stderr.
& $exe @pre -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)"
if ($LASTEXITCODE -eq 0) {
  & $exe @pre -m pytest -q
} else {
  Write-Host "pytest not installed: running the suite with python -m unittest discover -s tests"
  & $exe @pre -m unittest discover -s tests -q
}
exit $LASTEXITCODE
