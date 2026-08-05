#!/usr/bin/env bash
# Compile, archive the ELF, flash the board, restart the bridge daemon.
#
# The ELF archive is the point: a panic backtrace is only symbolizable
# against the exact binary that was running, and the 2026-08-05 crash spree
# left an unsymbolizable backtrace because the only ELF on disk had been
# rebuilt after the incident. Every flash now files its ELF away first.
#
# The daemon owns the serial port exclusively and its launchd unit has
# KeepAlive, so it must be booted OUT (not just stopped) before esptool
# touches the port — otherwise the flasher fails looking exactly like a
# bricked board.
set -euo pipefail
cd "$(dirname "$0")/.."

FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashMode=qio,FlashSize=16M,PSRAM=opi,PartitionScheme=huge_app"
PORT="${1:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -n "$PORT" ] || { echo "no /dev/cu.usbmodem* device found" >&2; exit 1; }

BUILD=$(mktemp -d)
arduino-cli compile -b "$FQBN" --build-path "$BUILD" \
  --build-property "compiler.cpp.extra_flags=-DUSER_SETUP_LOADED=1 -include $PWD/firmware/claude_pet/tft_setup.h" \
  firmware/claude_pet

SHA=$(git rev-parse --short HEAD)
git diff --quiet || SHA="$SHA-dirty"
mkdir -p firmware/build-archive
cp "$BUILD/claude_pet.ino.elf" \
   "firmware/build-archive/claude_pet-$SHA-$(date +%Y%m%d-%H%M%S).elf"
echo "archived ELF for $SHA"

launchctl bootout "gui/$(id -u)/com.github.cc-buddy-bridge.daemon" 2>/dev/null || true
sleep 2
arduino-cli upload -p "$PORT" -b "$FQBN" --input-dir "$BUILD" firmware/claude_pet
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
echo "flashed $SHA to $PORT; daemon restarted"
