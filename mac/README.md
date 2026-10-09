# Claude Phone: Mac side

Records the handset while it's lifted, transcribes it locally with Whisper, opens a visible Claude session (Superset or iTerm), and rings the phone when Claude finishes.

| Module | Job |
|---|---|
| `claude_phone/stt.py` | **Speech-to-text.** Local Whisper (`mlx-whisper`, Apple Silicon). Usable on its own |
| `claude_phone/audio.py` | Recording from the USB sound card, tones, ring sound |
| `claude_phone/phone.py` | Phone drivers: the Nano over serial, or a keyboard simulator |
| `claude_phone/runners.py` | Starts / continues / resumes Claude sessions in Superset or iTerm |
| `claude_phone/commands.py` | Spoken slash commands: "slash compact" → `/compact`, matched against installed commands and skills |
| `claude_phone/controller.py` | **The logic:** call → recording → transcript → conversation; hook events → ring |
| `claude_phone/daemon.py` | Wires it together; HTTP endpoint for the hooks |
| `hooks/claude_phone_hook.py` | Claude Code hook that reports Stop / Notification / SessionEnd events to the daemon |

## Setup

```sh
cd mac
uv sync
mkdir -p ~/.claude-phone && cp config.example.toml ~/.claude-phone/config.toml
```

Then edit `~/.claude-phone/config.toml`:
- **Superset runner:** log in once with `superset auth login`, then put a workspace id from `superset ws list --local` into `[targets.default]`.
- **iTerm runner:** set `runner = "iterm"` and a `cwd` in `[targets.default]`.
- **No handset yet:** set `audio_input = "MacBook Pro Microphone"` and `audio_output = "MacBook Pro Speakers"`.

## Speech-to-text on its own

```sh
uv run claude-phone-stt recording.wav
uv run claude-phone-stt --record 5                                   # from the USB card
uv run claude-phone-stt --record 5 --device "MacBook Pro Microphone"
```

The first run downloads the Whisper model (about 1.6GB). If the download fails with a "CAS Client Error", retry with `HF_HUB_DISABLE_XET=1`.

## Run the daemon

```sh
uv run claude-phone --phone keyboard   # simulator: Enter = lift / hang up; typing 0-9 * # then Enter lifts (if needed) and presses them
uv run claude-phone --phone serial     # the real phone (Nano)
```

A call:
1. Lift the handset (beep = recording).
2. Optionally press keys: digits pick a target from the config, `*` starts a new conversation.
3. Speak, then hang up.

### Conversations

Each target has one current conversation, and every call continues it:

| Situation | What happens |
|---|---|
| Its terminal is still open | The prompt is typed into it (`superset terminals send`) |
| Its terminal was closed, or Claude exited there | The conversation is resumed in a new terminal (`--resume-session`) |
| No conversation yet, can't resume, or `*` pressed | A new conversation starts |

Current conversations are kept in `~/.claude-phone/state.json`, so they survive a daemon restart. Resuming needs the Claude session id, which the daemon learns from the hooks below.

### Slash commands by voice

Start with "slash": "slash compact" → `/compact`, "slash clear" → `/clear`, "slash code review high" → `/code-review high`. Spoken names are matched against built-in commands plus the skills and commands installed in `~/.claude`, your plugins and the targets' projects, so hyphens and plugin prefixes come back ("slash sprint summary" → `/treyd-toolkit:sprint-summary`). Words after the name are passed as arguments. "Slash clear" also starts a fresh conversation in the same terminal.

Each call is saved in `~/.claude-phone/calls/<id>/`: audio, transcript, and `meta.json` with what was heard, what was sent and to which terminal.

## Ring when Claude finishes: hooks

This repo's `.claude/settings.json` already registers the hooks for Claude sessions running in this project, which covers phone calls to a target in this repo. For targets in other projects, add the same hooks to that project's `.claude/settings.json`, or to `~/.claude/settings.json` to cover every session. Use the absolute path to this repo there:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "python3 /path/to/repo/mac/hooks/claude_phone_hook.py" }] }
    ],
    "Notification": [
      { "matcher": "permission_prompt", "hooks": [{ "type": "command", "command": "python3 /path/to/repo/mac/hooks/claude_phone_hook.py" }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "python3 /path/to/repo/mac/hooks/claude_phone_hook.py" }] }
    ]
  }
}
```

SessionEnd tells the daemon when Claude exits in a phone terminal, so the next call resumes the conversation instead of typing into a bare shell.

| Event | Ring |
|---|---|
| Claude finished (Stop) | `done`: one short ring (0.35s) |
| Claude needs permission (Notification) | `attention`: two short rings |
| A call failed (empty transcript, unknown target, runner error) | `error`: three quick rings |

With `ring_scope = "phone"`, only sessions the phone started ring. They're recognised by `SUPERSET_TERMINAL_ID` (set by Superset) or `CLAUDE_PHONE_CALL_ID` (set by the iTerm runner). The phone doesn't ring while the handset is lifted.

Test the ringer by hand: `curl -X POST localhost:47333/ring -d '{"pattern":"done"}'`

## Tests

```sh
uv run pytest
```
