#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

echo "==> checking prerequisites"
command -v brew >/dev/null || { echo "Homebrew required: https://brew.sh"; exit 1; }
brew list portaudio >/dev/null 2>&1 || brew install portaudio

PY="${PYTHON:-python3.11}"
command -v "$PY" >/dev/null || PY=python3
echo "==> using $($PY --version)"

echo "==> creating venv"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel
"$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"

echo "==> warming the whisper model (first run downloads ~1.5GB)"
"$VENV/bin/python" - <<'PY'
import numpy as np, mlx_whisper
mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32),
                       path_or_hf_repo="mlx-community/whisper-large-v3-turbo")
print("model cached")
PY

cat <<'MSG'

==> permissions
Open System Settings > Privacy & Security and add your terminal app
(or the bundled .app) to ALL THREE of:

  - Microphone
  - Accessibility          (needed to paste into other apps)
  - Input Monitoring       (needed for the global hotkey)

macOS will not prompt for Input Monitoring reliably. Add it by hand.
You must fully quit and reopen the terminal after granting.

==> run it
  ./run.sh

==> autostart at login
  ./install.sh --autostart
MSG

if [[ "${1:-}" == "--autostart" ]]; then
  PLIST="$HOME/Library/LaunchAgents/com.local.flowclone.plist"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.flowclone</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>-m</string>
    <string>flow.main</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.flowclone/flowclone.log</string>
  <key>StandardErrorPath</key><string>$HOME/.flowclone/flowclone.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key><string>${ANTHROPIC_API_KEY:-}</string>
  </dict>
</dict>
</plist>
PLISTEOF
  mkdir -p "$HOME/.flowclone"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "==> autostart installed. Note: launchd-started processes need their"
  echo "    own Accessibility/Input Monitoring grants (grant to the python binary)."
fi
