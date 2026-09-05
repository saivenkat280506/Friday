#!/usr/bin/env bash
# F.R.I.D.A.Y. macOS launcher
# Starts the Friday desktop app (Electron). Electron boots backend + frontend.
# Closing the window stops the stack.
#
#   ./scripts/friday-launcher.sh           # start (or stop if already running)
#   ./scripts/friday-launcher.sh --start
#   ./scripts/friday-launcher.sh --stop

set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -m)" == "x86_64" && -x /usr/bin/arch ]]; then
  exec /usr/bin/arch -arm64 /usr/bin/env bash "$ROOT/scripts/friday-launcher.sh" "$@"
fi

BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
MAIN_PY="$BACKEND_DIR/main.py"
LOG_DIR="$ROOT/logs"
PID_FILE="$LOG_DIR/friday.pids"
OLLAMA_PID_FILE="$LOG_DIR/ollama.pid"
OLLAMA_LOG="$LOG_DIR/ollama.log"
ELECTRON_LOG="$LOG_DIR/electron.log"
LAUNCHER_LOG="$LOG_DIR/launcher.log"
OLLAMA_BIN=""
LLM_PROVIDER=""
OLLAMA_MODEL="qwen3.5:4b"
ELECTRON_APP="$FRONTEND_DIR/node_modules/electron/dist/Electron.app"
ELECTRON_BIN="$ELECTRON_APP/Contents/MacOS/Electron"
ELECTRON_ZIP_CACHE="$HOME/Library/Caches/electron"

DESKTOP_PID=""

export BROWSER=none
export BROWSER_PATH=/usr/bin/true
export FRIDAY_OPEN_MAIN=1

set +o huponexit 2>/dev/null || true
mkdir -p "$LOG_DIR"

if [[ ! -t 1 ]]; then
  exec >>"$LAUNCHER_LOG" 2>&1
fi

MODE="toggle"
for arg in "$@"; do
  case "$arg" in
    --start|-Start|-start) MODE="start" ;;
    --stop|-Stop|-stop) MODE="stop" ;;
  esac
done

cyan=$'\033[36m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'; gray=$'\033[90m'; white=$'\033[97m'; reset=$'\033[0m'

banner() {
  echo ""
  echo "${cyan}  ========================================================${reset}"
  echo "${white}       F.R.I.D.A.Y.  —  Female Replacement Intelligent Digital Assistant Youth${reset}"
  echo "${cyan}       $1  |  macOS${reset}"
  echo "${cyan}  ========================================================${reset}"
  echo ""
}

step() {
  local status="$2"
  local color="$gray"
  case "$status" in
    OK) color="$green" ;;
    WAIT) color="$yellow" ;;
    ERR) color="$red" ;;
    INFO) color="$cyan" ;;
  esac
  printf "  ${color}[%-4s]${reset} %s\n" "$status" "$1" >&2
}

load_backend_env() {
  local envf="$BACKEND_DIR/.env"
  [[ -f "$envf" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    export "$line"
  done < "$envf"
  LLM_PROVIDER="${LLM_PROVIDER:-}"
  OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:4b}"
}

resolve_ollama_bin() {
  if [[ -n "${OLLAMA_BIN:-}" && -x "$OLLAMA_BIN" ]]; then
    return 0
  fi
  for candidate in /usr/local/bin/ollama /opt/homebrew/bin/ollama "$(command -v ollama 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      OLLAMA_BIN="$candidate"
      return 0
    fi
  done
  OLLAMA_BIN=""
  return 1
}

ollama_up() {
  curl -sf --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1
}

start_ollama() {
  load_backend_env
  if [[ "${LLM_PROVIDER:-}" != "ollama" ]]; then
    step "Ollama not required (LLM_PROVIDER=${LLM_PROVIDER:-unset})" "INFO"
    return 0
  fi
  if ollama_up; then
    step "Ollama already running on port 11434" "OK"
    return 0
  fi
  step "Starting Ollama..." "WAIT"
  if resolve_ollama_bin; then
    nohup "$OLLAMA_BIN" serve >>"$OLLAMA_LOG" 2>&1 &
    echo $! > "$OLLAMA_PID_FILE"
  elif [[ -d "/Applications/Ollama.app" ]]; then
    open -a Ollama >/dev/null 2>&1 || true
  else
    die "Ollama is not installed. Install it, then retry."
  fi
  local i
  for i in $(seq 1 40); do
    if ollama_up; then
      step "Ollama online (model $OLLAMA_MODEL)" "OK"
      return 0
    fi
    sleep 0.35
  done
  die "Ollama did not start on port 11434"
}

stop_ollama() {
  load_backend_env
  if [[ "${LLM_PROVIDER:-}" != "ollama" && ! -f "$OLLAMA_PID_FILE" ]]; then
    return 0
  fi
  resolve_ollama_bin || true
  if [[ -n "$OLLAMA_BIN" && -x "$OLLAMA_BIN" && -n "${OLLAMA_MODEL:-}" ]]; then
    "$OLLAMA_BIN" stop "$OLLAMA_MODEL" >/dev/null 2>&1 || true
  fi
  if [[ -f "$OLLAMA_PID_FILE" ]]; then
    local pid
    pid="$(cat "$OLLAMA_PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] && kill "$pid" >/dev/null 2>&1 || true
    sleep 0.3
    [[ -n "$pid" ]] && kill -9 "$pid" >/dev/null 2>&1 || true
    rm -f "$OLLAMA_PID_FILE"
  fi
  free_port 11434
  osascript -e 'tell application "Ollama" to quit' >/dev/null 2>&1 || true
  killall Ollama ollama >/dev/null 2>&1 || true
  step "Ollama stopped" "OK"
}

port_open() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

backend_healthy() {
  curl -sf --max-time 2 "http://127.0.0.1:8000/health" >/dev/null 2>&1
}

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 >/dev/null 2>&1 || true
    step "Freed port $port" "OK"
  fi
}

notify() {
  local msg="${1:-}"
  [[ -z "$msg" ]] && return 0
  osascript -e "display notification \"${msg}\" with title \"F.R.I.D.A.Y.\"" >/dev/null 2>&1 || true
}

die() {
  local msg="${1:-F.R.I.D.A.Y. failed to start}"
  step "$msg" "ERR"
  notify "Failed to start"
  if [[ ! -t 1 ]]; then
    osascript -e "display dialog \"${msg}\" buttons {\"OK\"} default button 1 with title \"F.R.I.D.A.Y.\" with icon stop" >/dev/null 2>&1 || true
  fi
  exit 1
}

focus_desktop_window() {
  osascript >/dev/null 2>&1 <<'APPLESCRIPT' || true
tell application "System Events"
  set names to name of every process
  if names contains "F.R.I.D.A.Y." then
    set frontmost of process "F.R.I.D.A.Y." to true
  else if names contains "FRIDAY" then
    set frontmost of process "FRIDAY" to true
  else if names contains "Electron" then
    set frontmost of process "Electron" to true
  end if
end tell
APPLESCRIPT
}

desktop_window_running() {
  pgrep -f "$FRONTEND_DIR/desktop/main.js" >/dev/null 2>&1 && return 0
  pgrep -f "$ELECTRON_BIN" >/dev/null 2>&1 && return 0
  return 1
}

brand_electron_app() {
  local plist="$ELECTRON_APP/Contents/Info.plist"
  [[ -f "$plist" ]] || return 0
  /usr/libexec/PlistBuddy -c "Set :CFBundleName F.R.I.D.A.Y." "$plist" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleName string F.R.I.D.A.Y." "$plist" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName F.R.I.D.A.Y." "$plist" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string F.R.I.D.A.Y." "$plist" >/dev/null 2>&1 || true
  if [[ -f "$ROOT/friday_icon.icns" ]]; then
    cp "$ROOT/friday_icon.icns" "$ELECTRON_APP/Contents/Resources/electron.icns" 2>/dev/null || true
  fi
  xattr -cr "$ELECTRON_APP" >/dev/null 2>&1 || true
}

find_electron_zip() {
  find "$ELECTRON_ZIP_CACHE" -name 'electron-v*-darwin-*.zip' -type f 2>/dev/null | tail -n 1
}

ensure_electron() {
  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    echo "$ELECTRON_BIN"
    return 0
  fi
  step "Preparing Electron desktop runtime..." "WAIT"
  mkdir -p "$FRONTEND_DIR/node_modules/electron/dist"
  if [[ -f "$FRONTEND_DIR/node_modules/electron/install.js" ]]; then
    (cd "$FRONTEND_DIR" && node "$FRONTEND_DIR/node_modules/electron/install.js") >/dev/null 2>&1 || true
  fi
  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    echo "$ELECTRON_BIN"
    return 0
  fi
  local zip
  zip="$(find_electron_zip)"
  if [[ -z "$zip" && -f "$FRONTEND_DIR/node_modules/electron/install.js" ]]; then
    (cd "$FRONTEND_DIR" && node "$FRONTEND_DIR/node_modules/electron/install.js") >/dev/null 2>&1 || true
    zip="$(find_electron_zip)"
  fi
  if [[ -n "$zip" ]]; then
    rm -rf "$ELECTRON_APP"
    unzip -qo "$zip" -d "$FRONTEND_DIR/node_modules/electron/dist"
    echo "Electron.app/Contents/MacOS/Electron" > "$FRONTEND_DIR/node_modules/electron/path.txt"
  fi
  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    echo "$ELECTRON_BIN"
    return 0
  fi
  return 1
}

CLEANED=0

stop_friday() {
  step "Stopping F.R.I.D.A.Y. services..." "WAIT"
  curl -sf --max-time 4 -X POST "http://127.0.0.1:8000/app/shutdown" >/dev/null 2>&1 || true
  if [[ -f "$PID_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$PID_FILE" 2>/dev/null || true
    [[ -n "${electron:-}" ]] && kill "$electron" >/dev/null 2>&1 || true
    sleep 0.4
    [[ -n "${electron:-}" ]] && kill -9 "$electron" >/dev/null 2>&1 || true
    rm -f "$PID_FILE"
  fi
  pkill -f "$BACKEND_DIR/main.py" >/dev/null 2>&1 || true
  pkill -f "$FRONTEND_DIR/node_modules/next/dist/bin/next" >/dev/null 2>&1 || true
  pkill -f "$FRONTEND_DIR/desktop/main.js" >/dev/null 2>&1 || true
  pkill -f "$FRONTEND_DIR/node_modules/electron" >/dev/null 2>&1 || true
  free_port 8000
  free_port 3000
  stop_ollama
  echo ""
  step "F.R.I.D.A.Y. stopped." "OK"
  echo ""
}

cleanup_on_close() {
  [[ "$CLEANED" == "1" ]] && return
  CLEANED=1
  trap - EXIT INT TERM HUP
  echo ""
  banner "Shutdown"
  stop_friday
}

python_ready() {
  [[ -x "$PYTHON" ]] || return 1
  "$PYTHON" -c "import fastapi, uvicorn" >/dev/null 2>&1
}

resolve_python() {
  if python_ready; then
    return 0
  fi
  local py=""
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      py="$(command -v "$candidate")"
      break
    fi
  done
  if [[ -z "$py" ]]; then
    step "Python 3 is not installed. Install with: brew install python@3.12" "ERR"
    return 1
  fi
  if [[ ! -x "$PYTHON" ]]; then
    step "Creating Python venv with $py ..." "WAIT"
    notify "First launch — installing F.R.I.D.A.Y. Python packages"
    "$py" -m venv "$BACKEND_DIR/.venv"
  fi
  "$PYTHON" -m pip install --upgrade pip wheel setuptools >/dev/null
  if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
    step "Installing Python dependencies (first run, this can take a while)..." "WAIT"
    notify "Installing F.R.I.D.A.Y. dependencies — please wait"
    "$PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
  fi
  python_ready || return 1
}

start_friday() {
  resolve_python || exit 1
  if [[ ! -f "$MAIN_PY" ]]; then
    die "Backend entry missing: $MAIN_PY"
  fi
  if ! command -v npm >/dev/null 2>&1; then
    die "npm not found in PATH"
  fi
  if [[ ! -d "$FRONTEND_DIR/node_modules" || ! -x "$ELECTRON_BIN" ]]; then
    step "Installing frontend dependencies..." "WAIT"
    notify "Installing F.R.I.D.A.Y. desktop runtime"
    (cd "$FRONTEND_DIR" && npm install)
  fi

  local electron_bin=""
  if ! electron_bin="$(ensure_electron)"; then
    die "Electron runtime is not available. Run: cd frontend && npm install"
  fi

  step "Project root: $ROOT" "INFO"
  step "CPU $(uname -m)" "INFO"
  start_ollama
  notify "Starting F.R.I.D.A.Y...."
  step "Launching F.R.I.D.A.Y. desktop..." "WAIT"
  rm -f "$ELECTRON_LOG"
  (
    cd "$FRONTEND_DIR"
    export FRIDAY_OPEN_MAIN=1
    exec "$electron_bin" "$FRONTEND_DIR"
  ) >"$ELECTRON_LOG" 2>&1 &
  DESKTOP_PID=$!
  sleep 1.5
  if ! kill -0 "$DESKTOP_PID" 2>/dev/null; then
    tail -n 20 "$ELECTRON_LOG" >&2 2>/dev/null || true
    die "Desktop app exited immediately. See logs/electron.log"
  fi
  step "Desktop app launched (Electron PID $DESKTOP_PID)" "OK"
  focus_desktop_window
  printf 'electron=%s\nstarted=%s\n' "$DESKTOP_PID" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$PID_FILE"

  echo ""
  echo "${cyan}  --------------------------------------------------------${reset}"
  echo "${green}   SYSTEMS ONLINE${reset}"
  echo "${white}   Desktop window is the UI.${reset}"
  echo "${gray}   Logs:    $LOG_DIR${reset}"
  echo "${yellow}   Close the Friday window to STOP everything, including Ollama.${reset}"
  echo "${cyan}  --------------------------------------------------------${reset}"
  echo ""

  if [[ -n "$DESKTOP_PID" ]]; then
    wait "$DESKTOP_PID" 2>/dev/null || true
  fi
  while desktop_window_running; do
    sleep 1
  done
}

if [[ "$MODE" == "stop" ]]; then
  banner "Shutdown"
  stop_friday
  exit 0
fi

if [[ "$MODE" == "start" ]] && port_open 8000 && desktop_window_running; then
  banner "Already running"
  step "F.R.I.D.A.Y. is already online — bringing the desktop window forward" "OK"
  focus_desktop_window
  exit 0
fi

if [[ "$MODE" == "toggle" ]] && port_open 8000; then
  banner "Shutdown"
  stop_friday
  exit 0
fi

trap cleanup_on_close EXIT INT TERM HUP
banner "Launcher"
start_friday
