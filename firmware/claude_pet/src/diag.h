#pragma once
#include <Arduino.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <stdarg.h>

// ─────────────────── crash / hang diagnostics ───────────────────
//
// The problem this solves: when the board freezes, the screen tells you
// nothing and the serial link just goes quiet. Everything shares one USB CDC
// pipe that the daemon owns exclusively, so there is no second channel to ask
// "what were you doing?". era-firmware answers this by putting logs on UART0
// and protocol on USB — a real second port. This board's UART0 pins (43/44)
// are free, so that route stays open (see DESIGN.md), but it needs a USB-TTL
// adapter physically wired. This gives the same answer with no extra hardware.
//
// Two mechanisms:
//
// 1. An event ring in RTC_NOINIT memory. That region survives a software
//    reset, a panic and a watchdog reboot (it does NOT survive power loss),
//    so the last events written before a hang are readable on the next boot.
//    Combined with esp_reset_reason() that turns "it froze" into "it was
//    doing X, then the task watchdog fired".
//
// 2. The task watchdog itself. Without it a true hang is silent forever —
//    the board sits there and the host only sees absence. With loop()
//    subscribed, a hang becomes a reset with reason TASK_WDT plus the ring,
//    i.e. a diagnosable event instead of a mystery.
//
// The boot report goes out as {"diag":{...}} for the daemon to log, and as
// plain text for a bare serial monitor.

#define DIAG_MAGIC 0x43504431u   // "CPD1"
#define DIAG_SLOTS 16
#define DIAG_TEXT  30
// Generous: a slow GIF decode or LittleFS format must not trip it. We are
// hunting hard hangs, not jitter.
#define DIAG_WDT_SECS 15

// Loop phases. The ring says WHAT the board was doing; the phase says WHERE
// in the loop it stopped — one byte store per stage, cheap enough to run at
// frame rate, which the formatted ring is not. On a hang the last phase
// written is the call that never returned.
enum DiagPhase : uint8_t {
  DP_NONE = 0, DP_SETUP, DP_TOUCH, DP_DATA, DP_STATS, DP_LED, DP_GESTURE,
  DP_PROMPT, DP_RENDER, DP_CLOCK, DP_MENU, DP_SERIAL, DP_IDLE,
};

inline const char* diagPhaseName(uint8_t p) {
  switch (p) {
    case DP_SETUP:   return "setup";
    case DP_TOUCH:   return "touch/I2C (M5.update)";
    case DP_DATA:    return "serial parse (dataPoll)";
    case DP_STATS:   return "stats";
    case DP_LED:     return "LED (rgbLedWrite/RMT)";
    case DP_GESTURE: return "gesture handling";
    case DP_PROMPT:  return "prompt/card";
    case DP_RENDER:  return "render (buddy/sprite push)";
    case DP_CLOCK:   return "clock face";
    case DP_MENU:    return "menu/overlay";
    case DP_SERIAL:  return "serial write";
    case DP_IDLE:    return "loop end";
    default:         return "none";
  }
}

struct DiagRing {
  uint32_t magic;
  uint16_t bootCount;
  uint8_t  head;         // next write slot
  uint8_t  count;        // entries used (<= DIAG_SLOTS)
  uint32_t lastUptimeMs; // uptime at the most recent event
  uint8_t  phase;        // DiagPhase — where the loop was
  uint32_t phaseMs;      // when that phase was entered
  uint32_t loops;        // loop iterations this boot
  char     ev[DIAG_SLOTS][DIAG_TEXT];
  uint32_t evMs[DIAG_SLOTS];
};

// Placed in RTC slow memory, deliberately NOT zeroed at startup.
RTC_NOINIT_ATTR DiagRing _diagRing;
static bool _diagPrevValid = false;
static DiagRing _diagPrev;          // snapshot of the pre-reset ring
static esp_reset_reason_t _diagReason = ESP_RST_UNKNOWN;

inline const char* diagResetName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:   return "power-on";
    case ESP_RST_EXT:       return "external-pin";
    case ESP_RST_SW:        return "software";
    case ESP_RST_PANIC:     return "PANIC";        // crash: bad deref, abort()
    case ESP_RST_INT_WDT:   return "INT-WATCHDOG"; // interrupts blocked too long
    case ESP_RST_TASK_WDT:  return "TASK-WATCHDOG";// loop() hung
    case ESP_RST_WDT:       return "other-watchdog";
    case ESP_RST_DEEPSLEEP: return "deep-sleep";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";     // power sag — usually USB
    case ESP_RST_SDIO:      return "sdio";
    default:                return "unknown";
  }
}

// One store, no formatting — safe to call several times per frame.
// Also measures how long the PREVIOUS phase took, so the slow-loop detector
// can name the stage that ate the time rather than just the total.
static uint8_t  _diagPrevPhase = DP_NONE;
static uint32_t _diagPhaseStart = 0;
static uint8_t  _diagWorstPhase = DP_NONE;
static uint32_t _diagWorstMs = 0;

inline void diagPhase(uint8_t p) {
  uint32_t now = millis();
  uint32_t spent = now - _diagPhaseStart;
  if (spent > _diagWorstMs) { _diagWorstMs = spent; _diagWorstPhase = _diagPrevPhase; }
  _diagPrevPhase = p;
  _diagPhaseStart = now;
  _diagRing.phase = p;
  _diagRing.phaseMs = now;
}

// Call once per loop with the iteration's total time. A loop that suddenly
// takes hundreds of ms is the pathology; catching it BEFORE the watchdog
// fires is what turns "it froze" into "this stage got slow, then it froze".
inline void diagLoopEnd(uint32_t loopMs);

inline void diagLoopTick() { _diagRing.loops++; }

inline void diagLog(const char* fmt, ...) {
  DiagRing& d = _diagRing;
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(d.ev[d.head], DIAG_TEXT, fmt, ap);
  va_end(ap);
  d.evMs[d.head] = millis();
  d.lastUptimeMs = d.evMs[d.head];
  d.head = (d.head + 1) % DIAG_SLOTS;
  if (d.count < DIAG_SLOTS) d.count++;
}

inline void diagLoopEnd(uint32_t loopMs) {
  // Only the notable ones — a healthy loop is ~16ms and would flood the ring.
  if (loopMs >= 250) {
    diagLog("SLOW loop %lums @%s", (unsigned long)loopMs,
            diagPhaseName(_diagWorstPhase));
  }
  _diagWorstMs = 0;
  _diagWorstPhase = DP_NONE;
}

// Call FIRST in setup(), before anything that might crash.
inline void diagInit() {
  _diagReason = esp_reset_reason();
  bool valid = _diagRing.magic == DIAG_MAGIC
            && _diagRing.head < DIAG_SLOTS
            && _diagRing.count <= DIAG_SLOTS;
  if (valid && _diagReason != ESP_RST_POWERON) {
    // Ring survived a reset — keep a copy to report, then start fresh.
    memcpy(&_diagPrev, &_diagRing, sizeof(DiagRing));
    _diagPrevValid = true;
  }
  uint16_t boots = valid ? _diagRing.bootCount : 0;
  memset(&_diagRing, 0, sizeof(DiagRing));
  _diagRing.magic = DIAG_MAGIC;
  _diagRing.bootCount = boots + 1;
  diagLog("boot #%u %s", (unsigned)_diagRing.bootCount, diagResetName(_diagReason));
}

// Subscribe loop() to the task watchdog so a hang reboots (and is therefore
// reportable) instead of hanging silently forever.
inline void diagWatchdogBegin() {
  esp_task_wdt_config_t cfg = {
    .timeout_ms = DIAG_WDT_SECS * 1000,
    .idle_core_mask = 0,       // don't watch idle tasks; only our loop
    .trigger_panic = true,     // panic → reset → reason is TASK_WDT
  };
  // The Arduino core may already have initialised the TWDT; reconfigure is
  // fine, and ESP_ERR_INVALID_STATE just means "already running".
  esp_err_t e = esp_task_wdt_init(&cfg);
  if (e == ESP_ERR_INVALID_STATE) esp_task_wdt_reconfigure(&cfg);
  esp_task_wdt_add(NULL);      // NULL = the current (loop) task
}

inline void diagWatchdogFeed() { esp_task_wdt_reset(); }

// One JSON line the daemon can capture, plus human-readable text for a bare
// serial monitor. Called once at boot and on {"cmd":"diag"}.
inline void diagReport(const char* why) {
  const DiagRing& cur = _diagRing;
  Serial.printf("{\"diag\":{\"why\":\"%s\",\"boot\":%u,\"reset\":\"%s\","
                "\"up\":%lu,\"heap\":%u,\"minheap\":%u,\"psram\":%u,\"prev\":%s",
                why, (unsigned)cur.bootCount, diagResetName(_diagReason),
                (unsigned long)(millis() / 1000), (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getMinFreeHeap(), (unsigned)ESP.getFreePsram(),
                _diagPrevValid ? "true" : "false");
  // The single most useful field on a hang: which call never returned.
  if (_diagPrevValid) {
    Serial.printf(",\"diedIn\":\"%s\",\"diedAtMs\":%lu,\"loops\":%lu",
                  diagPhaseName(_diagPrev.phase),
                  (unsigned long)_diagPrev.phaseMs,
                  (unsigned long)_diagPrev.loops);
  }
  // Events from the boot that died, oldest first — the actual answer to
  // "what was it doing when it froze".
  if (_diagPrevValid) {
    Serial.print(",\"last\":[");
    uint8_t start = (_diagPrev.head + DIAG_SLOTS - _diagPrev.count) % DIAG_SLOTS;
    for (uint8_t i = 0; i < _diagPrev.count; i++) {
      uint8_t s = (start + i) % DIAG_SLOTS;
      Serial.printf("%s\"%lu:%s\"", i ? "," : "",
                    (unsigned long)_diagPrev.evMs[s], _diagPrev.ev[s]);
    }
    Serial.print("]");
  }
  Serial.println("}}");

  Serial.printf("[diag] boot #%u after %s, up=%lus heap=%u (min %u)\n",
                (unsigned)cur.bootCount, diagResetName(_diagReason),
                (unsigned long)(millis() / 1000),
                (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
  if (_diagPrevValid) {
    Serial.printf("[diag] DIED IN: %s (entered at %lums, %lu loops)\n",
                  diagPhaseName(_diagPrev.phase),
                  (unsigned long)_diagPrev.phaseMs,
                  (unsigned long)_diagPrev.loops);
    Serial.printf("[diag] previous boot ran %lums and logged %u events:\n",
                  (unsigned long)_diagPrev.lastUptimeMs, _diagPrev.count);
    uint8_t start = (_diagPrev.head + DIAG_SLOTS - _diagPrev.count) % DIAG_SLOTS;
    for (uint8_t i = 0; i < _diagPrev.count; i++) {
      uint8_t s = (start + i) % DIAG_SLOTS;
      Serial.printf("[diag]   %8lums  %s\n",
                    (unsigned long)_diagPrev.evMs[s], _diagPrev.ev[s]);
    }
  } else {
    Serial.println("[diag] no surviving ring (clean power-on)");
  }
}
