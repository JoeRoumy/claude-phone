"""Speech-to-text with Whisper running locally on Apple Silicon (mlx-whisper).

Standalone use:
    uv run claude-phone-stt recording.wav
    uv run claude-phone-stt --record 5
    uv run claude-phone-stt --record 5 --device "MacBook Pro Microphone"
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # mlx-whisper checks the model cache on every call

from . import config
from .audio import STT_RATE, record, rms_dbfs, to_16k


class Transcriber:
    def __init__(self, model: str, language: str | None = None, initial_prompt: str | None = None):
        self.model = model
        self.language = language
        self.initial_prompt = initial_prompt

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe mono float32 audio at 16 kHz."""
        if audio.size == 0:
            return ""
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=self.model,
            language=self.language,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
            verbose=None,
        )
        return result["text"].strip()

    def warm_up(self) -> None:
        """Load the model (downloading it on first use) so the first real call is fast."""
        self.transcribe(np.zeros(STT_RATE, np.float32))


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    parser = argparse.ArgumentParser(description="Transcribe a WAV file or a live recording with Whisper.")
    parser.add_argument("file", nargs="?", help="audio file to transcribe")
    parser.add_argument("--record", type=float, metavar="SECONDS", help="record from --device instead")
    parser.add_argument("--device", default=cfg.audio_input, help="input device name (substring match)")
    parser.add_argument("--model", default=cfg.stt_model)
    parser.add_argument("--language", default=cfg.language)
    parser.add_argument("--initial-prompt", default=cfg.initial_prompt, help="vocabulary hint, e.g. 'daemon, Superset'")
    args = parser.parse_args(argv)

    if args.record:
        print(f"Recording {args.record:g}s from {args.device!r}…")
        audio = record(args.device, args.record)
    elif args.file:
        import soundfile

        data, rate = soundfile.read(args.file, dtype="float32", always_2d=True)
        audio = to_16k(data.mean(axis=1), rate)
    else:
        parser.error("give a file or --record SECONDS")

    print(f"{len(audio) / STT_RATE:.1f}s of audio, level {rms_dbfs(audio):.1f} dBFS")
    transcriber = Transcriber(args.model, args.language, args.initial_prompt)
    started = time.monotonic()
    text = transcriber.transcribe(audio)
    print(f"[{time.monotonic() - started:.1f}s] {text}")


if __name__ == "__main__":
    main()
