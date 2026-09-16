"""Live 'what you're saying so far' decoding for the overlay.

Whisper is not a streaming model: it decodes a padded 30-second window in one
shot. There is no true word-by-word stream to tap into. What we do instead is
re-decode the whole utterance-so-far every N seconds with a deliberately tiny
model, and show that as provisional text. The accurate decode still happens
once, on release, with the real model.

That means the overlay text lags your voice by roughly one decode interval and
will visibly revise itself as more audio arrives. On a 2-core machine that is
the honest ceiling - see README 'Known gaps'.

The draft model is separate from the main one so that lowering draft quality
for speed never touches what actually gets pasted.
"""
import threading
import time

import numpy as np


class DraftDecoder:
    def __init__(self, cfg, on_text):
        self.cfg = cfg
        self.on_text = on_text
        self.model_name = cfg.get("overlay_draft_model", "tiny.en")
        self.interval = float(cfg.get("overlay_draft_interval", 1.5))
        self.min_seconds = float(cfg.get("overlay_draft_min_seconds", 0.7))
        self._model = None
        self._thread = None
        self._stop = threading.Event()

    def load(self):
        """Preload so the first press doesn't eat the model-load cost."""
        if self._model is not None:
            return self
        from faster_whisper import WhisperModel
        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        list(self._model.transcribe(np.zeros(16000, dtype=np.float32),
                                    language="en", beam_size=1)[0])
        return self

    def _decode(self, audio):
        segments, _ = self._model.transcribe(
            audio,
            language=self.cfg.get("language") or "en",
            beam_size=1,
            vad_filter=False,      # partial audio ends mid-word; VAD just clips it
            condition_on_previous_text=False,
            temperature=0.0,
        )
        return " ".join(s.text for s in segments).strip()

    def _run(self, snapshot, sample_rate):
        while not self._stop.is_set():
            if self._stop.wait(self.interval):
                break
            audio = snapshot()
            if audio.size < sample_rate * self.min_seconds:
                continue
            try:
                text = self._decode(audio)
            except Exception:
                continue          # a failed draft must never break dictation
            if text and not self._stop.is_set():
                self.on_text(text)

    def start(self, snapshot, sample_rate):
        if self._model is None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(snapshot, sample_rate),
            daemon=True, name="draft",
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Don't join: a decode in flight can take ~1s and the release path must
        # stay responsive. The thread is a daemon and checks _stop before
        # publishing, so a late result is discarded rather than shown.
        self._thread = None
