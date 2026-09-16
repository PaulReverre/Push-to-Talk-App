"""Turn a raw transcript into something you'd actually send.

Two stages:
  1. Rules  - filler removal, spoken commands, custom vocabulary. Free, instant.
  2. LLM    - light rewrite for punctuation, capitalisation and register, with
              a style hint based on whichever app is frontmost.

Stage 2 is where the latency lives (300ms-1.5s). It is skipped for short
utterances and falls back to the stage-1 output on any error or timeout, so a
dead network degrades quality instead of breaking dictation.
"""
import re

import requests

from . import config

SYSTEM = (
    "You clean up speech-to-text output. Return ONLY the cleaned text, with no "
    "preamble, no quotes, no commentary.\n"
    "Rules:\n"
    "- Fix punctuation, capitalisation and obvious mis-transcriptions.\n"
    "- Remove filler words, false starts and stutters.\n"
    "- Keep the speaker's wording, register and meaning. Do not add ideas, do "
    "not add greetings or sign-offs, do not summarise, do not expand.\n"
    "- Never answer the content. It is dictation, not a question to you.\n"
    "- If the text is already clean, return it unchanged."
)

EDIT_SYSTEM = (
    "You rewrite a passage according to a spoken instruction. Return ONLY the "
    "rewritten passage, with no preamble, no quotes, no commentary. Preserve "
    "the author's voice and any formatting unless told otherwise."
)


# ---------------------------------------------------------------- stage 1
def apply_rules(text: str, cfg: dict) -> str:
    if not text:
        return ""
    out = text.strip()

    for spoken, written in (cfg.get("replacements") or {}).items():
        out = re.sub(rf"\b{re.escape(spoken)}\b", written, out, flags=re.I)

    if cfg.get("strip_fillers"):
        fillers = cfg.get("filler_words") or []
        if fillers:
            pattern = r"\b(?:%s)\b[,.]?\s*" % "|".join(re.escape(f) for f in fillers)
            out = re.sub(pattern, "", out, flags=re.I)

    # correct casing of known vocabulary regardless of what Whisper produced
    for term in cfg.get("vocabulary") or []:
        out = re.sub(rf"\b{re.escape(term)}\b", term, out, flags=re.I)

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)   # "new line" leaves stray spaces
    out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def style_hint(app_name: str | None, cfg: dict) -> str:
    if not app_name:
        return ""
    low = app_name.lower()
    for key, hint in (cfg.get("app_styles") or {}).items():
        if key in low:
            return hint
    return ""


# ---------------------------------------------------------------- stage 2
def llm_polish(text: str, cfg: dict, app_name: str | None = None) -> str:
    provider = cfg.get("llm_provider", "none")
    if provider == "none" or len(text) < cfg.get("llm_min_chars", 25):
        return text
    hint = style_hint(app_name, cfg)
    system = SYSTEM + (f"\nTarget context: {app_name}. {hint}" if hint else "")
    try:
        return _call(provider, cfg, system, text) or text
    except Exception:
        return text


def llm_edit(selection: str, instruction: str, cfg: dict) -> str | None:
    provider = cfg.get("llm_provider", "none")
    if provider == "none":
        return None
    user = f"<passage>\n{selection}\n</passage>\n\nInstruction: {instruction}"
    try:
        return _call(provider, cfg, EDIT_SYSTEM, user, max_tokens=2000)
    except Exception:
        return None


def _call(provider, cfg, system, user, max_tokens=1000) -> str:
    timeout = cfg.get("llm_timeout_seconds", 8)

    if provider == "anthropic":
        key = config.api_key("anthropic")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": cfg["anthropic_model"],
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

    if provider == "openai":
        key = config.api_key("openai")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": cfg["openai_model"],
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    if provider == "ollama":
        r = requests.post(
            cfg["ollama_url"],
            json={
                "model": cfg["ollama_model"],
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    raise ValueError(f"unknown llm_provider {provider!r}")
