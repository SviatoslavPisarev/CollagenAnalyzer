#!/bin/bash
# Collagen Reflectance Analyzer — Mac launcher.
# Double-click to start. The first start installs the Python packages (2–5 minutes, internet needed);
# later starts take a few seconds. The packages go to ~/Library/Application Support/CollagenAnalyzer
# (not into this folder, so it is safe to keep the app folder in iCloud Drive).
cd "$(dirname "$0")" || exit 1
APP_DIR="$(pwd)"
SUPPORT="$HOME/Library/Application Support/CollagenAnalyzer"
VENV="$SUPPORT/venv"
mkdir -p "$SUPPORT"

fail() {
  echo ""
  echo "PROBLEM: $1"
  osascript -e "display dialog \"$1\" with title \"Collagen Reflectance Analyzer\" buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
  echo "Press Return to close this window."
  read -r _
  exit 1
}

# --- find Python 3.9 or newer (python.org installer, Homebrew, or Apple's)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  open "https://www.python.org/downloads/macos/"
  fail "Python 3.9 or newer is needed. Install it from python.org (the page is opening now), then double-click Start_Mac.command again."
fi

# --- create the environment once, reinstall when requirements.txt changes
REQ_HASH="$(shasum "$APP_DIR/requirements.txt" | cut -d' ' -f1)"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$SUPPORT/req_hash" 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "First start: setting up (Python: $("$PY" --version 2>&1)). This takes a few minutes…"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" || fail "Could not create the Python environment in $VENV."
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
  "$VENV/bin/python" -m pip install -r "$APP_DIR/requirements.txt" || fail "Installing the Python packages failed (see the messages above). Check the internet connection and try again."
  echo "$REQ_HASH" > "$SUPPORT/req_hash"
  echo "Setup finished."
fi

echo "Starting Collagen Reflectance Analyzer… (you can close this window once the app is open)"
nohup "$VENV/bin/python" "$APP_DIR/CollagenAnalyzer.py" "$@" >"$SUPPORT/last_run.log" 2>&1 &
sleep 2
if ! kill -0 $! 2>/dev/null; then
  cat "$SUPPORT/last_run.log"
  fail "The app stopped right after starting. The messages above (also in $SUPPORT/last_run.log) say why."
fi
exit 0
