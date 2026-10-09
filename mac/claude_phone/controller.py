"""The phone's brain: turns hook and key events into recordings, transcripts and Claude sessions,
and decides when Claude Code hook events should ring the phone.

Each target (no digits = "default", or the digits pressed) has one current conversation. A call continues it;
pressing * first starts a new one. If its terminal is gone, the conversation is resumed in a new terminal.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from .audio import STT_RATE, rms_dbfs
from .commands import CommandIndex, to_command
from .config import Config, Target
from .phone import Event
from .runners import RunnerError, Session

log = logging.getLogger(__name__)


@dataclass
class Call:
    id: str
    keys: str
    audio: np.ndarray
    started: datetime = field(default_factory=datetime.now)


def new_call_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")


class Controller:
    def __init__(
        self,
        cfg: Config,
        phone,
        recorder,
        transcriber,
        runner,
        earpiece,
        commands: CommandIndex | None = None,
        inline: bool = False,
    ):
        self.cfg = cfg
        self.phone = phone
        self.recorder = recorder
        self.transcriber = transcriber
        self.runner = runner
        self.earpiece = earpiece
        self.commands = commands or CommandIndex()
        self.inline = inline  # process calls on the caller's thread (tests)
        self.off_hook = False
        self.keys: list[str] = []
        self.call_started = datetime.now()
        self.sessions: dict[str, Session] = {}  # current conversation per target
        self._tracked: set[str] = set()  # call ids and terminal ids of every phone session
        self._lock = threading.Lock()
        self._calls: queue.Queue[Call] = queue.Queue()
        self._load_state()
        if not inline:
            threading.Thread(target=self._worker, daemon=True).start()

    # --- phone events -------------------------------------------------------

    def handle(self, event: Event) -> None:
        if event.kind == "hook_up" and not self.off_hook:
            self.off_hook = True
            self.keys = []
            self.call_started = datetime.now()
            self.earpiece.beep()  # "recording" cue; recording starts after it so the beep isn't captured
            self.recorder.start()
        elif event.kind == "key" and self.off_hook and event.key:
            self.keys.append(event.key)
        elif event.kind == "hook_down" and self.off_hook:
            self.off_hook = False
            call = Call(new_call_id(self.call_started), "".join(self.keys), self.recorder.stop(), self.call_started)
            if self.inline:
                self.process(call)
            else:
                self._calls.put(call)

    def _worker(self) -> None:
        while True:
            call = self._calls.get()
            try:
                self.process(call)
            except Exception:
                log.exception("call %s failed", call.id)
                self.ring("error")

    # --- one call: audio -> text -> Claude ------------------------------------

    def process(self, call: Call) -> None:
        seconds = len(call.audio) / STT_RATE
        if seconds < self.cfg.min_seconds:
            log.info("call %s: %.1fs, too short, ignoring", call.id, seconds)
            return
        call_dir = self.cfg.data_dir / "calls" / call.id
        call_dir.mkdir(parents=True, exist_ok=True)
        import soundfile

        soundfile.write(call_dir / "audio.wav", call.audio, STT_RATE)

        level = rms_dbfs(call.audio)
        if level < self.cfg.silence_dbfs:
            log.info("call %s: silent (%.1f dBFS), ignoring", call.id, level)
            return

        log.info("call %s: %.1fs at %.1f dBFS, keys %r, transcribing…", call.id, seconds, level, call.keys)
        heard = " ".join(self.transcriber.transcribe(call.audio).split())
        (call_dir / "transcript.txt").write_text(heard + "\n")
        if not heard:
            log.warning("call %s: empty transcript", call.id)
            self.ring("error")
            return
        command = to_command(heard, self.commands)
        text = command or heard
        log.info("call %s: %r%s", call.id, heard, f" -> {command}" if command else "")

        try:
            session, kind = self.dispatch(call, text)
        except (RunnerError, LookupError) as error:
            log.error("call %s: could not reach Claude: %s", call.id, error)
            (call_dir / "error.txt").write_text(f"{error}\n")
            self.ring("error")
            return
        meta = {"keys": call.keys, "heard": heard, "sent": text, "kind": kind, "session": asdict(session)}
        (call_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        log.info("call %s: %s, terminal %s", call.id, kind, session.terminal)

    def dispatch(self, call: Call, text: str) -> tuple[Session, str]:
        name = "".join(k for k in call.keys if k.isdigit()) or "default"
        target = self.cfg.targets.get(name)
        if target is None:
            raise LookupError(f"no target configured for {name!r}")
        current = self.sessions.get(name)

        if "*" in call.keys or current is None:
            return self._start(name, target, text, call.id), "new"
        if not current.ended:
            try:
                self.runner.follow_up(current, text)
                return current, "continued"
            except RunnerError as error:
                log.info("terminal %s is gone (%s)", current.terminal, error)
        if current.claude_session:
            try:
                return self._start(name, target, text, call.id, resume=current.claude_session), "resumed"
            except RunnerError as error:
                log.warning("could not resume %s (%s); starting a new conversation", current.claude_session, error)
        return self._start(name, target, text, call.id), "new"

    def _start(self, name: str, target: Target, text: str, call_id: str, resume: str | None = None) -> Session:
        session = self.runner.start(text, target, call_id, resume=resume)
        with self._lock:
            self.sessions[name] = session
            self._tracked.update(i for i in (session.call_id, session.terminal) if i)
        self._save_state()
        return session

    # --- Claude Code hooks -> ring ------------------------------------------------

    def on_hook(self, payload: dict) -> str | None:
        """Handle a Claude Code hook event. Returns the ring pattern used, if any."""
        self._learn(payload)
        event = payload.get("event")
        if event == "Stop":
            pattern = "done"
        elif event == "Notification":
            if payload.get("notification_type") not in (None, "permission_prompt"):
                return None
            if "waiting for your input" in (payload.get("message") or "").lower():
                return None  # idle reminder, not a question
            pattern = "attention"
        else:
            return None
        if self.cfg.ring_scope == "phone" and not self.is_phone_session(payload):
            return None
        if self.off_hook:
            return None  # you're holding the handset; don't ring in your hand
        self.ring(pattern)
        return pattern

    def _learn(self, payload: dict) -> None:
        """Remember which Claude session runs in a phone terminal, and when Claude exits there."""
        terminal, call_id = payload.get("superset_terminal_id"), payload.get("call_id")
        changed = False
        with self._lock:
            for session in self.sessions.values():
                if not ((terminal and session.terminal == terminal) or (call_id and session.call_id == call_id)):
                    continue
                if payload.get("session_id") and payload["session_id"] != session.claude_session:
                    session.claude_session = payload["session_id"]
                    changed = True
                # /clear also ends a Claude session, but the terminal keeps running Claude
                if payload.get("event") == "SessionEnd" and payload.get("reason") != "clear":
                    session.ended = True
                    changed = True
        if changed:
            self._save_state()

    def is_phone_session(self, payload: dict) -> bool:
        ids = {payload.get("call_id"), payload.get("superset_terminal_id")} - {None, ""}
        with self._lock:
            return bool(ids & self._tracked)

    def ring(self, pattern: str) -> None:
        cadence = self.cfg.ring_patterns.get(pattern)
        if not cadence:
            log.warning("unknown ring pattern %r", pattern)
            return
        try:
            self.phone.ring(cadence)
        except Exception:
            log.exception("ring failed")

    # --- state that survives restarts ---------------------------------------------

    def _load_state(self) -> None:
        path = self.cfg.data_dir / "state.json"
        try:
            data = json.loads(path.read_text())
            self.sessions = {name: Session(**s) for name, s in data.get("sessions", {}).items()}
            self._tracked = set(data.get("tracked", []))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            log.warning("ignoring unreadable %s: %s", path, error)

    def _save_state(self) -> None:
        with self._lock:
            data = {
                "sessions": {name: asdict(s) for name, s in self.sessions.items()},
                "tracked": sorted(self._tracked),
            }
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.data_dir / "state.json").write_text(json.dumps(data, indent=2) + "\n")
