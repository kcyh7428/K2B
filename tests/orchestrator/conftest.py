"""Test defaults for orchestrator CLI subprocesses."""

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def orchestrator_home_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run orchestrator unit tests under the explicit Home-writer role."""
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "home")
