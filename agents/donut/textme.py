#!/usr/bin/env python3
"""Texts the owner's iPhone. Sends from Donut's own iMessage number (Photon) when the gateway is up, otherwise
from the Mac's Messages app. It only ever texts the number in ~/.hermes/textme.json, never anyone else.

  textme.py test              send a test text
  textme.py send "message"    send a text now
  textme.py status            show settings and the last few sends

Settings (~/.hermes/textme.json, created on first run):
  "to":   who gets the texts (the owner's iPhone number or iCloud email)
  "send": which alerts also go by text - "sentinel" (website alerts), "standup" (7:30 morning standup)
Other scripts use it as:  from textme import text_robert;  text_robert("sentinel", "message")
  "via":  "auto" (default: Donut's number, then Messages), "photon" or "messages" to force one.
Fallback needs: Messages on the Mac signed in (Messages > Settings > iMessage).
"""
import datetime as dt, json, re, subprocess, sys
from pathlib import Path

H = Path.home() / ".hermes"
CONF = H / "textme.json"
LOG = H / "logs" / "textme.log"
DEFAULT = {"to": "+15555550100", "send": {"sentinel": True, "standup": True}}
MAX = 3500
LOCKED_TO = "+15555550100"   # HARD LOCK: the owner's iPhone. The only number this script will ever text.

APPLESCRIPT = '''
on run argv
    set theTo to item 1 of argv
    set theText to item 2 of argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        try
            send theText to participant theTo of svc
        on error
            send theText to buddy theTo of svc
        end try
    end tell
end run
'''


def conf():
    if not CONF.exists():
        CONF.write_text(json.dumps(DEFAULT, indent=1))
    c = json.loads(CONF.read_text())
    c.setdefault("send", {})
    return c


def log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")
    except OSError:
        pass


PHOTON_RECORD = H / "runtime" / "photon-sidecar.json"


def send_photon(to, text):
    """Send from Donut's own iMessage number (Photon, via the gateway's local sidecar). Returns (ok, error)."""
    try:
        rec = json.loads(PHOTON_RECORD.read_text())
        port, token = int(rec["port"]), rec["token"]
    except Exception:
        return False, "Photon isn't running (gateway down or Photon off)"
    import urllib.request
    body = json.dumps({"spaceId": to, "text": text[:7900]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/send", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Hermes-Sidecar-Token": token})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # local only, never a proxy
        with opener.open(req, timeout=45) as r:
            data = json.loads(r.read().decode() or "{}")
        return (True, "") if data.get("ok") else (False, f"Photon said: {str(data)[:150]}")
    except Exception as ex:
        return False, f"Photon: {type(ex).__name__}: {str(ex)[:150]}"


def send(text):
    """Send one text: from Donut's number (Photon) first, else from the Mac's Messages app.
    Set "via" in textme.json to "photon" or "messages" to force one. Returns (ok, error_text). Never raises."""
    c = conf()
    to = re.sub(r"[^\d+]", "", c.get("to", "") or LOCKED_TO)
    if len(to) == 10:
        to = "+1" + to
    if to != LOCKED_TO:                          # someone changed textme.json - refuse and say so
        log(f"BLOCKED - textme.json 'to' is not the owner's number; nothing sent")
        return False, "blocked: texts can only go to the owner's number (+15555550100)"
    text = text.strip()
    via = c.get("via", "auto")
    if via in ("auto", "photon"):
        ok, err = send_photon(to, text)
        log(("SENT via Donut's number " if ok else "FAILED via Donut's number ") + f"{len(text)} chars"
            + ("" if ok else f" - {err}"))
        if ok or via == "photon":
            return ok, err
    if len(text) > MAX:
        text = text[:MAX].rstrip() + "\n...(full version on Telegram)"
    try:
        r = subprocess.run(["/usr/bin/osascript", "-", to, text], input=APPLESCRIPT, capture_output=True,
                           text=True, timeout=150)
        ok = r.returncode == 0
        err = (r.stderr or "").strip()
    except Exception as ex:
        ok, err = False, f"{type(ex).__name__}: {ex}"
    log(("SENT via Messages " if ok else "FAILED via Messages ") + f"{len(text)} chars" + ("" if ok else f" - {err[:200]}"))
    return ok, err


def text_robert(kind, text):
    """Called by other scripts. Only sends if that kind of alert is switched on. Never raises."""
    try:
        if conf()["send"].get(kind):
            return send(text)[0]
    except Exception as ex:
        log(f"FAILED {kind} - {type(ex).__name__}: {ex}")
    return False


def explain(err):
    e = err.lower()
    if e.startswith("blocked"):
        return "Nothing was sent. Only the owner can change who gets texts."
    if e.startswith("photon"):
        return "Donut's number didn't work - check the gateway is running (hermes gateway status) and try again."
    if "-1743" in e or "not authorized" in e or "not allowed" in e:
        return ("macOS blocked it. Open System Settings > Privacy & Security > Automation and allow Terminal "
                "(and python / hermes, if listed) to control Messages, then try again.")
    if "-1728" in e or "can't get account" in e or "can’t get account" in e:
        return "Messages isn't signed in to iMessage. Open Messages > Settings > iMessage and sign in to the Mac's Apple ID."
    return "Open the Messages app on the Mac and check it's signed in to iMessage, then try again."


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "status":
        c = conf()
        print(f"Texts go to: {c.get('to')}")
        print("Alerts by text: " + ", ".join(f"{k} {'ON' if v else 'off'}" for k, v in c["send"].items()))
        if LOG.exists():
            print("Last sends:\n  " + "\n  ".join(LOG.read_text().splitlines()[-5:]))
    elif a[0] == "test":
        ok, err = send("Donut here - alerts now come from this number. You can reply right here.")
        print("OK - test text sent. Check your iPhone." if ok else f"FAILED - {err}\n{explain(err)}")
        sys.exit(0 if ok else 1)
    elif a[0] == "send" and len(a) > 1:
        ok, err = send(" ".join(a[1:]))
        print("OK - sent." if ok else f"FAILED - {err}\n{explain(err)}")
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
