# Claude Phone: Nano firmware

`claude_phone_nano/` runs on the LGT8F328P Nano and is the whole phone on one USB cable: it streams the handset mic to the Mac, plays earpiece cues, reads the hook switch and keypad, and drives the ringer. Protocol: see `PLAN.md`. Serial speed is **250000 baud**.

| Part | Pins | Behaviour |
|---|---|---|
| Mic in | A0 (12-bit ADC) | Sampled at 16kHz by a Timer1 interrupt and streamed as 8-bit µ-law frames (`0xFF`, length, samples) while the handset is lifted. `MIC 1` / `MIC 0` forces streaming on or back to automatic, for testing |
| Earpiece | D4 (built-in 8-bit DAC) | `BEEP` = 1kHz for 0.25s; `TONE <hz> <ms>` plays any tone. Idles at mid-scale (2.5V), so AC-couple the amplifier |
| Hook switch | D2 (`INPUT_PULLUP`); closed to GND = lifted | `HOOK_UP` / `HOOK_DOWN`, 30ms debounce. Flip `HOOK_LIFTED_LEVEL` if your switch opens when lifted |
| Keypad (4×3 grid) | Rows D3, D5, D6, D7; columns D8, D10, D12 | `KEY x` once per press, 20ms debounce |
| Ringer | D9 | `RING 350` / `RING 300,200,300`: 500/600Hz warble during "on" segments. `STOP` cancels |
| Onboard LED | D13 | On while the handset is lifted, flashes while ringing |
| Test | — | `PING` → `PONG`; `READY` on boot; `ERR …` for bad commands |

**Status:** v2 (audio) is flashed and compiles cleanly, but audio streaming is **not yet tested**. The first quick test got `READY` only after its commands were sent, so wait for `READY` after opening the port; it takes longer than 2 seconds.

## Build and upload

One-time setup:

```sh
brew install arduino-cli
arduino-cli config add board_manager.additional_urls https://raw.githubusercontent.com/dbuezas/lgt8fx/master/package_lgt8fx_index.json
arduino-cli core update-index
arduino-cli core install arduino:avr lgt8fx:avr   # arduino:avr provides the avr-gcc compiler
```

Then, from the repo root:

```sh
arduino-cli compile --fqbn lgt8fx:avr:328 firmware/claude_phone_nano
arduino-cli upload -p /dev/cu.usbserial-310 --fqbn lgt8fx:avr:328 firmware/claude_phone_nano
```

Find the port with `arduino-cli board list`. The board's USB chip is a CH340, which shows up as `/dev/cu.usbserial-…`. If the board doesn't appear at all, try another cable: many are charge-only.

## Bench test without a phone

- A jumper from D2 to GND simulates lifting the handset.
- A jumper from D4 to A0 loops earpiece tones back into the mic input (tests streaming without a mic).
- A piezo buzzer from D9 to GND lets you hear the ring.
- The Mac daemon doesn't read the audio frames yet, and still connects at 115200 baud. Until it's updated, the daemon's `--phone serial` mode won't talk to this firmware.
