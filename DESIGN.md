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
| Build | FQBN `esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashMode=qio,FlashSize=16M,PSRAM=opi,PartitionScheme=default_16MB` (LittleFS on spiffs partition) |

USB-serial note: opening the S3 native USB CDC port does **not** reset the sketch (unlike UART-bridge boards), so a host daemon holding the port open is safe.

## Architecture

```
Claude Code CLI ─hooks(async)→ unix socket → cc-buddy-bridge daemon ─┬─ BLE NUS (kept, works on S3)
        └─ statusline/JSONL tailer (tokens, messages) ───────────────┴─ NEW: USB CDC serial NDJSON
                                                                            ↓ /dev/cu.usbmodem101
                                             firmware (fork of claude-desktop-buddy)
                                             data.h NDJSON parser → TamaState → derive() → pet states
                                             touch: tap-left=approve/next · tap-right=deny/page ·
                                             long-press=menu · petting the sprite=heart · scrub=dizzy
```

## Firmware port map (M5StickC Plus → FNK0104B)

| M5 dependency | Replacement |
|---|---|
| `M5.Lcd` (135×240 ST7789) | `TFT_eSPI` w/ custom setup header; W=240 H=320; sprite in PSRAM |
| `M5.BtnA/BtnB` | `TouchBtn` compat class over FT6336U: left/right tap zones, same `wasReleased/pressedFor` API |
| `M5.Imu` shake/face-down/orientation | dropped; dizzy = fast scrub gesture on pet, nap = tap-and-hold on sleeping pet; clock fixed portrait |
| `M5.Rtc` | ESP32 system clock (`settimeofday` from bridge `{"time":[...]}` sync) |
| `M5.Axp` brightness/power/battery | LEDC PWM on GPIO45; battery = `analogReadMilliVolts(9)*2`; power-off menu item → backlight off |
| `M5.Beep` | stub (later: ES8311 I2S chirps) |
| red LED GPIO10 | WS2812 GPIO42 via `neopixelWrite()` — attention=pulsing orange, heart=pink, celebrate=rainbow |
| BLE bridge | kept as-is (ESP32 BLE works on S3) |

Everything else (data.h, stats.h, xfer.h, character.cpp GIF renderer, 18 species) ports unchanged
apart from geometry constants (`BUDDY_X_CENTER` 67→120, canvas 135→240, layout scale).

## Phases

- **A — display bring-up:** sketch compiles under arduino-cli, buddy idle animation renders correctly
  (colors, inversion), demo mode cycles states. Flash + visual verify.
- **B — input port:** touch buttons, petting/scrub gestures, menus, approval screen at 240×320.
- **C — host bridge:** vendored cc-buddy-bridge + `serial_transport.py` (pyserial), venv install,
  hook installation into `~/.claude/settings.json` (with user approval), end-to-end verify.

## Licenses

Firmware fork: MIT (Anthropic PBC) — preserved. Bridge fork: MIT (Snow) — preserved.
`characters/bufo` GIFs are third-party art — not redistributed here.
