"""Global modifier-hold hotkey listener built on a CoreGraphics event tap.

Runs its own CFRunLoop on a background thread so it does not fight with the
menu-bar app's NSApplication loop. Requires Accessibility + Input Monitoring
permission for whatever binary is hosting the process (Terminal, or the .app
if you bundle it).
"""
import threading
import time

from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventFlagsChanged,
    kCGEventTapOptionListenOnly,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# event types the tap can hand us when it gets killed
kCGEventTapDisabledByTimeout = 0xFFFFFFFE
kCGEventTapDisabledByUserInput = 0xFFFFFFFF

FLAG_CONTROL = 0x00040000
FLAG_SHIFT = 0x00020000
FLAG_COMMAND = 0x00100000
FLAG_OPTION = 0x00080000
FLAG_FN = 0x00800000

# keycode -> (flag mask, name). Left/right variants share a flag mask, so we
# key off the keycode in the flagsChanged event to tell them apart.
KEYS = {
    "fn":           (63, FLAG_FN),
    "right_cmd":    (54, FLAG_COMMAND),
    "left_cmd":     (55, FLAG_COMMAND),
    "right_option": (61, FLAG_OPTION),
    "left_option":  (58, FLAG_OPTION),
    "right_ctrl":   (62, FLAG_CONTROL),
    "left_ctrl":    (59, FLAG_CONTROL),
    "right_shift":  (60, FLAG_SHIFT),
}


class HotkeyListener:
    """Watches one or more modifier keys and fires press/release callbacks.

    bindings: {"dictate": "fn", "edit": "right_option"}
    on_press(name) / on_release(name, held_seconds)
    """

    def __init__(self, bindings: dict, on_press, on_release):
        self.bindings = {}
        for action, keyname in bindings.items():
            if keyname not in KEYS:
                raise ValueError(f"unknown hotkey {keyname!r}; pick from {list(KEYS)}")
            keycode, mask = KEYS[keyname]
            self.bindings[keycode] = (action, mask)
        self.on_press = on_press
        self.on_release = on_release
        self._down_at: dict[str, float] = {}
        self._tap = None
        self._thread = None

    # -- event tap ------------------------------------------------------
    def _callback(self, proxy, etype, event, refcon):
        if etype in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
            # macOS kills slow taps. Ours is listen-only and cheap, but re-arm
            # anyway rather than silently dying.
            CGEventTapEnable(self._tap, True)
            return event

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        binding = self.bindings.get(keycode)
        if binding is None:
            return event

        action, mask = binding
        pressed = bool(CGEventGetFlags(event) & mask)

        if pressed and action not in self._down_at:
            self._down_at[action] = time.monotonic()
            self._safe(self.on_press, action)
        elif not pressed and action in self._down_at:
            held = time.monotonic() - self._down_at.pop(action)
            self._safe(self.on_release, action, held)
        return event

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception:  # never let a handler kill the tap
            import traceback
            traceback.print_exc()

    def _run(self):
        self._tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            CGEventMaskBit(kCGEventFlagsChanged),
            self._callback,
            None,
        )
        if self._tap is None:
            raise RuntimeError(
                "Could not create event tap. Grant Accessibility and Input "
                "Monitoring permission to the host app in "
                "System Settings > Privacy & Security."
            )
        source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkey")
        self._thread.start()
        return self
