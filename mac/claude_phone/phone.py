"""Phone drivers: the real phone over USB serial (the LGT8F328P Nano), or a keyboard simulator.

Serial protocol, one message per line at 115200 baud:
    Nano -> Mac:  HOOK_UP | HOOK_DOWN | KEY <0-9 * #>
    Mac -> Nano:  RING <on_ms>[,<off_ms>,<on_ms>...]
"""

from __future__ import annotations

import glob
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass

from .audio import play, ring_sound

log = logging.getLogger(__name__)

KEYS = set("0123456789*#")
BAUD = 115200


@dataclass(frozen=True)
class Event:
    kind: str  # "hook_up", "hook_down" or "key"
    key: str | None = None


def parse_line(line: str) -> Event | None:
    parts = line.strip().split()
    if not parts:
        return None
    command = parts[0].upper()
    if command == "HOOK_UP" and len(parts) == 1:
        return Event("hook_up")
    if command == "HOOK_DOWN" and len(parts) == 1:
        return Event("hook_down")
    if command == "KEY" and len(parts) == 2 and parts[1] in KEYS:
        return Event("key", parts[1])
    return None


def format_ring(cadence: list[int]) -> str:
    return "RING " + ",".join(str(int(ms)) for ms in cadence)


def find_serial_port() -> str:
    ports = sorted(glob.glob("/dev/cu.wchusbserial*") + glob.glob("/dev/cu.usbserial*"))
    if not ports:
        raise LookupError("no USB serial port found. Is the Nano plugged in?")
    return ports[0]


class SerialPhone:
    def __init__(self, port: str | None = None):
        self.port = port or find_serial_port()
        self.events: queue.Queue[Event] = queue.Queue()
        self._lock = threading.Lock()
        self._serial = None

    def start(self) -> None:
        import serial

        self._serial = serial.Serial(self.port, BAUD, timeout=0.5)
        time.sleep(2)  # opening the port resets the Nano; wait for it to boot
        threading.Thread(target=self._read_loop, daemon=True).start()
        log.info("phone connected on %s", self.port)

    def _read_loop(self) -> None:
        while True:
            try:
                raw = self._serial.readline()
            except Exception:
                log.exception("serial read failed; phone disconnected?")
                return
            line = raw.decode(errors="replace").strip()
            event = parse_line(line)
            if event:
                self.events.put(event)
            elif line:
                log.debug("nano: %s", line)

    def ring(self, cadence: list[int]) -> None:
        with self._lock:
            self._serial.write((format_ring(cadence) + "\n").encode())


class KeyboardPhone:
    """Simulates the phone in the terminal. Rings play on the Mac's default output."""

    HELP = (
        "Keyboard phone: press Enter to lift / hang up the handset. "
        "Type keys (0-9 * #) then Enter to press them; this lifts the handset first if it's down."
    )

    def __init__(self):
        self.events: queue.Queue[Event] = queue.Queue()
        self.off_hook = False

    def start(self) -> None:
        print(self.HELP)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        for line in sys.stdin:
            self.handle_line(line)

    def handle_line(self, line: str) -> None:
        keys = [char for char in line if char in KEYS]
        if not line.strip() or (keys and not self.off_hook):
            self._toggle_hook()
        if keys:
            print(f"⌨️  pressed {' '.join(keys)}, speak now")
            for key in keys:
                self.events.put(Event("key", key))

    def _toggle_hook(self) -> None:
        self.off_hook = not self.off_hook
        print("📞 handset lifted, speak now" if self.off_hook else "📞 hung up")
        self.events.put(Event("hook_up" if self.off_hook else "hook_down"))

    def ring(self, cadence: list[int]) -> None:
        print(f"🔔 ring {cadence}")
        play(ring_sound(cadence))
