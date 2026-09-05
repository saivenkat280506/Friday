#!/bin/zsh
# Double-click opens the desktop app (Electron), not a browser tab.
cd "$(dirname "$0")"
chmod +x ./scripts/friday-launcher.sh 2>/dev/null
if [[ -d "./FRIDAY.app" && $# -eq 0 ]]; then
  open "./FRIDAY.app"
  exit 0
fi
exec ./scripts/friday-launcher.sh "$@"
