#include "board_compat.h"
#include <Wire.h>
#include <sys/time.h>
#include <time.h>

M5Compat M5;

// ---- WS2812 ----
// Skip unchanged writes. main.cpp calls this every loop iteration (~17/s), and
// each call is a full RMT transaction bit-banging the WS2812 protocol — pure
// waste when the colour is identical, which it is the overwhelming majority of
// the time. Also keeps the RMT peripheral idle instead of continuously driven.
void ledSet(uint8_t r, uint8_t g, uint8_t b) {
  static uint8_t lr = 0xFF, lg = 0xFF, lb = 0xFF;
  if (r == lr && g == lg && b == lb) return;
  lr = r; lg = g; lb = b;
  rgbLedWrite(PIN_STATUS_LED, r, g, b);
}

// ---- RTC via system clock ----
// data.h pushes *local* time components (epoch already tz-adjusted), so we
// store local-as-UTC in the system clock and read back with gmtime_r.
static struct tm _pendTm = {};
void RtcCompat::GetTime(RTC_TimeTypeDef* t) {
  time_t now = time(nullptr);
  struct tm lt; gmtime_r(&now, &lt);
  t->Hours = lt.tm_hour; t->Minutes = lt.tm_min; t->Seconds = lt.tm_sec;
}
void RtcCompat::GetDate(RTC_DateTypeDef* d) {
  time_t now = time(nullptr);
  struct tm lt; gmtime_r(&now, &lt);
  d->WeekDay = lt.tm_wday; d->Month = lt.tm_mon + 1;
  d->Date = lt.tm_mday;    d->Year = lt.tm_year + 1900;
}
void RtcCompat::SetTime(RTC_TimeTypeDef* t) {
  _pendTm.tm_hour = t->Hours; _pendTm.tm_min = t->Minutes; _pendTm.tm_sec = t->Seconds;
}
void RtcCompat::SetDate(RTC_DateTypeDef* d) {
  // SetTime is always called first by data.h; commit on SetDate.
  _pendTm.tm_mday = d->Date; _pendTm.tm_mon = d->Month - 1;
  _pendTm.tm_year = d->Year - 1900; _pendTm.tm_isdst = 0;
  time_t epoch = mktime(&_pendTm);   // interprets as "UTC" since TZ unset
  struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
  settimeofday(&tv, nullptr);
}

// ---- backlight / power / battery ----
void AxpCompat::begin() {
  ledcAttach(PIN_BACKLIGHT, 5000, 8);
  _apply();
  analogSetPinAttenuation(PIN_BAT_ADC, ADC_11db);
}
void AxpCompat::_apply() {
  ledcWrite(PIN_BACKLIGHT, _on ? (uint32_t)_pct * 255 / 100 : 0);
}
void AxpCompat::ScreenBreath(uint8_t pct) { _pct = pct > 100 ? 100 : pct; _apply(); }
void AxpCompat::SetLDO2(bool on)          { _on = on; _apply(); }
float AxpCompat::GetBatVoltage() {
  return analogReadMilliVolts(PIN_BAT_ADC) * 2.0f / 1000.0f;
}
float AxpCompat::GetTempInAXP192() { return temperatureRead(); }
void AxpCompat::PowerOff() {
  SetLDO2(false);
  // Wake on touch INT (active low) so a tap revives the pet.
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_TOUCH_INT, 0);
  esp_deep_sleep_start();
}

// ---- touch buttons ----
void TouchButton::feed(bool down, uint32_t now) {
  _prev = _down;
  if (down && !_down) _downAt = now;
  _down = down;
}

// ---- FT6336G polling ----
// Registers: 0x02 TD_STATUS (low nibble = touch count),
// 0x03 P1_XH / 0x04 P1_XL / 0x05 P1_YH / 0x06 P1_YL.
bool M5Compat::readTouch(int* x, int* y) {
  Wire.beginTransmission(0x38);
  Wire.write(0x02);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(0x38, 5) != 5) return false;
  uint8_t n = Wire.read() & 0x0F;
  uint8_t xh = Wire.read(), xl = Wire.read(), yh = Wire.read(), yl = Wire.read();
  if (n == 0 || n > 2) return false;
  *x = ((xh & 0x0F) << 8) | xl;
  *y = ((yh & 0x0F) << 8) | yl;
  return true;
}

void M5Compat::begin() {
  // Enlarge the HWCDC RX ring before begin(). The default is small, and the
  // bridge's heartbeats (json + transcript entries, up to ~ENTRY budget x 8)
  // arrive as one burst at USB speed — the leading suspect for the recurring
  // one-way wedge where the board keeps sending but stops receiving until a
  // hardware reset. Must be called before begin() to take effect.
  Serial.setRxBufferSize(4096);
  Serial.begin(115200);   // USB CDC — M5.begin() used to do this
  // Touch controller reset before Wire so first poll sees a live chip
  pinMode(PIN_TOUCH_RST, OUTPUT);
  digitalWrite(PIN_TOUCH_RST, LOW);  delay(10);
  digitalWrite(PIN_TOUCH_RST, HIGH); delay(60);
  Wire.begin(PIN_TOUCH_SDA, PIN_TOUCH_SCL, 400000);

  Lcd.init();
  Lcd.setRotation(0);
  Lcd.fillScreen(TFT_BLACK);
  Axp.begin();
  ledSet(0, 0, 0);
}

// Touch zone map (portrait 240x320):
//   y >= 250  → button strip: left half = BtnA, right half = BtnB
//   y <  250  → pet area: tap = pet (heart), scrubbing = dizzy
void M5Compat::update() {
  uint32_t now = millis();
  int x, y;
  bool raw = readTouch(&x, &y);

  // Debounce the RELEASE edge only. readTouch() returns false for a momentary
  // TD_STATUS==0 or any I2C hiccup mid-press, which used to read as a release:
  // that fired a tap, then the next poll saw the finger again and started a
  // fresh press. One finger produced a storm of ~6 taps/second (observed in
  // the diag ring: 15 taps at exactly ~175ms apart). Requiring consecutive
  // empty polls before believing a release costs one frame of latency and
  // makes a single press produce a single tap.
  if (raw) {
    _upPolls = 0;
  } else if (_touchDown && _upPolls < TOUCH_UP_POLLS) {
    _upPolls++;
    raw = true;              // still held as far as the gesture layer knows
  }

  bool down = raw;
  if (down && (_upPolls == 0)) { _tx = x; _ty = y; }   // keep last good coords
  _touchDown = down;

  bool inStrip = down && _ty >= 250;
  M5.BtnA.feed(inStrip && _tx < 120, now);
  M5.BtnB.feed(inStrip && _tx >= 120, now);

  bool inPet = down && _ty < 250;
  if (inPet && !_petTouch) {              // touch-down in pet area
    _petTouch = true;
    _petDownMs = now; _petDownX = _tx; _petDownY = _ty;
    _lastX = _tx; _dir = 0; _reversals = 0; _swipeFired = false;
  } else if (inPet && _petTouch) {        // drag: watch for direction flips
    // A steady 600ms press becomes a hold (push-to-talk); once a hold is
    // active, scrub detection is off — finger wobble mid-dictation must
    // not fire dizzy.
    if (!_holdActive && now - _petDownMs >= 600
        && abs(_tx - _petDownX) < 20 && abs(_ty - _petDownY) < 20) {
      _holdActive = true;
      _evHoldStart = true;
    }
    if (!_holdActive) {
      // Downward swipe → Enter. Checked before scrub so a deliberate vertical
      // drag can't accumulate reversals and read as a scrub instead. Requires
      // the motion to be mostly vertical, so a diagonal flick during a scrub
      // doesn't fire it.
      int dyTot = _ty - _petDownY;
      int dxTot = abs(_tx - _petDownX);
      if (!_swipeFired && dyTot > 55 && dyTot > dxTot * 2) {
        _evSwipeDown = true;
        _swipeFired = true;      // latch: one Enter per press
        _reversals = 0;          // and it is definitively not a scrub
      }
      if (!_swipeFired) {
        int dx = _tx - _lastX;
        if (abs(dx) > 12) {
          int8_t d = dx > 0 ? 1 : -1;
          if (_dir != 0 && d != _dir) _reversals++;
          _dir = d;
          _lastX = _tx;
        }
        if (_reversals >= 3) { _evScrub = true; _petTouch = false; }
      }
    }
  } else if (!down && _petTouch) {        // release
    _petTouch = false;
    if (_holdActive) {
      _holdActive = false;
      _evHoldEnd = true;                  // hold consumed the press: no tap
    } else if (!_swipeFired) {
      uint32_t held = now - _petDownMs;
      if (held < 450 && abs(_tx - _petDownX) < 20 && abs(_ty - _petDownY) < 20)
        _evTap = true;
    }
  } else if (!inPet) {
    // Finger left the pet zone. A hold must NOT end here while the finger is
    // still down: touch coordinates drift, and a single sample landing at
    // y>=250 used to end a push-to-talk hold the instant it began — the board
    // reported "hold released after 0.0s", so VoiceFlow keyed down and up with
    // nothing between and recorded silence. Only a real release ends a hold.
    if (_holdActive) {
      if (!down) { _holdActive = false; _evHoldEnd = true; }
    } else {
      _petTouch = false;
    }
  }
}

bool M5Compat::petTapped()   { bool e = _evTap;   _evTap = false;   return e; }
bool M5Compat::petScrubbed() { bool e = _evScrub; _evScrub = false; return e; }
bool M5Compat::petHoldStarted() { bool e = _evHoldStart; _evHoldStart = false; return e; }
bool M5Compat::petHoldEnded()   { bool e = _evHoldEnd;   _evHoldEnd = false;   return e; }
bool M5Compat::petSwipedDown()  { bool e = _evSwipeDown; _evSwipeDown = false; return e; }
