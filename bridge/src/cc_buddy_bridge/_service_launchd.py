"""macOS launchd backend for the daemon auto-start service.

Writes a user-level LaunchAgent at ``~/Library/LaunchAgents/<LABEL>.plist``
that runs ``cc-buddy-bridge daemon`` at login and keeps it alive across
crashes. Logs stdout/stderr to ``~/Library/Logs/cc-buddy-bridge.log``.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .claude_home import MULTI_ENV, daemon_config_dirs

NAME = "launchd"
LABEL = "com.github.cc-buddy-bridge.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "cc-buddy-bridge.log"


def _build_plist(serial_port: str | None = None) -> bytes:
    """Render the plist as XML bytes.

    ``ProgramArguments`` uses the Python interpreter that's running *this*
    install command, so a user who installs from inside the project venv
    gets a service pointing at that venv's python — which has bleak and
    watchfiles installed. No need for a separate executable path.

    ``ProcessType = Interactive`` tells launchd this agent runs in the user's
    GUI session; required for CoreBluetooth (BLE) access on macOS.
    """
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "cc_buddy_bridge.cli", "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            # Keep a reasonable default PATH — launchd starts with an empty
            # one, and some bleak paths shell out.
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    if serial_port:
        # cli.py's daemon subcommand defaults --serial-port from this env
        # var, so the service uses USB serial instead of BLE.
        plist["EnvironmentVariables"]["CC_BUDDY_SERIAL_PORT"] = serial_port
    # launchd starts the daemon outside any Claude Code session, so it can
    # never inherit a per-session $CLAUDE_CONFIG_DIR. Bake in the homes to
    # serve at install time — otherwise the daemon tails only ~/.claude and
    # sessions run by a wrapper (era-code) report no tokens and no entries.
    dirs = daemon_config_dirs()
    if dirs != [Path.home() / ".claude"]:
        plist["EnvironmentVariables"][MULTI_ENV] = os.pathsep.join(str(d) for d in dirs)
    return plistlib.dumps(plist)


def install(serial_port: str | None = None) -> int:
    if shutil.which("launchctl") is None:
        print("cc-buddy-bridge: `launchctl` not found on PATH", file=sys.stderr)
        return 2

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(_build_plist(serial_port))

    # Unload first so idempotent re-install picks up any new interpreter path.
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", "-w", str(PLIST_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"launchctl load failed ({result.returncode}): {result.stderr.strip()}",
              file=sys.stderr)
        return 2

    print(f"installed: {PLIST_PATH}")
    print(f"logs at:   {LOG_PATH}")
    print("daemon will start on your next login (and is starting now).")
    return 0


def uninstall() -> int:
    if not PLIST_PATH.exists():
        print("service not installed; nothing to do")
        return 0

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink()
    print(f"removed: {PLIST_PATH}")
    return 0


def is_installed() -> bool:
    return PLIST_PATH.exists()


def is_loaded() -> bool:
    """True iff launchctl reports the agent currently loaded."""
    if shutil.which("launchctl") is None:
        return False
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    return any(LABEL in line for line in result.stdout.splitlines())


def unit_path() -> Path:
    return PLIST_PATH


def log_path() -> Path:
    return LOG_PATH
