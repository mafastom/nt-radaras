#!/bin/bash
# NT radaras: įdiegimas / atnaujinimas Mac'e.
#   curl -fsSL https://raw.githubusercontent.com/mafastom/nt-radaras/main/install.sh | bash
set -euo pipefail

DIR="$HOME/NT-radaras"
REPO="https://github.com/mafastom/nt-radaras/archive/refs/heads/main.tar.gz"
PLIST="$HOME/Library/LaunchAgents/lt.ntradaras.plist"
PORT=8765

echo "▸ Diegiamas NT radaras į $DIR"

if ! /usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 9)' >/dev/null 2>&1; then
  echo
  echo "Reikia Apple „Command Line Tools“ (juose yra Python)."
  echo "Atsidarys langas: paspauskite „Install“. Kai diegimas baigsis, įklijuokite šią komandą dar kartą."
  xcode-select --install >/dev/null 2>&1 || true
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$REPO" | tar -xz -C "$TMP"
SRC="$TMP/nt-radaras-main"

mkdir -p "$DIR/docs"
cp "$SRC/app.py" "$SRC/monitor.py" "$SRC/requirements.txt" "$SRC/README.md" "$SRC/uninstall.sh" "$DIR/"
cp "$SRC/docs/index.html" "$DIR/docs/"
# jūsų nustatymai ir rasti skelbimai atnaujinant neperrašomi
[ -f "$DIR/config.yaml" ] || cp "$SRC/config.yaml" "$DIR/"

echo "▸ Ruošiama Python aplinka (gali užtrukti minutę)"
[ -x "$DIR/.venv/bin/python3" ] || /usr/bin/python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/python3" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$DIR/.venv/bin/python3" -m pip install -q -r "$DIR/requirements.txt"

echo "▸ Nustatomas automatinis paleidimas"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>lt.ntradaras</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python3</string>
    <string>$DIR/app.py</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/radaras.log</string>
  <key>StandardErrorPath</key><string>$DIR/radaras.log</string>
</dict>
</plist>
PLISTEOF
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

# nuoroda darbalaukyje
cat > "$HOME/Desktop/NT radaras.webloc" <<WEBLOC
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>URL</key><string>http://localhost:$PORT</string></dict></plist>
WEBLOC

# bandomasis pranešimas, kad macOS paklaustų leidimo
osascript -e 'display notification "Čia matysite naujus skelbimus" with title "NT radaras įdiegtas" sound name "Glass"' >/dev/null 2>&1 || true

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fs "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break
  sleep 1
done
open "http://localhost:$PORT"

echo
echo "✅ NT radaras veikia: http://localhost:$PORT"
echo "   Nuoroda „NT radaras“ yra jūsų darbalaukyje. Radaras pasileis pats, kai įjungsite Mac'ą."
