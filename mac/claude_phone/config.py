"""Configuration, loaded from ~/.claude-phone/config.toml (see config.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path.home() / ".claude-phone"
CONFIG_PATH = DATA_DIR / "config.toml"

# Ring cadences in ms: on, off, on, ...
DEFAULT_RING_PATTERNS = {
    "done": [350],
    "attention": [300, 200, 300],
    "error": [150, 100, 150, 100, 150],
}


@dataclass
class Target:
    """Where a call's prompt goes. Selected by the digits pressed before speaking."""

    workspace: str | None = None  # Superset workspace id (runner = "superset")
    cwd: str | None = None  # working directory (runner = "iterm")


@dataclass
class Config:
    phone: str = "keyboard"  # "keyboard" (simulator) or "serial" (the Nano)
    serial_port: str | None = None  # None = auto-detect
    audio_input: str = "USB PnP Sound Device"
    audio_output: str = "USB PnP Sound Device"
    runner: str = "superset"  # "superset" or "iterm"
    superset_bin: str = "superset"
    ring_scope: str = "phone"  # "phone" = only sessions started by the phone, "all" = every Claude session
    http_port: int = 47333
    min_seconds: float = 1.0  # shorter calls are treated as accidental lifts
    silence_dbfs: float = -50.0  # quieter recordings are treated as silence
    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    language: str | None = None  # None = auto-detect
    initial_prompt: str | None = None  # vocabulary hint for Whisper
    targets: dict[str, Target] = field(default_factory=dict)
    ring_patterns: dict[str, list[int]] = field(default_factory=lambda: dict(DEFAULT_RING_PATTERNS))
    data_dir: Path = DATA_DIR


_TOP_LEVEL_KEYS = (
    "phone",
    "serial_port",
    "audio_input",
    "audio_output",
    "runner",
    "superset_bin",
    "ring_scope",
    "http_port",
    "min_seconds",
    "silence_dbfs",
)


def load(path: Path = CONFIG_PATH) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    raw = tomllib.loads(path.read_text())
    for key in _TOP_LEVEL_KEYS:
        if key in raw:
            setattr(cfg, key, raw[key])
    stt = raw.get("stt", {})
    cfg.stt_model = stt.get("model", cfg.stt_model)
    cfg.language = stt.get("language", cfg.language)
    cfg.initial_prompt = stt.get("initial_prompt", cfg.initial_prompt)
    for name, target in raw.get("targets", {}).items():
        cfg.targets[str(name)] = Target(workspace=target.get("workspace"), cwd=target.get("cwd"))
    cfg.ring_patterns.update(raw.get("ring_patterns", {}))
    if "data_dir" in raw:
        cfg.data_dir = Path(raw["data_dir"]).expanduser()
    return cfg
