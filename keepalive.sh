#!/bin/bash
# Watchdog for FlowClone. launchd runs this every StartInterval seconds.
#
# Why not just KeepAlive on the app itself: the login agent launches via
# `open -a` so that macOS attributes the Microphone / Accessibility / Input
# Monitoring grants to FlowClone.app rather than to the shared Homebrew python
# binary. But `open` exits the moment the app is up, so launchd's KeepAlive
# would see it "die" instantly and relaunch in a tight loop. Polling for the
# real python process instead gets both.
#
# Deliberately lives OUTSIDE the .app bundle: editing anything inside it would
# invalidate the ad-hoc code signature and can drop the permission grants.

# Is the real app up? Match "-m flow.main" AND confirm the process is Python.
# A bare `pgrep -f flow.main` is not enough: it also matches any OTHER pgrep,
# grep or pkill command that happens to carry that string on its command line,
# which silently makes the watchdog think a dead app is alive.
is_running() {
  local pids
  pids=$(pgrep -f -- "-m flow\.main" 2>/dev/null) || return 1
  [ -n "$pids" ] || return 1
  ps -o comm= -p $pids 2>/dev/null | grep -qi python
}

is_running && exit 0

# Pause switch: `touch ~/.flowclone/paused` stops the watchdog reviving it.
[ -f "$HOME/.flowclone/paused" ] && exit 0

echo "$(date '+%Y-%m-%d %H:%M:%S') flowclone not running, relaunching" >> "$HOME/.flowclone/launch.log"
open -a "$HOME/Applications/FlowClone.app"
