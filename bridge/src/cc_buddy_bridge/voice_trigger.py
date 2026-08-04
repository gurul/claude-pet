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
MAX_HOLD_SECS = 60.0

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

    def post(keycode: int, down: bool, with_option: bool) -> None:
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        if with_option:
            Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskAlternate)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    return post


def _check_accessibility() -> bool:
    """True if this process may post events; prompts the user on first ask."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        return bool(AXIsProcessTrustedWithOptions(
            {kAXTrustedCheckOptionPrompt: True}))
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

    @property
    def active(self) -> bool:
        return self._held_since is not None

    def start(self) -> bool:
        if self._poster is None:
            return False
        if self.active:
            return True
        if self._trust_check is not None:
            if not self._trust_check():
                log.warning(
                    "voice: Accessibility not granted — grant the daemon's "
                    "python in System Settings > Privacy & Security > "
                    "Accessibility, then hold the pet again")
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

    def overdue(self) -> bool:
        """True when a hold has exceeded MAX_HOLD_SECS — the stop event was
        lost (dead link, board reset) and the keys must be force-released."""
        return (self._held_since is not None
                and self._clock() - self._held_since > MAX_HOLD_SECS)
