#!/usr/bin/env bash
# Build FRIDAY.app with the project FRIDAY.jpg as the Dock / Desktop icon.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_JPG=""
for candidate in "$ROOT/FRIDAY.jpg" "$ROOT/friday.jpg" "$ROOT/Friday.jpg"; do
  if [[ -f "$candidate" ]]; then
    SRC_JPG="$candidate"
    break
  fi
done
APP="$ROOT/FRIDAY.app"
DESKTOP_APP="$HOME/Desktop/FRIDAY.app"
ICONSET="$ROOT/build/friday.iconset"
ICNS="$ROOT/friday_icon.icns"

if [[ -z "$SRC_JPG" ]]; then
  echo "Missing logo: $ROOT/FRIDAY.jpg" >&2
  exit 1
fi

mkdir -p "$ICONSET" "$APP/Contents/MacOS" "$APP/Contents/Resources"

MASTER="$ROOT/build/friday-master.png"
mkdir -p "$ROOT/build"
sips -s format png "$SRC_JPG" --out "$MASTER" >/dev/null
sips -z 1024 1024 "$MASTER" --out "$MASTER" >/dev/null

make_icon() {
  local px="$1"
  local name="$2"
  sips -z "$px" "$px" "$MASTER" --out "$ICONSET/$name" >/dev/null
}

make_icon 16   icon_16x16.png
make_icon 32   icon_16x16@2x.png
make_icon 32   icon_32x32.png
make_icon 64   icon_32x32@2x.png
make_icon 128  icon_128x128.png
make_icon 256  icon_128x128@2x.png
make_icon 256  icon_256x256.png
make_icon 512  icon_256x256@2x.png
make_icon 512  icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$ICNS"
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>FRIDAY</string>
  <key>CFBundleDisplayName</key>
  <string>FRIDAY</string>
  <key>CFBundleIdentifier</key>
  <string>ai.friday.launcher</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleExecutable</key>
  <string>FRIDAY</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.utilities</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSSupportsAutomaticTermination</key>
  <false/>
  <key>LSRequiresNativeExecution</key>
  <true/>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
  </array>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/FRIDAY" <<'EXEC'
#!/bin/zsh
set -u
ROOT="/Users/saivenkat/Projects/Friday"
cd "$ROOT" || exit 1
chmod +x "$ROOT/scripts/friday-launcher.sh" 2>/dev/null || true
mkdir -p "$ROOT/logs"
if [[ -x /usr/bin/arch ]]; then
  exec /usr/bin/arch -arm64 /bin/bash "$ROOT/scripts/friday-launcher.sh" --start >>"$ROOT/logs/launcher.log" 2>&1
fi
exec /bin/bash "$ROOT/scripts/friday-launcher.sh" --start >>"$ROOT/logs/launcher.log" 2>&1
EXEC
chmod +x "$APP/Contents/MacOS/FRIDAY"

cat > "$ROOT/FRIDAY.command" <<'CMD'
#!/bin/zsh
# Double-click opens the desktop app (Electron), not a browser tab.
cd "$(dirname "$0")"
chmod +x ./scripts/friday-launcher.sh 2>/dev/null
if [[ -d "./FRIDAY.app" && $# -eq 0 ]]; then
  open "./FRIDAY.app"
  exit 0
fi
exec ./scripts/friday-launcher.sh "$@"
CMD
chmod +x "$ROOT/FRIDAY.command"

rm -rf "$DESKTOP_APP"
rm -f "$HOME/Desktop/FRIDAY.command"
ditto "$APP" "$DESKTOP_APP"
touch "$APP" "$DESKTOP_APP"

/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DESKTOP_APP" >/dev/null 2>&1 || true

echo "Built $APP"
echo "Installed $DESKTOP_APP"
echo "Icon $ICNS"
