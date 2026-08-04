"""Config-home resolution.

Regression cover for the failure this module exists to prevent: hooks installed
into ~/.claude while sessions run against $CLAUDE_CONFIG_DIR, so nothing ever
prompts and nothing ever warns.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cc_buddy_bridge.claude_home import (
    MULTI_ENV,
    claude_config_dir,
    claude_config_dirs,
    settings_path,
    transcript_roots,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv(MULTI_ENV, raising=False)


def test_defaults_to_dot_claude() -> None:
    assert claude_config_dir() == Path.home() / ".claude"
    assert settings_path() == Path.home() / ".claude" / "settings.json"


def test_env_config_dir_wins_over_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_config_dir() == tmp_path
    assert settings_path() == tmp_path / "settings.json"


def test_explicit_override_wins_over_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "from-env"))
    assert claude_config_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_blank_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "   ")
    assert claude_config_dir() == Path.home() / ".claude"


def test_tilde_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/somewhere")
    assert claude_config_dir() == Path.home() / "somewhere"


def test_dirs_defaults_to_single_home() -> None:
    assert claude_config_dirs() == [Path.home() / ".claude"]


def test_dirs_reads_multi_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(MULTI_ENV, f"{a}{os.pathsep}{b}")
    assert claude_config_dirs() == [a, b]


def test_dirs_dedupes_and_skips_blanks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a = tmp_path / "a"
    monkeypatch.setenv(MULTI_ENV, f"{a}{os.pathsep}{os.pathsep}{a}")
    assert claude_config_dirs() == [a]


def test_dirs_all_blank_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MULTI_ENV, os.pathsep)
    assert claude_config_dirs() == [Path.home() / ".claude"]


def test_multi_env_beats_single_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The daemon serves several homes; a stray single-value env must not shrink that."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "single"))
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(MULTI_ENV, f"{a}{os.pathsep}{b}")
    assert claude_config_dirs() == [a, b]


def test_transcript_roots_appends_projects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(MULTI_ENV, f"{a}{os.pathsep}{b}")
    assert transcript_roots() == [a / "projects", b / "projects"]
