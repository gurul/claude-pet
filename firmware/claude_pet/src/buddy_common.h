#pragma once
#include <stdint.h>

// Shared constants and helpers for buddy species files.
// Each species file (src/buddies/<name>.cpp) includes this header
// and defines its 7 state functions.

// Geometry — shared layout for all species
extern const int BUDDY_X_CENTER;
extern const int BUDDY_CANVAS_W;
extern const int BUDDY_Y_BASE;
extern const int BUDDY_Y_OVERLAY;
extern const int BUDDY_CHAR_W;
extern const int BUDDY_CHAR_H;

// Common colors species can use freely
extern const uint16_t BUDDY_BG;
extern const uint16_t BUDDY_HEART;
extern const uint16_t BUDDY_DIM;
extern const uint16_t BUDDY_YEL;
extern const uint16_t BUDDY_WHITE;
extern const uint16_t BUDDY_CYAN;
extern const uint16_t BUDDY_GREEN;
extern const uint16_t BUDDY_PURPLE;
extern const uint16_t BUDDY_RED;
extern const uint16_t BUDDY_BLUE;

// Print one line centered around BUDDY_X_CENTER, optionally x-offset.
void buddyPrintLine(const char* line, int yPx, uint16_t color, int xOff = 0);

// Print N-line sprite block. yOffset is added to BUDDY_Y_BASE for the top row.
void buddyPrintSprite(const char* const* lines, uint8_t nLines, int yOffset, uint16_t color, int xOff = 0);

// True while rendering to the physical LCD (landscape clock) instead of
// the shared sprite — that path's pet box is only x<115, so wide vector
// species recenter to fit it.
bool buddyRenderTargetIsExternal();

// Set sprite text color directly + cursor (for ad-hoc particle drawing).
void buddySetCursor(int x, int y);
void buddySetColor(uint16_t fg);
void buddyPrint(const char* s);

// Vector fill in 1x buddy coordinates: x is re-centered around
// BUDDY_X_CENTER and everything is multiplied by the internal scale,
// mirroring buddySetCursor, and routed through the current render target.
// Lets vector species (src/buddies/bongo.cpp) stay scale/target-agnostic.
void buddyFillRect(int x, int y, int w, int h, uint16_t color);
