"""USB CDC serial transport — drop-in replacement for BuddyBLE.

The claude-desktop-buddy firmware feeds the same newline-delimited JSON
parser from USB serial as from BLE NUS (see firmware data.h), so the only
difference is the pipe. On the ESP32-S3's native USB-Serial/JTAG port,
opening the port does NOT reset the sketch, but the host must assert DTR
or the firmware's HWCDC TX is silently dropped.

Duck-types BuddyBLE's surface used by Daemon: ``connected``,
``wait_connected()``, ``send(obj, codec=None)``, ``run()``, ``stop()``.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

import serial  # pyserial

from .protocol import encode

log = logging.getLogger(__name__)

IncomingHandler = Callable[[dict[str, Any]], Awaitable[None]]

RECONNECT_SECS = 3.0

# Board output worth surfacing at WARNING: ESP-IDF error lines, panic dumps,
# watchdog trips, and the ROM banner that means it just reset underneath us.
_IS_CRASH = re.compile(
    r"watchdog|Backtrace|Guru Meditation|abort\(\)|assert failed|"
    r"StoreProhibited|LoadProhibited|rst:0x|E \(\d+\)|CORRUPT HEAP|stack overflow",
    re.IGNORECASE,
)

# Treat the link as dead after this long with no bytes read at all.
#
# A USB re-enumeration — which every board reset causes, including the one
# esptool triggers on flash — leaves the host holding a file descriptor that no
# longer reaches the device. Writes succeed into the void, reads return empty,
# and nothing ever raises, so `connected` stays True forever: the daemon looks
# healthy, the log is clean, and the board sits there showing "No Claude
# connected". Only an exception used to trigger reconnect, and a stale fd never
# produces one.
#
# The firmware prints "[alive] ..." every 5s unconditionally (main.cpp), so
# silence this long means four missed pings and is unambiguous. Keep this in
# sync with that interval — if the firmware's heartbeat changes, this must too,
# or the transport will flap.
RX_SILENCE_SECS = 20.0


def _resolve_port(pattern: str) -> Optional[str]:
    """Expand a glob like /dev/cu.usbmodem* to a concrete port (macOS
    re-enumerates the number when the board changes USB sockets)."""
    if "*" not in pattern:
        return pattern
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


class BuddySerial:
    def __init__(
        self,
        on_message: IncomingHandler,
        port: str,
        baud: int = 115200,
    ) -> None:
        self.on_message = on_message
        self.port_pattern = port
        self.baud = baud
        self._ser: Optional[serial.Serial] = None
        self._connected_evt = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    async def wait_connected(self) -> None:
        await self._connected_evt.wait()

    async def send(self, obj: dict[str, Any], codec: Optional[str] = None) -> bool:
        if not self.connected or self._ser is None:
            return False
        data = encode(obj, codec=codec)
        loop = asyncio.get_running_loop()
        try:
            async with self._send_lock:
                await loop.run_in_executor(None, self._ser.write, data)
                await loop.run_in_executor(None, self._ser.flush)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("serial send failed: %s", e)
            self._teardown()
            return False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            port = _resolve_port(self.port_pattern)
            if port is None:
                await asyncio.sleep(RECONNECT_SECS)
                continue
            try:
                ser = serial.Serial(port, self.baud, timeout=1)
                ser.dtr = True  # firmware HWCDC drops TX without DTR
            except Exception as e:  # noqa: BLE001
                log.debug("serial open %s failed: %s", port, e)
                await asyncio.sleep(RECONNECT_SECS)
                continue

            self._ser = ser
            self._connected_evt.set()
            log.info("serial connected: %s @ %d", port, self.baud)
            buf = b""
            # Grace period starts now: a board mid-boot hasn't printed yet.
            last_rx = time.monotonic()
            try:
                while not self._stop.is_set():
                    chunk = await loop.run_in_executor(None, ser.readline)
                    if not chunk:
                        # readline returned on its 1s timeout. Silence past the
                        # watchdog means the fd is stale — drop it and let the
                        # outer loop re-resolve the port glob and reopen.
                        if time.monotonic() - last_rx > RX_SILENCE_SECS:
                            log.warning(
                                "serial: no data from board for %.0fs — assuming stale "
                                "handle (USB re-enumeration?), reconnecting",
                                RX_SILENCE_SECS,
                            )
                            break
                        continue
                    last_rx = time.monotonic()
                    if chunk:
                        buf += chunk
                        if not buf.endswith(b"\n"):
                            continue  # partial line (timeout mid-line)
                        line, buf = buf, b""
                        text = line.decode("utf-8", errors="replace").strip()
                        if not text.startswith("{"):
                            if text:
                                # Crash output must survive at the default log
                                # level. A panic backtrace logged at DEBUG is
                                # invisible exactly when it matters, and asking
                                # someone to reproduce a freeze under a
                                # hand-started DEBUG daemon is a bad trade.
                                log.log(
                                    logging.WARNING if _IS_CRASH.search(text)
                                    else logging.DEBUG,
                                    "stick: %s", text,
                                )
                            continue
                        try:
                            obj = json.loads(text)
                        except json.JSONDecodeError:
                            log.debug("serial: bad json: %r", text[:120])
                            continue
                        asyncio.create_task(self.on_message(obj))
            except Exception as e:  # noqa: BLE001
                log.warning("serial read error (%s) — reconnecting", e)
            finally:
                self._teardown()
            await asyncio.sleep(RECONNECT_SECS)

    def _teardown(self) -> None:
        self._connected_evt.clear()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
            self._ser = None

    def pulse_reset(self, why: str) -> None:
        """Hardware-reset the board via the RTS line, then reconnect.

        The escalation for a board-side USB wedge: the S3 keeps transmitting
        but stops receiving, and reopening the host port does nothing because
        the dead half is in the chip. Observed twice; both times only a reset
        cured it. The pet reboots (~5s, diag ring reports the reset as
        external-pin) — a short outage against an indefinitely deaf board.
        """
        ser = self._ser
        log.warning("serial: RTS-resetting the board — %s", why)
        if ser is not None:
            try:
                ser.dtr = False
                ser.rts = True
                time.sleep(0.1)
                ser.rts = False
            except Exception as e:  # noqa: BLE001
                log.warning("serial: RTS pulse failed: %s", e)
        self._teardown()

    def force_reconnect(self, why: str) -> None:
        """Drop the handle so run() reopens the port.

        For the half-dead link: writes silently go nowhere while reads keep
        working, so RX_SILENCE_SECS never trips and the daemon looks healthy
        while the board sits there showing "No Claude connected". Only the
        absence of *replies* reveals it — see the daemon's status-ack watchdog.
        """
        log.warning("serial: forcing reconnect — %s", why)
        self._teardown()

    async def stop(self) -> None:
        self._stop.set()
        self._teardown()
