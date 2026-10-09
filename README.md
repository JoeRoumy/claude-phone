# ☎️ Claude Phone

**An old push-button phone that talks to Claude.**

Pick up the handset. Say what you want. Hang up.
Your words become a prompt in a live Claude Code session you can watch on your Mac, and when Claude is done, **the phone rings**.

```
   📞 lift ──► 🗣️ "Hi Claude, add a hello world script to the project" ──► 📞 hang up
                                                                              │
     ┌────────────────────────────────────────────────────────────────────────┘
     ▼
   🎧 Whisper (local) ──► 💬 Claude Code session in Superset ──► ✅ done ──► 🔔 ring!
```

No app, no keyboard, no screen. Just a phone on your desk that gets things done.

---

## What a call looks like

```text
$ uv run claude-phone --phone keyboard
19:25:00 INFO 61 slash commands and skills available by voice
19:25:00 INFO listening for Claude Code hooks on http://127.0.0.1:47333

📞 handset lifted, speak now
📞 hung up
19:25:19 INFO call 20261008-192511: 7.0s at -34.8 dBFS, keys '', transcribing…
19:25:20 INFO call 20261008-192511: 'Add a hello world script to the project'
19:25:21 INFO call 20261008-192511: new, terminal 5afb9ddc-42d5-4ae4-a3b3-0e3762799dd5
         …Claude works in Superset while you watch…
🔔 ring [350]
```

Pick up again and keep talking: the next call **continues the same conversation**.

## Features

- 🗣️ **Talk, don't type.** Local speech-to-text with Whisper (`whisper-large-v3-turbo` on Apple Silicon). Nothing leaves your Mac to be transcribed, and it handles telephone-quality audio fine.
- 👀 **Watch Claude work.** Every call lands in a real, visible Claude Code session in Superset, not a hidden background process. iTerm works as a fallback.
- 🧵 **Conversations that stick.** Calls continue the current conversation. If you closed the tab, it's resumed in a new one. Conversations survive a daemon restart.
- ⌨️ **Slash commands by voice.** Say "slash compact" and Claude gets `/compact`. Spoken names are matched against your installed commands and skills.
- 🔔 **It rings when Claude is done**, and rings differently when Claude needs your permission.
- 🔢 **The keypad picks the project.** Dial `2` before speaking to talk to project #2, or `*` for a fresh conversation.

### Voice examples

| You say | Claude gets |
|---|---|
| "Add a hello world script to the project" | `Add a hello world script to the project` |
| "Hi Claude, slash compact, keep the hardware decisions" | `/compact keep the hardware decisions` |
| "Slash clear" | `/clear` (a fresh conversation in the same terminal) |
| "Slash code review high" | `/code-review high` |
| "Slash sprint summary" | `/treyd-toolkit:sprint-summary` (plugin prefix found automatically) |
| "Fix the slash in the URL" | `Fix the slash in the URL` (not a command; it isn't at the start) |

### Keypad

| Press before speaking | What happens |
|---|---|
| nothing | Continue the conversation in your default project |
| `1`–`9` | Continue the conversation in the project mapped to that key |
| `*` | Start a brand-new conversation |
| `#` | Reserved (redial, someday) |

### Ring patterns

| Pattern | Meaning |
|---|---|
| 🔔 one short ring (0.35s) | Claude finished |
| 🔔🔔 two short rings | Claude is waiting for your permission |
| 🔔🔔🔔 three quick rings | Something went wrong (empty transcript, unknown project, …) |

---

## How it works

```
┌─────────────────────── INSIDE THE PHONE ───────────────────────┐
│  Handset mic ─► preamp ─► A0 (ADC) ┐                           │
│  Earpiece ◄──── amp ◄──── D4 (DAC)  │                           │
│  Hook switch ───────────► D2        ├─ LGT8F328P Nano ══ 1 USB ═╪══► Mac
│  Keypad 4×3 ────────────► 7 pins    │  (serial: events, commands │
│  Ringer ◄──── driver ◄─── D9        ┘   and 16kHz mic audio)    │
└────────────────────────────────────────────────────────────────┘

┌───────────────────────────── MAC ──────────────────────────────┐
│  claude-phone daemon                                            │
│   ├─ serial link ◄─► Nano                                       │
│   ├─ Whisper speech-to-text (mlx-whisper, local)                │
│   ├─ Superset CLI: continue / resume / start Claude sessions    │
│   └─ HTTP :47333 ◄── Claude Code hooks (Stop, Notification, …)  │
└─────────────────────────────────────────────────────────────────┘
```

1. **Lift the handset.** The Nano sends `HOOK_UP`, beeps in the earpiece, and starts streaming the mic as 8-bit µ-law audio at 16kHz.
2. **Hang up.** That means "send". The Mac transcribes the recording with Whisper.
3. **The transcript goes to Claude.** The Mac types it into the open session with `superset terminals send`, resumes the conversation in a new terminal, or starts a new one.
4. **Claude finishes.** A Claude Code `Stop` hook pings the daemon, which sends `RING 350` to the Nano, and the phone rings.

The daemon only rings for sessions the phone started: Superset gives each session a `SUPERSET_TERMINAL_ID`, and the hooks report it.

### The serial protocol

One USB cable carries everything. Text lines for events and commands, binary frames for audio:

```text
Nano → Mac   READY · HOOK_UP · HOOK_DOWN · KEY 5 · PONG · ERR <reason>
             0xFF <n> <n µ-law samples>          ← mic audio while lifted
Mac → Nano   RING 350 · RING 300,200,300 · STOP · BEEP · TONE 1000 250 · PING
```

Text never contains `0xFF`, and the firmware maps the one µ-law value that would (`0xFF`, "+0") to its twin (`0x7F`, "−0"). So a `0xFF` byte always starts an audio frame.

---

## Status

| Part | Status |
|---|---|
| Mac daemon: Whisper, Superset, conversations, slash commands, hooks | ✅ Working, tested with the keyboard simulator and the laptop mic |
| Nano firmware: hook switch, keypad, ringer, serial | ✅ Flashed and tested (rings on command) |
| Nano firmware: mic streaming and earpiece tones | 🧪 Written and flashed, untested until a mic is connected |
| Mac side of the audio stream | 🔜 Next |
| The actual phone | 🔍 Still to be found and investigated (see the checklist in [`PLAN.md`](PLAN.md)) |

---

## Quick start (no phone needed)

You can run the whole thing today with your laptop's mic and the keyboard as the "phone".

**Requirements:** an Apple Silicon Mac, [uv](https://docs.astral.sh/uv/), and the Superset CLI (or iTerm).

```sh
cd mac
uv sync
uv run hf download mlx-community/whisper-large-v3-turbo     # ~1.6GB, once

mkdir -p ~/.claude-phone && cp config.example.toml ~/.claude-phone/config.toml
```

Edit `~/.claude-phone/config.toml`:

```toml
audio_input  = "MacBook Pro Microphone"
audio_output = "MacBook Pro Speakers"

[targets.default]
workspace = "<id from: superset ws list --local>"
```

Then call Claude:

```sh
uv run claude-phone --phone keyboard
# Enter = lift / hang up · type * or 0-9 then Enter = press keys (lifts first)
```

The ring hooks are already set up for this repo in [`.claude/settings.json`](.claude/settings.json). Test the ringer with:

```sh
curl -X POST localhost:47333/ring -d '{"pattern":"done"}'
```

Transcription on its own:

```sh
uv run claude-phone-stt --record 5 --device "MacBook Pro Microphone"
```

More in [`mac/README.md`](mac/README.md).

## Flashing the Nano

```sh
arduino-cli compile --fqbn lgt8fx:avr:328 firmware/claude_phone_nano
arduino-cli upload -p /dev/cu.usbserial-310 --fqbn lgt8fx:avr:328 firmware/claude_phone_nano
```

One-time toolchain setup, pinout and bench-test tips are in [`firmware/README.md`](firmware/README.md). A jumper from D2 to GND "lifts the handset".

---

## Repo layout

```
.
├── PLAN.md                    the full design: decisions, hardware, checklist, build phases
├── .claude/settings.json      Claude Code hooks that make the phone ring
├── mac/                       the Mac daemon (Python, uv)
│   ├── claude_phone/
│   │   ├── stt.py             Whisper speech-to-text
│   │   ├── controller.py      calls → transcripts → conversations; hooks → rings
│   │   ├── commands.py        "slash compact" → /compact
│   │   ├── runners.py         Superset / iTerm sessions
│   │   ├── phone.py           Nano serial driver + keyboard simulator
│   │   └── daemon.py          wires it all together
│   ├── hooks/                 the hook script
│   └── tests/                 61 tests
└── firmware/
    └── claude_phone_nano/     LGT8F328P firmware (Arduino)
```

---

## From the build log

> **The case of the silent sound card.** The first USB sound card recorded pure digital zeros, with a click whenever the plug moved. A recording analysed burst by burst showed every click jumping to full scale and fading smoothly, but no speech ever came through. The culprit: the coupling capacitor right after the mic jack, labelled C3, was a **blob of solder** instead of a capacitor. The mic bias voltage went straight into the amplifier and pinned it at its limit. Wicked off the solder, fitted a 2.2µF capacitor, and the card came to life. 🔧

> **Why one Nano does everything.** We considered a hidden USB hub, a Bluetooth headset module (its firmware turned out to be playback-only), tones over the audio lines, and even talking over HDMI's monitor bus. In the end the Nano's 12-bit ADC and built-in DAC are plenty for telephone-quality audio, so one microcontroller on one USB cable runs the whole phone.

---

## Safety

- **Never** connect the phone or the electronics to a real wall phone line. A live line carries ~48V, and ~90V when ringing.
- Don't drive a speaker straight from a microcontroller pin; use a transistor.
