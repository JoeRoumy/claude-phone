# Claude Phone — Plan

An old push-button phone that talks to Claude. Pick up the handset, speak a prompt, hang up. The Mac transcribes it and starts (or continues) a visible Claude session in Superset. When Claude finishes, the phone rings briefly.

---

## How it works

```
pick up → (optional: press keys) → speak → hang up
  → Nano streams the handset mic to the Mac over USB serial
  → Whisper turns it into text (locally)
  → Superset: continue the conversation (or start one)     ← you watch Claude work in Superset
  → Claude finishes → Stop hook → Mac daemon → Nano → phone rings briefly (0.35s)
```

## System overview

```
┌─────────────────────────── INSIDE THE PHONE ───────────────────────────┐
│                                                                        │
│  Handset mic ─► mic front end (bias + preamp) ─► A0 (ADC) ┐            │
│  Earpiece ◄──── small amp ◄─────────────────── D4 (DAC)   │            │
│  Hook switch ─────────────────────────────────► D2        ├─ LGT8F328P Nano ══ 1 USB cable ══► Mac
│  Keypad (4×3) ────────────────────────────────► 7 pins    │   (USB serial: events, commands
│  Electronic ringer ◄── transistor driver ◄──── D9         │    and mic audio on one link)
│  Status LED ◄───────────────────────────────── D13        ┘            │
│                                                                        │
│  Everything runs on 5V from that one USB cable.                        │
└────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────── MAC ───────────────────────────────────┐
│  Phone daemon (Python, runs under launchd)                             │
│   ├─ serial link ◄─► Nano  (events, ring/beep commands, audio frames)  │
│   ├─ speech-to-text: local Whisper                                     │
│   ├─ Superset: continue / resume / start visible Claude sessions       │
│   └─ HTTP endpoint ◄── Claude Code hooks (Stop, Notification, SessionEnd)│
└────────────────────────────────────────────────────────────────────────┘
```

---

## Decisions so far

| Decision | Why |
|---|---|
| **No phone line.** Handset, hook switch, keypad and ringer are wired directly to the electronics | No line voltage, no ring voltage, cleaner audio. The phone becomes a USB device |
| **One USB cable: the Nano does everything, including audio** | The Nano's USB chip (CH340) can only be a serial port, so the mic is sampled by the Nano and streamed as data. No sound card, no hub. The Mac never sees a microphone, so no permanent mic indicator; audio only streams while the handset is lifted |
| **LGT8F328P Nano as the controller** | Already on hand and flashed. Has a 12-bit ADC (mic), a built-in 8-bit DAC (earpiece) and runs at 32MHz. Pro Mini is the backup; ATtiny85 is too small |
| **Earpiece cues generated on the Nano** | The Mac only needs short cues (beep, error tone), so audio streams one way only: phone → Mac |
| **Push-button phone** (no rotary dial) | The keypad gives more controls (`*`, `#`, digits) |
| **Use the handset's original mic** if possible | Carbon works with a bias circuit; dynamic and electret need a preamp (or a MAX9814 module) |
| **Electronic ringer driven by the Nano** | No high voltage. The ring sound is made in software, so any pattern is possible |
| **Visible Claude sessions in Superset**, not headless `claude -p` | You see the prompt, the thinking and the output. Superset's CLI supports creating sessions, sending follow-ups and resuming |
| **Hanging up means "send"** | Natural, and no silence detection needed |

### Options considered and dropped
- **Analog telephone adapter (Grandstream ATA + SIP):** works without soldering, but the electronics are the fun part.
- **AG1171 phone-line module / home-built line circuit:** only needed if the phone stays on a real phone line.
- **Mechanical bell with a 40V driver (DRV8871 + boost converter):** the phone will likely have an electronic ringer. Revisit if it turns out to have a real bell.
- **USB sound card + Nano + hub board inside the phone:** works and is the most dependable, but needs a hub and keeps two devices. The repaired sound card stays as a **fallback and test tool**.
- **Audio-signalling hack** (Nano powered by the sound card, signals as tones over the audio lines): needs the Mac's mic open permanently, and tones can be missed.
- **HDMI monitor-bus (DDC) hack:** needs a phantom display, private macOS interfaces and an adapter that passes the bus through, and is still two cables.
- **Bluetooth headset module (BT-163, RDA5851S):** its firmware only offers music playback (A2DP/AVRCP), no microphone (HFP). Not useful.
- **Bluetooth serial module (HM-10) for the Nano:** workable, but adds pairing and reconnect handling; the sound card would still be needed for audio.

---

## Hardware components

| Component | Job | Status |
|---|---|---|
| Phone (push-button, modern-ish) | Body, handset, hook switch, keypad, ringer | **To find and investigate** (see checklist below) |
| LGT8F328P Nano (CH340 USB serial) | Everything: mic sampling, earpiece cues, hook, keypad, ringer, LED, USB link | ✅ On hand, control firmware flashed and tested |
| Mic front end | Bias and amplify the handset mic for the ADC | To build. MAX9814 module (~$3) if electret; bias circuit if carbon (below); the LGT8F328P's built-in amplifier might be enough, to test |
| Earpiece amplifier | Drive the 150–300Ω earpiece from the DAC | To build: one transistor, or a PAM8302 / LM386 module |
| Ringer driver | Small MOSFET or NPN transistor (2N7000 / BC547) plus a resistor | Probably on hand |
| Supply filtering | Keep USB 5V noise out of the mic signal | A few capacitors and a resistor (RC filter) |
| Perfboard, JST connectors, wire, heat-shrink | Mounting inside the base | Probably on hand |
| USB sound card ("USB PnP Sound Device") | Fallback audio path and test instrument | ✅ Repaired and tested |

### Nano pins (tentative, firmware to follow)

| Function | Pin | Notes |
|---|---|---|
| Mic in | A0 (ADC) | Biased to mid-scale; sampled by a timer interrupt |
| Earpiece out | D4 (DAC0) | LGT8F328P's built-in 8-bit DAC, into the earpiece amp |
| Hook switch | D2 | `INPUT_PULLUP`; closed to GND = lifted |
| Keypad rows | D3, D5, D6, D7 | Moved off D4 to free the DAC |
| Keypad columns | D8, D10, D12 | `INPUT_PULLUP` |
| Ringer | D9 | Warble via `tone()` |
| Status LED | D13 | Onboard |

Firmware v2 (flashed) uses this layout. Pins get a final adjustment once the phone's keypad is mapped.

### Audio streaming (draft)

| Choice | Value | Why |
|---|---|---|
| Sample rate | 16kHz (8kHz fallback) | Whisper works at 16kHz; telephone audio fits in 8kHz |
| Sample format | 8-bit µ-law, companded from the 12-bit ADC | Keeps quiet speech detailed at half the data of 16-bit |
| Data rate | 16 KB/s | |
| Serial speed | 250000 baud (~25 KB/s) | Divides exactly from the 32MHz clock; room for events. Raise if needed |
| Streaming | Only while the handset is lifted | |

- **Clock accuracy:** the internal oscillator is only accurate to about 1–2%, so the sample rate drifts slightly. Irrelevant for Whisper.
- **Noise:** filter the mic's supply and keep the analog ground separate from the ringer's current. USB 5V is noisy, and anything on it ends up in the audio.

### Carbon mic bias circuit (only if the mic is carbon)

```
USB 5V ─ 47Ω ─┬─ 470µF ─ GND        ← cleans USB noise (anything on this line ends up in the audio)
              └─ 150Ω ─┬─ carbon mic ─ GND        (about 20–25mA through the mic)
                       └─ 10µF (+ toward the mic) ─ divider ─┬─ A0
                                                             └─ 2 × 10kΩ from VREF and GND (biases A0 to mid-scale)
```

Carbon mics put out hundreds of mV, so they may not need a preamp. Tune the divider so speech fills the ADC without clipping.

### Ringer options

| Option | How | Notes |
|---|---|---|
| **Nano drives the transducer directly** (recommended) | Bypass the ringer chip. Generate the warble in software: `tone()` swapping between about 500 and 600Hz roughly 16 times a second | Push-pull across two pins doubles the volume. Add a transistor and a higher voltage if it's still too quiet. Never drive a speaker straight from a pin |
| Keep the original ringer chip | Feed it about 12–24V DC from a small boost converter, switched by a transistor | The exact original sound; depends on the chip's datasheet |

### USB sound card: what we found and fixed (fallback / test tool)
- Mac sees it as **"USB PnP Sound Device"**, manufacturer "C-Media Electronics Inc.", vendor/product `0x8086:0x0808` (Intel's vendor ID, borrowed by a clone). 1-channel input, 2-channel output, 48kHz. No driver needed.
- **Fault:** the mic input recorded only exact digital zeros, plus clicks when the plug moved.
- **Cause:** the series coupling capacitor **C3** right after the mic jack was a **solder bridge** instead of a capacitor. The jack's DC bias went straight into the mic amplifier and pinned it at its limit.
- **Fix:** removed the solder bridge and fitted a capacitor of about 2–4.7µF across C3. The mic input now works.
- **Measured levels** (loopback, speech at exact attenuation steps): clean from about 20dB below full output down; noise floor about −57dBFS; target for speech roughly −15 to −40dBFS.
- **Quirk:** the card's output volume control is fake (setting −28dB changed nothing).

---

## Software components

| Component | Job | Status |
|---|---|---|
| **Nano firmware** | Hook switch, keypad, ringer, serial protocol | ✅ Control part done and tested (`firmware/`) |
| | Mic sampling and streaming, earpiece cues | Written and flashed (v2); untested until a mic is connected |
| **Serial link** (Mac) | Events in, ring commands out | ✅ Done (`mac/claude_phone/phone.py`) |
| | Audio frames in, beep commands out | To do: replaces the sound-card recorder and earpiece |
| **Speech-to-text** (Mac) | Local Whisper (`mlx-whisper`, `whisper-large-v3-turbo`) | ✅ Done; works on telephone-quality audio |
| **Superset runner** (Mac) | Continue / resume / start visible Claude sessions | ✅ Done |
| **Ring trigger** | Claude Code hooks (project `.claude/settings.json`) call the daemon | ✅ Done |
| **Service and logs** | launchd keeps the daemon running; each call is saved to `~/.claude-phone/calls/` | Logs done; launchd to do |

### Serial protocol

| Direction | Message | Meaning | Status |
|---|---|---|---|
| Nano → Mac | `READY` | Booted | ✅ |
| Nano → Mac | `HOOK_UP` / `HOOK_DOWN` | Handset lifted / put down | ✅ |
| Nano → Mac | `KEY <x>` | Key pressed: `0`–`9`, `*`, `#` | ✅ |
| Nano → Mac | `PONG` / `ERR <reason>` | Reply to `PING` / bad command | ✅ |
| Nano → Mac | Audio frame: byte `0xFF`, length byte `n`, then `n` µ-law samples | Mic audio while lifted. Text lines never contain `0xFF`, so frames and lines can't be confused | Draft |
| Mac → Nano | `RING <on_ms>[,<off_ms>,<on_ms>…]` | Ring with this rhythm, e.g. `RING 350` = one short ring | ✅ |
| Mac → Nano | `STOP` / `PING` | Stop ringing / health check | ✅ |
| Mac → Nano | `BEEP` / `TONE <hz> <ms>` | Earpiece cues | Draft |

Firmware v2 runs at 250000 baud and sends audio frames, and also handles `BEEP`, `TONE` and `MIC 0|1` (testing). The Mac side is still at 115200 baud with text only, and needs updating to match.

### Superset commands (Superset CLI v1.30.2)

| Command | Use |
|---|---|
| `superset agents create --local --workspace <id> --agent claude --prompt "<text>"` | New conversation |
| `superset agents create … --resume-session <claude session id>` | Resume a conversation whose terminal was closed |
| `superset terminals send --local --workspace <id> --terminal <id> --text "…"` | Continue the conversation in its open terminal |
| `superset terminals read` / `superset agents read` | Check what a session is doing |

Fallback without Superset: an iTerm window running interactive `claude "<prompt>"`.

### Keypad and voice controls

| Keys pressed before speaking | Effect |
|---|---|
| None | Continue the current conversation in the default project (start one if there is none) |
| `1`–`9` | Same, in the project mapped to that number (config file). Each project has its own conversation |
| `*` | Start a new conversation |
| `#` | Reserved (e.g. redial: repeat the last prompt) |

- If the conversation's terminal was closed, it's resumed in a new terminal.
- Saying "slash …" sends a slash command or skill: "slash compact" → `/compact`, "slash sprint summary" → `/treyd-toolkit:sprint-summary`. "Slash clear" starts a fresh conversation in the same terminal. Up to 4 words of lead-in ("Hi Claude, …") are dropped.

### Sounds and ring patterns

| Event | Where | Sound |
|---|---|---|
| Handset lifted | Earpiece | Short beep: recording has started |
| Recording too short or empty | Ringer | Error pattern (three quick rings) |
| Claude finished (Stop hook) | Ringer | One short ring (0.35s) |
| Claude waiting for you, e.g. a permission prompt (Notification hook) | Ringer | Two short rings |

**Ring scope:** the hooks fire for every Claude session in this project. Superset sets `SUPERSET_TERMINAL_ID` in each session's environment, and hooks inherit it, so the daemon only rings for terminals it started (`ring_scope = "phone"`). `ring_scope = "all"` makes every finished session ring.

---

## Phone investigation checklist

Do the stages in order, and the boxes within a stage top to bottom.

### Before you start
- Never investigate the phone while it's plugged into a real wall phone line. A live line carries about 48V, and about 90V when ringing.
- When choosing a phone, look for:
  - push buttons
  - a handset cord with plugs on both ends
  - enough room inside the base

### Stage A: Outside of the phone
- [ ] Photograph it from all sides, including the label on the bottom (model, maker, year)
- [ ] Check the handset cord: plugs at both ends, or wired in? How many wires?

### Stage B: Handset mic (mouthpiece)
- [ ] Unscrew the mouthpiece cap and photograph the capsule
- [ ] Measure resistance across its two terminals and match it to a type:

  | Reading | Mic type |
  |---|---|
  | About 50–200Ω, jumps around when you tap or blow on it | Carbon |
  | About 100–300Ω, steady | Dynamic |
  | Reads differently each way round, or open one way | Electret (note which side is +) |

- [ ] If it's carbon: tap it firmly a few times and check that the reading still moves

### Stage C: Handset earpiece
- [ ] Unscrew the earpiece cap and photograph it
- [ ] Measure resistance across its terminals (expect about 100–300Ω)

### Stage D: Handset cord wiring
- [ ] Use continuity mode to match each plug pin to the mic or earpiece terminals
- [ ] Write it down, for example: mic = pins 1 + 4, earpiece = pins 2 + 3 (add wire colours if you can see them)

### Stage E: Quick audio test with the USB sound card (no soldering)
- [ ] Earpiece → green jack (tip + sleeve), then play speech into it
- [ ] Mic → pink jack, only if it's dynamic or electret, then run the level meter while talking
- [ ] If the mic is carbon, skip this. It needs the bias circuit first

### Stage F: Inside the base
- [ ] Remove the screws underneath and lift the cover
- [ ] Photograph everything from several angles before you touch any wires
- [ ] Measure the free space inside (length × width × height, in mm)

### Stage G: Hook switch
- [ ] Find the switch under the cradle: a plunger with a stack of metal contacts
- [ ] Find one pair of contacts that closes when the handset is lifted, and note which one it is

### Stage H: Keypad
- [ ] Photograph where the keypad connects to the board, including the pin numbers
- [ ] Hold each key down and find which two pins connect. Fill in:

  | Key | Pin | Pin |
  |---|---|---|
  | 1 | | |
  | 2 | | |
  | 3 | | |
  | 4 | | |
  | 5 | | |
  | 6 | | |
  | 7 | | |
  | 8 | | |
  | 9 | | |
  | * | | |
  | 0 | | |
  | # | | |

- [ ] Expect the pins to form 4 rows × 3 columns (7 pins in total)
- [ ] Note the part number of the main chip on the board

### Stage I: Ringer
- [ ] Find the ringer: a small chip near a piezo disc (a flat brass disc) or a tiny speaker
- [ ] Photograph it and note the chip's part number. Common ones are KA2411, ML8204, LS1240 and TCM1506
- [ ] Find out which transducer it is:

  | Reading across its two wires | Transducer |
  |---|---|
  | Open, or very high resistance (tens of nF in capacitance mode) | Piezo disc |
  | About 8–100Ω | Small speaker |

- [ ] Check for a ringer volume switch (Hi / Lo / Off), which could become a hardware volume control
- [ ] If it's a mechanical bell instead (two metal domes): measure the coil resistance (expect about 1–4kΩ) and see "Options considered and dropped" above
- [ ] Don't power anything yet

### Record when done
- Model number, plus photos of the label, the inside, the keypad connector, the hook switch and both handset capsules
- Mic type and resistance, earpiece resistance
- Handset cord pin mapping
- The hook switch contact pair
- The keypad table
- Ringer chip and transducer type
- Free space inside

---

## Build phases

| Phase | Goal | Status / done when |
|---|---|---|
| **1. Mac pipeline** | Mic → Whisper → Superset session; hooks → ring; keyboard simulator | ✅ Done (`mac/`) |
| **2. Nano control firmware** | Hook switch, keypad, ringer, serial protocol | ✅ Flashed and tested over serial (`firmware/`) |
| **3. Nano audio, bench prototype** | Mic front end on A0, timer sampling, audio frames to the Mac; Mac recorder reading them; `BEEP` on the DAC | Speech recorded through the Nano transcribes as well as through the sound card, at −15 to −40dBFS without clipping |
| **4. Investigate the phone** | Work through the checklist above | All findings recorded; final pins and parts list |
| **5. Handset wiring** | Real mic (with bias circuit if carbon), earpiece amp, hook switch, keypad, ringer | Every part works on the bench, wired to the Nano |
| **6. Integration** | Full loop on the real phone; launchd service | Pick up → speak → hang up → session → ring, without the laptop mic |
| **7. Fit inside the phone** | Perfboard, one USB cable out the back | Phone closes up and works as a single USB device |
| **8. Polish** | Dial tone, busy signal, `#` redial, optional spoken summary of the result | Nice to have |

---

## Open questions
1. **Which phone?** It decides the mic type, the keypad wiring, the ringer type and the space inside.
2. **Mic front end:** does the LGT8F328P's built-in amplifier give enough gain for the handset mic, or does it need a preamp / MAX9814?
3. **Audio quality over the Nano:** noise floor of the ADC with USB power, and whether 8-bit µ-law at 16kHz transcribes as well as the sound card.
4. **Ringer volume:** is the transducer loud enough on 5V push-pull, or does it need a transistor and a higher voltage?

## Safety
- Never connect the phone or the electronics to a real wall phone line.
- Don't drive a speaker straight from a microcontroller pin; use a transistor.
- If a mechanical bell is ever used, its driver runs at about 40V: build it on perfboard with a fuse and keep fingers off it while it rings.
