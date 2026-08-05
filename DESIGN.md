# Claude Pet on Freenove FNK0104B (ESP32-S3 2.8" Touch)

A desk pet that reacts to Claude Code activity, built by forking open-source repos:

- **Firmware base:** [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) (MIT) — official 7-state pet
  (`sleep/idle/busy/attention/celebrate/dizzy/heart`), 18 ASCII species, GIF character packs,
  NDJSON wire protocol, and — key — **USB serial ingest already implemented** (`data.h` feeds `Serial`).
- **Host bridge base:** [SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge) (MIT) —
  Claude Code CLI hooks → Unix socket → daemon → device, with the documented state mapping
  (`total`←SessionStart/End, `running`←UserPromptSubmit/Stop, `waiting`+`prompt`←PreToolUse,
  tokens/messages←`~/.claude/projects/*.jsonl` tailer). We add a **serial transport** beside its BLE one.
- **Board pin data:** [Freenove/Freenove_ESP32_S3_Display](https://github.com/Freenove/Freenove_ESP32_S3_Display) (official vendor repo).

## Board facts (verified)

| Subsystem | Details |
|---|---|
| MCU | ESP32-S3 QFN56 rev0.2, 16MB QIO flash, 8MB **OPI** PSRAM, native USB-Serial/JTAG (`/dev/cu.usbmodem101`) |
| LCD | ILI9341(V) 240×320 SPI40MHz — MOSI=11 SCLK=12 MISO=13 CS=10 DC=46 RST=-1, BL=GPIO45 **active HIGH**; needs `ILI9341_2_DRIVER`, `TFT_INVERSION_ON`, `TFT_RGB_ORDER TFT_BGR` |
| Touch | FT6336G capacitive @I2C 0x38 — SDA=16 SCL=15 INT=17 RST=18 |
| RGB LED | 1× WS2812B GPIO42 (GRB) |
| Audio | ES8311 codec + FM8002E amp (I2S MCLK4 BCLK5 WS7 DOUT8 DIN6, amp-en GPIO1) + onboard mic — **deferred to later phase** |
| SD | 4-bit SD_MMC only (CLK38 CMD40 D0=39 D1=41 D2=48 D3=47) — unused |
| Battery | ADC GPIO9, ÷2 divider; TP4054 charger |
| Build | FQBN `esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashMode=qio,FlashSize=16M,PSRAM=opi,PartitionScheme=huge_app` — 3MB app at 0x10000, LittleFS on the 896KB `spiffs` partition at 0x310000 |

USB-serial note: opening the S3 native USB CDC port does **not** reset the sketch (unlike UART-bridge boards), so a host daemon holding the port open is safe. It does hold it *exclusively*, though — unload the daemon before flashing or esptool cannot connect.

Partition note: `huge_app` is a 4MB layout on a 16MB part, so ~12MB of flash is unaddressed and LittleFS gets 896KB. That is enough for the GIF character packs today. Moving to `default_16MB` (6.25MB app ×2 OTA + 3.5MB LittleFS) means relocating `spiffs` from 0x310000 to 0xc90000, so it needs a full `esptool erase-flash` first — see the partition gotcha below before attempting it.

## Architecture

```
Claude Code CLI ─hooks(async)→ unix socket → cc-buddy-bridge daemon ─┬─ BLE NUS (kept, works on S3)
        └─ statusline/JSONL tailer (tokens, messages) ───────────────┴─ NEW: USB CDC serial NDJSON
                                                                            ↓ /dev/cu.usbmodem101
                                             firmware (fork of claude-desktop-buddy)
                                             data.h NDJSON parser → TamaState → derive() → pet states
                                             touch: swipe card right=approve · left=deny ·
                                             hold pet=dictate · swipe down=Enter ·
                                             tap-right=scroll transcript
```

## Firmware port map (M5StickC Plus → FNK0104B)

| M5 dependency | Replacement |
|---|---|
| `M5.Lcd` (135×240 ST7789) | `TFT_eSPI` w/ custom setup header; W=240 H=320; sprite in PSRAM |
| `M5.BtnA/BtnB` | `TouchBtn` compat class over FT6336U: left/right tap zones, same `wasReleased/pressedFor` API |
| `M5.Imu` shake/face-down/orientation | dropped; dizzy = fast scrub gesture on pet, nap = tap-and-hold on sleeping pet; clock fixed portrait |
| `M5.Rtc` | ESP32 system clock (`settimeofday` from bridge `{"time":[...]}` sync) |
| `M5.Axp` brightness/power/battery | LEDC PWM on GPIO45; battery = `analogReadMilliVolts(9)*2`; power off → backlight off |
| `M5.Beep` | stub (later: ES8311 I2S chirps) |
| red LED GPIO10 | WS2812 GPIO42 via `neopixelWrite()` — attention=pulsing orange, heart=pink, celebrate=rainbow |
| BLE bridge | kept as-is (ESP32 BLE works on S3) |

Everything else (data.h, stats.h, xfer.h, character.cpp GIF renderer, 18 species) ports unchanged
apart from geometry constants (`BUDDY_X_CENTER` 67→120, canvas 135→240, layout scale).

## Swipe-card approvals

Permission prompts render as a draggable card (`main.cpp`, `CARD_*` constants + `drawApproval`)
instead of the upstream tap-left/tap-right panel:

- **Input** comes from the raw FT6336 state (`M5.touching()/touchX()/touchY()`), not the
  synthetic `BtnA/BtnB` zones. A drag can start anywhere in the card band (y ≥ 204). While a
  prompt is visible the button handlers and pet gestures are swallowed — the tail of a swipe
  crossing the strip must not cycle screens, and a drag through the pet zone must not scrub.
- **Tilt** is real rotation: the card face is drawn into a second 210×80 sprite and composited
  into the full-screen sprite with `pushRotated` (±9° clamp, `TFT_TRANSPARENT` corners). At ±9°
  the rotated half-height is ~56px, so a center at y=262 keeps every pixel inside the 204..320
  band, which is cleared each frame — same self-clearing model as the old panel, no trails over
  the dirty-region pet renderer.
- **State machine** `REST → DRAG → FLY | SNAP`: release past 60px (or a >10px/frame flick)
  sends the decision (`once`/`deny`) immediately and accelerates the card off-screen (×1.12/frame);
  otherwise it springs back (×0.65/frame). The stamp + border color flip at 30% of the commit
  distance, with a beep latch at 100%.
- **Memory:** the card sprite (33KB) allocates on prompt arrival and frees on resolve; if
  allocation fails the card draws directly on the main sprite, untilted, and swiping still works.

## Diagnostics

A frozen board used to tell you nothing: the screen is stale and the single USB CDC pipe (which
the daemon owns exclusively) just goes quiet. UART0 (43/44) is free but needs a USB-TTL adapter
wired on, so `src/diag.h` takes the no-extra-hardware route instead — an event ring in
`RTC_NOINIT` memory (survives panics, watchdog reboots and software resets, not power loss), the
decoded `esp_reset_reason()`, and a task watchdog on `loop()` so a true hang reboots and reports
rather than sitting there silently. A one-byte phase marker per loop stage names the call that
never returned, and any iteration ≥250ms is logged with the stage that ate the time.
`cc-buddy-bridge diag` decodes all of it on the next boot.

Two hangs are still open, distinguished by the phase marker: `DIED IN: loop end` (loop task
stopped being scheduled inside `delay()`, no slow iteration beforehand, cause unknown) and
`DIED IN: render` (a 240×320 16-bit `pushSprite` is ~153KB over SPI at 40MHz, ~31ms). Both
self-heal — the watchdog reboots within 15s.

## Gotchas

- **Touch releases need debouncing.** `readTouch()` returns false on a momentary `TD_STATUS==0`
  or any I2C hiccup mid-press. Treating that as a release fired a tap, then the next poll saw the
  finger again and began a fresh press — one finger produced ~6 taps/second, and the storm wedged
  the loop task into a watchdog reset. `TOUCH_UP_POLLS` consecutive empty reads are now required
  before believing a release; any new gesture must debounce the same way.
- **LittleFS formats itself on first boot.** A freshly flashed board's `spiffs` partition has
  never been formatted and `LittleFS.begin(false)` will not format it — it stays at `fsTotal=0`
  forever and the daemon rejects every character push. `characterInit()` formats once when the
  mount genuinely fails; safe, since the partition only holds re-pushable GIF packs.
- **Partition gotcha.** A sketch-local `partitions.csv` is silently ignored by arduino-cli +
  esp32 3.3.10 — the flash ends up with a stale default table and an unbootable app. Use an
  explicit `PartitionScheme`, and `esptool erase-flash` when in doubt.
- **HWCDC needs DTR.** The S3 drops all `Serial` TX unless the host asserts DTR — `cat` sees
  nothing, pyserial with `dtr=True` does.
- **A one-way serial link looks healthy.** USB re-enumeration (every board reset, including
  esptool's) leaves the host holding a file descriptor that no longer reaches the device: writes
  succeed into the void, reads return empty, nothing raises. The firmware prints `[alive]` every
  5s and the transport forces a reconnect after 20s of silence.
- **Clock glyph padding is load-bearing.** Both faces centre the time and repaint only their own
  glyph cells, so the 5→4 character shrink at 12:59 → 1:00 would strand pixels. The hour is
  space-padded, and the leading space draws as a background-filled cell that clears them.
- **The host allowlists exactly one key.** `{"cmd":"key","name":...}` accepts only `enter` — a
  peripheral on a serial line asking for arbitrary keystrokes is a much larger surface than this
  needs. It is also refused during a push-to-talk hold, where Option is still down and a bare
  Return would arrive as Opt+Return.
- **Held modifiers are released defensively.** A stuck modifier breaks typing system-wide, so the
  daemon force-releases after 60s if the release event is lost, and always on shutdown. Releases
  carry `flags=0` — asserting the modifier flag on key-up tells macOS the key is still down.
- **Bash matchers are anchored at the start of the command** (`^git push( |$)`, …). A leading
  variable assignment or `cd` defeats them, and the command quietly takes the default path.

## Phases

- **A — display bring-up:** sketch compiles under arduino-cli, buddy idle animation renders correctly
  (colors, inversion), demo mode cycles states. Flash + visual verify.
- **B — input port:** touch buttons, gestures, approval screen at 240×320.
- **C — host bridge:** vendored cc-buddy-bridge + `serial_transport.py` (pyserial), venv install,
  hook installation into `~/.claude/settings.json` (with user approval), end-to-end verify.

## Licenses

Firmware fork: MIT (Anthropic PBC) — preserved. Bridge fork: MIT (Snow) — preserved.
`characters/bufo` GIFs are third-party art — not redistributed here.
