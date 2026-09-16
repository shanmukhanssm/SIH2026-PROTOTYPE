"""Tests for src/roadfix/config.py — constants and call-time path helpers."""

from pathlib import Path

import pytest

from roadfix import config


def test_threshold_constants_match_code_standards() -> None:
    assert config.FRAMES_PER_SECOND == 2.5
    assert config.GATE_PUBLISH == 0.7
    assert config.GATE_RECHECK == 0.4
    assert config.MAX_VERIFY_ATTEMPTS == 2
    assert config.RECURSION_LIMIT == 50


def test_data_dir_defaults_and_honors_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROADFIX_DATA_DIR", raising=False)
    assert config.data_dir() == Path("data")

    monkeypatch.setenv("ROADFIX_DATA_DIR", "/tmp/roadfix-test-data")
    assert config.data_dir() == Path("/tmp/roadfix-test-data")


def test_run_artifacts_live_under_runs_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROADFIX_DATA_DIR", "/tmp/roadfix-test-data")
    runs = Path("/tmp/roadfix-test-data") / "runs"
    assert config.runs_dir() == runs
    assert config.checkpoint_db() == runs / "checkpoints.sqlite"
    assert config.events_db() == runs / "events.db"
