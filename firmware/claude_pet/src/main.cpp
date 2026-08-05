#include "board_compat.h"
#include <LittleFS.h>
#include <stdarg.h>
#include "ble_bridge.h"
#include "data.h"
#include "buddy.h"

TFT_eSprite spr = TFT_eSprite(&M5.Lcd);

// Advertise as "Claude-XXXX" (last two BT MAC bytes) so multiple sticks
// in one room are distinguishable in the desktop picker. Name persists in
// btName for the BLUETOOTH info page.
static char btName[16] = "Claude";
static void startBt() {
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_BT);
  snprintf(btName, sizeof(btName), "Claude-%02X%02X", mac[4], mac[5]);
  bleInit(btName);
}

#include "character.h"
#include "stats.h"
const int W = 240, H = 320;
const int CX = W / 2;
const int CY_BASE = 160;

// Colors used across multiple UI surfaces
const uint16_t HOT   = 0xFA20;   // red-orange: warnings, impatience, deny
const uint16_t PANEL = 0x2104;   // overlay panel background

enum PersonaState { P_SLEEP, P_IDLE, P_BUSY, P_ATTENTION, P_CELEBRATE, P_DIZZY, P_HEART };
const char* stateNames[] = { "sleep", "idle", "busy", "attention", "celebrate", "dizzy", "heart" };

TamaState    tama;
PersonaState baseState   = P_SLEEP;
PersonaState activeState = P_SLEEP;
uint32_t     oneShotUntil = 0;
uint32_t     lastShakeCheck = 0;
float        accelBaseline = 1.0f;
unsigned long t = 0;

uint8_t brightLevel = 4;           // 0..4 → ScreenBreath 20..100

uint8_t msgScroll = 0;
uint16_t lastLineGen = 0;
char     lastPromptId[40] = "";
uint32_t lastInteractMs = 0;
bool     dimmed = false;
bool     screenOff = false;
bool     swallowBtnB = false;
bool     buddyMode = false;
bool     gifAvailable = false;
const uint8_t SPECIES_GIF = 0xFF;   // species NVS sentinel: use the installed GIF
uint32_t wakeTransitionUntil = 0;
const uint32_t SCREEN_OFF_MS = 30000;

bool     napping = false;
uint32_t napStartMs = 0;
uint32_t promptArrivedMs = 0;

// Face-down = Z-axis dominant and negative. Debounced so a toss doesn't count.
static bool isFaceDown() {
  float ax, ay, az;
  M5.Imu.getAccelData(&ax, &ay, &az);
  return az < -0.7f && fabsf(ax) < 0.4f && fabsf(ay) < 0.4f;
}

static void applyBrightness() { M5.Axp.ScreenBreath(20 + brightLevel * 20); }

static void wake() {
  lastInteractMs = millis();
  if (screenOff) {
    M5.Axp.SetLDO2(true);
    applyBrightness();
    screenOff = false;
    wakeTransitionUntil = millis() + 12000;
  }
  if (dimmed) { applyBrightness(); dimmed = false; }
}
bool     responseSent = false;

static void beep(uint16_t freq, uint16_t dur) {
  if (settings().sound) M5.Beep.tone(freq, dur);
}

static void sendCmd(const char* json) {
  Serial.println(json);
  size_t n = strlen(json);
  bleWrite((const uint8_t*)json, n);
  bleWrite((const uint8_t*)"\n", 1);
}

// Full repaint: clear the sprite and let the character/buddy redraw from
// scratch on the next tick. Used when an overlay (card, passkey) goes away.
static void repaintAll() {
  spr.fillSprite(0x0000);
  characterInvalidate();
}

// The on-device menu (and its settings/reset panels) is gone — customization
// lives host-side in the CLI (`cc-buddy-bridge species`, matchers.toml), and
// a 2.8" panel poked with a fingertip was a bad settings UI that suspended
// the gestures that are the point of the device. Settings persist in NVS and
// keep whatever values they last had; the structs stay for the render paths
// that read them.

// Clock orientation: gravity along the in-plane X axis means the stick is
// on its side. Signed counter for hysteresis on both transitions — same
// pattern as face-down nap.
//   0 = portrait (sprite path, pet sleeps underneath)
//   1 = landscape, BtnA-side down (M5.Lcd rotation 1)
//   3 = landscape, USB-side down (M5.Lcd rotation 3)
static uint8_t clockOrient   = 0;
static int8_t  orientFrames  = 0;
static uint8_t paintedOrient = 0;
// RTC and IMU share an I2C bus. Reading the RTC at 60fps starves the IMU
// reads in clockUpdateOrient — orientation detection gets noisy. Cache the
// time once per second; mood logic and drawClock both read from here.
static RTC_TimeTypeDef _clkTm;
static RTC_DateTypeDef _clkDt;
uint32_t               _clkLastRead = 0;   // zeroed by data.h on time-sync
static bool            _onUsb       = false;
static void clockRefreshRtc() {
  if (millis() - _clkLastRead < 1000) return;
  _clkLastRead = millis();
  _onUsb = M5.Axp.GetVBusVoltage() > 4.0f;
  M5.Rtc.GetTime(&_clkTm);
  M5.Rtc.GetDate(&_clkDt);
}

static void clockUpdateOrient() {
  float ax, ay, az;
  M5.Imu.getAccelData(&ax, &ay, &az);
  uint8_t lock = settings().clockRot;
  if (lock == 1) { clockOrient = 0; return; }
  if (lock == 2) {
    // Locked landscape: never drop to 0, but still pick 1 vs 3 from
    // gravity so the cradle works either way up. Need a strong tilt
    // for the 1↔3 swap so handling jitter doesn't flip it; otherwise
    // hold whatever we last had (or 1 from boot).
    if (clockOrient == 0) clockOrient = (ax >= 0) ? 1 : 3;
    if      (ax >  0.5f && clockOrient != 1) clockOrient = 1;
    else if (ax < -0.5f && clockOrient != 3) clockOrient = 3;
    return;
  }
  // Dual threshold: strict to enter (must be clearly sideways), loose to
  // stay (tolerate ~65° of tilt). With one shared threshold a slight lean
  // while sitting on the long edge puts ax right at the boundary and the
  // counter ratchets down in ~half a second.
  bool side = (clockOrient == 0)
    ? fabsf(ax) > 0.7f && fabsf(ay) < 0.5f && fabsf(az) < 0.5f
    : fabsf(ax) > 0.4f;
  if (side) { if (orientFrames < 20) orientFrames++; }
  else      { if (orientFrames > -10) orientFrames--; }
  if (clockOrient == 0 && orientFrames >= 15) {
    clockOrient = (ax > 0) ? 1 : 3;
  } else if (clockOrient != 0 && orientFrames <= -8) {
    clockOrient = 0;
  } else if (clockOrient != 0 && side) {
    // Direct 1↔3: a fast flip keeps |ax|>0.7 (just changes sign), so
    // `side` never drops and the exit-via-0 path can't fire. Watch for
    // ax sign disagreeing with the stored orientation.
    static int8_t swapFrames = 0;
    uint8_t want = (ax > 0) ? 1 : 3;
    if (want != clockOrient) { if (++swapFrames >= 8) { clockOrient = want; swapFrames = 0; } }
    else swapFrames = 0;
  }
}

// Clock face: shown when charging on USB with nothing else going on.
// Portrait paints the upper ~110px to the sprite; pet renders below.
// Landscape draws direct to LCD with rotation — sprite stays untouched.
static const char* const MON[] = {
  "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"
};
static const char* const DOW[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};

static uint8_t clockDow() { return _clkDt.WeekDay % 7; }

// 12-hour face. The hour is space-padded rather than zero-padded so the
// string keeps a constant width: both faces centre the time with MC_DATUM
// and only repaint their own glyph cells, so a 5→4 char shrink (12:59 → 1:00)
// would strand pixels. A leading space is drawn as a background-filled cell,
// which clears them. Mood logic below still reads the raw 24h _clkTm.Hours.
static uint8_t clockHour12() {
  uint8_t h = _clkTm.Hours % 12;
  return h == 0 ? 12 : h;
}
static const char* clockMeridiem() { return _clkTm.Hours < 12 ? "AM" : "PM"; }

// Small always-on clock, top-left of the normal pet screen. Drawn LAST each
// frame (just before pushSprite) because the buddy strip clears y<126 full
// width — anything painted earlier in the corner is erased. Overlap with the
// pet's overlay particles (hearts/Zzz) is possible and harmless; the clock
// wins for one frame and the particles are transient.
static void drawMiniClock() {
  if (!dataRtcValid()) return;
  const Palette& p = characterPalette();
  char mc[12];
  snprintf(mc, sizeof(mc), "%u:%02u %s", clockHour12(), _clkTm.Minutes, clockMeridiem());
  spr.setTextSize(2);
  spr.setTextColor(p.textDim, p.bg);
  spr.setCursor(4, 4);
  spr.print(mc);
  spr.setTextSize(1);
}

static void drawClock() {
  const Palette& p = characterPalette();
  char hm[6]; snprintf(hm, sizeof(hm), "%2u:%02u", clockHour12(), _clkTm.Minutes);
  char ss[8]; snprintf(ss, sizeof(ss), ":%02u %s", _clkTm.Seconds, clockMeridiem());
  uint8_t mi = (_clkDt.Month >= 1 && _clkDt.Month <= 12) ? _clkDt.Month - 1 : 0;
  char dl[8]; snprintf(dl, sizeof(dl), "%s %02u", MON[mi], _clkDt.Date);

  if (clockOrient == 0) {
    paintedOrient = 0;
    // Bottom half — buddy naturally lives at y=0..82, GIF peeks at top
    // via peek mode. Clearing from 90 leaves both untouched.
    spr.fillRect(0, 90, W, H - 90, p.bg);
    spr.setTextDatum(MC_DATUM);
    spr.setTextSize(4); spr.setTextColor(p.text, p.bg);    spr.drawString(hm, CX, 140);
    spr.setTextSize(2); spr.setTextColor(p.textDim, p.bg); spr.drawString(ss, CX, 175);
    spr.setTextSize(1);                                     spr.drawString(dl, CX, 200);
    spr.setTextDatum(TL_DATUM);
    return;
  }

  // Landscape: 240×135 direct-to-LCD. Full fill only on entry; after that
  // text glyph bg cells repaint themselves and the pet box (small, ~90×50)
  // gets a fillRect each pet tick — small enough not to tear.
  M5.Lcd.setRotation(clockOrient);
  static uint8_t lastSec = 0xFF;
  bool repaint = paintedOrient != clockOrient;
  if (repaint) { M5.Lcd.fillScreen(p.bg); paintedOrient = clockOrient; lastSec = 0xFF; }

  // Seconds tick at 1Hz; redrawing 3 strings at 60fps is 180 SPI ops/sec
  // for nothing. Gate on the second changing (or full repaint).
  if (repaint || _clkTm.Seconds != lastSec) {
    lastSec = _clkTm.Seconds;
    char wdl[12]; snprintf(wdl, sizeof(wdl), "%s %s %02u", DOW[clockDow()], MON[mi], _clkDt.Date);
    char ssl[7]; snprintf(ssl, sizeof(ssl), "%02u %s", _clkTm.Seconds, clockMeridiem());
    M5.Lcd.setTextDatum(MC_DATUM);
    M5.Lcd.setTextSize(3); M5.Lcd.setTextColor(p.text, p.bg);    M5.Lcd.drawString(hm, 170, 42);
    M5.Lcd.setTextSize(2); M5.Lcd.setTextColor(p.textDim, p.bg); M5.Lcd.drawString(ssl, 170, 72);
                                                                  M5.Lcd.drawString(wdl, 170, 102);
    M5.Lcd.setTextDatum(TL_DATUM);
    M5.Lcd.setTextSize(1);
  }

  // Pet on left at 5 fps. Clear includes the overlay-particle zone above
  // the body (y<30) — species draw Zzz/hearts there via BUDDY_Y_OVERLAY=6
  // which doesn't go through _yb, so the box has to cover it.
  static uint32_t lastPetTick = 0;
  if (millis() - lastPetTick >= 200) {
    lastPetTick = millis();
    if (buddyMode) {
      // ASCII glyphs don't self-clear; wipe the box each tick. Species
      // hardcode BUDDY_X_CENTER=67 / BUDDY_Y_OVERLAY=6 for particles so
      // keep portrait coords and just swap the surface — pet lands
      // upper-left of landscape, which is where we want it anyway.
      M5.Lcd.fillRect(0, 0, 115, 90, p.bg);
      buddyRenderTo(&M5.Lcd, activeState);
    } else {
      // Full-frame GIFs paint every pixel (transparent → pal.bg), so a
      // per-tick clear just adds a visible black flash between wipe and
      // last scanline. The entry fillScreen on paintedOrient change
      // already covers the surround.
      characterSetState(activeState);
      characterRenderTo(&M5.Lcd, 57, 45);
    }
  }
  M5.Lcd.setRotation(0);
}

PersonaState derive(const TamaState& s) {
  if (!s.connected)            return P_IDLE;
  if (s.sessionsWaiting > 0)   return P_ATTENTION;
  if (s.recentlyCompleted)     return P_CELEBRATE;
  if (s.sessionsRunning >= 3)  return P_BUSY;
  return P_IDLE;   // connected, 0+ sessions, nothing urgent — hang out
}

void triggerOneShot(PersonaState s, uint32_t durMs) {
  activeState = s;
  oneShotUntil = millis() + durMs;
}

bool checkShake() {
  float ax, ay, az;
  M5.Imu.getAccelData(&ax, &ay, &az);
  float mag = sqrtf(ax*ax + ay*ay + az*az);
  float delta = fabsf(mag - accelBaseline);
  accelBaseline = accelBaseline * 0.95f + mag * 0.05f;
  return delta > 0.8f;
}




void drawPasskey() {
  const Palette& p = characterPalette();
  spr.fillSprite(p.bg);
  spr.setTextSize(1);
  spr.setTextColor(p.textDim, p.bg);
  spr.setCursor(8, 56);  spr.print("BLUETOOTH PAIRING");
  spr.setCursor(8, 184); spr.print("enter on desktop:");
  spr.setTextSize(3);
  spr.setTextColor(p.text, p.bg);
  char b[8]; snprintf(b, sizeof(b), "%06lu", (unsigned long)blePasskey());
  spr.setCursor((W - 18 * 6) / 2, 110);
  spr.print(b);
}

// Greedy word-wrap into fixed-width rows. Continuation rows get a leading
// space. Returns number of rows written.
static uint8_t wrapInto(const char* in, char out[][40], uint8_t maxRows, uint8_t width) {
  uint8_t row = 0, col = 0;
  const char* p = in;
  while (*p && row < maxRows) {
    while (*p == ' ') p++;                     // skip leading spaces
    // measure next word
    const char* w = p;
    while (*p && *p != ' ') p++;
    uint8_t wlen = p - w;
    if (wlen == 0) break;
    uint8_t need = (col > 0 ? 1 : 0) + wlen;
    if (col + need > width) {
      out[row][col] = 0;
      if (++row >= maxRows) return row;
      out[row][0] = ' '; col = 1;              // continuation indent
    }
    if (col > 1 || (col == 1 && out[row][0] != ' ')) out[row][col++] = ' ';
    else if (col == 1 && row > 0) {}           // already have the indent space
    // hard-break words that still don't fit
    while (wlen > width - col) {
      uint8_t take = width - col;
      memcpy(&out[row][col], w, take); col += take; w += take; wlen -= take;
      out[row][col] = 0;
      if (++row >= maxRows) return row;
      out[row][0] = ' '; col = 1;
    }
    memcpy(&out[row][col], w, wlen); col += wlen;
  }
  if (col > 0 && row < maxRows) { out[row][col] = 0; row++; }
  return row;
}

// ---- swipe-to-decide card ----
// The pending permission renders on a card you swipe like a dating app:
// drag right = approve, drag left = deny. The card follows the finger
// with a tilt (pushRotated via a second sprite), an APPROVE/DENY stamp
// appears past the commit threshold, and release either flies the card
// off-screen or springs it back to center.
//
// Geometry: the band (y >= CARD_BAND_Y) is cleared every frame while a
// prompt is up — same self-clearing model the old panel used, just taller.
// A 210x80 card at ±9° has a rotated half-height of ~56px, so centering
// it at y=262 keeps every rotated pixel inside the 204..320 band.
enum CardPhase : uint8_t { CARD_REST, CARD_DRAG, CARD_SNAP, CARD_FLY };
static CardPhase   cardPhase = CARD_REST;
static TFT_eSprite cardSpr(&M5.Lcd);
static float cardX = 0, cardVel = 0;
static int   cardGrabX = 0;
static int   cardGrabY = 0;   // for the swipe-up focus gesture
static bool  cardTouchPrev = false;
static bool  cardArmed = false;        // past-threshold beep latch
const int    CARD_W = 210, CARD_H = 80;
const int    CARD_BAND_Y = 204;
const int    CARD_CX = 120, CARD_CY = 262;
const float  CARD_COMMIT = 60.0f;      // px of drag that commits a decision

// Horizontal commit ratio for border/stamp. (An up-swipe approve existed
// briefly and was removed at the owner's request — right is enough.)
static float cardRatio() {
  float r = cardX / CARD_COMMIT;
  if (r > 1) r = 1; if (r < -1) r = -1;
  return r;
}

static void sendDecision(bool approve) {
  char cmd[96];
  snprintf(cmd, sizeof(cmd), "{\"cmd\":\"permission\",\"id\":\"%s\",\"decision\":\"%s\"}",
           tama.promptId, approve ? "once" : "deny");
  diagLog("decision %s", approve ? "approve" : "deny");
  sendCmd(cmd);
  responseSent = true;
  if (approve) {
    uint32_t tookS = (millis() - promptArrivedMs) / 1000;
    statsOnApproval(tookS);
    beep(2400, 60);
    if (tookS < 5) triggerOneShot(P_HEART, 2000);
  } else {
    statsOnDenial();
    beep(600, 60);
  }
}

// Card face, drawn at (ox,oy) on any surface — cardSpr normally, spr
// directly when the card sprite failed to allocate (no tilt then).
static void drawCardFace(TFT_eSPI* g, int ox, int oy, const Palette& p) {
  float r = cardRatio();
  uint16_t edge = (r > 0.3f) ? GREEN : (r < -0.3f) ? HOT : p.textDim;
  g->fillRoundRect(ox, oy, CARD_W, CARD_H, 8, PANEL);
  g->drawRoundRect(ox, oy, CARD_W, CARD_H, 8, edge);
  if (fabsf(r) >= 1.0f) g->drawRoundRect(ox + 1, oy + 1, CARD_W - 2, CARD_H - 2, 7, edge);

  // Size 2 only if the tool name fits one line (~16 chars at 12px)
  int toolLen = strlen(tama.promptTool);
  g->setTextColor(p.text, PANEL);
  g->setTextSize(toolLen <= 16 ? 2 : 1);
  g->setCursor(ox + 10, oy + 8);
  g->print(tama.promptTool);
  g->setTextSize(1);
  // Which session is asking — cwd basename, top-right. With several
  // concurrent sessions the cards queue, and this is how you tell them apart.
  if (tama.promptSess[0]) {
    int sw = (int)strlen(tama.promptSess) * 6;
    g->setTextColor(p.textDim, PANEL);
    g->setCursor(ox + CARD_W - 10 - sw, oy + 6);
    g->print(tama.promptSess);
  }

  // Hint wraps at 31 chars to two lines under the tool name
  g->setTextColor(p.textDim, PANEL);
  int hlen = strlen(tama.promptHint);
  g->setCursor(ox + 10, oy + 30);
  g->printf("%.31s", tama.promptHint);
  if (hlen > 31) {
    g->setCursor(ox + 10, oy + 40);
    g->printf("%.31s", tama.promptHint + 31);
  }

  // Bottom row: swipe affordances + wait timer in the middle
  g->setTextColor(HOT, PANEL);
  g->setCursor(ox + 10, oy + CARD_H - 14);
  g->print("< deny");
  g->setTextColor(GREEN, PANEL);
  g->setCursor(ox + CARD_W - 10 - 9 * 6, oy + CARD_H - 14);
  g->print("approve >");
  uint32_t waited = (millis() - promptArrivedMs) / 1000;
  char wb[8]; snprintf(wb, sizeof(wb), "%lus", (unsigned long)waited);
  g->setTextColor(waited >= 10 ? HOT : p.textDim, PANEL);
  g->setCursor(ox + (CARD_W - (int)strlen(wb) * 6) / 2, oy + CARD_H - 14);
  g->print(wb);

  // Stamp fades in past ~30% of the commit distance
  if (fabsf(r) > 0.3f) {
    bool ok = r > 0;
    const char* s = ok ? "APPROVE" : "DENY";
    uint16_t c = ok ? GREEN : HOT;
    int tw = (int)strlen(s) * 12;
    int sx = ox + (CARD_W - tw) / 2, sy = oy + CARD_H / 2 - 8;
    g->setTextSize(2);
    g->setTextColor(c, PANEL);
    g->setCursor(sx, sy);
    g->print(s);
    g->drawRoundRect(sx - 6, sy - 5, tw + 12, 26, 4, c);
    g->setTextSize(1);
  }
}

static void drawApproval() {
  const Palette& p = characterPalette();
  spr.fillRect(0, CARD_BAND_Y, W, H - CARD_BAND_Y, p.bg);
  spr.drawFastHLine(0, CARD_BAND_Y, W, p.textDim);

  if (responseSent && cardPhase != CARD_FLY) {
    spr.setTextSize(1);
    spr.setTextColor(p.textDim, p.bg);
    spr.setCursor(4, H - 12);
    spr.print("sent...");
    return;
  }

  int16_t ang = (int16_t)(cardX * 0.075f);
  if (ang > 9) ang = 9; if (ang < -9) ang = -9;

  spr.setTextSize(1);
  if (cardSpr.created()) {
    cardSpr.fillSprite(TFT_TRANSPARENT);
    drawCardFace(&cardSpr, 0, 0, p);
    cardSpr.setPivot(CARD_W / 2, CARD_H / 2);
    spr.setPivot(CARD_CX + (int)cardX, CARD_CY);
    cardSpr.pushRotated(&spr, ang, TFT_TRANSPARENT);
  } else {
    drawCardFace(&spr, CARD_CX + (int)cardX - CARD_W / 2, CARD_CY - CARD_H / 2, p);
  }
}

void drawHUD() {
  if (tama.promptId[0]) { drawApproval(); return; }
  const Palette& p = characterPalette();
  const int SHOW = 3, LH = 8, WIDTH = 38;
  const int AREA = SHOW * LH + 4;
  spr.fillRect(0, H - AREA, W, AREA, p.bg);
  spr.setTextSize(1);

  if (tama.lineGen != lastLineGen) { msgScroll = 0; lastLineGen = tama.lineGen; wake(); }

  if (tama.nLines == 0) {
    spr.setTextColor(p.text, p.bg);
    spr.setCursor(4, H - LH - 2);
    spr.print(tama.msg);
    return;
  }

  // Wrap all transcript lines into a flat display buffer. Track which
  // transcript index each display row came from, so we can dim older ones.
  static char disp[32][40];
  static uint8_t srcOf[32];
  uint8_t nDisp = 0;
  for (uint8_t i = 0; i < tama.nLines && nDisp < 32; i++) {
    uint8_t got = wrapInto(tama.lines[i], &disp[nDisp], 32 - nDisp, WIDTH);
    for (uint8_t j = 0; j < got; j++) srcOf[nDisp + j] = i;
    nDisp += got;
  }

  uint8_t maxBack = (nDisp > SHOW) ? (nDisp - SHOW) : 0;
  if (msgScroll > maxBack) msgScroll = maxBack;

  int end = (int)nDisp - msgScroll;
  int start = end - SHOW; if (start < 0) start = 0;
  uint8_t newest = tama.nLines - 1;
  for (int i = 0; start + i < end; i++) {
    uint8_t row = start + i;
    bool fresh = (srcOf[row] == newest) && (msgScroll == 0);
    spr.setTextColor(fresh ? p.text : p.textDim, p.bg);
    spr.setCursor(4, H - AREA + 2 + i * LH);
    spr.print(disp[row]);
  }
  if (msgScroll > 0) {
    spr.setTextColor(p.body, p.bg);
    spr.setCursor(W - 18, H - LH - 2);
    spr.printf("-%u", msgScroll);
  }
}

void setup() {
  // FIRST: capture the reset reason and the pre-reset event ring before any
  // init can crash. Everything after this point is diagnosable.
  diagInit();
  M5.begin();
  delay(2000);                       // let the host attach before first prints
  diagReport("boot");                // why the last run ended + what it was doing
  Serial.println("[boot] M5.begin done");
  M5.Lcd.setRotation(0);
  M5.Imu.Init();
  M5.Beep.begin();
  startBt();
  applyBrightness();
  Serial.println("[boot] bt+brightness done");
  lastInteractMs = millis();
  statsLoad();
  settingsLoad();
  petNameLoad();
  buddyInit();

  // BLE stays always-on; s.bt is stored as a preference only.
  spr.createSprite(W, H);
  Serial.printf("[boot] sprite created=%d psram=%d heap=%u\n",
                (int)spr.created(), (int)psramFound(), ESP.getFreeHeap());
  characterInit(nullptr);  // scan /characters/ for whatever is installed
  Serial.println("[boot] characterInit done");
  gifAvailable = characterLoaded();
  // species NVS: 0..N-1 = ASCII species, 0xFF = use GIF (also the default,
  // so a fresh install lands on the GIF). With no GIF installed, 0xFF falls
  // through to buddyInit()'s clamped default.
  buddyMode = !(gifAvailable && speciesIdxLoad() == SPECIES_GIF);
  repaintAll();

  {
    const Palette& p = characterPalette();
    spr.fillSprite(p.bg);
    spr.setTextDatum(MC_DATUM);
    spr.setTextSize(2);
    if (ownerName()[0]) {
      char line[40];
      snprintf(line, sizeof(line), "%s's", ownerName());
      spr.setTextColor(p.text, p.bg);   spr.drawString(line, W/2, H/2 - 12);
      spr.setTextColor(p.body, p.bg);   spr.drawString(petName(), W/2, H/2 + 12);
    } else {
      // First boot, no owner pushed yet — say hi.
      spr.setTextColor(p.body, p.bg);   spr.drawString("Hello!", W/2, H/2 - 12);
      spr.setTextSize(1);
      spr.setTextColor(p.textDim, p.bg);
      spr.drawString("a buddy appears", W/2, H/2 + 12);
    }
    spr.setTextDatum(TL_DATUM); spr.setTextSize(1);
    spr.pushSprite(0, 0);
    delay(1800);
  }

  Serial.printf("buddy: %s\n", buddyMode ? "ASCII mode" : "GIF character loaded");
  diagLog("setup done buddy=%d", (int)buddyMode);
  // Arm last: the splash delays above would trip it.
  diagWatchdogBegin();
}

void loop() {
  diagWatchdogFeed();   // a hang past DIAG_WDT_SECS now resets + reports
  static uint32_t _loopStart = 0;
  if (_loopStart) diagLoopEnd(millis() - _loopStart);
  _loopStart = millis();
  diagLoopTick();
  diagPhase(DP_TOUCH);
  M5.update();
  M5.Beep.update();
  t++;
  uint32_t now = millis();
  static uint32_t aliveAt = 0;
  if ((int32_t)(now - aliveAt) >= 0) {
    aliveAt = now + 5000;
    Serial.printf("[alive] up=%lus heap=%u state=%s\n",
                  now / 1000, ESP.getFreeHeap(), stateNames[activeState]);
  }

  diagPhase(DP_DATA);
  dataPoll(&tama);
  diagPhase(DP_STATS);
  if (statsPollLevelUp()) triggerOneShot(P_CELEBRATE, 3000);
  baseState = derive(tama);

  // After waking the screen, hold sleep for 12s so users see the wake-up
  // animation. Urgent states (attention, celebrate, busy) override this.
  if (baseState == P_IDLE && (int32_t)(now - wakeTransitionUntil) < 0) baseState = P_SLEEP;

  if ((int32_t)(now - oneShotUntil) >= 0) activeState = baseState;

  diagPhase(DP_LED);
  // WS2812: pulse orange on attention, pink on heart, green on celebrate.
  // Solid blue overrides everything while a push-to-talk hold is active —
  // it is the "mic is live" indicator.
  if (settings().led && M5.petHeldNow()) {
    ledSet(0, 30, 90);
  } else if (settings().led && activeState == P_ATTENTION) {
    bool on = (now / 400) % 2;
    ledSet(on ? 255 : 0, on ? 80 : 0, 0);
  } else if (settings().led && activeState == P_HEART) {
    ledSet(60, 0, 20);
  } else if (settings().led && activeState == P_CELEBRATE) {
    ledSet(0, 60, 10);
  } else {
    ledSet(0, 0, 0);
  }

  diagPhase(DP_GESTURE);
  // touch gestures on the pet: tap = pet it (heart), scrub = dizzy,
  // press-and-hold = push-to-talk (the bridge holds Opt+Space for
  // VoiceFlow while the finger is down).
  // Drained during a prompt — the swipe card's band overlaps the pet
  // zone, and a card drag must not read as a scrub.
  static bool voiceHold = false;
  bool gesturesLive = !screenOff && !tama.promptId[0];
  if (gesturesLive) {
    if (M5.petHoldStarted()) {
      wake();
      voiceHold = true;
      diagLog("voice start");
      sendCmd("{\"cmd\":\"voice\",\"state\":\"start\"}");
      beep(1200, 30);
    }
    if (M5.petSwipedDown()) {
      wake();
      diagLog("swipe down -> enter");
      sendCmd("{\"cmd\":\"key\",\"name\":\"enter\"}");
      beep(2000, 40);
    }
    // Tap-heart and scrub-dizzy are gone (owner request: touching the pet is
    // functional only — hold to dictate, swipe down for Enter; the pet's
    // moods come from Claude's state, not from being poked). The events are
    // still drained so the compat-layer state machine can't latch.
    if (M5.petTapped()) wake();
    M5.petScrubbed();
  } else {
    // drain while overlays open, so a stale gesture can't fire later
    M5.petTapped(); M5.petScrubbed(); M5.petHoldStarted(); M5.petSwipedDown();
  }
  // Stop is handled OUTSIDE the overlay gate: if a prompt or menu opens
  // mid-hold, the release must still end the dictation — a swallowed stop
  // means the bridge holds Opt+Space until its own watchdog fires.
  if (M5.petHoldEnded() && voiceHold) {
    voiceHold = false;
    diagLog("voice stop");
    sendCmd("{\"cmd\":\"voice\",\"state\":\"stop\"}");
    beep(900, 30);
  }

  // BtnA: step through fake scenarios
  diagPhase(DP_PROMPT);
  // Prompt arrival: beep, reset response flag
  if (strcmp(tama.promptId, lastPromptId) != 0) {
    strncpy(lastPromptId, tama.promptId, sizeof(lastPromptId)-1);
    lastPromptId[sizeof(lastPromptId)-1] = 0;
    responseSent = false;
    if (tama.promptId[0]) {
      diagLog("prompt %.20s", tama.promptTool);
      promptArrivedMs = millis();
      wake();
      beep(1200, 80);   // alert chirp
      // Repaint so the card band starts from a clean frame.
      repaintAll();
      characterInvalidate();
      if (buddyMode) buddyInvalidate();
      cardPhase = CARD_REST;
      cardX = 0; cardVel = 0; cardArmed = false;
      if (!cardSpr.created()) cardSpr.createSprite(CARD_W, CARD_H);
    } else {
      // Prompt resolved — free the card sprite and repaint everything so
      // the card band doesn't leave stale rows under the transcript HUD.
      if (cardSpr.created()) cardSpr.deleteSprite();
      cardPhase = CARD_REST;
      cardX = 0; cardVel = 0;
      repaintAll();
      if (buddyMode) buddyInvalidate();
    }
  }

  bool inPrompt = tama.promptId[0] && !responseSent;

  // Swipe card: raw-touch drag + release physics. Runs the whole time a
  // prompt is on screen; decisions commit on release past CARD_COMMIT px
  // (or a fast flick), then the card flies off while "sent" follows.
  if (tama.promptId[0]) {
    bool tdown = M5.touching();
    if (!responseSent) {
      if ((cardPhase == CARD_REST || cardPhase == CARD_SNAP)
          && tdown && !cardTouchPrev && M5.touchY() >= CARD_BAND_Y) {
        cardPhase = CARD_DRAG;
        cardGrabX = M5.touchX() - (int)cardX;   // grab mid-snap without a jump
        cardGrabY = M5.touchY();
        cardVel = 0;
        wake();
      }
      if (cardPhase == CARD_DRAG) {
        if (tdown) {
          float nx = (float)(M5.touchX() - cardGrabX);
          cardVel = nx - cardX;
          cardX = nx;
          bool past = fabsf(cardRatio()) >= 1.0f;
          if (past && !cardArmed) beep(1800, 20);
          cardArmed = past;
          lastInteractMs = millis();
        } else {                               // release
          // Swipe UP = "show me": raise that session's terminal on the Mac.
          // NOT a decision — the card snaps back and stays pending. Only
          // counts when the drag was clearly vertical, so a sloppy
          // approve/deny swipe can't turn into a window switch.
          // 35px, not 50: the drag must START inside the 116px-tall card
          // band, so a 50px upward flick left almost no room and the gesture
          // kept failing in practice. 35px is still well clear of a jittery
          // tap, and the sideways gate keeps it distinct from approve/deny.
          int dyUp = cardGrabY - M5.touchY();
          if (dyUp > 35 && fabsf(cardX) < CARD_COMMIT * 0.6f) {
            char fc[80];
            snprintf(fc, sizeof(fc), "{\"cmd\":\"focus\",\"id\":\"%s\"}", tama.promptId);
            sendCmd(fc);
            diagLog("card focus swipe");
            beep(1500, 25);
            cardPhase = CARD_SNAP;
            cardArmed = false;
          } else {
          bool commit = fabsf(cardX) > CARD_COMMIT || fabsf(cardVel) > 10.0f;
          if (commit) {
            float m = fabsf(cardX) > 2.0f ? cardX : cardVel;
            bool approve = m > 0;
            sendDecision(approve);
            cardPhase = CARD_FLY;
            float dir = approve ? 1.0f : -1.0f;
            cardVel = fmaxf(fabsf(cardVel), 16.0f) * dir;
          } else {
            cardPhase = CARD_SNAP;
          }
          cardArmed = false;
          }
        }
      }
    }
    if (cardPhase == CARD_SNAP) {              // spring back to center
      cardX *= 0.65f;
      if (fabsf(cardX) < 1.5f) { cardX = 0; cardVel = 0; cardPhase = CARD_REST; }
    } else if (cardPhase == CARD_FLY) {        // accelerate off-screen
      cardVel *= 1.12f;
      cardX += cardVel;
      if (fabsf(cardX) > (float)(W / 2 + CARD_W)) {
        cardX = 0; cardVel = 0; cardPhase = CARD_REST;
      }
    }
    cardTouchPrev = tdown;
  } else {
    cardTouchPrev = false;
  }

  // Button-press wake. BtnB's press is swallowed when it wakes the screen
  // so the same tap doesn't also scroll the transcript. BtnA is wake-only:
  // the screens/menu it used to cycle are gone (owner request — bottom-left
  // does nothing but wake).
  if (M5.BtnA.isPressed() || M5.BtnB.isPressed()) {
    if (screenOff && M5.BtnB.isPressed()) swallowBtnB = true;
    wake();
  }

  // AXP power button (left side): short-press toggles screen off.
  // Long-press (6s) still powers off the device via AXP hardware.
  if (M5.Axp.GetBtnPress() == 0x02) {
    if (screenOff) {
      wake();
    } else {
      M5.Axp.SetLDO2(false);
      screenOff = true;
    }
  }

  // BtnB (bottom-right): scroll back through the transcript HUD.
  if (M5.BtnB.wasPressed()) {
    if (swallowBtnB) { swallowBtnB = false; }
    else if (tama.promptId[0]) {
      // swipe card owns decisions — swallow strip touches during a prompt
    } else {
      beep(2400, 30);
      msgScroll = (msgScroll >= 30) ? 0 : msgScroll + 1;
    }
  }

  // blink bookkeeping

  diagPhase(DP_CLOCK);
  clockRefreshRtc();   // 1Hz internal throttle; also caches _onUsb
  // The full-screen clock takeover is retired (owner request: the pet should
  // ALWAYS be visible). Time now lives as a small always-on overlay in the
  // top-left of the normal screen — see drawMiniClock(). The takeover and
  // landscape machinery are kept compiled but permanently gated off.
  bool clocking = false;
  if (clocking) clockUpdateOrient();
  else { clockOrient = 0; orientFrames = 0; paintedOrient = 0; }
  bool landscapeClock = clocking && clockOrient != 0;

  static bool wasClocking = false;
  static bool wasLandscape = false;
  if (clocking != wasClocking || landscapeClock != wasLandscape) {
    if (clocking && !landscapeClock) characterSetPeek(true);
    else { characterSetPeek(false); buddySetPeek(false); repaintAll(); }
    characterInvalidate();
    if (buddyMode) buddyInvalidate();
    wasClocking = clocking;
    wasLandscape = landscapeClock;
  }
  if (clocking) {
    uint8_t dow = clockDow();
    bool weekend = (dow == 0 || dow == 6);
    bool friday  = (dow == 5);

    uint8_t h = _clkTm.Hours;
    if (h >= 1 && h < 7)             activeState = P_SLEEP;
    else if (weekend)                activeState = (now/8000 % 6 == 0) ? P_HEART : P_SLEEP;
    else if (h < 9)                  activeState = (now/6000 % 4 == 0) ? P_IDLE  : P_SLEEP;
    else if (h == 12)                activeState = (now/5000 % 3 == 0) ? P_HEART : P_IDLE;
    else if (friday && h >= 15)      activeState = (now/4000 % 3 == 0) ? P_CELEBRATE : P_IDLE;
    else if (h >= 22 || h == 0)      activeState = (now/7000 % 3 == 0) ? P_DIZZY : P_SLEEP;
    else                             activeState = (now/10000 % 5 == 0) ? P_SLEEP : P_IDLE;
  }

  static uint32_t lastPasskey = 0;
  uint32_t pk = blePasskey();
  if (pk && !lastPasskey) { wake(); beep(1800, 60); }
  lastPasskey = pk;

  if (napping || screenOff || landscapeClock) {
    // skip sprite render — face-down, powered off, or landscape clock
    // (which draws direct-to-LCD below)
  } else if (buddyMode) {
    diagPhase(DP_RENDER);
    buddyTick(activeState);
  } else if (characterLoaded()) {
    characterSetState(activeState);
    characterTick();
  } else {
    const Palette& p = characterPalette();
    spr.fillSprite(p.bg);
    spr.setTextColor(p.textDim, p.bg);
    spr.setTextSize(1);
    if (xferActive()) {
      uint32_t done = xferProgress(), total = xferTotal();
      spr.setCursor(8, 90);
      spr.print("installing");
      spr.setCursor(8, 102);
      spr.printf("%luK / %luK", done/1024, total/1024);
      int barW = W - 16;
      spr.drawRect(8, 116, barW, 8, p.textDim);
      if (total > 0) {
        int fill = (int)((uint64_t)barW * done / total);
        if (fill > 1) spr.fillRect(9, 117, fill - 1, 6, p.body);
      }
    } else {
      spr.setCursor(8, 100);
      spr.print("no character loaded");
    }
  }
  if (landscapeClock) {
    drawClock();
  } else if (!napping && !screenOff) {
    if (blePasskey()) drawPasskey();
    else if (clocking) drawClock();
    else if (settings().hud) drawHUD();
    drawMiniClock();
    spr.pushSprite(0, 0);
  }

  // Face-down nap: dim immediately, pause animations, accumulate sleep time.
  // Skipped during approval — you're holding it to read, not sleeping it.
  // Exit needs sustained not-down so IMU noise at the threshold doesn't
  // bounce brightness between 8 and full every few frames.
  static int8_t faceDownFrames = 0;
  if (!inPrompt) {
    bool down = isFaceDown();
    if (down)       { if (faceDownFrames < 20) faceDownFrames++; }
    else            { if (faceDownFrames > -10) faceDownFrames--; }
  }

  if (!napping && faceDownFrames >= 15) {
    napping = true;
    napStartMs = now;
    M5.Axp.ScreenBreath(8);
    dimmed = true;
  } else if (napping && faceDownFrames <= -8) {
    napping = false;
    statsOnNapEnd((now - napStartMs) / 1000);
    statsOnWake();
    wake();
  }

  // millis() not the cached `now`: wake() runs after `now` is captured,
  // so now - lastInteractMs underflows when a button is held → flicker.
  // No auto-off on USB power — clock face wants to stay visible while charging.
  if (!screenOff && !inPrompt && !_onUsb
      && millis() - lastInteractMs > SCREEN_OFF_MS) {
    M5.Axp.SetLDO2(false);
    screenOff = true;
  }

  diagPhase(DP_IDLE);   // reached the end cleanly — a hang here is the delay
  delay(screenOff ? 100 : 16);
}
