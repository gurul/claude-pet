"""Read-scope policy — what a card approval actually grants.

The dangerous failure modes here are over-grants: approving one file must
never silently allow $HOME or the filesystem root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_buddy_bridge.read_policy import is_within, read_scope

# ---- is_within ----

def test_within_direct_child() -> None:
    assert is_within("/a/b/c.txt", "/a/b")


def test_within_equal_path() -> None:
    assert is_within("/a/b", "/a/b")


def test_not_within_sibling() -> None:
    assert not is_within("/a/bc/file", "/a/b")  # prefix trap: /a/bc is not in /a/b


def test_not_within_parent() -> None:
    assert not is_within("/a", "/a/b")


def test_relative_paths_rejected() -> None:
    assert not is_within("b/c.txt", "/a")
    assert not is_within("/a/b", "b")


def test_empty_rejected() -> None:
    assert not is_within("", "/a")
    assert not is_within("/a", "")


# ---- read_scope ----

def test_scope_is_repo_root_when_git_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "src" / "lib"
    deep.mkdir(parents=True)
    assert read_scope(str(deep / "x.py")) == str(repo)


def test_scope_is_parent_dir_without_git(tmp_path: Path) -> None:
    d = tmp_path / "plain" / "dir"
    d.mkdir(parents=True)
    assert read_scope(str(d / "x.txt")) == str(d)


def test_scope_never_escapes_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A .git above $HOME must not be found — the walk stops at home."""
    fake_home = tmp_path / "home" / "user"
    d = fake_home / "notes"
    d.mkdir(parents=True)
    (tmp_path / ".git").mkdir()          # hostile marker above home
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert read_scope(str(d / "x.txt")) == str(d)


def test_file_directly_in_home_grants_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert read_scope(str(fake_home / "secrets.txt")) is None


def test_file_in_root_grants_nothing() -> None:
    assert read_scope("/etc-hosts-like-file") is None


def test_relative_path_grants_nothing() -> None:
    assert read_scope("relative/path.txt") is None
