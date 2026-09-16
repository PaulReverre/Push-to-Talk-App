#!/usr/bin/env bash
# flowclone self-test. Verifies the install and reports permission state.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "=============================================="
echo " flowclone self-test"
echo "=============================================="
echo

echo "-- install --"
[ -x .venv/bin/python ] && echo "  venv        OK  $(.venv/bin/python --version 2>&1)" \
                        || { echo "  venv        MISSING - re-run ./install.sh"; exit 1; }
brew list portaudio >/dev/null 2>&1 && echo "  portaudio   OK" || echo "  portaudio   MISSING"
[ -d "$HOME/Applications/FlowClone.app" ] && echo "  app bundle  OK  ~/Applications/FlowClone.app" \
                                          || echo "  app bundle  MISSING"
# Match the agent's own label only. A running GUI app also shows up in
# `launchctl list` as application.com.local.flowclone.<nnn>, so a loose
# grep on the bundle id gives a false positive.
if [ -f "$HOME/Library/LaunchAgents/com.local.flowclone.login.plist" ]; then
  if launchctl list 2>/dev/null | awk '{print $3}' | grep -qx "com.local.flowclone.login"; then
    echo "  autostart   OK  (login + watchdog agent loaded, checks every 20s)"
  else
    echo "  autostart   plist present but NOT loaded"
  fi
else
  echo "  autostart   not installed"
fi
if pgrep -f flow.main >/dev/null; then
  echo "  process     RUNNING (pid $(pgrep -f flow.main | head -1))"
else
  echo "  process     not running  -> open -a FlowClone"
fi
echo

.venv/bin/python - <<'PY'
import ctypes, ctypes.util, json, os, sys

print("-- config --")
try:
    cfg = json.load(open(os.path.expanduser("~/.flowclone/config.json")))
    for k in ("asr_backend", "faster_whisper_model", "dictate_key", "edit_key",
              "llm_provider", "inject_method"):
        print(f"  {k:22} {cfg.get(k)}")
    if cfg.get("asr_backend") == "mlx":
        print("  !! asr_backend=mlx will NOT work on this Intel Mac. Use faster_whisper.")
    if cfg.get("llm_provider") == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("  note: llm_provider=anthropic but no API key -> rules-only cleanup (still works)")
except Exception as e:
    print("  config unreadable:", e)
print()

print("-- permissions (for whoever launched THIS script) --")
lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
lib.AXIsProcessTrusted.restype = ctypes.c_bool
print("  Accessibility      ", "GRANTED" if lib.AXIsProcessTrusted() else "NOT GRANTED  <- paste will fail")

try:
    from Quartz import (CGEventTapCreate, CGEventMaskBit, kCGEventFlagsChanged,
                        kCGSessionEventTap, kCGHeadInsertEventTap,
                        kCGEventTapOptionListenOnly)
    tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                           kCGEventTapOptionListenOnly,
                           CGEventMaskBit(kCGEventFlagsChanged), lambda *a: a[2], None)
    print("  Input Monitoring   ", "GRANTED" if tap else "NOT GRANTED  <- hotkey will not fire")
except Exception as e:
    print("  Input Monitoring    check failed:", e)

try:
    import numpy as np, sounddevice as sd
    a = sd.rec(int(0.5 * 16000), samplerate=16000, channels=1, dtype="float32"); sd.wait()
    print(f"  Microphone          GRANTED (peak {float(np.abs(a).max()):.5f})")
except Exception as e:
    print("  Microphone          FAILED:", e)

print()
print("  NOTE: these reflect the app that ran this script (e.g. Terminal),")
print("        NOT FlowClone.app. Grants are per-app. The real test is to")
print("        launch FlowClone.app and dictate into TextEdit.")
print()

print("-- speech-to-text --")
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import subprocess, wave, tempfile
    from flow import config, transcribe, polish
    wav = os.path.join(tempfile.mkdtemp(), "t.wav")
    subprocess.run(["say", "-o", wav, "--file-format=WAVE",
                    "--data-format=LEI16@16000",
                    "Testing the dictation pipeline about Alpha Hub."], check=True)
    w = wave.open(wav)
    import numpy as np
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    cfg = config.load()
    text = transcribe.Transcriber(cfg).load().transcribe(audio)
    print("  decoded:", repr(polish.apply_rules(text, cfg)))
    print("  ASR                 OK")
except Exception as e:
    print("  ASR                 FAILED:", e)
PY

echo
echo "=============================================="
