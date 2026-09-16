"""flowclone - push-to-talk dictation for macOS.

Hold the dictate key, talk, release. The transcript is cleaned up and typed
into whatever window has focus. Hold the edit key with text selected to speak
an instruction that rewrites the selection in place.
"""
import queue
import subprocess
import threading
import time
import traceback

import rumps

from . import config, history, inject, polish
from .draft import DraftDecoder
from .hotkey import HotkeyListener
from .overlay import Overlay
from .recorder import Recorder, duration
from .transcribe import Transcriber

IDLE = "●"
RECORDING = "◉"
BUSY = "◌"
PAUSED = "○"


class FlowClone(rumps.App):
    def __init__(self):
        super().__init__("flowclone", title=BUSY, quit_button=None)
        self.cfg = config.load()
        self.paused = False
        self.jobs: queue.Queue = queue.Queue()

        self.recorder = Recorder(
            sample_rate=self.cfg["sample_rate"],
            device=self.cfg["input_device"],
            max_seconds=self.cfg["max_recording_seconds"],
        )
        self.asr = Transcriber(self.cfg)
        self.overlay = Overlay(self.cfg)
        self.draft = (
            DraftDecoder(self.cfg, self._on_draft_text)
            if self.cfg.get("overlay", True) and self.cfg.get("overlay_live_text", True)
            else None
        )

        # hotkey state
        self.active_action = None
        self.toggle_armed = False
        self.pending_toggle = None
        self.ending_toggle = False
        self.started_at = 0.0

        self.menu = [
            rumps.MenuItem("Status: starting", callback=None),
            None,
            rumps.MenuItem("Pause", callback=self.toggle_pause, key="p"),
            rumps.MenuItem("Recent dictations", callback=self.show_recent),
            rumps.MenuItem("Stats", callback=self.show_stats),
            None,
            rumps.MenuItem("Edit config", callback=self.edit_config),
            rumps.MenuItem("Reload config", callback=self.reload_config),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app, key="q"),
        ]

    # ------------------------------------------------------------ startup
    def boot(self):
        threading.Thread(target=self._worker, daemon=True, name="worker").start()
        threading.Thread(target=self._load_models, daemon=True, name="load").start()
        if self.cfg.get("overlay", True):
            # AppKit is main-thread only, and rumps.Timer fires on the main
            # run loop, so this is the one safe place to touch the panel.
            rumps.Timer(self._tick, 1 / 12.0).start()

    def _tick(self, _timer):
        try:
            if self.recorder.recording:
                self.overlay.update(level=self.recorder.level)
            self.overlay.render()
        except Exception:
            traceback.print_exc()

    def _on_draft_text(self, text):
        self.overlay.update(text=text)

    def _load_models(self):
        try:
            self.recorder.open()
            self.asr.load()
            self.asr.warmup()
            if self.draft is not None:
                # a failed draft model must not stop dictation working
                try:
                    self.draft.load()
                except Exception:
                    traceback.print_exc()
                    self.draft = None
        except Exception as e:
            traceback.print_exc()
            self._status(f"Error: {e}")
            self.title = PAUSED
            self.paused = True
            return
        HotkeyListener(
            {"dictate": self.cfg["dictate_key"], "edit": self.cfg["edit_key"]},
            self._on_press,
            self._on_release,
        ).start()
        self.title = IDLE
        self._status(
            f"Ready - hold {self.cfg['dictate_key']} to dictate, "
            f"{self.cfg['edit_key']} to edit"
        )

    # ------------------------------------------------------------ hotkeys
    # These run on the event-tap thread. Keep them to microseconds; anything
    # slow here gets the tap killed by the OS watchdog.
    def _on_press(self, action):
        if self.paused or self.active_action is not None:
            return
        if self.toggle_armed and action == self.pending_toggle:
            self.ending_toggle = True
            self.active_action = action
            return
        self.active_action = action
        self.started_at = time.monotonic()
        self.recorder.start()
        self.title = RECORDING
        self.overlay.show("recording")
        if self.draft is not None:
            self.draft.start(self.recorder.snapshot, self.cfg["sample_rate"])
        self._chime(self.cfg["start_sound"])

    def _on_release(self, action, held):
        if action != self.active_action:
            return
        threshold = self.cfg.get("toggle_threshold_ms", 0) / 1000.0

        if self.ending_toggle:
            self.ending_toggle = False
            self.toggle_armed = False
            self.pending_toggle = None
            self.active_action = None
            self._finish(action)
            return

        if threshold and held < threshold:
            # short tap: latch on, keep recording until the next tap
            self.toggle_armed = True
            self.pending_toggle = action
            self.active_action = None
            return

        self.active_action = None
        self._finish(action)

    def _finish(self, action):
        audio = self.recorder.stop()
        if self.draft is not None:
            self.draft.stop()
        self.title = BUSY
        self.overlay.show("thinking")
        self._chime(self.cfg["stop_sound"])
        self.jobs.put((action, audio, self.started_at))

    # ------------------------------------------------------------ worker
    def _worker(self):
        while True:
            action, audio, started_at = self.jobs.get()
            try:
                self._process(action, audio, started_at)
            except Exception as e:
                traceback.print_exc()
                self._status(f"Error: {e}")
                self.overlay.hide()
                self._chime(self.cfg["error_sound"])
            finally:
                self.title = PAUSED if self.paused else IDLE

    def _process(self, action, audio, started_at):
        secs = duration(audio, self.cfg["sample_rate"])
        if secs < 0.25:
            self._status("Too short - nothing captured")
            self.overlay.hide()
            return

        app_name = inject.frontmost_app()
        raw = self.asr.transcribe(audio)
        if not raw.strip():
            self._status("No speech detected")
            self.overlay.hide()
            return

        if action == "edit":
            selection = inject.read_selection()
            if not selection:
                # nothing selected, so fall through and just dictate it
                action = "dictate"
            else:
                rewritten = polish.llm_edit(selection, raw, self.cfg)
                if not rewritten:
                    self._status("Edit failed - check llm_provider / API key")
                    self.overlay.hide()
                    self._chime(self.cfg["error_sound"])
                    return
                inject.insert(
                    rewritten,
                    self.cfg["inject_method"],
                    self.cfg["restore_clipboard"],
                )
                self.overlay.finish(rewritten)
                self._log(app_name, "edit", secs, started_at, raw, rewritten)
                return

        text = polish.apply_rules(raw, self.cfg)
        text = polish.llm_polish(text, self.cfg, app_name)
        if self.cfg.get("trailing_space") and not text.endswith((" ", "\n")):
            text += " "

        self.overlay.finish(text)
        inject.insert(text, self.cfg["inject_method"], self.cfg["restore_clipboard"])
        self._log(app_name, "dictate", secs, started_at, raw, text)

    def _log(self, app_name, mode, secs, started_at, raw, final):
        latency_ms = int((time.monotonic() - started_at - secs) * 1000)
        if self.cfg.get("save_history"):
            history.log(app_name, mode, secs, latency_ms, raw, final)
        preview = final.strip().replace("\n", " ")
        self._status(f"{preview[:48]}{'…' if len(preview) > 48 else ''}")

    # ------------------------------------------------------------ menu
    def _status(self, text):
        self.menu["Status: starting"].title = f"Status: {text}"[:70]

    def _chime(self, path):
        if not self.cfg.get("play_sounds"):
            return
        try:
            subprocess.Popen(
                ["afplay", "-v", "0.35", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def toggle_pause(self, sender):
        self.paused = not self.paused
        sender.title = "Resume" if self.paused else "Pause"
        self.title = PAUSED if self.paused else IDLE
        self._status("Paused" if self.paused else "Ready")

    def show_recent(self, _):
        rows = history.recent(12)
        if not rows:
            rumps.alert("Recent dictations", "Nothing yet.")
            return
        body = "\n\n".join(
            f"{time.strftime('%H:%M', time.localtime(ts))}  [{app or '?'}]\n{final.strip()[:180]}"
            for ts, app, final in rows
        )
        rumps.alert("Recent dictations", body)

    def show_stats(self, _):
        s = history.stats()
        rumps.alert(
            "Stats",
            f"Dictations: {s['dictations']}\n"
            f"Words: {s['words']:,}\n"
            f"Avg latency after release: {s['avg_latency_ms']} ms\n"
            f"Time saved vs typing: {s['minutes_saved']:.0f} min",
        )

    def edit_config(self, _):
        subprocess.Popen(["open", "-t", str(config.CONFIG_PATH)])

    def reload_config(self, _):
        self.cfg = config.load()
        self.asr = Transcriber(self.cfg)
        threading.Thread(target=self._reload_models, daemon=True).start()
        self._status("Reloading model…")

    def _reload_models(self):
        try:
            self.asr.load()
            self.asr.warmup()
            self._status("Config reloaded (hotkeys need a restart)")
        except Exception as e:
            self._status(f"Error: {e}")

    def quit_app(self, _):
        try:
            self.recorder.close()
        except Exception:
            pass
        rumps.quit_application()


def run():
    app = FlowClone()
    app.boot()
    app.run()


if __name__ == "__main__":
    run()
