"""Serial transport — receive-silence watchdog.

Regression cover for a link that dies without raising. When the USB device
re-enumerates (every board reset, including esptool's), the host keeps a file
descriptor that no longer reaches the board: writes succeed, reads return empty,
nothing raises, and the daemon reports itself healthy forever while the board
shows "No Claude connected". Only a read-silence timeout catches that.

Async tests follow the repo convention (see test_ble_radio_reset): an explicit
_run() around asyncio.run(), rather than a pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

import pytest

from cc_buddy_bridge import serial_transport
from cc_buddy_bridge.serial_transport import BuddySerial

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(coro)


class FakeSerial:
    """Minimal pyserial stand-in.

    ``lines`` are returned one per readline() call; once exhausted every
    subsequent call returns b"" — exactly what a stale handle looks like, since
    pyserial returns empty on its read timeout rather than raising.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self.is_open = True
        self.dtr = False
        self.closed = False
        self.written: list[bytes] = []

    def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.is_open = False


@pytest.fixture
def fast_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the timeouts so these run in milliseconds, not half a minute."""
    monkeypatch.setattr(serial_transport, "RX_SILENCE_SECS", 0.05)
    monkeypatch.setattr(serial_transport, "RECONNECT_SECS", 0.01)


async def _noop(obj: dict) -> None:
    pass


async def _drive(bs: BuddySerial, seconds: float) -> None:
    """Run the transport loop for a bounded time, then shut it down cleanly."""
    task = asyncio.create_task(bs.run())
    await asyncio.sleep(seconds)
    await bs.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _patch_port(monkeypatch: pytest.MonkeyPatch, opener) -> None:
    monkeypatch.setattr(serial_transport.serial, "Serial", opener)
    monkeypatch.setattr(serial_transport, "_resolve_port", lambda p: "/dev/fake")


def test_silence_triggers_reconnect(fast_watchdog: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A port that goes quiet is reopened rather than held forever."""
    opened: list[FakeSerial] = []

    def fake_open(port: str, baud: int, timeout: int = 1) -> FakeSerial:
        s = FakeSerial([b"[alive] up=1s\n"])  # one line, then permanent silence
        opened.append(s)
        return s

    _patch_port(monkeypatch, fake_open)

    bs = BuddySerial(on_message=_noop, port="/dev/fake")
    _run(_drive(bs, 0.4))

    # Without the watchdog this stays at 1 forever.
    assert len(opened) > 1, "stale handle was never dropped"
    assert opened[0].closed, "the dead handle should be closed on teardown"


def test_traffic_keeps_link_open(fast_watchdog: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chatty board is never reconnected — the watchdog must not flap."""
    opened: list[FakeSerial] = []

    def fake_open(port: str, baud: int, timeout: int = 1) -> FakeSerial:
        # Far more lines than the test will consume: the board keeps talking.
        s = FakeSerial([b"[alive] up=1s\n"] * 100_000)
        opened.append(s)
        return s

    _patch_port(monkeypatch, fake_open)

    bs = BuddySerial(on_message=_noop, port="/dev/fake")
    _run(_drive(bs, 0.4))

    assert len(opened) == 1, "a live link was needlessly reconnected"


def test_json_lines_reach_the_handler(fast_watchdog: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON boot chatter is skipped; JSON is parsed and dispatched."""
    seen: list[dict] = []

    async def capture(obj: dict) -> None:
        seen.append(obj)

    def fake_open(port: str, baud: int, timeout: int = 1) -> FakeSerial:
        return FakeSerial([
            b"[alive] up=1s\n",
            b'{"cmd":"permission","id":"abc","decision":"once"}\n',
        ])

    _patch_port(monkeypatch, fake_open)

    bs = BuddySerial(on_message=capture, port="/dev/fake")
    _run(_drive(bs, 0.2))

    assert {"cmd": "permission", "id": "abc", "decision": "once"} in seen
