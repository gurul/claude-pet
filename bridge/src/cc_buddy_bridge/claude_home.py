"""Locating Claude Code's config home(s).

Claude Code reads its settings and writes its transcripts under
``$CLAUDE_CONFIG_DIR`` when that variable is set, and under ``~/.claude``
otherwise. Wrappers set it: era-code runs sessions against a private config
home, and anything else driving Claude Code with its own profile does the same.

Hardcoding ``~/.claude`` fails silently and confusingly:

* hooks get installed into a file the running sessions never read, so the board
  never prompts and nothing logs an error;
* the transcript tailer watches an empty tree, so token counts and the entry
  feed stay at zero while the session is plainly busy.

The daemon is the awkward case. It is one long-lived background process (a
launchd agent / systemd unit) that outlives and out-scopes any single session,
so it cannot see a per-session ``$CLAUDE_CONFIG_DIR``. It therefore takes a
list: ``$CC_BUDDY_CLAUDE_CONFIG_DIRS`` (os.pathsep-separated) enumerates every
home to serve, which is how one daemon covers both a plain ``~/.claude`` and a
wrapper's private home at the same time.
"""

from __future__ import annotations

import os
from pathlib import Path

MULTI_ENV = "CC_BUDDY_CLAUDE_CONFIG_DIRS"


def claude_config_dir(override: str | None = None) -> Path:
    """The single config home to act on.

    Precedence: explicit override > ``$CLAUDE_CONFIG_DIR`` > ``~/.claude``.
    Used by session-scoped commands (hook install/uninstall/status), which run
    inside the same environment as the sessions they configure.
    """
    base = (override or os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(base).expanduser() if base else Path.home() / ".claude"


def claude_config_dirs(override: str | None = None) -> list[Path]:
    """Every config home to watch, de-duplicated, order preserved.

    Precedence: explicit override (os.pathsep-separated) > ``$CC_BUDDY_CLAUDE_CONFIG_DIRS``
    > the single home from :func:`claude_config_dir`. Used by the daemon, which
    serves whatever sessions happen to connect.
    """
    raw = (override or os.environ.get(MULTI_ENV) or "").strip()
    if not raw:
        return [claude_config_dir()]
    out: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        p = Path(part).expanduser()
        if p not in out:
            out.append(p)
    return out or [claude_config_dir()]


def daemon_config_dirs(override: str | None = None) -> list[Path]:
    """Homes to bake into a service definition at install time.

    Distinct from :func:`claude_config_dirs`, which is what the *running*
    daemon reads. ``install --service`` is typically run from inside a wrapper's
    session, so ``$CLAUDE_CONFIG_DIR`` names that wrapper's private home —
    taking it alone would silently drop plain ``~/.claude`` sessions from the
    daemon's view. Serve the union instead: whatever the installing session
    uses, plus the default home.
    """
    raw = (override or os.environ.get(MULTI_ENV) or "").strip()
    if raw:
        return claude_config_dirs(raw)
    out = [claude_config_dir()]
    default = Path.home() / ".claude"
    if default not in out:
        out.append(default)
    return out


def settings_path(override: str | None = None) -> Path:
    """Where to install hooks: ``settings.json``.

    Measured, not assumed. ``settings.local.json`` looked like the safer home —
    wrappers own settings.json and era-code rewrites it wholesale — but an A/B
    test settled it: with hooks ONLY in settings.local.json no hook fired at
    all, and adding them to settings.json made prompts work immediately. Claude
    Code does not read the local file from a $CLAUDE_CONFIG_DIR home.

    So the collision with era-code has to be fixed on era-code's side (it now
    preserves third-party hooks when regenerating this file) rather than by
    hiding in a file nothing reads.
    """
    return claude_config_dir(override) / "settings.json"


def transcript_roots(override: str | None = None) -> list[Path]:
    return [d / "projects" for d in claude_config_dirs(override)]
