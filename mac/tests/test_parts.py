import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from claude_phone import config
from claude_phone.audio import STT_RATE, ring_sound, rms_dbfs, to_16k, tone
from claude_phone.phone import Event, KeyboardPhone, format_ring, parse_line
from claude_phone.runners import applescript_string, find_terminal_id

HOOK = Path(__file__).parents[1] / "hooks" / "claude_phone_hook.py"


def test_parse_line():
    assert parse_line("HOOK_UP\r\n") == Event("hook_up")
    assert parse_line("hook_down") == Event("hook_down")
    assert parse_line("KEY #") == Event("key", "#")
    assert parse_line("KEY 7") == Event("key", "7")
    assert parse_line("KEY x") is None
    assert parse_line("booting v1") is None
    assert parse_line("") is None


def drain(phone):
    events = []
    while not phone.events.empty():
        events.append(phone.events.get())
    return events


def test_keyboard_phone_enter_toggles_hook():
    phone = KeyboardPhone()
    phone.handle_line("\n")
    phone.handle_line("\n")
    assert drain(phone) == [Event("hook_up"), Event("hook_down")]


def test_keyboard_phone_keys_lift_the_handset_first():
    phone = KeyboardPhone()
    phone.handle_line("*\n")
    phone.handle_line("2\n")  # already lifted: just the key
    assert drain(phone) == [Event("hook_up"), Event("key", "*"), Event("key", "2")]


def test_format_ring():
    assert format_ring([300, 200, 300]) == "RING 300,200,300"


def test_to_16k_resamples_length():
    audio = np.zeros(48_000, np.float32)
    assert len(to_16k(audio, 48_000)) == STT_RATE
    assert len(to_16k(np.zeros(44_100, np.float32), 44_100)) == STT_RATE


def test_rms_dbfs():
    assert rms_dbfs(np.zeros(10, np.float32)) == float("-inf")
    assert abs(rms_dbfs(np.ones(10, np.float32)) - 0.0) < 1e-9


def test_tone_and_ring_lengths():
    assert len(tone([1000], 0.25)) == 12_000
    assert len(ring_sound([300, 200, 300])) == int(0.8 * 48_000)


def test_find_terminal_id():
    assert find_terminal_id('{"sessionId": "abc"}') == "abc"
    assert find_terminal_id('{"data": {"terminal": {"terminalId": "t-1"}}}') == "t-1"
    assert find_terminal_id("not json") is None


def test_applescript_string_escapes():
    assert applescript_string('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


def test_config_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'phone = "serial"\n'
        "[stt]\nlanguage = \"en\"\n"
        '[targets.default]\nworkspace = "ws"\n'
        '[targets.1]\ncwd = "~/x"\n'
        "[ring_patterns]\ndone = [500]\n"
    )
    cfg = config.load(path)
    assert cfg.phone == "serial"
    assert cfg.language == "en"
    assert cfg.targets["default"].workspace == "ws"
    assert cfg.targets["1"].cwd == "~/x"
    assert cfg.ring_patterns["done"] == [500]
    assert cfg.ring_patterns["attention"] == [300, 200, 300]


def test_config_example_parses():
    cfg = config.load(Path(__file__).parents[1] / "config.example.toml")
    assert "default" in cfg.targets


def test_hook_script_posts_payload():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {"CLAUDE_PHONE_PORT": str(server.server_port), "SUPERSET_TERMINAL_ID": "term-9"}
    stdin = json.dumps({"hook_event_name": "Stop", "session_id": "s1"})
    proc = subprocess.run([sys.executable, str(HOOK)], input=stdin, text=True, env=env, timeout=10)
    server.shutdown()
    assert proc.returncode == 0
    assert received[0]["event"] == "Stop"
    assert received[0]["superset_terminal_id"] == "term-9"


def test_hook_script_exits_cleanly_without_daemon():
    env = {"CLAUDE_PHONE_PORT": "1"}
    proc = subprocess.run([sys.executable, str(HOOK)], input="{}", text=True, env=env, timeout=10)
    assert proc.returncode == 0
