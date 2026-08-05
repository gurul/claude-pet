"""Entry point. `cc-buddy-bridge [daemon|install|uninstall|status]`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from . import __version__
from .daemon import Daemon
from .ipc import make_transport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-buddy-bridge")
    parser.add_argument("--version", action="version", version=f"cc-buddy-bridge {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_daemon = sub.add_parser("daemon", help="Run the bridge daemon (connects to BLE device, serves hooks)")
    p_daemon.add_argument("--socket", default=None, help="IPC path or host:port override")
    p_daemon.add_argument("--device-name", default="Claude", help="BLE name prefix to match (default: Claude)")
    p_daemon.add_argument("--device-address", default=None, help="BLE address to connect to (skips scan)")
    p_daemon.add_argument(
        "--serial-port",
        default=os.environ.get("CC_BUDDY_SERIAL_PORT") or None,
        help="USB serial port of the buddy (e.g. /dev/cu.usbmodem*). Uses serial instead of BLE.",
    )
    p_daemon.add_argument("--log-level", default="INFO")

    CONFIG_DIR_HELP = (
        "Claude Code config home to operate on (default: $CLAUDE_CONFIG_DIR, else ~/.claude). "
        "Wrappers such as era-code run sessions against a private home — hooks installed into "
        "the wrong one never fire and never warn."
    )

    p_install = sub.add_parser(
        "install", help="Register hooks in Claude Code's settings.json")
    p_install.add_argument("--config-dir", default=None, help=CONFIG_DIR_HELP)
    p_install.add_argument(
        "--service", action="store_true",
        help="Install a user-level service so the daemon auto-starts on login "
             "(macOS: launchd agent; Linux: systemd user unit) instead of registering hooks",
    )
    p_install.add_argument(
        "--serial-port",
        default=os.environ.get("CC_BUDDY_SERIAL_PORT") or None,
        help="With --service: bake this USB serial port (glob ok, e.g. '/dev/cu.usbmodem*') "
             "into the service so the daemon uses serial instead of BLE.",
    )
    p_uninstall = sub.add_parser(
        "uninstall", help="Remove cc-buddy-bridge hooks from Claude Code's settings.json")
    p_uninstall.add_argument("--config-dir", default=None, help=CONFIG_DIR_HELP)
    p_uninstall.add_argument(
        "--service", action="store_true",
        help="Remove the user-level service (launchd agent / systemd unit) instead of removing hooks",
    )
    p_status = sub.add_parser("status", help="Show install status")
    p_status.add_argument("--config-dir", default=None, help=CONFIG_DIR_HELP)

    sub.add_parser(
        "voice-check",
        help="Diagnose hold-the-pet push-to-talk (Accessibility grant, signing, pyobjc)",
    )

    p_diag = sub.add_parser(
        "diag",
        help="Ask the board why it last reset and what it was doing (crash/hang report)",
    )
    p_diag.add_argument("--socket", default=None, help="IPC path or host:port override")
    p_diag.add_argument("--watch", action="store_true",
                        help="Poll every 2s — leave running to catch the next freeze")

    p_hud = sub.add_parser(
        "hud",
        help="Print a one-line stick status summary (stdout; designed for Claude Code's statusLine)",
    )
    p_hud.add_argument("--ascii", action="store_true", help="ASCII-only output (no emoji)")
    p_hud.add_argument("--socket", default=None, help="IPC path or host:port override")

    sub.add_parser(
        "unpair",
        help="Clear the stick's stored BLE bond (you must also Forget on the macOS side afterwards)",
    )

    p_push = sub.add_parser(
        "push-character",
        help="Upload a GIF character pack folder to the stick (manifest.json + *.gif)",
    )
    p_push.add_argument("path", help="Path to the character folder")

    p_update = sub.add_parser(
        "check-update",
        help="Check GitHub for a newer cc-buddy-bridge release (forces refresh)",
    )
    p_update.add_argument("--no-cache", action="store_true",
                          help="Ignore cache; always hit the network (default already does)")

    p_upgrade = sub.add_parser(
        "update",
        help="Pull latest release, reinstall the package, and restart the daemon",
    )
    p_upgrade.add_argument("-y", "--yes", action="store_true",
                           help="Skip the confirmation prompt")

    p_audit = sub.add_parser(
        "audit",
        help="Show the PreToolUse decision audit log (tail + filter + follow)",
    )
    p_audit.add_argument("-n", "--last", type=int, default=20, help="Show the last N entries (default 20; 0 = all)")
    p_audit.add_argument("-f", "--follow", action="store_true", help="Stream new entries as they're written")
    p_audit.add_argument("--decision", choices=["allow", "deny"], help="Filter by final decision")
    p_audit.add_argument("--source", choices=["auto_allow", "stick", "timeout", "defer", "ble_disconnected"],
                         help="Filter by decision source")
    p_audit.add_argument("--tool", help="Filter by tool name (Bash, Edit, ...)")
    p_audit.add_argument("--ascii", action="store_true", help="ASCII-only output (no colour)")
    p_audit.add_argument("--path", action="store_true", help="Print the audit log path and exit")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 1

    if args.cmd == "daemon":
        return _run_daemon(args)
    if args.cmd == "install":
        if getattr(args, "service", False):
            from .service import install_service
            return install_service(serial_port=getattr(args, "serial_port", None))
        from .installer import install_hooks
        return install_hooks(config_dir=getattr(args, "config_dir", None))
    if args.cmd == "uninstall":
        if getattr(args, "service", False):
            from .service import uninstall_service
            return uninstall_service()
        from .installer import uninstall_hooks
        return uninstall_hooks(config_dir=getattr(args, "config_dir", None))
    if args.cmd == "status":
        from .installer import show_status
        return show_status(config_dir=getattr(args, "config_dir", None))
    if args.cmd == "diag":
        return _run_diag(args.socket, args.watch)
    if args.cmd == "voice-check":
        from .voice_trigger import VoiceHold
        return VoiceHold().diagnose()
    if args.cmd == "hud":
        from .hud import run as hud_run
        return hud_run(ascii_only=args.ascii, socket_path=args.socket)
    if args.cmd == "unpair":
        return _run_unpair()
    if args.cmd == "push-character":
        return _run_push_character(args.path)
    if args.cmd == "audit":
        from .audit import default_path, render
        if args.path:
            print(default_path())
            return 0
        return render(
            last=args.last,
            decision=args.decision,
            source=args.source,
            tool=args.tool,
            ascii_only=args.ascii,
            follow=args.follow,
        )
    if args.cmd == "check-update":
        from .version_check import check, render_cli
        info = check(force=True)
        print(render_cli(info))
        return 1 if info.has_update else 0
    if args.cmd == "update":
        from .update import run_update
        return run_update(yes=args.yes)

    return 1


def _run_daemon(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Refuse to start if another daemon is already listening on this IPC
    # address. A stale Unix socket is safe to remove and proceed.
    try:
        transport = make_transport(args.socket)
    except ValueError as e:
        print(f"cc-buddy-bridge: invalid IPC address: {e}", file=sys.stderr)
        return 2
    if transport.is_in_use():
        print(
            f"cc-buddy-bridge: another daemon is already listening at {transport.address}.\n"
            f"  Stop it first, or pass --socket to use a different path.",
            file=sys.stderr,
        )
        return 2

    daemon = Daemon(
        socket_path=args.socket,
        device_name_prefix=args.device_name,
        device_address=args.device_address,
        serial_port=args.serial_port,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _sigterm(*_: object) -> None:
        asyncio.ensure_future(daemon.shutdown(), loop=loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sigterm)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return 0


def _run_push_character(path: str) -> int:
    from .hooks._client import post

    # Pushing a full 1.8 MB pack at BLE speeds can take 1-2 minutes with the
    # per-chunk ack requirement. Give the IPC call plenty of headroom.
    resp = post({"evt": "push_character", "path": path}, timeout=600.0)
    if resp is None:
        print(
            "cc-buddy-bridge: daemon not reachable. Start it first.",
            file=sys.stderr,
        )
        return 2
    if not resp.get("ok"):
        print(f"push failed: {resp.get('error', 'unknown')}", file=sys.stderr)
        return 2

    name = resp.get("name", "?")
    files = resp.get("files", 0)
    size = resp.get("total_bytes", 0)
    print(f"pushed '{name}': {files} files, {size:,} bytes")
    print("the stick has switched to the new character.")
    return 0


def _render_diag(d: dict | None, connected: bool) -> None:
    """Print a board diag report. Abnormal resets get the loud treatment —
    that line is the answer to 'why did it freeze'."""
    print(f"board connected: {connected}")
    if not d:
        print("no diag report yet — the board sends one at boot; if it just\n"
              "reconnected, run this again in a couple of seconds.")
        return
    reset = d.get("reset", "?")
    abnormal = reset in ("PANIC", "TASK-WATCHDOG", "INT-WATCHDOG", "BROWNOUT",
                         "other-watchdog")
    mark = "  <-- ABNORMAL" if abnormal else ""
    print(f"boot #{d.get('boot')}   last reset: {reset}{mark}")
    print(f"uptime {d.get('up')}s   heap {d.get('heap')} (min {d.get('minheap')})"
          f"   psram {d.get('psram')}")
    if d.get("diedIn"):
        # The single most useful line on a hang: the call that never returned.
        print(f"\nDIED IN: {d['diedIn']}   (entered {d.get('diedAtMs')}ms, "
              f"{d.get('loops')} loops)")
    last = d.get("last") or []
    if not last:
        print("no surviving event ring (clean power-on, or first boot on this build)")
        return
    print("\nwhat it was doing before that reset (oldest first):")
    for ev in last:
        ms, _, text = str(ev).partition(":")
        try:
            stamp = f"{int(ms) / 1000:8.2f}s"
        except ValueError:
            stamp = ms
        print(f"  {stamp}  {text}")


def _run_diag(socket_path: str | None, watch: bool) -> int:
    import time as _t

    from .hooks._client import post

    def ask() -> dict | None:
        return post({"evt": "diag"}, socket_path=socket_path, timeout=3.0)

    resp = ask()
    if resp is None:
        print("cc-buddy-bridge: daemon not reachable "
              "(start it, or check `launchctl list | grep cc-buddy`).",
              file=sys.stderr)
        return 2
    if not watch:
        _render_diag(resp.get("diag"), bool(resp.get("connected")))
        return 0

    # Watch mode: the point is to be running WHEN it freezes, so the
    # post-reset report is caught the moment the board comes back.
    print("watching for board resets — Ctrl-C to stop\n")
    seen: tuple | None = None
    try:
        while True:
            resp = ask()
            d = (resp or {}).get("diag")
            key = ((d or {}).get("boot"), (d or {}).get("reset"))
            if d and key != seen:
                seen = key
                print(f"--- {_t.strftime('%H:%M:%S')}")
                _render_diag(d, bool((resp or {}).get("connected")))
                print()
            _t.sleep(2.0)
    except KeyboardInterrupt:
        return 0


def _run_unpair() -> int:
    """Tell the running daemon to send cmd:unpair to the stick."""
    from .hooks._client import post

    resp = post({"evt": "unpair"}, timeout=2.0)
    if resp is None:
        print(
            "cc-buddy-bridge: daemon not reachable. Start it with "
            "`cc-buddy-bridge daemon` (or via the launchd agent).",
            file=sys.stderr,
        )
        return 2
    if not resp.get("ok"):
        err = resp.get("error", "unknown")
        print(f"cc-buddy-bridge: unpair failed ({err})", file=sys.stderr)
        return 2

    print("sent cmd:unpair to the stick; its stored bond is cleared.")
    print("")
    print("Next: open macOS System Settings > Bluetooth > Claude-5C66 > info")
    print("'Forget This Device' to purge the cached LTK. Then the next reconnect")
    print("will prompt for a fresh 6-digit passkey (displayed on the stick).")
    print("")
    print("Watch `tail -f ~/Library/Logs/cc-buddy-bridge.log` for the moment of truth:")
    print("  \"stick link: ENCRYPTED (was None)\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
