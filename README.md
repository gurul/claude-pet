# Claude Pet 🐾

![claude pet — esp32 desk pet for claude code](docs/assets/thumbnail.png)

A desk pet on an ESP32-S3 touchscreen that reacts to **Claude Code** in real time —
it sleeps when you're idle, gets busy when Claude is working, demands attention when a
permission prompt is pending, and lets you **approve or deny tool calls by swiping the prompt
card — right to approve, left to deny** (Tinder-style, tilt and all).

Built by porting two MIT-licensed open-source projects to the
**Freenove FNK0104B** (ESP32-S3 Display 2.8" Touch, "CYD"-style):

- **Firmware:** fork of [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) —
  the official 7-state pet (18 ASCII species + GIF character packs), retargeted from the
  M5StickC Plus to this board's ILI9341 240×320 panel + FT6336G capacitive touch.
- **Host bridge:** fork of [SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge) —
  Claude Code CLI hooks → Unix socket → daemon, extended with a **USB CDC serial transport**
  (`serial_transport.py`) replacing BLE, since the board sits on USB anyway.

```
Claude Code CLI ─hooks→ unix socket → bridge daemon ─NDJSON over USB serial→ ESP32-S3
   └─ JSONL tailer (tokens, transcript entries) ┘        pet state machine + touch UI
```

## Hardware (Freenove FNK0104B)

| Part | Detail |
|---|---|
| MCU | ESP32-S3, 16MB QIO flash, 8MB OPI PSRAM, native USB-Serial/JTAG |
| LCD | ILI9341(V) 240×320 SPI — MOSI 11 / SCLK 12 / CS 10 / DC 46, backlight GPIO45 (PWM), `TFT_INVERSION_ON` + BGR |
| Touch | FT6336G @ I2C 0x38 — SDA 16 / SCL 15 / INT 17 / RST 18 |
| Extras | WS2812 LED (GPIO42), ES8311 codec + mic + speaker (unused for now), microSD (SDMMC), battery ADC GPIO9 |

## Touch controls

| Gesture | Action |
|---|---|
| **Swipe card right** | approve prompt (card flies off) |
| **Swipe card left** | deny prompt |
| Tap bottom-**left** | next screen |
| Tap bottom-**right** | next page |
| **Hold** bottom-left | menu |
| **Hold the pet** | push-to-talk: hold your dictation app's hotkey (LED solid blue) |
| **Swipe down** (anywhere) | press Enter on the Mac — dictate, then swipe to send |

The WS2812 pulses orange while an approval is pending, green on celebrate, solid blue while dictating.

### The approval card

When a permission prompt arrives, it renders as a **card** in the bottom band of the
screen — tool name, a two-line hint, and a wait timer (turns red-orange after 10s).
Deciding is a swipe, not a tap:

- **Drag** the card left or right — it follows your finger and tilts up to ±9°.
- Past **60px** of drag the border turns green (right) or red (left) and an
  **APPROVE** / **DENY** stamp appears with a chirp.
- **Release past the threshold** (or flick fast) → the decision sends immediately and the
  card accelerates off-screen; `sent...` shows until Claude Code acks.
- **Release early** → the card springs back to center. Nothing sent.

Approve maps to Claude Code's *allow once*; deny is *deny*. Fast approvals (<5s) earn a
heart. While a prompt is up, the tap zones and pet gestures are suspended so a swipe
crossing them can't change screens or dizzy the pet.

### Hold the pet to dictate

Press and hold the pet for 600ms (steady finger, <20px drift) and the bridge
holds your dictation app's global push-to-talk hotkey until you let go. Finger
down = recording (WS2812 goes solid blue), finger up = the app transcribes and
pastes at the cursor. Release works even if a prompt or menu opens mid-hold.

**The pet is dictation-app agnostic.** It knows nothing about any particular
app — it just holds a hotkey, so anything with press-and-hold dictation works:
[Willow Voice](https://willowvoice.com), [Wispr Flow](https://wisprflow.ai),
[VoiceFlow](https://github.com/Alexander-Ollman/voiceflow), macOS's built-in
dictation, or whatever you use. Point `CC_BUDDY_VOICE_HOTKEY` at whichever
chord that app listens for:

| Value | Chord | Typical app |
|---|---|---|
| `option` *(default)* | hold ⌥ | Willow Voice |
| `opt-space` | hold ⌥Space | VoiceFlow |
| `fn` | hold fn | macOS dictation, Willow's default binding |

Adding another chord is a one-line entry in `HOTKEYS` (`voice_trigger.py`) —
an ordered list of `(keycode, flags)` pairs, pressed in order and released in
reverse.

**A warning about `fn`, learned the hard way.** It is not an ordinary key but a
*secondary-fn modifier*, and many apps detect it through the raw HID usage that
synthetic events cannot reach — Willow Voice ignored ours completely while
logging a perfectly clean hold on our side. If your app's hotkey is fn and
nothing happens, rebind it in that app to an ordinary chord (⌥, ⌥Space, F13…);
those synthesize reliably. Ordinary modifiers are the safe choice.

Requirements on the Mac: your dictation app running, and **Accessibility
permission** for the daemon's python (System Settings → Privacy & Security →
Accessibility — the first hold triggers the system prompt; nothing is posted
until granted, since macOS silently filters synthetic events from untrusted
processes). `cc-buddy-bridge voice-check` diagnoses this and prints the exact
binary to add — note that a venv python is a symlink and macOS keys the grant
to the resolved interpreter, so stale entries in that list will shadow a good
grant.

Stuck-key safety: a held modifier breaks typing system-wide, so the daemon
force-releases after 60s if the release event is lost (board reset mid-hold,
serial drop) and always releases on shutdown. Releases carry `flags=0` —
asserting the modifier flag on a key-up tells macOS the key is *still down*,
which is exactly how fn once stuck for real.

### Swipe down to send

A mostly-vertical drag of more than 45px — starting anywhere on the panel —
taps **Enter** on the Mac,
so the natural loop is: hold to dictate, release, swipe down to send. It fires
the moment the threshold is crossed rather than on release, and latches one
Enter per press.

Gestures start **anywhere on the screen**, including the bottom button strip,
and keep tracking wherever the finger travels. An earlier version aborted a
drag the instant it crossed into the strip, which killed the swipe ~39px into
a 45px threshold and made it fail from most of the panel. Taps in the strip
still work as buttons — the button is withdrawn only once the press actually
becomes a swipe or a hold.

Touching the pet is functional only: tap-for-heart and scrub-for-dizzy were
removed by owner decree — the pet's moods come from Claude's state, not from
being poked.

**If Enter lands in the wrong place** (Warp is the known case — it routes
CGEventPost synthetics to its own "Warp anything" agent bar instead of the
focused terminal pane, so the Return appears to open a new input rather than
submit), set `CC_BUDDY_KEY_METHOD=osascript`. That delivers the key through
System Events / the accessibility API, which targets the actually-focused
element. It costs a ~40ms subprocess per press, which is why it is opt-in
rather than the default.

The host allowlists exactly one key. A peripheral on a serial line asking for
arbitrary keystrokes is a far larger surface than this feature needs, so
`{"cmd":"key","name":...}` accepts only `enter`. It is also refused while a
push-to-talk hold is active — Option is still held down there, and a bare
Return would arrive as Opt+Return.

### Debugging a freeze — the diag port

A frozen board used to tell you nothing: the screen is stale and the single USB
CDC pipe (which the daemon owns exclusively) just goes quiet.
[era-firmware](https://github.com/Era-Laboratories/era-firmware-rs) solves this
by splitting protocol onto USB and logs onto UART0. This board's UART0 pins
(43/44) are free so that route stays open, but it needs a USB-TTL adapter wired
on — so the same answer is available here with no extra hardware:

```bash
cc-buddy-bridge diag           # why the board last reset, and what it was doing
cc-buddy-bridge diag --watch   # leave running to catch the next freeze
```

```
boot #6   last reset: TASK-WATCHDOG  <-- ABNORMAL
DIED IN: loop end   (entered 58121ms, 866 loops)

what it was doing before that reset (oldest first):
    42.40s  gesture tap
    42.58s  gesture tap
    ...
```

Three mechanisms in `src/diag.h`: an event ring in `RTC_NOINIT` memory (survives
panics, watchdog reboots and software resets — not power loss), the decoded
`esp_reset_reason()`, and a **task watchdog** on `loop()` so a true hang reboots
and reports rather than sitting there silently forever. A one-byte phase marker
per loop stage names the call that never returned, and any iteration ≥250ms is
logged with the stage that ate the time.

This found the tap-storm hang within minutes of existing: bursts of ~15 taps at
a metronomic 175ms, dying 39ms after the last one — see the touch-debounce note
in Porting notes.

**Known issue it is currently chasing.** Some hangs still occur. The phase
marker separates them into two distinct failures rather than one vague
"it froze":

* `DIED IN: loop end` — the loop task stopped being scheduled inside
  `delay()`, with no slow iteration beforehand. Cause not yet identified.
* `DIED IN: render (buddy/sprite push)` — a 240x320 16-bit `pushSprite` is
  ~153KB over SPI at 40MHz (~31ms), and the hang lands inside that path.

Both self-heal: the watchdog reboots within 15s and the next boot reports what
it was doing. That is the difference between a dead board needing a replug and
a diagnosable event.

### The clock face

When the board is on USB power and genuinely idle — no menu, no prompt, no running or
waiting sessions, and the bridge has synced the RTC — the home screen becomes a clock
with the pet dozing underneath. A heartbeat alone doesn't count as activity, since it's
the only way the RTC ever gets set.

The time reads **12-hour with AM/PM** (`1:05` / `:07 PM`), hour space-padded rather than
zero-padded. That padding is load-bearing: both the portrait and landscape faces centre
the time and repaint only their own glyph cells, so a 5→4 character shrink at 12:59 → 1:00
would strand pixels; the leading space draws as a background-filled cell and clears them.

The pet's mood on the clock screen still runs off the real 24-hour hour — asleep 1–7am and
after 10pm, hearts at noon, celebrating Friday afternoons, lazier on weekends.

## Build & flash (firmware)

```bash
arduino-cli core install esp32:esp32          # tested with 3.3.10
arduino-cli lib install ArduinoJson AnimatedGIF TFT_eSPI

FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashMode=qio,FlashSize=16M,PSRAM=opi,PartitionScheme=huge_app"
arduino-cli compile -b "$FQBN" \
  --build-property "compiler.cpp.extra_flags=-DUSER_SETUP_LOADED=1 -include $PWD/firmware/claude_pet/tft_setup.h" \
  firmware/claude_pet

# Free the serial port first — the bridge daemon holds it exclusively (see below)
launchctl bootout gui/$(id -u)/com.github.cc-buddy-bridge.daemon      # macOS
# systemctl --user stop cc-buddy-bridge                               # Linux

arduino-cli upload -p /dev/cu.usbmodem* -b "$FQBN" firmware/claude_pet

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
# systemctl --user start cc-buddy-bridge
```

**Unload the daemon before flashing.** If it's installed as a service it owns
`/dev/cu.usbmodem*` for as long as it runs, and esptool fails with
`A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.`
That looks identical to a bricked board — check `lsof /dev/cu.usbmodem*` before
reaching for recovery steps. `launchctl stop` is not enough: the plist sets
`KeepAlive`, so launchd restarts the daemon immediately and it retakes the port.
Use `bootout` / `bootstrap` (or `systemctl --user stop` / `start`).

If the flasher still can't connect: hold **BOOT**, tap **RESET**, release BOOT, retry.

## Host bridge

```bash
cd bridge
python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/cc-buddy-bridge daemon --serial-port '/dev/cu.usbmodem*'   # keep running
.venv/bin/cc-buddy-bridge install    # registers 7 hooks in Claude Code's settings.json
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'
                                     # auto-start the daemon on login (launchd/systemd)
```

### Which Claude config home?

Claude Code reads its settings and writes its transcripts under `$CLAUDE_CONFIG_DIR`
when that is set, and `~/.claude` otherwise. Wrappers set it — era-code, for one, runs
every session against a private config home.

This matters because installing into the wrong home **fails silently**: the hooks are
written, the daemon runs, the board connects and animates, and no session ever prompts,
because the sessions you actually run never read that file. Nothing errors.

`install` / `uninstall` / `status` follow `$CLAUDE_CONFIG_DIR` by default and take
`--config-dir` to target a specific home. `status` prints the path it resolved, which is
the fastest way to check you configured the home you're actually using:

```bash
.venv/bin/cc-buddy-bridge status                                  # honours $CLAUDE_CONFIG_DIR
.venv/bin/cc-buddy-bridge install --config-dir ~/.claude          # plain sessions
.venv/bin/cc-buddy-bridge install --config-dir ~/.era/era-code/claude-home
```

The daemon is the awkward case: it's a launchd agent / systemd unit started outside any
session, so it can never inherit a per-session `$CLAUDE_CONFIG_DIR`. It reads
`CC_BUDDY_CLAUDE_CONFIG_DIRS` — an `os.pathsep`-separated list of homes to serve — and
`install --service` bakes the union of your current home and `~/.claude` into the service
definition. One daemon then covers both, tailing every home's `projects/` tree for tokens
and transcript entries. Watch for `tailing transcripts: …` in the log to confirm.

Hook flow: trivial Bash commands are auto-allowed, risky ones (`git push`, `rm`, …) prompt
**on the pet** with a 300s timeout falling back to the terminal, everything else uses
Claude Code's normal flow. Whenever Claude is blocked on you — a permission prompt in the
terminal, or it's idle waiting for input — the `Notification` hook puts the pet into its
**attention** animation (impatient pet + pulsing orange LED) until you respond.
`cc-buddy-bridge audit` shows the decision log.

**If the board shows "No Claude connected" while the daemon looks healthy**, the
serial link has gone one-way. A USB re-enumeration — which every board reset causes,
including the one esptool triggers on flash — leaves the host holding a file
descriptor that no longer reaches the device: writes succeed into the void, reads
return empty, and nothing raises, so the daemon reports itself connected forever.
The transport now watches for this: the firmware prints `[alive]` every 5s, so
20s of total silence is treated as a stale handle and forces a reconnect
(`serial: no data from board for 20s …` in the log). Restarting the daemon also
clears it.

### Read cards

File reads outside the session's working directory used to prompt only in the
terminal — during a subagent swarm that meant a stream of terminal prompts, each
flapping the pet into its attention animation with no way to act from the board.

Those reads now surface as cards too (`Read` + the home-relative path), and an
**approval grants the whole enclosing scope** — the containing git repo when
there is one, else the file's parent directory — for the rest of the daemon's
life. The next hundred reads under that repo sail through without a card; that
scope-not-file grant is the point. Deny stays per-file and grants nothing.
Grants are deliberately not persisted: restarting the daemon forgets them all.
Approving a file directly in `~` or `/` grants nothing (it would be everything).
Reads inside the session cwd never card — Claude Code already allows those
silently. Swipe right to approve; new sessions pick up the `Read`
hook, already-running ones keep the old terminal behaviour until restarted.

The matcher patterns are **anchored at the start of the command** (`^chmod( |$)`,
`^git push( |$)`, …). A leading variable assignment or `cd` defeats them —
`SP=/tmp chmod 644 $SP/f` does not match `^chmod` and quietly takes the default path
instead of prompting. Worth knowing when a command you expected to gate doesn't.

## Porting notes

- `firmware/claude_pet/src/board_compat.h/.cpp` is the whole port: a shim that impersonates
  the `M5StickCPlus.h` API (`M5.Lcd`, `M5.BtnA/BtnB`, `M5.Imu`, `M5.Rtc`, `M5.Axp`, `M5.Beep`)
  on this board's hardware. Buttons are touch zones; the IMU is inert; the RTC is the system
  clock synced from the bridge; brightness is LEDC PWM on GPIO45.
- BLE is stubbed out (`ble_bridge.cpp`): esp32 core 3.x moved to NimBLE and broke the upstream
  Bluedroid code; USB serial replaces it entirely. Restore from upstream if you want desktop-app pairing.
- **HWCDC gotcha:** the S3 drops all `Serial` TX unless the host asserts **DTR** — `cat`
  won't see output; pyserial with `dtr=True` will. Opening the port does *not* reset the sketch.
- **Touch releases need debouncing.** `readTouch()` returns false on a momentary
  `TD_STATUS==0` or any I2C hiccup mid-press. Treating that as a release fired a
  tap, then the next poll saw the finger again and began a fresh press — one
  finger produced ~6 taps/second, and the storm wedged the loop task into a
  watchdog reset. `TOUCH_UP_POLLS` consecutive empty reads are now required
  before believing a release. Any new gesture must debounce the same way.
- **LittleFS formats itself on first boot.** A freshly flashed board has a `spiffs`
  partition that has never been formatted, and `LittleFS.begin(false)` will not format it —
  it stays at `fsTotal=0` forever and the daemon rejects every character push with
  *"stick LittleFS appears unformatted"* once a minute. `characterInit()` now formats once
  when the mount genuinely fails. Safe to do: the partition only holds GIF packs, which are
  re-pushable from the host, whereas an unformatted partition blocks the feature for good.
- **Partition gotcha:** a sketch-local `partitions.csv` is silently ignored by arduino-cli +
  esp32 3.3.10 — the flash ended up with a stale default table and an unbootable app.
  Use an explicit `PartitionScheme` and, when in doubt, `esptool erase-flash` first.

## Licenses

Both upstreams are MIT and remain MIT here (Anthropic PBC / Snow). Reference clones live
outside the repo (`vendor/`, gitignored). See `DESIGN.md` for the full architecture rationale.
