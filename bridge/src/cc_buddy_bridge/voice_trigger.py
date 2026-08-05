"""Hold-the-pet push-to-talk: synthesize a global dictation-hotkey hold.

The stick sends {"cmd":"voice","state":"start"} when a finger settles on the
pet for 600ms and "stop" on release. Push-to-talk dictation apps record while
their global hotkey is held and paste on release — so the bridge simply holds
that hotkey between the two events: finger down = key down, finger up = key
up, dictation lands wherever the cursor is.

Which hotkey is configurable (CC_BUDDY_VOICE_HOTKEY), because it is a property
of the dictation app, not of the pet: `fn` for Willow Voice (default), or
`opt-space` for VoiceFlow. See HOTKEYS.

Mechanics: Quartz CGEventPost at the HID tap, from a real HIDSystemState
event source — events built with a NULL source carry no keyboard state and
apps drop them. Chord keys press in order and release in reverse.

Stuck-key safety, because a system-wide held modifier is genuinely
disruptive (fn or Option stuck down breaks typing everywhere):

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
import os
import sys
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

KEY_SPACE = 49     # kVK_Space
KEY_OPTION = 58    # kVK_Option
KEY_RETURN = 36    # kVK_Return
KEY_FUNCTION = 63  # kVK_Function — the fn key
MAX_HOLD_SECS = 60.0

# Event flag masks (Quartz constants, hardcoded so the module imports on
# non-mac hosts for testing).
FLAG_ALTERNATE = 0x00080000   # kCGEventFlagMaskAlternate
FLAG_SECONDARY_FN = 0x00800000  # kCGEventFlagMaskSecondaryFn

# Push-to-talk chords, as ordered (keycode, flags) pairs pressed in sequence
# and released in reverse. Which dictation app you use decides the chord:
#
#   fn         Willow Voice, and macOS's own dictation — a bare fn hold. fn is
#              a *secondary-fn modifier*, not an ordinary key, so the event
#              must carry FLAG_SECONDARY_FN or listeners ignore it.
#   opt-space  VoiceFlow. Option goes down as its own key event first so apps
#              tracking the physical modifier (flagsChanged) see it, then
#              Space carries the alternate flag for apps that read flags.
HOTKEYS: dict[str, list[tuple[int, int]]] = {
    # Bare Option hold — Willow Voice, once rebound off fn. An ordinary
    # modifier, so unlike fn it synthesizes reliably.
    "option": [(KEY_OPTION, FLAG_ALTERNATE)],
    "fn": [(KEY_FUNCTION, FLAG_SECONDARY_FN)],
    "opt-space": [(KEY_OPTION, 0), (KEY_SPACE, FLAG_ALTERNATE)],
}
DEFAULT_HOTKEY = "option"


def _configured_hotkey() -> str:
    name = (os.environ.get("CC_BUDDY_VOICE_HOTKEY") or DEFAULT_HOTKEY).strip().lower()
    if name not in HOTKEYS:
        log.warning("voice: unknown CC_BUDDY_VOICE_HOTKEY=%r; using %s",
                    name, DEFAULT_HOTKEY)
        return DEFAULT_HOTKEY
    return name

KEY_KEYPAD_ENTER = 76  # kVK_ANSI_KeypadEnter

# Keys the board may ask us to tap. Deliberately a tiny allowlist: the board
# is a peripheral on a serial line, and "synthesize any keystroke on request"
# is a much larger surface than this feature needs.
#
# "enter" resolves to the main Return key (kVK_Return 36) by default. A few
# apps distinguish it from the numeric keypad's Enter (kVK_ANSI_KeypadEnter
# 76) — notably some editors and terminal multiplexers, where Return inserts
# a newline and keypad Enter submits. CC_BUDDY_ENTER_KEY=keypad switches it.
def _enter_keycode() -> int:
    choice = (os.environ.get("CC_BUDDY_ENTER_KEY") or "return").strip().lower()
    if choice in ("keypad", "keypad-enter", "enter"):
        return KEY_KEYPAD_ENTER
    return KEY_RETURN


TAPPABLE = {"enter": _enter_keycode()}

# poster signature: (keycode, down, flags_mask, is_hold=True) -> None
# is_hold selects the event source; see _quartz_poster.
Poster = Callable[..., None]


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

    # Source choice is load-bearing and app-dependent:
    #
    # * Modifier HOLDS want the real HIDSystemState source — events built with
    #   a NULL source carry no keyboard state, and dictation apps watching for
    #   a held modifier ignore them.
    # * Plain TAPS want the NULL source. With a real source, Warp's global key
    #   handling swallowed Return before it reached the focused terminal app —
    #   the dictated text sat in the prompt unsent. NULL-source Return worked
    #   for hours before this was changed, so taps go back to it.
    #
    # CC_BUDDY_KEY_SOURCE=hid forces the real source everywhere if some other
    # app ever needs the opposite.
    force_hid = (os.environ.get("CC_BUDDY_KEY_SOURCE") or "").strip().lower() == "hid"

    def post(keycode: int, down: bool, flags: int, hold: bool = True) -> None:
        ev = Quartz.CGEventCreateKeyboardEvent(
            src if (hold or force_hid) else None, keycode, down)
        if flags:
            Quartz.CGEventSetFlags(ev, flags)
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
                 trust_check: Optional[Callable[[], bool]] = None,
                 hotkey: Optional[str] = None) -> None:
        self._hotkey = (hotkey or _configured_hotkey())
        self._chord = HOTKEYS[self._hotkey]
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
        for keycode, flags in self._chord:
            self._poster(keycode, True, flags)
        self._held_since = self._clock()
        log.info("voice: hold started (%s down)", self._hotkey)
        return True

    def stop(self) -> None:
        if not self.active or self._poster is None:
            self._held_since = None
            return
        # Flags describe the modifier state AFTER the event, so a release must
        # clear them. Sending fn-up while still asserting FLAG_SECONDARY_FN
        # told macOS fn was *still held* — the key stuck down system-wide and
        # dictation never stopped. Release always carries flags=0.
        for keycode, _flags in reversed(self._chord):
            self._poster(keycode, False, 0)
        held = self._clock() - (self._held_since or self._clock())
        self._held_since = None
        log.info("voice: hold released after %.1fs (%s up)", held, self._hotkey)

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

    @staticmethod
    def _tap_via_system_events(keycode: int) -> bool:
        """Deliver a key through System Events instead of CGEventPost.

        Warp ignores CGEventPost synthetics — the Enter arrives, the log says
        it was tapped, and nothing submits, while the identical event works in
        every other app. System Events posts through a different path that
        Warp does accept. Costs a subprocess (~40ms), so it is opt-in.
        """
        import subprocess
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to key code {keycode}'],
                capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                log.warning("key: System Events failed: %s",
                            r.stderr.strip()[:200])
                return False
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("key: System Events error: %s", e)
            return False

    def tap(self, name: str) -> bool:
        """Press and release one allowlisted key (swipe-down → Enter).

        Refused while a push-to-talk hold is active: the dictation modifier
        is still down, so a bare Return would arrive as a different chord.
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
        method = (os.environ.get("CC_BUDDY_KEY_METHOD") or "auto").strip().lower()
        if method in ("osascript", "system-events"):
            ok = self._tap_via_system_events(key)
            log.info("key: tapped %s via System Events (%s)", name,
                     "ok" if ok else "FAILED")
            return ok

        self._poster(key, True, 0, False)
        # Hold briefly. A down/up in the same microsecond is not a keypress any
        # human could produce, and apps that debounce or sample input on a
        # frame boundary drop it entirely.
        time.sleep(0.03)
        self._poster(key, False, 0, False)
        log.info("key: tapped %s", name)
        return True

    def overdue(self) -> bool:
        """True when a hold has exceeded MAX_HOLD_SECS — the stop event was
        lost (dead link, board reset) and the keys must be force-released."""
        return (self._held_since is not None
                and self._clock() - self._held_since > MAX_HOLD_SECS)
