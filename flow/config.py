"""Config loading / saving. Lives at ~/.flowclone/config.json."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".flowclone"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.sqlite3"
LOG_PATH = CONFIG_DIR / "flowclone.log"

DEFAULTS = {
    # ---- hotkeys -------------------------------------------------------
    # push-to-talk: hold to record, release to transcribe + paste.
    # one of: fn | right_cmd | right_option | right_ctrl | right_shift
    "dictate_key": "fn",
    # hold this one to speak an instruction that rewrites the CURRENT SELECTION
    "edit_key": "right_option",
    # if you hold the key for less than this, treat it as a toggle instead of
    # push-to-talk (tap once to start, tap again to stop). 0 disables.
    "toggle_threshold_ms": 350,
    "max_recording_seconds": 120,

    # ---- audio ---------------------------------------------------------
    "sample_rate": 16000,
    "input_device": None,          # None = system default. Int index or name substring.
    "play_sounds": True,
    "start_sound": "/System/Library/Sounds/Tink.aiff",
    "stop_sound": "/System/Library/Sounds/Pop.aiff",
    "error_sound": "/System/Library/Sounds/Basso.aiff",

    # ---- transcription -------------------------------------------------
    # mlx | faster_whisper | openai
    "asr_backend": "mlx",
    "mlx_model": "mlx-community/whisper-large-v3-turbo",
    "faster_whisper_model": "small.en",
    "openai_asr_model": "whisper-1",
    "language": "en",              # None for auto-detect (slower, less accurate)
    # words fed to the decoder as a prompt so it spells your jargon right
    "vocabulary": [
        "Alpha Hub", "Konzortia", "tokenization", "TSXV", "CFA", "Manraj",
    ],

    # ---- post-processing ------------------------------------------------
    "strip_fillers": True,
    "filler_words": ["um", "uh", "erm", "uhh", "umm", "hmm", "mhm"],
    # spoken -> written substitutions applied before the LLM pass
    "replacements": {
        "new paragraph": "\n\n",
        "new line": "\n",
        "open quote": "\u201c",
        "close quote": "\u201d",
    },

    # none | anthropic | openai | ollama
    "llm_provider": "anthropic",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "openai_model": "gpt-4o-mini",
    "ollama_model": "llama3.2:3b",
    "ollama_url": "http://localhost:11434/api/chat",
    # skip the LLM pass for very short utterances - not worth the latency
    "llm_min_chars": 25,
    "llm_timeout_seconds": 8,

    # per-app tone hints. key = frontmost app name (substring match, lowercased)
    "app_styles": {
        "slack": "Casual internal chat. Short. No greeting or sign-off.",
        "mail": "Professional email prose. Full sentences.",
        "superhuman": "Professional email prose. Full sentences.",
        "messages": "Casual text message. Very short.",
        "notion": "Clean written notes. Keep structure if dictated.",
        "obsidian": "Clean written notes. Markdown allowed.",
        "code": "Technical. Preserve identifiers and code-like tokens verbatim.",
        "terminal": "Output a shell command only, no prose, no backticks.",
        "iterm": "Output a shell command only, no prose, no backticks.",
    },

    # ---- on-screen overlay ----------------------------------------------
    # floating pill showing recording state, a level meter and the transcript
    "overlay": True,
    # live "what you're saying so far" text while you talk. Whisper has no true
    # streaming mode, so this re-decodes the utterance every interval with a
    # small draft model: the text lags and revises itself. The final paste
    # always comes from the real model, not from this.
    "overlay_live_text": True,
    "overlay_draft_model": "tiny.en",
    "overlay_draft_interval": 1.5,   # seconds between draft decodes
    "overlay_draft_min_seconds": 0.7,

    # ---- output ---------------------------------------------------------
    # paste | type   (paste is fast; type works in apps that block Cmd+V)
    "inject_method": "paste",
    "restore_clipboard": True,
    "trailing_space": True,
    "save_history": True,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2))
        return dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"config.json is not valid JSON: {e}")
    return _deep_merge(DEFAULTS, user)


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def api_key(provider: str) -> str | None:
    return {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
    }.get(provider)
