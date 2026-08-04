#pragma once
// FNK0104B board compat layer — replaces <M5StickCPlus.h> for the
// Freenove ESP32-S3 Display 2.8" Touch (ILI9341 240x320, FT6336G touch,
// WS2812 on GPIO42, backlight GPIO45, battery ADC GPIO9).
//
// Provides just enough of the M5StickCPlus API surface that the buddy
// firmware compiles unchanged: M5.Lcd (a real TFT_eSPI), M5.BtnA/BtnB
// (driven by touch zones), M5.Imu (inert), M5.Rtc (system clock), M5.Axp
// (backlight PWM + battery ADC), M5.Beep (stub).
#include <Arduino.h>
#include <TFT_eSPI.h>
#include <FS.h>
// TFT_eSPI defines FS_NO_GLOBALS, which suppresses the global fs:: aliases
// that M5StickCPlus.h used to expose. Restore the one the buddy code uses.
using fs::File;
#include <esp_mac.h>   // esp_read_mac / ESP_MAC_BT (came via M5 lib before)
// M5's display lib exposed bare color names; map to TFT_eSPI's.
#ifndef GREEN
#define GREEN TFT_GREEN
#endif
#ifndef RED
#define RED TFT_RED
#endif

// ---- pins ----
constexpr int PIN_TOUCH_SDA = 16;
constexpr int PIN_TOUCH_SCL = 15;
constexpr int PIN_TOUCH_INT = 17;
constexpr int PIN_TOUCH_RST = 18;
constexpr int PIN_STATUS_LED   = 42;
constexpr int PIN_BACKLIGHT = 45;
constexpr int PIN_BAT_ADC   = 9;

// ---- RTC structs (M5-compatible field names) ----
struct RTC_TimeTypeDef { uint8_t Hours, Minutes, Seconds; };
struct RTC_DateTypeDef { uint8_t WeekDay, Month, Date; uint16_t Year; };

class RtcCompat {
 public:
  void GetTime(RTC_TimeTypeDef* t);
  void GetDate(RTC_DateTypeDef* d);
  void SetTime(RTC_TimeTypeDef* t);
  void SetDate(RTC_DateTypeDef* d);
};

class ImuCompat {
 public:
  void Init() {}
  // Board sits flat on a desk: report a steady upright 1g. Shake / face-down
  // / orientation logic all degrade to "never triggers".
  void getAccelData(float* ax, float* ay, float* az) { *ax = 0; *ay = 0; *az = 1.0f; }
};

class AxpCompat {
 public:
  void  begin();
  void  ScreenBreath(uint8_t pct);       // 0..100 → backlight PWM
  void  SetLDO2(bool on);                // backlight hard on/off
  float GetBatVoltage();                 // volts, via GPIO9 divider
  float GetBatCurrent() { return 0; }    // no coulomb counter on board
  float GetVBusVoltage() { return 5.0f; }// desk display: assume USB power
  uint8_t GetBtnPress() { return 0; }    // no AXP power button
  float GetTempInAXP192();
  void  PowerOff();                      // backlight off + deep sleep
 private:
  uint8_t _pct = 80;
  bool    _on  = true;
  void    _apply();
};

class BeepCompat {
 public:
  void begin() {}
  void update() {}
  void tone(uint16_t, uint16_t) {}       // ES8311 chirps: later phase
};

// M5-style button fed from touch zones each M5.update()
class TouchButton {
 public:
  void feed(bool down, uint32_t now);
  bool isPressed()  const { return _down; }
  bool wasPressed() const { return _down && !_prev; }
  bool wasReleased()const { return !_down && _prev; }
  bool pressedFor(uint32_t ms) const { return _down && (millis() - _downAt) >= ms; }
 private:
  bool _down = false, _prev = false;
  uint32_t _downAt = 0;
};

class M5Compat {
 public:
  TFT_eSPI   Lcd;
  RtcCompat  Rtc;
  ImuCompat  Imu;
  AxpCompat  Axp;
  BeepCompat Beep;
  TouchButton BtnA, BtnB;   // bottom-left / bottom-right touch zones

  void begin();
  void update();            // polls FT6336, updates buttons + gestures

  // Raw touch state (portrait coords, 240x320)
  bool touching() const { return _touchDown; }
  int  touchX()   const { return _tx; }
  int  touchY()   const { return _ty; }

  // One-shot gesture events in the pet area (consumed on read)
  bool petTapped();     // quick tap on the pet → heart
  bool petScrubbed();   // rapid left-right scrubbing → dizzy

 private:
  bool _touchDown = false;
  int  _tx = 0, _ty = 0;
  // gesture tracking
  bool     _petTouch = false;
  uint32_t _petDownMs = 0;
  int      _petDownX = 0, _petDownY = 0;
  int      _lastX = 0;
  int8_t   _dir = 0;
  uint8_t  _reversals = 0;
  bool     _evTap = false, _evScrub = false;
  bool readTouch(int* x, int* y);
};

extern M5Compat M5;

// WS2812 status LED (GRB on GPIO42)
void ledSet(uint8_t r, uint8_t g, uint8_t b);
