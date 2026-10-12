#!/usr/bin/env bash
# Install (or reinstall) the weekly refresh as a launchd job for this user.
#
# launchd is per-machine state, so nothing here is tracked beyond the template:
# this script writes the real plist into ~/Library/LaunchAgents with the
# checkout path filled in.
#
#   scripts/install_schedule.sh          install and load
#   scripts/install_schedule.sh --remove unload and delete

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.dcbudget.weekly"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$TARGET"
  echo "removed $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/data/logs"
sed "s|__REPO__|$REPO|g" "$REPO/scripts/com.dcbudget.weekly.plist" > "$TARGET"

# bootout first so reinstalling picks up an edited schedule.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "next run:"
launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -iE 'runs|state' | head -5 || true
echo
echo "run it now without waiting:  launchctl kickstart -p gui/$(id -u)/$LABEL"
echo "remove it:                   scripts/install_schedule.sh --remove"
