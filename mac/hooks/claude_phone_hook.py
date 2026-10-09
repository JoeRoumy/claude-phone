#!/usr/bin/env python3
"""Claude Code hook (Stop, Notification, SessionEnd) that tells the Claude Phone daemon a session finished,
needs you, or exited.

Standard library only and Python 3.9 compatible, so it runs with the system python3.
Never blocks or fails Claude: errors are ignored and it always exits 0.
"""

import json
import os
import sys
import urllib.request


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    body = {
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "notification_type": payload.get("notification_type"),
        "message": payload.get("message"),
        "reason": payload.get("reason"),  # SessionEnd: "clear", "logout", "prompt_input_exit", "other"
        # Set by Superset for sessions it runs, and by the iTerm runner for phone calls.
        "superset_terminal_id": os.environ.get("SUPERSET_TERMINAL_ID"),
        "call_id": os.environ.get("CLAUDE_PHONE_CALL_ID"),
    }
    port = os.environ.get("CLAUDE_PHONE_PORT", "47333")
    request = urllib.request.Request(
        "http://127.0.0.1:%s/hook" % port,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=1).read()
    except Exception:
        pass  # daemon not running: nothing to ring


if __name__ == "__main__":
    main()
    sys.exit(0)
