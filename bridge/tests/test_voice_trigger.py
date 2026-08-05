"""VoiceHold — the Opt+Space push-to-talk state machine.

The failure mode that matters is a stuck key: a system-wide held Opt+Space
breaks typing everywhere until something releases it. Every test that ends a
hold asserts the exact release sequence actually happened.
"""

from __future__ import annotations

from cc_buddy_bridge.voice_trigger import (
    FLAG_ALTERNATE,
    FLAG_SECONDARY_FN,
    KEY_FUNCTION,
    KEY_OPTION,
    KEY_RETURN,
    KEY_SPACE,
    MAX_HOLD_SECS,
    VoiceHold,
)


class FakePoster:
    def __init__(self) -> None:
        self.events: list[tuple[int, bool, int]] = []
        self.sources: list[bool] = []   # True = HID source (holds)

    def __call__(self, keycode: int, down: bool, flags: int,
                 hold: bool = True) -> None:
        self.events.append((keycode, down, flags))
        self.sources.append(hold)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _hold(trusted: bool = True) -> tuple[VoiceHold, FakePoster, FakeClock]:
    p, c = FakePoster(), FakeClock()
    v = VoiceHold(poster=p, clock=c, hotkey="opt-space",
                  trust_check=(lambda: trusted) if not trusted else None)
    return v, p, c


def test_start_posts_option_then_space_down() -> None:
    v, p, _ = _hold()
    assert v.start()
    assert p.events == [(KEY_OPTION, True, 0), (KEY_SPACE, True, FLAG_ALTERNATE)]
    assert v.active


def test_stop_releases_space_then_option() -> None:
    v, p, _ = _hold()
    v.start()
    v.stop()
    assert p.events[2:] == [(KEY_SPACE, False, 0), (KEY_OPTION, False, 0)]
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
    assert p.events[-2:] == [(KEY_SPACE, False, 0), (KEY_OPTION, False, 0)]


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
    assert p.events == [(KEY_RETURN, True, 0), (KEY_RETURN, False, 0)]


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


# ---- hotkey selection (Willow Voice = fn, VoiceFlow = opt-space) ----

def test_fn_hotkey_holds_the_function_key() -> None:
    p = FakePoster()
    v = VoiceHold(poster=p, hotkey="fn", trust_check=None)
    assert v.start()
    # fn must carry the secondary-fn flag or listeners ignore it entirely.
    assert p.events == [(KEY_FUNCTION, True, FLAG_SECONDARY_FN)]
    v.stop()
    # Release MUST clear flags: asserting fn on the key-up tells macOS the
    # modifier is still held, and it sticks down system-wide.
    assert p.events[-1] == (KEY_FUNCTION, False, 0)


def test_option_is_the_default() -> None:
    v = VoiceHold(poster=FakePoster(), trust_check=None)
    assert v._hotkey == "option"


def test_option_chord_holds_and_clears_on_release() -> None:
    from cc_buddy_bridge.voice_trigger import FLAG_ALTERNATE
    p = FakePoster()
    v = VoiceHold(poster=p, hotkey="option", trust_check=None)
    v.start()
    assert p.events == [(KEY_OPTION, True, FLAG_ALTERNATE)]
    v.stop()
    assert p.events[-1] == (KEY_OPTION, False, 0)


def test_unknown_hotkey_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("CC_BUDDY_VOICE_HOTKEY", "banana")
    v = VoiceHold(poster=FakePoster(), trust_check=None)
    assert v._hotkey == "option"


def test_env_selects_opt_space(monkeypatch) -> None:
    monkeypatch.setenv("CC_BUDDY_VOICE_HOTKEY", "opt-space")
    v = VoiceHold(poster=FakePoster(), trust_check=None)
    assert v._hotkey == "opt-space"


def test_release_always_clears_flags() -> None:
    """Regression: a release that still asserts its modifier flag sticks the
    key down for the whole system — observed with fn and Willow Voice."""
    for name in ("fn", "opt-space"):
        p = FakePoster()
        v = VoiceHold(poster=p, hotkey=name, trust_check=None)
        v.start()
        v.stop()
        releases = [e for e in p.events if e[1] is False]
        assert releases, name
        assert all(flags == 0 for _k, _d, flags in releases), (name, releases)


def test_enter_defaults_to_main_return() -> None:
    from cc_buddy_bridge.voice_trigger import KEY_RETURN, _enter_keycode
    assert _enter_keycode() == KEY_RETURN


def test_enter_can_be_switched_to_keypad(monkeypatch) -> None:
    from cc_buddy_bridge.voice_trigger import KEY_KEYPAD_ENTER, _enter_keycode
    monkeypatch.setenv("CC_BUDDY_ENTER_KEY", "keypad")
    assert _enter_keycode() == KEY_KEYPAD_ENTER


def test_taps_use_the_null_event_source() -> None:
    """Regression: Return built from a real HID source was swallowed by Warp's
    global key handling and never reached the focused app — the dictated text
    sat in the prompt unsent. Taps must use the NULL source."""
    v, p, _ = _hold()
    v.tap("enter")
    assert p.sources == [False, False]


def test_holds_use_the_hid_event_source() -> None:
    """Modifier holds need real keyboard state or dictation apps ignore them."""
    v, p, _ = _hold()
    v.start()
    assert all(p.sources), p.sources
