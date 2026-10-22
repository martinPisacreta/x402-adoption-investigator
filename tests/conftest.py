"""pytest fixtures: fixture paths, sample clock, temp run dirs (SPEC.md tree). The suites are unittest-based so
they also run with `python -m unittest discover tests`; these fixtures are conveniences for pytest-style tests."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.sources.manifest import load_manifest  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return REPO_ROOT / "fixtures" / "raw"


@pytest.fixture(scope="session")
def manifest():
    return load_manifest(REPO_ROOT / "fixtures" / "raw" / "MANIFEST.json")


@pytest.fixture()
def sample_clock(manifest) -> SampleClock:
    return SampleClock(manifest.fetched_at)


@pytest.fixture()
def tmp_run_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture()
def sample_config(tmp_run_dir):
    return build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(tmp_run_dir)})(), load_dotenv=False)
