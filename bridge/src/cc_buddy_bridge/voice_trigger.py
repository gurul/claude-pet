"""Hold-the-pet push-to-talk: synthesize a global Option+Space hold.

The stick sends {"cmd":"voice","state":"start"} when a finger settles on the
pet for 600ms and "stop" on release. VoiceFlow (menu-bar dictation app)
records while its global hotkey Option+Space is held and pastes on release —
so the bridge simply holds the hotkey down between the two events: finger
down = key down, finger up = key up, dictation lands wherever the cursor is.

Mechanics: Quartz CGEventPost at the HID tap. Option (kVK_Option 58) goes
down as its own key event before Space (kVK_Space 49) carrying the alternate
flag — apps that track the physical modifier (flagsChanged) and apps that
only read event flags both see a plausible human hold. Release runs in
reverse order.

Stuck-key safety, because a system-wide held Option+Space is genuinely
disruptive (every space types "·"-style alternates, other hotkeys misfire):

* a watchdog force-releases after MAX_HOLD_SECS if the stop event never
  arrives (serial died mid-hold, board reset while recording);
* release() is idempotent and runs unconditionally at daemon shutdown.

Requires macOS Accessibility permission for the daemon's python — the first
start attempts AXIsProcessTrustedWithOptions with the system prompt enabled,
and posts nothing until trust is granted (CGEventPost is silently filtered
for untrusted processes, so without the check the feature would just
mysteriously do nothing).
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

KEY_SPACE = 49   # kVK_Space
KEY_OPTION = 58  # kVK_Option
KEY_RETURN = 36  # kVK_Return
MAX_HOLD_SECS = 60.0

# Keys the board may ask us to tap. Deliberately a tiny allowlist: the board
# is a peripheral on a serial line, and "synthesize any keystroke on request"
# is a much larger surface than this feature needs.
TAPPABLE = {"enter": KEY_RETURN}

# poster signature: (keycode, down, with_option_flag) -> None
Poster = Callable[[int, bool, bool], None]


def _quartz_poster() -> Optional[Poster]:
    """Build the real CGEvent poster; None when Quartz is unavailable."""
    if sys.platform != "darwin":
        return None
    try:
        import Quartz
    except ImportError:
        log.warning(
            "voice: pyobjc-framework-Quartz not installed — hold-the-pet "
            "does nothing (pip install pyobjc-framework-Quartz)")
        return None

    # A real event source rather than None. Events created with a NULL source
    # carry no keyboard state and some apps drop them; HIDSystemState makes the
    # synthesized key look like it came from the actual keyboard.
    try:
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    except Exception:  # noqa: BLE001
        src = None

    def post(keycode: int, down: bool, with_option: bool) -> None:
        ev = Quartz.CGEventCreateKeyboardEvent(src, keycode, down)
        if with_option:
            Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskAlternate)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    return post


def _check_accessibility(prompt: bool = False) -> bool:
    """True if this process may post events.

    ``prompt`` shows the system "would like to control this computer" dialog —
    pass it at most ONCE per daemon life. Prompting on every failed attempt
    re-pops the dialog on every hold, which is indistinguishable from the grant
    not working and trains the user to dismiss it.
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        if prompt:
            return bool(AXIsProcessTrustedWithOptions(
                {kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except ImportError:
        # Can't check — post anyway; if trust is missing the events are
        # silently dropped, and the log line below is the only breadcrumb.
        log.warning(
            "voice: ApplicationServices unavailable — cannot verify "
            "Accessibility permission; if dictation never starts, grant it "
            "to the daemon's python in System Settings > Privacy & Security")
        return True


class VoiceHold:
    """One press-and-hold of Option+Space. Idempotent on both edges."""

    def __init__(self, poster: Optional[Poster] = None,
                 clock: Callable[[], float] = time.monotonic,
                 trust_check: Optional[Callable[[], bool]] = None) -> None:
        # Injected poster (tests) skips the Accessibility machinery entirely;
        # the real Quartz poster gets the real trust check unless overridden.
        if poster is None:
            poster = _quartz_poster()
            if trust_check is None and poster is not None:
                trust_check = _check_accessibility
        self._poster = poster
        self._trust_check = trust_check
        self._clock = clock
        self._held_since: Optional[float] = None
        self._prompted = False   # the system dialog is shown at most once

    @property
    def active(self) -> bool:
        return self._held_since is not None

    def start(self) -> bool:
        if self._poster is None:
            return False
        if self.active:
            return True
        if self._trust_check is not None:
            # Silent check every attempt; the modal dialog only the first time.
            try:
                ok = self._trust_check(prompt=not self._prompted)  # type: ignore[call-arg]
            except TypeError:
                ok = self._trust_check()   # injected checks take no kwargs
            self._prompted = True
            if not ok:
                log.warning(
                    "voice: Accessibility not granted for %s — see "
                    "`cc-buddy-bridge voice-check` for the exact binary to add "
                    "in System Settings > Privacy & Security > Accessibility",
                    sys.executable)
                return False        # keep the check armed for the next hold
            self._trust_check = None  # verified once; stop re-checking
        self._poster(KEY_OPTION, True, False)
        self._poster(KEY_SPACE, True, True)
        self._held_since = self._clock()
        log.info("voice: hold started (Opt+Space down)")
        return True

    def stop(self) -> None:
        if not self.active or self._poster is None:
            self._held_since = None
            return
        self._poster(KEY_SPACE, False, True)
        self._poster(KEY_OPTION, False, False)
        held = self._clock() - (self._held_since or self._clock())
        self._held_since = None
        log.info("voice: hold released after %.1fs (Opt+Space up)", held)

    def diagnose(self) -> int:
        """Print why push-to-talk is or isn't working. Returns an exit code.

        Accessibility is granted per *binary*, and a venv's python is a symlink
        — macOS resolves it, so the path that must appear (and be toggled ON)
        in System Settings is the real interpreter, not the venv one.
        """
        import os
        import subprocess

        real = os.path.realpath(sys.executable)
        print(f"daemon python (argv):  {sys.executable}")
        print(f"resolved binary:       {real}")
        if real != sys.executable:
            print("  ^ ADD THIS ONE in System Settings — the venv path is a symlink")

        sig = "unknown"
        try:
            out = subprocess.run(["codesign", "-dv", "--verbose=2", real],
                                 capture_output=True, text=True, timeout=10)
            blob = out.stderr or out.stdout
            adhoc = "adhoc" in blob or "Signature=adhoc" in blob
            sig = "ad-hoc / linker-signed" if adhoc else "signed"
        except Exception:  # noqa: BLE001
            pass
        print(f"code signature:        {sig}")
        if sig.startswith("ad-hoc"):
            print("  note: ad-hoc-signed interpreters (uv/pyenv builds) are the")
            print("  usual cause of a grant that 'won't stick' — macOS keys the")
            print("  grant to the signature. Remove every stale python entry in")
            print("  the Accessibility list, then re-add the resolved path above.")

        if self._poster is None:
            print("quartz poster:         UNAVAILABLE (pyobjc not installed?)")
            return 2
        print("quartz poster:         ok")

        trusted = _check_accessibility(prompt=False)
        print(f"accessibility trusted: {trusted}")
        if not trusted:
            print("\nFIX: System Settings > Privacy & Security > Accessibility")
            print("  1. remove any existing 'python3.12' rows (stale grants)")
            print(f"  2. '+', then Cmd+Shift+G, paste: {real}")
            print("  3. make sure its toggle is ON (adding alone does not enable it)")
            print("  4. restart the daemon:")
            print("     launchctl kickstart -k gui/$(id -u)/com.github.cc-buddy-bridge.daemon")
            return 1
        print("\nReady — hold the pet to dictate.")
        return 0

    def tap(self, name: str) -> bool:
        """Press and release one allowlisted key (swipe-down → Enter).

        Refused while a push-to-talk hold is active: injecting Return with
        Option still down would send Opt+Return, which is a different chord in
        most apps.
        """
        key = TAPPABLE.get(name)
        if key is None:
            log.warning("key: refusing unknown key %r", name)
            return False
        if self._poster is None:
            return False
        if self.active:
            log.warning("key: ignoring %r while a voice hold is active", name)
            return False
        if self._trust_check is not None:
            try:
                ok = self._trust_check(prompt=not self._prompted)  # type: ignore[call-arg]
            except TypeError:
                ok = self._trust_check()
            self._prompted = True
            if not ok:
                log.warning("key: Accessibility not granted — see `voice-check`")
                return False
            self._trust_check = None
        self._poster(key, True, False)
        # Hold briefly. A down/up in the same microsecond is not a keypress any
        # human could produce, and apps that debounce or sample input on a
        # frame boundary drop it entirely.
        time.sleep(0.03)
        self._poster(key, False, False)
        log.info("key: tapped %s", name)
        return True

    def overdue(self) -> bool:
        """True when a hold has exceeded MAX_HOLD_SECS — the stop event was
        lost (dead link, board reset) and the keys must be force-released."""
        return (self._held_since is not None
                and self._clock() - self._held_since > MAX_HOLD_SECS)
