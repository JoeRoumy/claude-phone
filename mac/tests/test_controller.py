import json

import numpy as np
import pytest

from claude_phone.audio import STT_RATE
from claude_phone.commands import CommandIndex
from claude_phone.config import Config, Target
from claude_phone.controller import Controller
from claude_phone.phone import Event
from claude_phone.runners import RunnerError, Session


class FakePhone:
    def __init__(self):
        self.rings = []

    def ring(self, cadence):
        self.rings.append(cadence)


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        return self.audio


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


class FakeRunner:
    def __init__(self, fail_start=False, gone_terminals=(), fail_resume=False):
        self.started = []  # (prompt, target, resume)
        self.follow_ups = []
        self.fail_start = fail_start
        self.gone = set(gone_terminals)
        self.fail_resume = fail_resume

    def start(self, prompt, target, call_id, resume=None):
        if self.fail_start or (resume and self.fail_resume):
            raise RunnerError("boom")
        self.started.append((prompt, target, resume))
        return Session(call_id, target.workspace, f"term-{len(self.started)}", claude_session=resume)

    def follow_up(self, session, prompt):
        if session.terminal in self.gone:
            raise RunnerError(f'Terminal session "{session.terminal}" has ended')
        self.follow_ups.append((session.terminal, prompt))


class FakeEarpiece:
    def __init__(self):
        self.beeps = 0

    def beep(self):
        self.beeps += 1


def speech(seconds=2.0, level=0.1):
    t = np.arange(int(seconds * STT_RATE)) / STT_RATE
    return (level * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


@pytest.fixture
def make(tmp_path):
    def _make(audio=None, text="fix the tests", runner=None, commands=None, **cfg_overrides):
        cfg = Config(
            data_dir=tmp_path,
            targets={"default": Target(workspace="ws-default"), "2": Target(workspace="ws-two")},
            **cfg_overrides,
        )
        return Controller(
            cfg,
            FakePhone(),
            FakeRecorder(speech() if audio is None else audio),
            FakeTranscriber(text),
            runner or FakeRunner(),
            FakeEarpiece(),
            commands=commands,
            inline=True,
        )

    return _make


def call(controller, *keys):
    controller.handle(Event("hook_up"))
    for key in keys:
        controller.handle(Event("key", key))
    controller.handle(Event("hook_down"))


def last_meta(tmp_path):
    latest = sorted((tmp_path / "calls").iterdir())[-1]
    return json.loads((latest / "meta.json").read_text())


# --- calls and conversations ---------------------------------------------------


def test_first_call_starts_session_in_default_target(make, tmp_path):
    controller = make()
    call(controller)
    assert controller.earpiece.beeps == 1
    assert controller.recorder.started
    assert controller.runner.started == [("fix the tests", Target(workspace="ws-default"), None)]
    (call_dir,) = (tmp_path / "calls").iterdir()
    assert (call_dir / "transcript.txt").read_text() == "fix the tests\n"
    assert (call_dir / "audio.wav").exists()
    assert last_meta(tmp_path)["kind"] == "new"


def test_next_call_continues_the_conversation(make, tmp_path):
    controller = make()
    call(controller)
    call(controller)
    assert len(controller.runner.started) == 1
    assert controller.runner.follow_ups == [("term-1", "fix the tests")]
    assert last_meta(tmp_path)["kind"] == "continued"


def test_star_starts_a_new_conversation(make):
    controller = make()
    call(controller)
    call(controller, "*")
    assert len(controller.runner.started) == 2
    assert controller.runner.follow_ups == []
    assert controller.sessions["default"].terminal == "term-2"


def test_each_target_has_its_own_conversation(make):
    controller = make()
    call(controller)
    call(controller, "2")
    call(controller, "2")
    assert [t.workspace for _, t, _ in controller.runner.started] == ["ws-default", "ws-two"]
    assert controller.runner.follow_ups == [("term-2", "fix the tests")]


def test_closed_terminal_resumes_the_conversation(make, tmp_path):
    controller = make(runner=FakeRunner(gone_terminals={"term-1"}))
    call(controller)
    controller.on_hook({"event": "Stop", "superset_terminal_id": "term-1", "session_id": "claude-abc"})
    call(controller)
    assert controller.runner.started[-1][2] == "claude-abc"
    assert last_meta(tmp_path)["kind"] == "resumed"


def test_closed_terminal_without_known_conversation_starts_new(make, tmp_path):
    controller = make(runner=FakeRunner(gone_terminals={"term-1"}))
    call(controller)
    call(controller)
    assert controller.runner.started[-1][2] is None
    assert last_meta(tmp_path)["kind"] == "new"


def test_failed_resume_starts_new(make, tmp_path):
    controller = make(runner=FakeRunner(gone_terminals={"term-1"}, fail_resume=True))
    call(controller)
    controller.on_hook({"event": "Stop", "superset_terminal_id": "term-1", "session_id": "claude-abc"})
    call(controller)
    assert last_meta(tmp_path)["kind"] == "new"


def test_session_end_skips_follow_up_and_resumes(make, tmp_path):
    controller = make()
    call(controller)
    controller.on_hook({"event": "SessionEnd", "superset_terminal_id": "term-1", "session_id": "claude-abc"})
    call(controller)
    assert controller.runner.follow_ups == []  # never type into a terminal where Claude has exited
    assert controller.runner.started[-1][2] == "claude-abc"


def test_clear_does_not_end_the_session(make, tmp_path):
    controller = make()
    call(controller)
    controller.on_hook({"event": "SessionEnd", "reason": "clear", "superset_terminal_id": "term-1"})
    call(controller)
    assert controller.runner.follow_ups == [("term-1", "fix the tests")]


def test_sessions_survive_a_restart(make, tmp_path):
    controller = make()
    call(controller)
    controller.on_hook({"event": "Stop", "superset_terminal_id": "term-1", "session_id": "claude-abc"})
    restarted = make()
    assert restarted.sessions["default"].terminal == "term-1"
    assert restarted.sessions["default"].claude_session == "claude-abc"
    assert restarted.on_hook({"event": "Stop", "superset_terminal_id": "term-1"}) == "done"


# --- spoken slash commands ---------------------------------------------------------


def test_spoken_slash_command_is_sent_as_command(make, tmp_path):
    controller = make(text="Slash code review high.", commands=CommandIndex(["code-review"]))
    call(controller)
    assert controller.runner.started[0][0] == "/code-review high"
    meta = last_meta(tmp_path)
    assert meta["heard"] == "Slash code review high."
    assert meta["sent"] == "/code-review high"


# --- failures -------------------------------------------------------------------------


def test_unknown_target_rings_error(make):
    controller = make()
    call(controller, "9")
    assert controller.runner.started == []
    assert controller.phone.rings == [controller.cfg.ring_patterns["error"]]


def test_short_call_is_ignored(make):
    controller = make(audio=speech(seconds=0.3))
    call(controller)
    assert controller.transcriber.calls == 0
    assert controller.phone.rings == []


def test_silent_call_is_ignored(make):
    controller = make(audio=speech(level=0.0001))
    call(controller)
    assert controller.transcriber.calls == 0
    assert controller.phone.rings == []


def test_empty_transcript_rings_error(make):
    controller = make(text="  ")
    call(controller)
    assert controller.runner.started == []
    assert controller.phone.rings == [controller.cfg.ring_patterns["error"]]


def test_runner_failure_rings_error(make):
    controller = make(runner=FakeRunner(fail_start=True))
    call(controller)
    assert controller.phone.rings == [controller.cfg.ring_patterns["error"]]


def test_keys_before_lift_are_ignored(make):
    controller = make()
    controller.handle(Event("key", "2"))
    call(controller)
    assert controller.runner.started[0][1].workspace == "ws-default"


# --- ringing ---------------------------------------------------------------------------


def test_stop_hook_rings_for_phone_session(make):
    controller = make()
    call(controller)
    assert controller.on_hook({"event": "Stop", "superset_terminal_id": "term-1"}) == "done"
    assert controller.phone.rings == [[350]]


def test_stop_hook_matches_iterm_call_id(make):
    controller = make()
    call(controller)
    call_id = controller.sessions["default"].call_id
    assert controller.on_hook({"event": "Stop", "call_id": call_id}) == "done"


def test_stop_hook_ignores_other_sessions(make):
    controller = make()
    call(controller)
    assert controller.on_hook({"event": "Stop", "superset_terminal_id": "someone-else"}) is None
    assert controller.phone.rings == []


def test_ring_scope_all_rings_for_any_session(make):
    controller = make(ring_scope="all")
    assert controller.on_hook({"event": "Stop"}) == "done"


def test_permission_notification_rings_attention(make):
    controller = make(ring_scope="all")
    payload = {"event": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission"}
    assert controller.on_hook(payload) == "attention"


def test_idle_notification_does_not_ring(make):
    controller = make(ring_scope="all")
    assert controller.on_hook({"event": "Notification", "notification_type": "idle_prompt"}) is None
    assert controller.on_hook({"event": "Notification", "message": "Claude is waiting for your input"}) is None


def test_session_end_does_not_ring(make):
    controller = make(ring_scope="all")
    assert controller.on_hook({"event": "SessionEnd"}) is None


def test_no_ring_while_handset_is_lifted(make):
    controller = make(ring_scope="all")
    controller.handle(Event("hook_up"))
    assert controller.on_hook({"event": "Stop"}) is None
