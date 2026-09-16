"""Getting text into whatever app the cursor is in.

Two strategies:
  paste - stash the clipboard, write our text, synthesise Cmd+V, restore.
          Instant regardless of length. Occasionally blocked by password
          fields and a few Electron apps.
  type  - synthesise the characters directly as Unicode key events. Works
          almost everywhere, but ~15ms per chunk, so it's slow for long text.
"""
import time

from AppKit import NSPasteboard, NSStringPboardType, NSWorkspace
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    kCGHIDEventTap,
)

FLAG_COMMAND = 0x00100000
KEY_V = 9
KEY_C = 8


# ---------------------------------------------------------------- context
def frontmost_app() -> str | None:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app else None


# ---------------------------------------------------------------- clipboard
def clipboard_get() -> str | None:
    return NSPasteboard.generalPasteboard().stringForType_(NSStringPboardType)


def clipboard_set(text: str) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSStringPboardType)


# ---------------------------------------------------------------- keystrokes
def _tap_key(keycode: int, flags: int = 0):
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, keycode, down)
        if flags:
            CGEventSetFlags(ev, flags)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.004)


def press_cmd_v():
    _tap_key(KEY_V, FLAG_COMMAND)


def press_cmd_c():
    _tap_key(KEY_C, FLAG_COMMAND)


def type_text(text: str, chunk: int = 12):
    """Send text as synthetic Unicode key events, 12 chars at a time.
    CGEventKeyboardSetUnicodeString tolerates more than one char per event but
    gets flaky past ~20, so we chunk."""
    for i in range(0, len(text), chunk):
        piece = text[i : i + chunk]
        ev = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(ev, len(piece), piece)
        CGEventPost(kCGHIDEventTap, ev)
        up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(up, len(piece), piece)
        CGEventPost(kCGHIDEventTap, up)
        time.sleep(0.006)


# ---------------------------------------------------------------- public
def insert(text: str, method: str = "paste", restore_clipboard: bool = True):
    if not text:
        return
    if method == "type":
        type_text(text)
        return

    previous = clipboard_get() if restore_clipboard else None
    clipboard_set(text)
    time.sleep(0.03)  # give the pasteboard a beat to settle
    press_cmd_v()
    if restore_clipboard:
        time.sleep(0.25)  # let the target app finish reading the pasteboard
        clipboard_set(previous if previous is not None else "")


def read_selection(timeout: float = 0.4) -> str | None:
    """Grab whatever is selected in the frontmost app by copying it.
    Returns None if nothing was selected (clipboard did not change)."""
    previous = clipboard_get()
    sentinel = "\x00flowclone\x00"
    clipboard_set(sentinel)
    press_cmd_c()

    deadline = time.time() + timeout
    got = None
    while time.time() < deadline:
        current = clipboard_get()
        if current is not None and current != sentinel:
            got = current
            break
        time.sleep(0.02)

    clipboard_set(previous if previous is not None else "")
    return got
