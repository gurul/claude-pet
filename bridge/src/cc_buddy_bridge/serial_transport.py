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
from typing import Any, Awaitable, Callable, Optional

import serial  # pyserial

from .protocol import encode

log = logging.getLogger(__name__)

IncomingHandler = Callable[[dict[str, Any]], Awaitable[None]]

RECONNECT_SECS = 3.0


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
            try:
                while not self._stop.is_set():
                    chunk = await loop.run_in_executor(None, ser.readline)
                    if chunk:
                        buf += chunk
                        if not buf.endswith(b"\n"):
                            continue  # partial line (timeout mid-line)
                        line, buf = buf, b""
                        text = line.decode("utf-8", errors="replace").strip()
                        if not text.startswith("{"):
                            if text:
                                log.debug("stick: %s", text)  # boot/debug prints
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

    async def stop(self) -> None:
        self._stop.set()
        self._teardown()
