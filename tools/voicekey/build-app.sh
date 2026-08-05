#!/bin/bash
# Build + install VoiceKey.app — the TCC-stable Accessibility helper.
#
# macOS grants Accessibility per code signature. A bare uv/pyenv python is
# "linker-signed" with Identifier=-, so a grant to it does not persist: it
# appears to work once, then silently disappears from the Accessibility list.
# A signed bundle with a fixed CFBundleIdentifier is what TCC is built around.
#
# Re-run after editing main.swift. Re-signing changes the binary, so macOS may
# ask you to re-grant once after a rebuild.
set -euo pipefail
cd "$(dirname "$0")"
APP="${1:-$HOME/Applications/VoiceKey.app}"

swiftc -O -o voicekey main.swift

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp voicekey "$APP/Contents/MacOS/voicekey"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>voicekey</string>
  <key>CFBundleIdentifier</key><string>com.claudepet.voicekey</string>
  <key>CFBundleName</key><string>VoiceKey</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

# -i pins a STABLE designated requirement. This is the whole point: without a
# fixed identifier TCC has nothing durable to key the grant to.
codesign --force --sign - --identifier com.claudepet.voicekey "$APP"
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Identifier=|Signature=" || true
echo "installed: $APP"
