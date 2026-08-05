"""Diag report rendering — the output someone reads at 2am mid-freeze.

Covers the classification that decides whether a reset gets flagged, since
mislabelling a watchdog reboot as normal is the failure that wastes an hour.
"""

from __future__ import annotations

import pytest

from cc_buddy_bridge.cli import _render_diag


def test_abnormal_reset_is_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    _render_diag({"boot": 7, "reset": "TASK-WATCHDOG", "up": 42,
                  "heap": 1000, "minheap": 900, "psram": 8000,
                  "last": ["1200:voice start", "1500:gesture tap"]}, True)
    out = capsys.readouterr().out
    assert "ABNORMAL" in out
    assert "TASK-WATCHDOG" in out
    assert "voice start" in out
    assert "1.20s" in out          # ms stamps rendered as seconds
    assert "gesture tap" in out


def test_clean_poweron_is_not_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    _render_diag({"boot": 1, "reset": "power-on", "up": 3,
                  "heap": 1000, "minheap": 900, "psram": 8000, "last": []}, True)
    out = capsys.readouterr().out
    assert "ABNORMAL" not in out
    assert "no surviving event ring" in out


@pytest.mark.parametrize("reason", ["PANIC", "INT-WATCHDOG", "BROWNOUT",
                                    "other-watchdog"])
def test_every_crash_class_is_flagged(reason: str,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    _render_diag({"boot": 2, "reset": reason, "up": 1, "heap": 1,
                  "minheap": 1, "psram": 1, "last": []}, True)
    assert "ABNORMAL" in capsys.readouterr().out


def test_no_report_yet_explains_itself(capsys: pytest.CaptureFixture[str]) -> None:
    _render_diag(None, False)
    out = capsys.readouterr().out
    assert "no diag report yet" in out
    assert "board connected: False" in out


def test_malformed_timestamp_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    _render_diag({"boot": 1, "reset": "software", "up": 1, "heap": 1,
                  "minheap": 1, "psram": 1, "last": ["notanumber:weird"]}, True)
    assert "weird" in capsys.readouterr().out
