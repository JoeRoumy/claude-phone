"""Entry point: wires the phone, audio, Whisper and Claude runner together, and serves the hook endpoint.

    uv run claude-phone                  # uses ~/.claude-phone/config.toml
    uv run claude-phone --phone keyboard # simulate the phone with the keyboard

HTTP (127.0.0.1 only):
    POST /hook  Claude Code hook events (sent by hooks/claude_phone_hook.py)
    POST /ring  {"pattern": "done"} rings the phone directly
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .audio import Earpiece, Recorder
from .commands import CommandIndex
from .controller import Controller
from .phone import KeyboardPhone, SerialPhone
from .runners import ITermRunner, SupersetRunner
from .stt import Transcriber

log = logging.getLogger("claude_phone")


def make_server(controller: Controller, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                self._reply(400, {"error": "invalid json"})
                return
            if self.path == "/hook":
                self._reply(200, {"rang": controller.on_hook(payload)})
            elif self.path == "/ring":
                pattern = payload.get("pattern", "done")
                controller.ring(pattern)
                self._reply(200, {"rang": pattern})
            else:
                self._reply(404, {"error": "not found"})

        def _reply(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:
            log.debug("http: " + format, *args)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Claude Phone daemon")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH)
    parser.add_argument("--phone", choices=["keyboard", "serial"], help="override the configured phone driver")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = config.load(args.config)
    if args.phone:
        cfg.phone = args.phone
    if not cfg.targets:
        log.warning("no targets configured in %s; calls will fail until you add [targets.default]", args.config)

    phone = SerialPhone(cfg.serial_port) if cfg.phone == "serial" else KeyboardPhone()
    runner = SupersetRunner(cfg.superset_bin) if cfg.runner == "superset" else ITermRunner()
    transcriber = Transcriber(cfg.stt_model, cfg.language, cfg.initial_prompt)
    log.info("loading Whisper model %s (the first run downloads it)…", cfg.stt_model)
    transcriber.warm_up()

    commands = CommandIndex.discover(Path(t.cwd) for t in cfg.targets.values() if t.cwd)
    log.info("%d slash commands and skills available by voice", len(commands))

    controller = Controller(
        cfg, phone, Recorder(cfg.audio_input), transcriber, runner, Earpiece(cfg.audio_output), commands
    )
    for name, session in controller.sessions.items():
        log.info("target %r continues terminal %s", name, session.terminal)
    server = make_server(controller, cfg.http_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("listening for Claude Code hooks on http://127.0.0.1:%d", cfg.http_port)

    phone.start()
    try:
        while True:
            controller.handle(phone.events.get())
    except KeyboardInterrupt:
        log.info("bye")


if __name__ == "__main__":
    main()
