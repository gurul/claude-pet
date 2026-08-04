"""Policy for surfacing Read-tool prompts on the stick.

Claude Code auto-allows reads inside the session's working directory, so those
never prompt anywhere. Reads *outside* it prompt in the terminal — and during a
subagent swarm that means dozens of terminal prompts, each firing the
Notification hook and flapping the pet in and out of attention with no way to
act from the board.

The policy here turns that flood into (usually) one swipe: an out-of-cwd read
surfaces as a card, and approving it grants the whole enclosing *scope* — the
containing git repo when there is one, else the parent directory — for the rest
of the daemon's life. Subsequent reads under that scope are allowed without a
card. Deny stays per-file: refusing one read grants no memory in either
direction.

Scope-not-file is the entire point: agents read repos, not files, and a
per-file card per source file is exactly the "continuously tweaks out"
behaviour this replaces. Daemon-lifetime (not persisted) is deliberate — a
daemon restart forgets every grant.
"""

from __future__ import annotations

from pathlib import Path


def is_within(path: str, base: str) -> bool:
    """True if `path` is inside (or equal to) `base`. Purely lexical — no
    filesystem access, so it cannot block the event loop or leak reads."""
    if not path or not base:
        return False
    try:
        p = Path(path).expanduser()
        b = Path(base).expanduser()
    except (ValueError, RuntimeError):
        return False
    if not (p.is_absolute() and b.is_absolute()):
        return False
    return p == b or b in p.parents


def read_scope(path: str) -> str | None:
    """The directory an approval of `path` grants.

    Walk up from the file's parent looking for a `.git` marker (repo root).
    Stop below $HOME — approving one file must never grant the whole home
    directory or filesystem root. No repo found → the file's parent directory.
    Returns None for paths that are not absolute (nothing sane to grant).
    """
    try:
        p = Path(path).expanduser()
    except (ValueError, RuntimeError):
        return None
    if not p.is_absolute():
        return None
    home = Path.home()
    parent = p.parent
    # A file sitting directly in $HOME or in the filesystem root has no sane
    # enclosing scope — granting one would auto-allow essentially everything.
    # Return None: such reads card every time, with no memory.
    if parent == home or parent == Path(parent.anchor):
        return None
    d = parent
    while d != d.parent:               # stop at filesystem root
        if d == home:                  # never grant $HOME itself
            break
        if (d / ".git").exists():
            return str(d)
        d = d.parent
    return str(parent)
