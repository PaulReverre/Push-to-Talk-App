"""ASR backends.

Default is mlx-whisper: runs on the Apple Silicon GPU, no network, no per-word
cost. large-v3-turbo does a 5 second utterance in roughly 300-600ms on an
M-series chip once the model is warm. The warm-up is the catch, so we run a
dummy decode at startup.
"""
import io
import os
import wave

import numpy as np


class Transcriber:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.backend = cfg["asr_backend"]
        self._impl = None
        self.prompt = ", ".join(cfg.get("vocabulary") or []) or None

    # -- lifecycle ------------------------------------------------------
    def load(self):
        if self.backend == "mlx":
            import mlx_whisper  # noqa: F401  (module-level API, nothing to hold)
            self._impl = "mlx"
        elif self.backend == "faster_whisper":
            from faster_whisper import WhisperModel
            self._impl = WhisperModel(
                self.cfg["faster_whisper_model"], device="cpu", compute_type="int8"
            )
        elif self.backend == "openai":
            from openai import OpenAI
            self._impl = OpenAI()
        else:
            raise ValueError(f"unknown asr_backend {self.backend!r}")
        return self

    def warmup(self):
        """Force weights onto the GPU so the first real press isn't 4 seconds."""
        if self.backend in ("mlx", "faster_whisper"):
            silence = np.zeros(self.cfg["sample_rate"], dtype=np.float32)
            try:
                self.transcribe(silence)
            except Exception:
                pass

    # -- inference ------------------------------------------------------
    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        if self.backend == "mlx":
            return self._mlx(audio)
        if self.backend == "faster_whisper":
            return self._faster(audio)
        return self._openai(audio)

    def _mlx(self, audio):
        import mlx_whisper
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.cfg["mlx_model"],
            language=self.cfg.get("language"),
            initial_prompt=self.prompt,
            condition_on_previous_text=False,
            temperature=0.0,
            fp16=True,
        )
        return (result.get("text") or "").strip()

    def _faster(self, audio):
        segments, _ = self._impl.transcribe(
            audio,
            language=self.cfg.get("language"),
            initial_prompt=self.prompt,
            vad_filter=True,
            beam_size=1,
        )
        return " ".join(s.text for s in segments).strip()

    def _openai(self, audio):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.cfg["sample_rate"])
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        buf.seek(0)
        buf.name = "speech.wav"
        resp = self._impl.audio.transcriptions.create(
            model=self.cfg["openai_asr_model"],
            file=buf,
            language=self.cfg.get("language") or None,
            prompt=self.prompt,
        )
        return (resp.text or "").strip()
