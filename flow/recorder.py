"""Mic capture.

The stream is opened once at startup and left running. Opening a CoreAudio
stream costs 100-300ms, which you would otherwise pay on every single press.
We just discard frames unless recording is armed.
"""
import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate=16000, device=None, max_seconds=120):
        self.sample_rate = sample_rate
        self.max_frames = int(sample_rate * max_seconds)
        self.device = self._resolve_device(device)
        self._armed = threading.Event()
        self._buf: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._level = 0.0

    @staticmethod
    def _resolve_device(device):
        if device is None or isinstance(device, int):
            return device
        for idx, info in enumerate(sd.query_devices()):
            if info["max_input_channels"] > 0 and device.lower() in info["name"].lower():
                return idx
        raise ValueError(f"no input device matching {device!r}")

    def _callback(self, indata, frames, time_info, status):
        if not self._armed.is_set():
            return
        mono = indata[:, 0]
        # cheap RMS for the overlay's level meter; must stay trivial because
        # CoreAudio kills slow callbacks
        self._level = float(np.sqrt((mono ** 2).mean()))
        with self._lock:
            self._buf.append(mono.copy())

    def open(self):
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        return self

    def close(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # -- control --------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._armed.is_set()

    @property
    def level(self) -> float:
        """Most recent RMS, 0..~1. Only meaningful while recording."""
        return self._level if self._armed.is_set() else 0.0

    def snapshot(self) -> np.ndarray:
        """Copy of what's been captured so far, without stopping the recording.
        Used by the overlay's draft decode. Deliberately does NOT trim silence -
        that's for the final pass."""
        with self._lock:
            if not self._buf:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._buf)[: self.max_frames]

    def start(self):
        with self._lock:
            self._buf.clear()
        self._level = 0.0
        self._armed.set()

    def stop(self) -> np.ndarray:
        """Disarm and return mono float32 audio at self.sample_rate."""
        self._armed.clear()
        with self._lock:
            chunks = self._buf
            self._buf = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)[: self.max_frames]
        return trim_silence(audio, self.sample_rate)


def trim_silence(audio: np.ndarray, sample_rate: int, threshold=0.006, pad_ms=120):
    """Crude energy gate. Cuts the dead air before you actually start talking
    and after you stop, which is usually 300-800ms of pure decode cost."""
    if audio.size == 0:
        return audio
    win = max(1, sample_rate // 100)  # 10ms frames
    n = audio.size // win
    if n == 0:
        return audio
    frames = audio[: n * win].reshape(n, win)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    loud = np.where(energy > threshold)[0]
    if loud.size == 0:
        return np.zeros(0, dtype=np.float32)
    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, loud[0] * win - pad)
    end = min(audio.size, (loud[-1] + 1) * win + pad)
    return audio[start:end]


def duration(audio: np.ndarray, sample_rate: int) -> float:
    return audio.size / float(sample_rate)
