from cc_buddy_bridge.focus_terminal import _DEFAULT_ACTIVATE_TARGETS, activate_targets


def test_default_targets_without_env(monkeypatch):
    monkeypatch.delenv("CC_BUDDY_FOCUS_APPS", raising=False)
    assert activate_targets() == _DEFAULT_ACTIVATE_TARGETS


def test_env_overrides_order_and_keeps_known_bundle_ids(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_FOCUS_APPS", "cmux, Warp, MyTerm")
    assert activate_targets() == [
        ("cmux", "com.cmuxterm.app"),      # known → keeps its bundle id
        ("Warp", "dev.warp.Warp"),
        ("MyTerm", None),                  # unknown → name activation
    ]


def test_env_matches_known_names_case_insensitively(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_FOCUS_APPS", "warp,composer")
    assert activate_targets() == [
        ("Warp", "dev.warp.Warp"),
        ("Composer", None),
    ]


def test_env_blank_or_garbage_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_FOCUS_APPS", "  ")
    assert activate_targets() == _DEFAULT_ACTIVATE_TARGETS
    monkeypatch.setenv("CC_BUDDY_FOCUS_APPS", ", ,")
    assert activate_targets() == _DEFAULT_ACTIVATE_TARGETS
