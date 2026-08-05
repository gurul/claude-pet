# Claude Pet 🐾

![claude pet — esp32 desk pet for claude code](docs/assets/thumbnail.png)

A desk pet on an ESP32-S3 touchscreen that reacts to **Claude Code** in real time. It sleeps
when you're idle, gets busy when Claude is working, and demands attention when Claude is
blocked on you. Permission prompts arrive as a card you approve or deny by swiping —
right to approve, left to deny.

Two MIT projects ported to the **Freenove FNK0104B** (ESP32-S3 Display 2.8" Touch):
[anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) for the
firmware (the 7-state pet, retargeted from the M5StickC Plus) and
[SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge) for the host
daemon (extended with a USB serial transport in place of BLE).

```
Claude Code CLI ─hooks→ unix socket → bridge daemon ─NDJSON over USB serial→ ESP32-S3
   └─ JSONL tailer (tokens, transcript entries) ┘        pet state machine + touch UI
```

## What it does

- **Mirrors Claude's state.** Sleeping, busy, waiting, celebrating — driven by Claude Code
  hooks plus a tailer over the session transcripts for tokens and message counts.
- **Approves tool calls from the board.** Risky commands (`git push`, `rm`, …) and reads
  outside the session directory render as a swipe card. The card shows which session is
  asking, how many more prompts wait behind it (a peeking deck + "+N" badge), and a bar
  draining toward the 300s terminal fallback. Destructive commands (`rm`, `sudo`,
  `git reset --hard`, …) get a red border and need a longer, harder swipe to approve.
  Tap the card for a full-screen view of the whole command; hold it at the right edge to
  approve **and stop being asked** for that command shape (daemon lifetime). Approving a
  read grants its whole enclosing repo for the daemon's lifetime.
- **Push-to-talk dictation.** Hold the pet and the daemon holds your dictation app's global
  hotkey until you let go — app-agnostic, it just holds a chord.
- **Hands-on-pet option picking.** Swipe left/right to walk Claude Code's option pickers
  (Up/Down arrows), swipe down for Enter — so the loop is: hold to dictate, release,
  swipe to choose, swipe down to send.
- **Always shows the time.** A small clock sits top-left of the pet whenever the bridge has
  synced the RTC.
- **Reports its own crashes — and recovers alone.** `cc-buddy-bridge diag` prints why the
  board last reset, what it was doing, and which loop phase hung, from an event ring that
  survives panics and watchdog reboots. The daemon watches the link both ways and escalates
  from reconnects (with a deliberate closed-port hold) up to an automatic RTS hardware reset
  of the board, so a wedged link heals without touching a cable.

## Hardware

| Part | Detail |
|---|---|
| MCU | ESP32-S3, 16MB QIO flash, 8MB OPI PSRAM, native USB-Serial/JTAG |
| LCD | ILI9341(V) 240×320 SPI — MOSI 11 / SCLK 12 / CS 10 / DC 46, backlight GPIO45 |
| Touch | FT6336G @ I2C 0x38 — SDA 16 / SCL 15 / INT 17 / RST 18 |
| Extras | WS2812 LED (GPIO42), ES8311 codec + mic + speaker (unused), microSD, battery ADC GPIO9 |

## Controls

| Gesture | Action |
|---|---|
| **Swipe card right / left** | approve / deny the pending prompt |
| **Hold card at the right edge** (700ms) | stamp flips to ALWAYS — approve and stop carding this command shape for the daemon's lifetime |
| **Tap the card** | expand to a full-screen view of the whole command (long commands truncate on the card); tap again to close |
| **Hold the pet** | push-to-talk: holds your dictation hotkey while held |
| **Swipe down** (anywhere) | press Enter on the Mac |
| **Swipe left / right** (no card up) | previous / next option in Claude Code's pickers (Up/Down arrow) |
| Tap bottom-right | scroll back through the transcript |

There is no on-device menu, settings, or info screen — the pet is always the
whole UI. Species and settings are host-side via the CLI
(`cc-buddy-bridge species`, `matchers.toml`); battery and link state via
`cc-buddy-bridge status` / `diag`.

The WS2812 pulses orange while an approval is pending, green on celebrate, pink on heart,
solid blue while dictating.

## Build & flash

```bash
arduino-cli core install esp32:esp32          # tested with 3.3.10
arduino-cli lib install ArduinoJson AnimatedGIF TFT_eSPI

./tools/flash.sh                              # compile → archive ELF → flash → restart daemon
```

`tools/flash.sh` handles the whole dance: it compiles, files the exact ELF away under
`firmware/build-archive/` (so any future panic backtrace stays symbolizable), boots the
daemon **out** (it owns `/dev/cu.usbmodem*` exclusively and the plist sets `KeepAlive`, so
merely stopping it isn't enough — esptool would fail looking exactly like a bricked board),
uploads, and bootstraps the daemon back. If the flasher still can't connect: hold **BOOT**,
tap **RESET**, release BOOT, retry.

## Host bridge

```bash
cd bridge
python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/cc-buddy-bridge install    # registers the hooks in Claude Code's settings.json
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'
```

| Command | What |
|---|---|
| `daemon --serial-port …` | run the bridge in the foreground |
| `install` / `uninstall` / `status` | manage hooks; `--service` also installs the launchd/systemd unit |
| `audit` | the approval decision log |
| `diag` / `diag --watch` | why the board last reset, and what it was doing |
| `voice-check` | diagnose push-to-talk (Accessibility permission, hotkey delivery) |
| `celebrate` | make the pet celebrate |

### Configuration

| Variable | Purpose |
|---|---|
| `CLAUDE_CONFIG_DIR` | which Claude config home `install`/`status` target (default `~/.claude`) |
| `CC_BUDDY_CLAUDE_CONFIG_DIRS` | `os.pathsep`-separated homes the daemon serves — it runs outside any session, so it can't inherit the above |
| `CC_BUDDY_VOICE_HOTKEY` | `option` (default), `opt-space`, or `fn` — match your dictation app |
| `CC_BUDDY_KEY_METHOD` | `osascript` routes Enter through System Events, for apps that swallow synthetic key events (Warp) |

Installing into the wrong config home **fails silently** — hooks written, board animating,
no session ever prompting. `status` prints the home it resolved; check it first.

Push-to-talk needs **Accessibility permission** for the daemon's python (macOS filters
synthetic events from untrusted processes). `voice-check` prints the exact binary to grant.
Avoid `fn` as a hotkey: it's a secondary-fn modifier that many apps read from raw HID, which
synthetic events can't reach — rebind to an ordinary chord.

## Layout

| Path | What |
|---|---|
| `firmware/claude_pet` | the sketch — pet state machine, touch UI, swipe cards, clock, diag ring |
| `firmware/claude_pet/src/board_compat.*` | the port: shims the `M5StickCPlus.h` API onto this board |
| `bridge/src/cc_buddy_bridge` | daemon, hooks, serial transport, voice trigger, read policy |
| `tools/flash.sh` | compile + ELF archive + daemon-safe flash in one step |
| `DESIGN.md` | architecture, board facts, port map, disconnect runbook, and the gotchas worth knowing |

## Licenses

Both upstreams are MIT (Anthropic PBC / Snow) and remain MIT here.
