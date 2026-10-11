#!/bin/bash
# Installs Donut's trip travel tools (about 2 minutes, ~60 MB). Safe to run again.
#   icalendar     - builds the trip calendar file        holidays        - public holidays per country
#   FlightRadarAPI - live flight status (unofficial)     Open-Meteo / Frankfurter / Transitous - free web services, no keys
set -uo pipefail
PY="$HOME/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11"
[ -x "$PY" ] || PY="$(grep '^home' "$HOME/.hermes/hermes-agent/venv/pyvenv.cfg" | cut -d= -f2 | xargs)/python3.11"
[ -x "$PY" ] || { echo "Can't find Python 3.11 (the one Hermes uses). Stopping."; exit 1; }
V="$HOME/.local/travel"
S="$HOME/.hermes/scripts"

echo "=== 1/4 Libraries -> ~/.local/travel"
[ -x "$V/venv/bin/python" ] || "$PY" -m venv "$V/venv"
"$V/venv/bin/pip" install -q --upgrade pip
"$V/venv/bin/pip" install -q "icalendar==7.3.0" "holidays==0.106" "FlightRadarAPI==1.6.1" || { echo "pip install failed - send this output to Claude."; exit 1; }
echo "OK"

echo; echo "=== 2/4 Self-test (each service once)"
"$V/venv/bin/python" "$S/trip_tools.py" selftest

echo; echo "=== 3/4 Trip calendar"
"$V/venv/bin/python" "$S/trip_tools.py" calendar
"$V/venv/bin/python" - <<'PYEOF'
import json, pathlib
p = pathlib.Path.home() / ".hermes/textme.json"
if p.exists():
    c = json.loads(p.read_text()); c.setdefault("send", {}).setdefault("flight", True)
    p.write_text(json.dumps(c, indent=1)); print("Flight alerts will also go by text when iMessage works.")
PYEOF

echo; echo "=== 4/4 Scheduled jobs for Donut"
LIST="$(hermes cron list 2>/dev/null)"
if echo "$LIST" | grep -q "Flight watch"; then echo "  Flight watch already scheduled"
else hermes cron create "*/30 * * * *" --name "Flight watch (trip)" --script flight_watch.py --no-agent --deliver telegram \
  && echo "  Flight watch: every 30 min (only does anything 6 h before a trip flight)"; fi
if echo "$LIST" | grep -q "Trip calendar"; then echo "  Calendar refresh already scheduled"
else hermes cron create "15 2 * * *" --name "Trip calendar refresh" --script trip_calendar_nightly.py --no-agent --deliver telegram \
  && echo "  Calendar refresh: nightly 2:15 AM (messages you only if the trip changed)"; fi
hermes gateway restart >/dev/null 2>&1 && echo "  Donut restarted"
echo; echo "DONE. Send this output to Claude."
