#!/usr/bin/env python3
"""Donut's runner for Eraser (data-broker removal requests, github.com/drumandbytes/eraser).

  eraser_job.py daily    every morning: keeps a removal round going (Eraser caps itself at 450 emails/day,
                         so a full round of ~700 brokers takes 2 days). Starts a new round 90 days after the
                         last one began. Silent unless a round starts/finishes or something fails.
  eraser_job.py weekly   Sunday report: checks the inbox for broker replies, then lists what needs the owner
                         (confirmation links, web forms, ID requests).
  eraser_job.py status   same as weekly, without checking the inbox.

Eraser itself holds the owner's details and the Gmail app password (~/.eraser/config.yaml, entered by the owner in
its setup page). This script never reads or prints them; it only runs Eraser and reads its history counts.
"""
import datetime as dt, json, sqlite3, subprocess, sys
from pathlib import Path

ERASER = Path.home() / ".local/eraser/eraser"
CONF = Path.home() / ".eraser/config.yaml"
DB = Path.home() / ".eraser/history.db"
STATE = Path.home() / ".hermes/eraser_state.json"
LOG = Path.home() / ".hermes/logs/eraser_job.log"
ROUND_DAYS = 90
TODAY = dt.date.today()


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def run(args, timeout=3600):
    r = subprocess.run([str(ERASER)] + args, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    log(f"eraser {' '.join(args)} -> rc {r.returncode}; last line: {out.strip().splitlines()[-1][:160] if out.strip() else ''}")
    return r.returncode, out


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save(st):
    STATE.write_text(json.dumps(st, indent=1))


def q(sql, args=()):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def stats():
    """Latest pipeline status per broker, and open tasks for the owner."""
    if not DB.exists():
        return None
    try:
        return _stats()
    except sqlite3.Error as ex:
        log(f"history not readable yet: {ex}")
        return None


def _stats():
    latest = q("""SELECT pipeline_status, COUNT(*) FROM (
                    SELECT r.broker_id, r.pipeline_status FROM removal_requests r
                    WHERE r.status='sent' AND r.sent_at = (SELECT MAX(sent_at) FROM removal_requests x
                          WHERE x.broker_id=r.broker_id AND x.profile_id=r.profile_id AND x.status='sent')
                  ) GROUP BY pipeline_status""")
    by = {k: n for k, n in latest}
    tasks = q("SELECT broker_name, task_type, COALESCE(form_url,'') FROM pending_tasks WHERE status='pending' "
              "ORDER BY created_at")
    last = q("SELECT MAX(sent_at) FROM removal_requests WHERE status='sent'")[0][0]
    return {"by": by, "emailed": sum(by.values()), "tasks": tasks, "last_sent": last}


def ready():
    if not ERASER.exists():
        return "Eraser isn't installed yet (run ~/.hermes/scripts/install_eraser.sh)."
    if not CONF.exists():
        return "Eraser is installed but not set up - run ~/.local/eraser/eraser serve and finish the setup page."
    return None


def daily():
    why = ready()
    if why:
        return                                   # the weekly report reminds the owner; stay quiet daily
    st = state()
    s = stats()
    if not st:                                   # first run: adopt whatever the owner already sent
        if s and s["last_sent"]:
            st = {"round_started": str(TODAY), "round_open": True}
            save(st)
        else:
            st = {"round_started": str(TODAY), "round_open": True, "first": True}
            save(st)
            print("PRIVACY: Eraser is set up - Donut is starting your first round of data-broker removal requests "
                  "(about 450 emails today, the rest tomorrow). Replies will show up in your Gmail.")
    started = dt.date.fromisoformat(st["round_started"])
    if not st.get("round_open") and (TODAY - started).days >= ROUND_DAYS:
        run(["update-brokers"], timeout=300)
        st.update(round_started=str(TODAY), round_open=True)
        save(st)
        print(f"PRIVACY: {ROUND_DAYS} days are up - Donut is re-sending your data-broker removal requests "
              "(brokers re-list people, so this keeps you off their sites).")
    if not st.get("round_open"):
        return
    rc, out = run(["send"])
    if "Nothing to send" in out or "No brokers to process" in out:
        st.update(round_open=False, round_finished=str(TODAY))
        st.pop("first", None)
        save(st)
        s = stats() or {"emailed": 0}
        nxt = dt.date.fromisoformat(st["round_started"]) + dt.timedelta(days=ROUND_DAYS)
        print(f"PRIVACY: removal round finished - {s['emailed']} data brokers have your request. "
              f"Next round {nxt:%b %d}. Weekly report on Sundays.")
    elif rc != 0 or "❌ Failed" in out:
        fails = out.count("❌")
        auth = "auth" in out.lower() or "username and password" in out.lower()
        print("PRIVACY: Eraser had trouble sending today" +
              (" - Gmail refused the login; the app password in Eraser's settings may have been revoked."
               if auth else f" ({fails} failed). It will retry tomorrow."))


def weekly(check_inbox=True):
    why = ready()
    if why:
        print("PRIVACY (weekly): " + why)
        return
    if check_inbox:
        run(["monitor", "--days", "8"], timeout=900)
    s = stats()
    if not s or not s["emailed"]:
        print("PRIVACY (weekly): no removal requests sent yet - the first round starts on the next morning run.")
        return
    by = s["by"]
    done = by.get("confirmed", 0)
    waiting = by.get("email_sent", 0) + by.get("awaiting_response", 0)
    st = state()
    nxt = ""
    if st.get("round_started"):
        nxt = f" Next re-send round: {dt.date.fromisoformat(st['round_started']) + dt.timedelta(days=ROUND_DAYS):%b %d}."
    lines = [f"PRIVACY (weekly): {s['emailed']} data brokers emailed - {done} confirmed removal, "
             f"{waiting} still processing (they have 30-45 days), {by.get('rejected', 0)} refused.{nxt}"]
    what = {"confirm": "click the confirmation link they emailed you", "manual_form": "fill in their opt-out form",
            "captcha": "solve a CAPTCHA on their opt-out form",
            "review": "read their reply (if they want ID, skip unless it's a site scammers use)"}
    todo = [(n, what.get(t, t.replace("_", " ")), u) for n, t, u in s["tasks"]]
    if todo:
        lines.append(f"Needs you ({len(todo)}) - about 2 minutes each:")
        for n, what, u in todo[:10]:
            lines.append(f"  - {n}: {what}" + (f" {u}" if u else ""))
        if len(todo) > 10:
            lines.append(f"  ...and {len(todo) - 10} more - see them all: ~/.local/eraser/eraser serve, Pipeline page")
        lines.append("Tip: `~/.local/eraser/eraser confirm` clicks the confirmation links for you; "
                     "`eraser fill` fills the forms (both open Chrome).")
    else:
        lines.append("Nothing needs you this week.")
    print("\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "daily":
            daily()
        elif mode == "weekly":
            weekly()
        elif mode == "status":
            weekly(check_inbox=False)
        else:
            print(__doc__)
    except Exception as ex:
        log(f"ERROR {type(ex).__name__}: {ex}")
        print(f"PRIVACY: Donut's Eraser job hit an error ({type(ex).__name__}: {ex}). Log: ~/.hermes/logs/eraser_job.log")
        sys.exit(1)
