#!/bin/bash
# Installs Eraser (github.com/drumandbytes/eraser, MIT) - a free, open-source DeleteMe alternative - on
# the owner's Mac mini without Homebrew. Safe to run again: it upgrades in place.
# Donut runs it on a schedule (weekly report + 90-day re-sends); Eraser's own scheduler is NOT installed.
set -euo pipefail

DEST="$HOME/.local/eraser"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
case "$(uname -m)" in
  arm64) ARCH=arm64 ;;
  x86_64) ARCH=amd64 ;;
  *) echo "Unknown Mac CPU: $(uname -m)"; exit 1 ;;
esac

echo "1/4 Finding the latest Eraser release..."
API=$(curl -fsSL https://api.github.com/repos/drumandbytes/eraser/releases/latest)
TAG=$(printf '%s' "$API" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')
ASSET=$(printf '%s' "$API" | /usr/bin/python3 -c "
import json,sys
for a in json.load(sys.stdin)['assets']:
    n=a['name']
    if n.endswith('_darwin_$ARCH.tar.gz'): print(n); break")
[ -n "$ASSET" ] || { echo "No macOS ($ARCH) download in release $TAG"; exit 1; }
BASE="https://github.com/drumandbytes/eraser/releases/download/$TAG"
echo "    $TAG -> $ASSET"

echo "2/4 Downloading and checking the checksum..."
curl -fsSL -o "$TMP/$ASSET" "$BASE/$ASSET"
curl -fsSL -o "$TMP/checksums.txt" "$BASE/checksums.txt"
WANT=$(grep " $ASSET\$" "$TMP/checksums.txt" | awk '{print $1}')
GOT=$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')
if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
  echo "CHECKSUM MISMATCH - not installing. (wanted $WANT, got $GOT)"; exit 1
fi
echo "    checksum OK"

echo "3/4 Installing to $DEST ..."
mkdir -p "$DEST"
tar xzf "$TMP/$ASSET" -C "$TMP"
cp "$TMP/eraser" "$DEST/eraser"
chmod 755 "$DEST/eraser"
xattr -d com.apple.quarantine "$DEST/eraser" 2>/dev/null || true
"$DEST/eraser" --version || "$DEST/eraser" version || true

echo "4/4 Done."
echo
echo "NEXT: start the setup page and fill it in yourself:"
echo "    $DEST/eraser serve"
echo "then open http://localhost:8080 in a browser ON THE MAC."
echo "  - Email provider: Gmail, address you@example.com, and a NEW Gmail app password"
echo "    named 'Eraser' (myaccount.google.com/apppasswords). Turn on inbox monitoring with the same app password."
echo "  - Do NOT turn on Settings > Automation - Donut handles the schedule."
echo "  - When the wizard is done, press Ctrl-C in Terminal to stop the page."
