# Push-to-Talk Application for Macbooks

Push-to-talk dictation for macOS. Hold a key, talk, release, and cleaned-up text
lands in whatever window has focus. Local transcription by default, so audio
never leaves the machine unless you turn the LLM polish pass on.

Two modes:

| Hold | What happens |
|---|---|
| `fn` | Dictate. Transcript is cleaned and pasted at the cursor. |
| `right option` | Edit. With text selected, speak an instruction ("make this shorter", "turn it into bullets") and the selection is rewritten in place. |

Tap either key briefly instead of holding and it latches on — tap again to stop.
Useful for anything longer than a sentence or two.

---

## Install

```bash
./install.sh          # venv, deps, downloads the whisper model (~1.5GB)
./run.sh              # starts the menu bar app
./install.sh --autostart   # optional launchd agent
```

### Permissions — this is where it will break

System Settings → Privacy & Security. Add your terminal app (or the bundled
`.app`, if you build one) to **all three**:

- **Microphone** — macOS prompts for this one
- **Accessibility** — required to paste into other apps
- **Input Monitoring** — required for the global hotkey

macOS does not reliably prompt for Input Monitoring. Add it by hand, then fully
quit and reopen the terminal. If the hotkey silently does nothing, this is the
reason ~90% of the time.

If you use the `--autostart` agent, the grants have to be on the venv's
`python` binary, not on Terminal.

---

## Architecture

```
hotkey.py     CGEventTap on flagsChanged, own CFRunLoop on a bg thread
recorder.py   sounddevice InputStream held open permanently, energy-gate trim
transcribe.py mlx-whisper (default) / faster-whisper / OpenAI API
polish.py     rules pass (fillers, vocab, spoken commands) then optional LLM
inject.py     clipboard stash → Cmd+V → restore, with a type-it-out fallback
main.py       rumps menu bar app, single worker thread, job queue
history.py    sqlite log of every dictation
```

Design decisions worth knowing:

- **The audio stream stays open.** Opening a CoreAudio stream costs 100–300ms.
  Paying that on every keypress is the difference between feeling instant and
  feeling laggy. We just discard frames unless armed.
- **The event tap handler does almost nothing.** macOS kills taps whose callback
  is slow. All real work is pushed onto a queue.
- **The LLM pass fails open.** Timeout, no API key, no network — you get the
  rules-only output instead of an error. Dictation should never hard-fail
  because a remote call was slow.
- **Silence is trimmed before decode.** Most presses carry 300–800ms of dead air
  at each end. Whisper charges you for it.

---

## Config

`~/.flowclone/config.json`, created on first run. Menu bar → Edit config.

The ones that matter:

```jsonc
{
  "dictate_key": "fn",              // fn | right_cmd | right_option | right_ctrl | right_shift
  "asr_backend": "mlx",             // mlx | faster_whisper | openai
  "mlx_model": "mlx-community/whisper-large-v3-turbo",
  "llm_provider": "anthropic",      // none | anthropic | openai | ollama
  "vocabulary": ["Alpha Hub", "Konzortia", "TSXV"],
  "inject_method": "paste"          // "type" if an app blocks Cmd+V
}
```

`vocabulary` is fed to the decoder as an initial prompt and also used to fix
casing afterwards. Add names, tickers and jargon here — it is the single
highest-leverage setting for accuracy.

`app_styles` maps a frontmost app name to a tone hint given to the LLM pass, so
the same sentence comes out terse in Slack and in full prose in Mail. Terminal
and iTerm are set to emit a bare shell command.

For the LLM pass, export the key before launching:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Set `"llm_provider": "none"` for fully offline operation, or `"ollama"` for a
local model. Rules-only output is still perfectly usable — Whisper already
punctuates. The LLM mostly buys you filler cleanup, false-start repair and
register matching.

---

## Latency

Measured from key release to text appearing, M-series, 6-second utterance:

| Setup | Rough |
|---|---|
| turbo + no LLM | 0.4–0.8s |
| turbo + Haiku polish | 0.9–1.8s |
| `small.en` faster-whisper, no LLM | 0.5–1.0s |
| OpenAI ASR + LLM | 1.5–3s, network dependent |

If it feels slow: drop `llm_provider` to `none` first, then swap the model to
`mlx-community/whisper-small.en-mlx` — accuracy falls off noticeably on names
and numbers, which is exactly where you don't want it to.

Numbers above are estimates from the design, not measured on your machine. Run
Stats in the menu bar after a day of use for your real distribution.

---

## Known gaps vs the commercial product

Being straight about what this doesn't do:

- **No streaming transcription.** Wispr Flow decodes while you talk; this
  decodes on release. Costs you roughly 0.5s of perceived latency and it is the
  single biggest quality-of-feel difference. Fixable with a chunked
  rolling-window decode, but it's a real piece of work.
- **No accessibility-API text insertion.** Paste is fine ~95% of the time but
  loses to secure input fields and a few Electron apps. `"inject_method":
  "type"` is the escape hatch.
- **No learned personal dictionary.** Yours is a static list. The commercial
  product mines your corrections. You can approximate it by reading the sqlite
  history and adding recurring misses.
- **Not code-signed or notarised.** It runs as a Python process out of a venv.
  Bundle it with `py2app` if you want a real `.app` with its own permission
  identity — worth doing, since permission grants then survive terminal changes.
- **fn-key capture is imperfect.** The tap sees fn, but macOS also routes fn to
  its own handlers (emoji picker, dictation) depending on your keyboard
  settings. Set System Settings → Keyboard → "Press fn key to" → "Do Nothing",
  or bind `right_cmd` instead, which is cleaner.
- **Single language per session.** `"language": null` enables auto-detect at a
  meaningful accuracy and speed cost.

## Why Python and not Swift

Swift would give you a signed, notarised, single-binary menu bar app with
proper AXUIElement insertion and no venv. It is the right answer if this becomes
something you use every day for years. It is the wrong answer for getting a
working tool this week: you'd be writing AVAudioEngine buffer plumbing and
whisper.cpp bridging headers instead of tuning the thing that actually
determines whether you keep using it, which is the cleanup prompt and the
vocabulary list.

Run the Python one for a few weeks. If it sticks, port it — the module
boundaries here map cleanly onto Swift types.
