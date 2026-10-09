"""Audio I/O: recording the handset mic and playing tones to the earpiece (or Mac speakers)."""

from __future__ import annotations

import logging
import threading
import time
from fractions import Fraction

import numpy as np

log = logging.getLogger(__name__)

STT_RATE = 16_000  # what Whisper expects
PLAY_RATE = 48_000


def to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    if rate == STT_RATE:
        return audio.astype(np.float32)
    from scipy.signal import resample_poly

    ratio = Fraction(STT_RATE, int(rate))
    return resample_poly(audio, ratio.numerator, ratio.denominator).astype(np.float32)


def rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return 20 * np.log10(rms) if rms > 0 else float("-inf")


def find_device(name: str, kind: str) -> int:
    """Index of the first device whose name contains `name` and has `kind` ("input"/"output") channels."""
    import sounddevice as sd

    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device[channels] > 0:
            return index
    raise LookupError(f"no {kind} audio device matching {name!r}")


class Recorder:
    """Records mono audio from one input device. stop() returns it resampled to 16 kHz."""

    def __init__(self, device_name: str):
        self.device_name = device_name
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._rate = PLAY_RATE

    def start(self) -> None:
        import sounddevice as sd

        device = find_device(self.device_name, "input")
        self._rate = int(sd.query_devices(device)["default_samplerate"])
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(
            device=device, channels=1, samplerate=self._rate, dtype="float32", callback=self._callback
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("input status: %s", status)
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        return to_16k(audio, self._rate)


def record(device_name: str, seconds: float) -> np.ndarray:
    recorder = Recorder(device_name)
    recorder.start()
    time.sleep(seconds)
    return recorder.stop()


def tone(freqs: list[float], seconds: float, volume: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * PLAY_RATE)) / PLAY_RATE
    wave = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
    ramp = min(len(t) // 2, int(0.005 * PLAY_RATE))  # 5 ms fades avoid clicks
    envelope = np.ones_like(t)
    envelope[:ramp] = np.linspace(0, 1, ramp)
    envelope[len(t) - ramp :] = np.linspace(1, 0, ramp)
    return (volume * wave * envelope).astype(np.float32)


def ring_sound(cadence: list[int], volume: float = 0.3) -> np.ndarray:
    """Electronic-ringer warble: 500/600 Hz alternating 16 times a second during each "on" period."""
    parts = []
    for i, ms in enumerate(cadence):
        n = int(ms / 1000 * PLAY_RATE)
        if i % 2 == 0:
            t = np.arange(n) / PLAY_RATE
            freq = np.where((t * 16).astype(int) % 2 == 0, 500.0, 600.0)
            parts.append(volume * np.sin(2 * np.pi * np.cumsum(freq) / PLAY_RATE))
        else:
            parts.append(np.zeros(n))
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)


def play(samples: np.ndarray, device_name: str | None = None) -> None:
    """Blocking playback of mono samples on both channels. device_name None = system default output."""
    import sounddevice as sd

    device = find_device(device_name, "output") if device_name else None
    stereo = np.repeat(samples[:, None], 2, axis=1)
    with sd.OutputStream(device=device, samplerate=PLAY_RATE, channels=2, dtype="float32") as stream:
        stream.write(stereo)


class Earpiece:
    """Short cues played into the handset earpiece."""

    def __init__(self, device_name: str):
        self.device_name = device_name

    def beep(self) -> None:
        try:
            play(tone([1000], 0.25), self.device_name)
        except Exception:
            log.exception("could not play beep on %r", self.device_name)
