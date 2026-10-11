#!/bin/bash
# Sterling's finances: Beancount (the books), beangulp/beanquery (import + queries), Fava (dashboard on this Mac only).
# ~2 minutes. Safe to run again.
set -uo pipefail
PY="$HOME/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11"
[ -x "$PY" ] || PY="$(grep '^home' "$HOME/.hermes/hermes-agent/venv/pyvenv.cfg" | cut -d= -f2 | xargs)/python3.11"
[ -x "$PY" ] || { echo "Can't find Python 3.11. Stopping."; exit 1; }
V="$HOME/.local/finance"; FIN="$HOME/Documents/Finances"; PORT=5055

echo "=== 1/4 Libraries -> ~/.local/finance"
[ -x "$V/venv/bin/python" ] || "$PY" -m venv "$V/venv"
"$V/venv/bin/pip" install -q --upgrade pip
"$V/venv/bin/pip" install -q "beancount==3.2.3" "beangulp==0.2.0" "beanquery==0.2.0" "fava==1.30.16" \
  || { echo "pip install failed - send this to Claude."; exit 1; }
echo OK

echo; echo "=== 2/4 Books"
[ -f "$FIN/main.beancount" ] || { echo "Missing $FIN/main.beancount - tell Claude."; exit 1; }
"$V/venv/bin/python" "$HOME/.hermes/scripts/fin_tool.py" check

echo; echo "=== 3/4 Dashboard (Fava) at http://127.0.0.1:$PORT - this Mac only"
PLIST="$HOME/Library/LaunchAgents/local.sterling.fava.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.sterling.fava</string>
  <key>ProgramArguments</key><array>
    <string>$V/venv/bin/fava</string><string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string><string>$FIN/main.beancount</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/fava.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/fava.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 1; done
curl -s -o /dev/null -w "  dashboard answers: HTTP %{http_code}\n" "http://127.0.0.1:$PORT/"

echo; echo "=== 4/4 Sterling's schedule"
LIST="$(hermes -p sterling cron list 2>/dev/null)"
echo "$LIST" | grep -q "Monthly finance report" || hermes -p sterling cron create "0 8 1 * *" --name "Monthly finance report" \
  --script fin_monthly.py --no-agent --deliver telegram >/dev/null && echo "  Monthly report: 1st of each month, 8 AM"
echo "$LIST" | grep -q "Bill reminders" || hermes -p sterling cron create "0 9 * * 1" --name "Bill reminders" \
  --script fin_bills.py --no-agent --deliver telegram >/dev/null && echo "  Bill reminders: scheduled every Monday (texts 2 days before each due date)"
echo; echo "DONE. Send this output to Claude, then tell Sterling /new."
