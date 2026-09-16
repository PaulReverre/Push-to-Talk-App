"""Floating on-screen dictation indicator.

A borderless pill near the bottom of the screen showing recording state, a live
level meter, elapsed time, and the draft transcript as it comes in.

Two hard constraints shape this file:

1. **It must never take focus.** The whole app works by pasting into whatever
   window is frontmost. If this panel became key, `frontmost_app()` would
   report FlowClone and the paste would land here instead of in your document.
   Hence NSPanel + nonactivating + canBecomeKeyWindow=False + ignoresMouseEvents.

2. **AppKit is main-thread only.** Worker threads call `update()`, which just
   stores plain Python state under a lock. The actual drawing happens in
   `render()`, driven by a rumps.Timer on the main thread.
"""
import threading
import time

import objc
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
)

# NSWindowStyleMask bits. NSPanel + nonactivating is what keeps focus where it is.
NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskNonactivatingPanel = 1 << 7
NSStatusWindowLevel = 25

WIDTH, HEIGHT = 560.0, 68.0
BOTTOM_MARGIN = 130.0
N_BARS = 14


class _PillView(NSView):
    """Draws the rounded background, the level meter and the text."""

    def initWithFrame_(self, frame):
        # objc.super, not builtin super() - PyObjC needs its own super for
        # ObjC subclasses or the designated initialiser chain misbehaves.
        self = objc.super(_PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._text = ""
        self._level = 0.0
        self._bars = [0.0] * N_BARS
        self._mode = "idle"      # idle | recording | thinking | done
        self._elapsed = 0.0
        return self

    def isFlipped(self):
        return True

    # -- painting -------------------------------------------------------
    def drawRect_(self, rect):
        b = self.bounds()

        NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 16.0, 16.0).fill()

        accent = {
            "recording": NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.30, 0.32, 1.0),
            "thinking":  NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.76, 0.20, 1.0),
            "done":      NSColor.colorWithCalibratedRed_green_blue_alpha_(0.36, 0.82, 0.45, 1.0),
        }.get(self._mode, NSColor.colorWithCalibratedWhite_alpha_(0.6, 1.0))

        # status dot
        accent.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(18, HEIGHT / 2 - 5, 10, 10)).fill()

        # level meter
        x = 40.0
        for v in self._bars:
            h = max(3.0, min(1.0, v * 7.0) * 30.0)
            y = HEIGHT / 2 - h / 2
            accent.colorWithAlphaComponent_(0.35 + 0.65 * min(1.0, v * 7.0)).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, 3.0, h), 1.5, 1.5
            ).fill()
            x += 6.0

        # text
        label = self._text
        if not label:
            label = {
                "recording": "Listening…",
                "thinking": "Transcribing…",
                "done": "Done",
            }.get(self._mode, "")
        if label:
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(14.0),
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.96, 1.0),
            }
            s = NSAttributedString.alloc().initWithString_attributes_(label, attrs)
            avail = WIDTH - 150.0
            size = s.size()
            # keep the tail visible: text grows leftward once it overflows
            tx = 132.0
            ty = HEIGHT / 2 - size.height / 2
            if size.width > avail:
                tx -= size.width - avail
            s.drawAtPoint_((tx, ty))

        if self._mode == "recording":
            t = f"{self._elapsed:0.1f}s"
            attrs = {
                NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(11.0, 0.0),
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0),
            }
            s = NSAttributedString.alloc().initWithString_attributes_(t, attrs)
            s.drawAtPoint_((WIDTH - s.size().width - 16.0, HEIGHT / 2 - s.size().height / 2))


class Overlay:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._state = {"mode": "idle", "text": "", "level": 0.0, "started": 0.0}
        self._panel = None
        self._view = None
        self._hide_at = 0.0

    # -- thread-safe API (called from worker threads) --------------------
    def update(self, **kw):
        with self._lock:
            self._state.update(kw)

    def show(self, mode):
        self.update(mode=mode, started=time.monotonic() if mode == "recording" else self._state.get("started", 0.0))
        if mode == "recording":
            self.update(text="")
        self._hide_at = 0.0

    def finish(self, text, linger=1.4):
        self.update(mode="done", text=text)
        self._hide_at = time.monotonic() + linger

    def hide(self):
        self.update(mode="idle", text="")
        self._hide_at = 0.0
        if self._panel is not None:
            self._panel.orderOut_(None)

    # -- main-thread only -------------------------------------------------
    def _build(self):
        screen = NSScreen.mainScreen()
        if screen is None:
            return False
        sf = screen.frame()
        rect = NSMakeRect(
            sf.origin.x + (sf.size.width - WIDTH) / 2.0,
            sf.origin.y + BOTTOM_MARGIN,
            WIDTH, HEIGHT,
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(NSStatusWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        )
        view = _PillView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        panel.setContentView_(view)
        self._panel, self._view = panel, view
        return True

    def render(self):
        """Called on the main thread by a rumps.Timer, ~12x/sec."""
        if not self.cfg.get("overlay", True):
            return
        with self._lock:
            st = dict(self._state)

        if st["mode"] == "idle":
            if self._panel is not None and self._panel.isVisible():
                self._panel.orderOut_(None)
            return

        if self._hide_at and time.monotonic() > self._hide_at:
            self.hide()
            return

        if self._panel is None and not self._build():
            return

        v = self._view
        v._mode = st["mode"]
        v._text = st["text"]
        v._elapsed = (time.monotonic() - st["started"]) if st["mode"] == "recording" else 0.0
        lvl = st["level"] if st["mode"] == "recording" else 0.0
        v._bars = v._bars[1:] + [lvl]
        v.setNeedsDisplay_(True)

        if not self._panel.isVisible():
            # orderFrontRegardless, NOT makeKeyAndOrderFront - taking key would
            # change the frontmost app and break paste targeting.
            self._panel.orderFrontRegardless()
