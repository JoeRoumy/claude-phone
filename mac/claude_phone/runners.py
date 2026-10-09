"""Start or continue a visible Claude session for a phone call: in Superset, or in an iTerm window."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Target


class RunnerError(RuntimeError):
    pass


@dataclass
class Session:
    call_id: str
    workspace: str | None = None
    terminal: str | None = None  # Superset terminal id, or iTerm session id
    claude_session: str | None = None  # Claude Code session id, learned from hook events; used to resume
    ended: bool = False  # Claude exited in that terminal (SessionEnd hook)


def find_terminal_id(output: str) -> str | None:
    """Pull the session/terminal id out of `superset agents create --json` output."""
    try:
        data = json.loads(output)
    except ValueError:
        return None
    for key in ("sessionId", "terminalId", "session_id", "terminal_id"):
        found = _find_key(data, key)
        if found:
            return found
    return None


def _find_key(obj, key: str) -> str | None:
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_key(child, key)
        if found:
            return found
    return None


class SupersetRunner:
    def __init__(self, binary: str = "superset"):
        fallback = Path.home() / ".superset" / "bin" / "superset"
        self.binary = shutil.which(binary) or str(fallback)

    def _run(self, *args: str) -> str:
        proc = subprocess.run([self.binary, *args, "--json"], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RunnerError((proc.stderr or proc.stdout).strip())
        return proc.stdout

    def start(self, prompt: str, target: Target, call_id: str, resume: str | None = None) -> Session:
        """New Claude session in a new terminal; `resume` continues that Claude session id's conversation."""
        if not target.workspace:
            raise RunnerError("target has no Superset workspace id")
        args = ["agents", "create", "--local", "--workspace", target.workspace, "--agent", "claude", "--prompt", prompt]
        if resume:
            args += ["--resume-session", resume]
        output = self._run(*args)
        return Session(call_id, target.workspace, find_terminal_id(output), claude_session=resume)

    def follow_up(self, session: Session, prompt: str) -> None:
        if not session.terminal:
            raise RunnerError("previous session has no terminal id to send to")
        self._run(
            "terminals", "send", "--local",
            "--workspace", session.workspace,
            "--terminal", session.terminal,
            "--text", prompt,
        )  # fmt: skip


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osascript(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RunnerError(proc.stderr.strip())
    return proc.stdout.strip()


class ITermRunner:
    """Fallback without Superset: interactive `claude` in a new iTerm window."""

    def start(self, prompt: str, target: Target, call_id: str, resume: str | None = None) -> Session:
        cwd = Path(target.cwd or "~").expanduser()
        resume_flag = f"--resume {shlex.quote(resume)} " if resume else ""
        command = (
            f"cd {shlex.quote(str(cwd))} && "
            f"CLAUDE_PHONE_CALL_ID={call_id} claude {resume_flag}{shlex.quote(prompt)}"
        )
        session_id = _osascript(
            'tell application "iTerm"\n'
            "  activate\n"
            "  set w to (create window with default profile)\n"
            "  tell current session of w\n"
            f"    write text {applescript_string(command)}\n"
            "    return id\n"
            "  end tell\n"
            "end tell"
        )
        return Session(call_id, None, session_id, claude_session=resume)

    def follow_up(self, session: Session, prompt: str) -> None:
        result = _osascript(
            'tell application "iTerm"\n'
            "  repeat with w in windows\n"
            "    repeat with t in tabs of w\n"
            "      repeat with s in sessions of t\n"
            f"        if id of s is {applescript_string(session.terminal or '')} then\n"
            f"          tell s to write text {applescript_string(prompt)}\n"
            '          return "ok"\n'
            "        end if\n"
            "      end repeat\n"
            "    end repeat\n"
            "  end repeat\n"
            "end tell\n"
            'return "missing"'
        )
        if result != "ok":
            raise RunnerError("the previous iTerm session is gone")
