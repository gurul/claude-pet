/*
 * mic_test — onboard MEMS mic (LMA2718B381) → ES8311 ADC capture check
 * for the Freenove FNK0104B. Derived from Freenove's Sketch_07.2_Echo
 * (Tutorial_With_Touch, author Eason Shen); es8311.{cpp,h}, es8311_reg.h
 * are their driver, unmodified.
 *
 * Loop: record 5s → print RMS/peak + an ASCII VU bar per 250ms window
 * (proves capture with nothing but a serial monitor) → play the clip
 * back through the FM8002E amp if a speaker is on the JST.
 *
 * Serial output needs DTR asserted (S3 HWCDC) — use pyserial with
 * dtr=True, not `cat`. Flash with the same FQBN as the pet firmware;
 * remember to bootout the cc-buddy-bridge daemon first (it owns the port).
 */

#include <Arduino.h>
#include "Wire.h"
#include "ESP_I2S.h"
#include "es8311.h"

// FNK0104AB pin map (matches DESIGN.md and the pet firmware)
#define I2S_MCK 4
#define I2S_BCK 5
#define I2S_DINT 6   // codec → ESP32 (mic data)
#define I2S_DOUT 8   // ESP32 → codec (playback)
#define I2S_WS 7
#define AP_ENABLE 1  // FM8002E amp enable
#define I2C_SCL 15
#define I2C_SDA 16
#define I2C_SPEED 400000

I2SClass es8311_i2s;

void driver_es8311_init(void) {
  pinMode(AP_ENABLE, OUTPUT);
  digitalWrite(AP_ENABLE, LOW);   // as in Freenove's sketch; flip HIGH if playback is silent

  Wire.begin(I2C_SDA, I2C_SCL, I2C_SPEED);

  es8311_i2s.setPins(I2S_BCK, I2S_WS, I2S_DOUT, I2S_DINT, I2S_MCK);
  if (!es8311_i2s.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) {
    Serial.println("Failed to initialize I2S bus!");
  }
}

// Per-window RMS/peak over the recorded WAV (44-byte header, then s16 mono).
static void printLevels(const uint8_t* wav, size_t size) {
  if (size <= 44) { Serial.println("no samples!"); return; }
  const int16_t* s = (const int16_t*)(wav + 44);
  size_t n = (size - 44) / 2;
  const size_t win = 16000 / 4;              // 250ms @ 16kHz
  Serial.println("t(ms)  rms   peak  level");
  for (size_t off = 0; off + win <= n; off += win) {
    uint64_t acc = 0; int32_t peak = 0;
    for (size_t i = off; i < off + win; i++) {
      int32_t v = s[i];
      acc += (uint64_t)(v * v);
      if (abs(v) > peak) peak = abs(v);
    }
    int32_t rms = (int32_t)sqrt((double)(acc / win));
    int bars = rms > 0 ? (int)(log10((double)rms) * 12) : 0;  // log scale, ~48 cols max
    if (bars < 0) bars = 0; if (bars > 48) bars = 48;
    char bar[50];
    memset(bar, '#', bars); bar[bars] = 0;
    Serial.printf("%5u %5ld %6ld  %s\n",
                  (unsigned)(off / 16), (long)rms, (long)peak, bar);
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  Serial.println("Start initializing the audio device...");
  driver_es8311_init();
  if (es8311_codec_init() != ESP_OK) {
    Serial.println("ES8311 init failed!");
    return;
  }
  delay(1000);
  Serial.println("Initialization completed.");
}

void loop() {
  Serial.println("\n=== Recording 5s — SPEAK NOW ===");
  size_t wav_size = 0;
  uint8_t* wav = es8311_i2s.recordWAV(5, &wav_size);
  Serial.printf("Recorded %u bytes.\n", (unsigned)wav_size);
  printLevels(wav, wav_size);

  Serial.println("Playing back (audible only with a speaker on the JST)...");
  es8311_i2s.playWAV(wav, wav_size);
  Serial.println("Done.");
  free(wav);
  delay(1500);
}
