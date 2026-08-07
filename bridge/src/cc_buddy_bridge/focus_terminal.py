"""Raise the terminal window running a given session.

Two gestures land here. Swipe UP on the board's permission card = "show me":
the human wants to see the session that is asking before deciding. Tap the
pet while it demands attention = "take me there": a session is blocked on
input and the human wants the terminal in front. Either way we (usually)
know the session's cwd; the
best cross-terminal heuristic is a window whose title mentions the cwd
basename — Terminal.app titles include the working directory by default, and
iTerm2 exposes per-session names the same way.

Strategy, in order:
  1. iTerm2 running → AppleScript search its windows/tabs/sessions for the
     repo name; select + raise on match.
  2. Terminal.app running → same over its windows/tabs.
  3. Match failed or errored → just activate the frontmost of the two apps,
     which at least lands the human in their terminal.

Requires macOS Automation permission (daemon python → the terminal app);
macOS prompts once on first use. Failure is always non-fatal — worst case
the card still works and nothing raises.
"""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

_ITERM_SCRIPT = '''
on run argv
  set needle to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (name of s as string) contains needle then
            select w
            select t
            select s
            activate
            return "matched"
          end if
        end repeat
      end repeat
    end repeat
    activate
    return "activated"
  end tell
end run
'''

# Ghostty, Warp, cmux and friends expose no useful AppleScript window model
# (Warp's is minimal; Ghostty has none), so there is no way to pick the right
# window by cwd. Activating the app is the honest best effort: it puts the
# human in the right program, one Cmd+` from the right window.
_ACTIVATE_SCRIPT = '''
on run argv
  tell application id (item 1 of argv) to activate
  return "activated"
end run
'''

# Same, but by app name — for apps configured via CC_BUDDY_FOCUS_APPS (or
# defaults whose bundle id isn't stable) where all we have is the name.
_ACTIVATE_BY_NAME_SCRIPT = '''
on run argv
  tell application (item 1 of argv) to activate
  return "activated"
end run
'''

# (process name for the running check, bundle id or None) tried in order
# after the AppleScript-capable terminals. None = activate by process name.
# cmux id from manaflow-ai/cmux's own scripts (stable app; DEV builds append
# .debug). Composer has no bundle id we could verify — name activation only.
_DEFAULT_ACTIVATE_TARGETS: list[tuple[str, str | None]] = [
    ("ghostty", "com.mitchellh.ghostty"),
    ("stable", "dev.warp.Warp-Stable"),
    ("Warp", "dev.warp.Warp"),
    ("cmux", "com.cmuxterm.app"),
    ("Composer", None),
    ("Cursor", "com.todesktop.230313mzl4w4u92"),
    ("Code", "com.microsoft.VSCode"),
]


def activate_targets() -> list[tuple[str, str | None]]:
    """The ordered activate list, overridable via CC_BUDDY_FOCUS_APPS.

    The env var is a comma-separated list of app/process names in priority
    order (e.g. "Warp,cmux,Composer"). A name matching a default entry keeps
    its known bundle id; anything else activates by name.
    """
    raw = os.environ.get("CC_BUDDY_FOCUS_APPS", "").strip()
    if not raw:
        return _DEFAULT_ACTIVATE_TARGETS
    known = {name.lower(): (name, bid) for name, bid in _DEFAULT_ACTIVATE_TARGETS}
    targets: list[tuple[str, str | None]] = []
    for part in raw.split(","):
        name = part.strip()
        if name:
            targets.append(known.get(name.lower(), (name, None)))
    return targets or _DEFAULT_ACTIVATE_TARGETS

_TERMINAL_SCRIPT = '''
on run argv
  set needle to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if (custom title of t as string) contains needle or (name of w as string) contains needle then
          set selected of t to true
          set index of w to 1
          activate
          return "matched"
        end if
      end repeat
    end repeat
    activate
    return "activated"
  end tell
end run
'''


async def _app_running(bundle_hint: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-x", bundle_hint,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return (await proc.wait()) == 0


async def _osascript(script: str, arg: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script, arg,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        log.debug("focus: osascript failed: %s", err.decode(errors="replace").strip()[:200])
        return None
    return out.decode(errors="replace").strip()


async def focus_session_terminal(cwd: str) -> None:
    """Best-effort raise of the terminal for `cwd`. Never raises."""
    needle = os.path.basename(cwd.rstrip("/")) if cwd else ""
    try:
        if await _app_running("iTerm2"):
            result = await _osascript(_ITERM_SCRIPT, needle or "~")
            log.info("focus: iTerm2 %s (needle=%r)", result or "error", needle)
            if result:
                return
        if await _app_running("Terminal"):
            result = await _osascript(_TERMINAL_SCRIPT, needle or "~")
            log.info("focus: Terminal %s (needle=%r)", result or "error", needle)
            if result:
                return
        # No scriptable window model — just raise the app that is running.
        for proc_name, bundle_id in activate_targets():
            if await _app_running(proc_name):
                if bundle_id is not None:
                    result = await _osascript(_ACTIVATE_SCRIPT, bundle_id)
                else:
                    result = await _osascript(_ACTIVATE_BY_NAME_SCRIPT, proc_name)
                log.info("focus: activated %s (%s)", proc_name,
                         result or "error")
                if result:
                    return
        log.info("focus: no known terminal app running (needle=%r)", needle)
    except Exception as e:  # noqa: BLE001
        log.warning("focus: failed non-fatally: %s", e)
