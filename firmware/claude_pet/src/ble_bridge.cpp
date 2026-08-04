// FNK0104B port: BLE disabled for v0 — the host bridge feeds NDJSON over
// native USB CDC instead (see data.h). The upstream Bluedroid implementation
// doesn't compile against esp32 core 3.x (NimBLE-backed BLE library); restore
// from vendor/claude-desktop-buddy if BLE pairing is wanted later.
#include "ble_bridge.h"
#include <Arduino.h>

void bleInit(const char* deviceName) {
  Serial.printf("[ble] disabled in this build (USB serial only); name would be '%s'\n", deviceName);
}
bool bleConnected() { return false; }
bool bleSecure()    { return false; }
uint32_t blePasskey() { return 0; }
void bleClearBonds() {}
size_t bleAvailable() { return 0; }
int bleRead() { return -1; }
size_t bleWrite(const uint8_t*, size_t len) { return len; }
