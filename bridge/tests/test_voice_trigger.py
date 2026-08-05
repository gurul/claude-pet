"""VoiceHold — the Opt+Space push-to-talk state machine.

The failure mode that matters is a stuck key: a system-wide held Opt+Space
breaks typing everywhere until something releases it. Every test that ends a
hold asserts the exact release sequence actually happened.
"""

from __future__ import annotations

from cc_buddy_bridge.voice_trigger import (
    KEY_OPTION,
    KEY_RETURN,
    KEY_SPACE,
    MAX_HOLD_SECS,
    VoiceHold,
)


class FakePoster:
    def __init__(self) -> None:
        self.events: list[tuple[int, bool, bool]] = []

    def __call__(self, keycode: int, down: bool, with_option: bool) -> None:
        self.events.append((keycode, down, with_option))


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _hold(trusted: bool = True) -> tuple[VoiceHold, FakePoster, FakeClock]:
    p, c = FakePoster(), FakeClock()
    v = VoiceHold(poster=p, clock=c,
                  trust_check=(lambda: trusted) if not trusted else None)
    return v, p, c


def test_start_posts_option_then_space_down() -> None:
    v, p, _ = _hold()
    assert v.start()
    assert p.events == [(KEY_OPTION, True, False), (KEY_SPACE, True, True)]
    assert v.active


def test_stop_releases_space_then_option() -> None:
    v, p, _ = _hold()
    v.start()
    v.stop()
    assert p.events[2:] == [(KEY_SPACE, False, True), (KEY_OPTION, False, False)]
    assert not v.active


def test_start_is_idempotent() -> None:
    v, p, _ = _hold()
    v.start()
    v.start()
    assert len(p.events) == 2  # no double key-down


def test_stop_without_start_posts_nothing() -> None:
    v, p, _ = _hold()
    v.stop()
    assert p.events == []


def test_stop_is_idempotent() -> None:
    v, p, _ = _hold()
    v.start()
    v.stop()
    v.stop()
    assert len(p.events) == 4  # exactly one release sequence


def test_overdue_after_max_hold() -> None:
    v, p, c = _hold()
    v.start()
    assert not v.overdue()
    c.t += MAX_HOLD_SECS + 1
    assert v.overdue()
    v.stop()   # watchdog path: releases cleanly
    assert not v.overdue()
    assert p.events[-2:] == [(KEY_SPACE, False, True), (KEY_OPTION, False, False)]


def test_untrusted_never_posts_and_stays_armed() -> None:
    calls = []

    def deny() -> bool:
        calls.append(1)
        return False

    p = FakePoster()
    v = VoiceHold(poster=p, trust_check=deny)
    assert not v.start()
    assert not v.start()
    assert p.events == []          # no half-held hotkey without permission
    assert len(calls) == 2         # re-checked per attempt (user may grant)
    assert not v.active


def test_no_poster_is_a_clean_noop() -> None:
    v = VoiceHold(poster=None, trust_check=lambda: True)
    # Simulates a host without Quartz — must not raise anywhere.
    v._poster = None
    assert not v.start()
    v.stop()
    assert not v.active


# ---- tap (swipe-down → Enter) ----

def test_tap_enter_presses_and_releases() -> None:
    v, p, _ = _hold()
    assert v.tap("enter")
    assert p.events == [(KEY_RETURN, True, False), (KEY_RETURN, False, False)]


def test_tap_refuses_unknown_key() -> None:
    v, p, _ = _hold()
    assert not v.tap("rm-rf")
    assert p.events == []


def test_tap_refused_during_voice_hold() -> None:
    """Opt is down mid-hold; a bare Return would become Opt+Return."""
    v, p, _ = _hold()
    v.start()
    before = len(p.events)
    assert not v.tap("enter")
    assert len(p.events) == before


def test_tap_untrusted_posts_nothing() -> None:
    p = FakePoster()
    v = VoiceHold(poster=p, trust_check=lambda: False)
    assert not v.tap("enter")
    assert p.events == []
