"""USB CDC serial transport — drop-in replacement for BuddyBLE.

The claude-desktop-buddy firmware feeds the same newline-delimited JSON
parser from USB serial as from BLE NUS (see firmware data.h), so the only
difference is the pipe. On the ESP32-S3's native USB-Serial/JTAG port,
opening the port does NOT reset the sketch, but the host must assert DTR
or the firmware's HWCDC TX is silently dropped.

Duck-types BuddyBLE's surface used by Daemon: ``connected``,
``wait_connected()``, ``send(obj, codec=None)``, ``run()``, ``stop()``.

Threading model: run()'s reader thread is the SOLE owner of the port
handle's close. Event-loop-side paths (force_reconnect, pulse_reset,
send-failure, stop) never close the fd — closing it under the reader's
blocked readline() raced (paired EBADF on every forced reconnect, and an
fd-reuse hazard). They set a drop reason and cancel_read() instead; the
reader notices, breaks, and closes in its finally.
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
# After an ack-loss forced reconnect, hold the port CLOSED this long before
# reopening. Field evidence: the only things that ever revived the deaf-board
# wedge short of a reflash were daemon restarts — i.e. tens of seconds of
# port-closed time — while 3s in-process reopens never did.
FORCED_HOLDOFF_SECS = 15.0
# A board that is enumerated-but-silent (fresh opens, zero bytes) this many
# times within the window gets an RTS hardware reset on the next open. The
# silent-board crash loops of 2026-08-05 ran 20+ minutes of reopen loops that
# could never help; the RTS pulse reboots the chip and ends it in ~3 minutes.
RX_TRIP_PULSE_COUNT = 3
RX_TRIP_WINDOW_SECS = 600.0
# ESP32-S3 native USB-Serial/JTAG. (TinyUSB CDC builds present 303a:0002 —
# this firmware uses the built-in peripheral.)
_ESP32S3_VID = 0x303A

# Board output worth surfacing at WARNING: ESP-IDF error lines, panic dumps,
# watchdog trips, the diag ring's died-in-phase line, and the ROM banner that
# means it just reset underneath us.
_IS_CRASH = re.compile(
    r"watchdog|Backtrace|Guru Meditation|abort\(\)|assert failed|DIED IN|"
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
# The firmware prints "[alive] ..." every 5s unconditionally WHILE loop() runs
# (main.cpp) — so silence this long means either a stale fd or a hung/crashed
# board (the USB peripheral is autonomous silicon and keeps enumerating while
# the CPU is stuck). Keep in sync with the firmware interval.
RX_SILENCE_SECS = 20.0


def _resolve_port(pattern: str) -> Optional[str]:
    """Expand a glob like /dev/cu.usbmodem* to a concrete port (macOS
    re-enumerates the number when the board changes USB sockets). With
    multiple matches, prefer the node whose USB VID is the ESP32-S3's, and
    say so — silently picking sorted()[0] grabbed whatever enumerated
    first."""
    if "*" not in pattern:
        return pattern
    hits = sorted(glob.glob(pattern))
    if not hits:
        return None
    if len(hits) > 1:
        try:
            from serial.tools import list_ports
            s3 = sorted(
                p.device for p in list_ports.comports()
                if p.device in hits and p.vid == _ESP32S3_VID
            )
        except Exception:  # noqa: BLE001
            s3 = []
        if s3:
            log.warning("serial: %d nodes match %s — preferring ESP32-S3 %s",
                        len(hits), pattern, s3[0])
            return s3[0]
        log.warning("serial: %d nodes match %s — picking %s",
                    len(hits), pattern, hits[0])
    return hits[0]


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
        self._drop_reason: Optional[str] = None  # reader-owned teardown request
        self._reopen_holdoff = 0.0               # extra close-time before next open
        self._rx_trips: list[tuple[float, str]] = []  # (monotonic, port) silence trips
        self._pulse_on_open = False              # RTS-reset the board on next open

    @property
    def connected(self) -> bool:
        return (self._ser is not None and self._ser.is_open
                and self._drop_reason is None)

    async def wait_connected(self) -> None:
        await self._connected_evt.wait()

    async def send(self, obj: dict[str, Any], codec: Optional[str] = None) -> bool:
        # Capture once: self._ser can be swapped/dropped across the await
        # suspension points below (the old TOCTOU produced NoneType bursts
        # during teardown windows).
        ser = self._ser
        if ser is None or not ser.is_open or self._drop_reason is not None:
            return False
        data = encode(obj, codec=codec)
        loop = asyncio.get_running_loop()
        try:
            async with self._send_lock:
                if self._ser is not ser or not ser.is_open or self._drop_reason is not None:
                    return False
                # write_timeout bounds this (set at open). No flush(): that was
                # termios.tcdrain, which no timeout covers — one board that
                # stopped draining the OUT endpoint would block a sender
                # forever while every other sender queued on the lock.
                await loop.run_in_executor(None, ser.write, data)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("serial send failed: %s", e)
            # Only condemn the handle the failure belongs to — a write that
            # raises late must not tear down a fresh reopened connection.
            if self._ser is ser:
                self._request_drop(f"send failed: {e}")
            return False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        missing_since: Optional[float] = None
        next_missing_warn = 30.0
        while not self._stop.is_set():
            port = _resolve_port(self.port_pattern)
            if port is None:
                # Device off the bus: say so, loudly then backing off. The
                # 52-minute silent log hole of 2026-08-05 04:43 was this path
                # having no log line at all.
                now = time.monotonic()
                if missing_since is None:
                    missing_since = now
                    next_missing_warn = 30.0
                elif now - missing_since >= next_missing_warn:
                    log.warning(
                        "serial: no port matching %s for %.0fs — board unplugged?",
                        self.port_pattern, now - missing_since)
                    next_missing_warn = min(next_missing_warn * 2, 600.0)
                await asyncio.sleep(RECONNECT_SECS)
                continue
            if missing_since is not None:
                log.info("serial: device reappeared at %s after %.0fs away",
                         port, time.monotonic() - missing_since)
                missing_since = None
            try:
                ser = serial.Serial(port, self.baud, timeout=1,
                                    write_timeout=2, exclusive=True)
                ser.dtr = True  # firmware HWCDC drops TX without DTR
            except Exception as e:  # noqa: BLE001
                log.warning("serial open %s failed: %s", port, e)
                await asyncio.sleep(RECONNECT_SECS)
                continue

            if self._pulse_on_open:
                # Escalation for the enumerated-but-silent board: pulse RTS on
                # the FRESH handle (pulsing before reopen is a no-op — the old
                # handle is already gone). Logs unconditionally: this line's
                # absence from a log is proof the pulse never ran.
                self._pulse_on_open = False
                log.warning(
                    "serial: RTS-resetting the board — %d silent reopens of %s "
                    "within %.0f min", RX_TRIP_PULSE_COUNT, port,
                    RX_TRIP_WINDOW_SECS / 60)
                try:
                    ser.rts = True
                    time.sleep(0.1)
                    ser.rts = False
                    ser.dtr = True
                except Exception as e:  # noqa: BLE001
                    log.warning("serial: RTS pulse failed: %s", e)
            try:
                ser.reset_input_buffer()
            except Exception:  # noqa: BLE001
                pass

            self._drop_reason = None
            self._ser = ser
            self._connected_evt.set()
            log.info("serial connected: %s @ %d", port, self.baud)
            buf = b""
            # Grace period starts now: a board mid-boot hasn't printed yet.
            last_rx = time.monotonic()
            try:
                while not self._stop.is_set():
                    chunk = await loop.run_in_executor(None, ser.readline)
                    if self._drop_reason is not None:
                        log.warning("serial: dropping link — %s", self._drop_reason)
                        break
                    if not chunk:
                        # readline returned on its 1s timeout (or a
                        # cancel_read). Silence past the watchdog means a stale
                        # fd or a hung board — drop and let the outer loop
                        # re-resolve the glob and reopen.
                        if time.monotonic() - last_rx > RX_SILENCE_SECS:
                            log.warning(
                                "serial: no data from board for %.0fs — assuming stale "
                                "handle (USB re-enumeration?), reconnecting",
                                RX_SILENCE_SECS,
                            )
                            self._note_rx_trip(port)
                            break
                        continue
                    last_rx = time.monotonic()
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
                self._close_current()
            holdoff = self._reopen_holdoff or RECONNECT_SECS
            self._reopen_holdoff = 0.0
            await asyncio.sleep(holdoff)

    def _close_current(self) -> None:
        """Close the current handle. Reader-side only (run()'s finally)."""
        self._connected_evt.clear()
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    def _request_drop(self, why: str) -> None:
        """Ask the reader to drop and reopen. Never closes the fd here —
        the reader owns the close (see module docstring)."""
        if self._drop_reason is None:
            self._drop_reason = why
        self._connected_evt.clear()
        ser = self._ser
        if ser is not None:
            try:
                ser.cancel_read()  # unblocks the reader's readline immediately
            except Exception:  # noqa: BLE001
                pass

    def _note_rx_trip(self, port: str) -> None:
        """Sliding-window counter of enumerated-but-silent episodes; arms the
        RTS pulse when the same port trips repeatedly."""
        now = time.monotonic()
        self._rx_trips = [
            (t, p) for (t, p) in self._rx_trips
            if now - t < RX_TRIP_WINDOW_SECS
        ]
        self._rx_trips.append((now, port))
        if sum(1 for (_, p) in self._rx_trips if p == port) >= RX_TRIP_PULSE_COUNT:
            self._pulse_on_open = True
            self._rx_trips.clear()

    def pulse_reset(self, why: str) -> None:
        """Hardware-reset the board via the RTS line, then reconnect.

        The escalation for a board-side USB wedge: the S3 keeps transmitting
        but stops receiving, and reopening the host port does nothing because
        the dead half is in the chip. The pet reboots (~5s, diag ring reports
        the reset as external-pin) — a short outage against an indefinitely
        deaf board. Pulses the LIVE handle first (ioctls under a concurrent
        select are safe; the fd must be open to pulse), then hands the drop
        to the reader.
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
        self._request_drop(f"RTS reset: {why}")

    def force_reconnect(self, why: str) -> None:
        """Drop the handle so run() reopens the port — after a deliberate
        closed-port holdoff. Field evidence: daemon restarts (tens of seconds
        closed) revived the deaf wedge; 3s in-process reopens never did.

        For the half-dead link: writes silently go nowhere while reads keep
        working, so RX_SILENCE_SECS never trips and the daemon looks healthy
        while the board sits there showing "No Claude connected". Only the
        absence of *replies* reveals it — see the daemon's status-ack watchdog.
        """
        log.warning("serial: forcing reconnect (holding port closed %.0fs) — %s",
                    FORCED_HOLDOFF_SECS, why)
        self._reopen_holdoff = FORCED_HOLDOFF_SECS
        self._request_drop(why)

    async def stop(self) -> None:
        self._stop.set()
        self._request_drop("stopping")
