#!/bin/bash
# NT radaras: pašalinimas.  bash ~/NT-radaras/uninstall.sh
PLIST="$HOME/Library/LaunchAgents/lt.ntradaras.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST" "$HOME/Desktop/NT radaras.webloc"
rm -rf "$HOME/NT-radaras"
echo "NT radaras pašalintas."
