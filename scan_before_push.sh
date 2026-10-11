#!/bin/bash
# Run before every push: fails if anything that looks like a secret or personal detail slipped in.
cd "$(dirname "$0")"
FAIL=0
check() { if grep -rInE --exclude=scan_before_push.sh --exclude=.denylist --exclude-dir=.git "$1" . ; then echo ">> found: $2"; FAIL=1; fi; }
check '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN)' "API key / private key"
check '[0-9]{8,10}:[A-Za-z0-9_-]{30,}' "Telegram bot token"
check '@(gmail|icloud|yahoo|outlook|hotmail)\.com' "personal email"
check '192\.168\.(50)\.' "home network address"
[ -f .denylist ] && while read -r w; do [ -n "$w" ] && check "$w" "denylisted word"; done < .denylist
[ $FAIL -eq 0 ] && echo "OK - nothing sensitive found." || { echo "STOP - fix the lines above before pushing."; exit 1; }
