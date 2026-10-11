#!/usr/bin/env python3
"""Donut's morning digest: one Telegram message with everything the owner needs to know.

Reads (never changes): last night's fleet report, today's job list, the follow-up tracker,
Athena's deadlines, Vlad's briefing run, and the Mac's health. No arguments.
"""
import contextlib, datetime as dt, io, json, re, runpy, shutil, sqlite3, subprocess, sys, urllib.request
from pathlib import Path

H = Path.home() / ".hermes"
P = H / "profiles"
TODAY = dt.date.today()
needs = []


def section(title, lines):
    return [title] + [f"  {l}" for l in lines] + [""]


def fleet():
    """Per-agent rows from last night's fleet report: {name: (status, conversations, files, errors, problems)}."""
    rows = {}
    f = H / "audits" / "supervisor" / f"{TODAY}.md"
    if not f.exists():
        needs.append("Last night's fleet report is missing - the 1:30am Donut job may not have run.")
        return rows
    for line in f.read_text(errors="replace").splitlines():
        m = re.match(r"\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|$", line)
        if m and m.group(1) != "Agent":
            name, status, conv, files, errs, probs = m.groups()
            probs = "" if probs in ("", "—") else probs
            rows[name] = (status, int(conv), int(files), int(errs), probs)
            if "do NOT exist" in probs:
                needs.append(f"{name} claimed files that don't exist - see the fleet report.")
            if "nearly full" in probs:
                needs.append(f"{name}'s memory is nearly full.")
    return rows


def yesterday(rows, name):
    r = rows.get(name)
    if not r:
        return "no report from last night"
    status, conv, files, errs, probs = r
    if status == "IDLE":
        return "quiet - no conversations"
    return f"{conv} conversation(s), {files} file(s) made, {errs} tool error(s)"


def blocker(rows, name):
    r = rows.get(name)
    return r[4][:120] if r and r[4] else "none"


def jobs():
    f = Path.home() / "Documents/Reginald/jobs/daily" / f"{TODAY}.json"
    out = []
    if f.exists():
        d = json.load(open(f))
        js = d.get("jobs", [])
        out.append(f"{len(js)} new jobs in Reginald's list" + (" - top picks:" if js else "."))
        for j in js[:3]:
            out.append(f"  {j['n']}. {j['title'][:50]} - {j['company'][:25]} ({j.get('fit', '')})")
    else:
        out.append("No job list yet today (it runs at 7:15).")
    try:
        t = runpy.run_path(str(P / "reginald/scripts/tracker.py"), run_name="digest")
        _, rows = t["load"]()
        apps = t["open_apps"](rows)
        out.append(f"{len(apps)} open application(s).")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            t["daily"](drafts=False)          # follow-ups due + "did you apply?" reminders, no long drafts
        out += [l for l in buf.getvalue().splitlines() if l.strip()]
    except Exception as ex:
        out.append(f"(application tracker unavailable: {type(ex).__name__})")
    return out


def school():
    """Athena's school-today update (reads the portal calendar feed - read only - and her planner)."""
    try:
        r = subprocess.run([sys.executable, str(P / "athena/scripts/today.py")], capture_output=True, text=True, timeout=120)
        lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("SCHOOL TODAY")]
        if lines:
            if any("overdue" in l.lower() for l in lines):
                needs.append("Something for school is overdue - see Athena's section.")
            return lines
    except Exception:
        pass
    f = P / "athena/school/deadlines.json"
    if not f.exists():
        return ["No deadlines saved yet."]
    return ["(Athena's school update didn't run - ask her \"what should I do for school today?\")"]


def run_script(name, timeout=90, args=()):
    """Run one of Donut's read-only helper scripts and return its non-empty lines."""
    try:
        r = subprocess.run([sys.executable, str(Path.home() / ".hermes/scripts" / name), *args], capture_output=True,
                           text=True, timeout=timeout)
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception as ex:
        return [f"({name} didn't run: {type(ex).__name__})"]


def your_day():
    """Calendar (read-only iCal links) + emails worth a look (read-only, flag-only)."""
    out = []
    cal = run_script("calendar_feed.py")
    if cal and cal != ["No calendar linked yet."]:
        out.append("Calendar:")
        out += ["  " + l for l in cal]
    mail = run_script("inbox_triage.py", timeout=150)
    if mail and mail != ["No mailbox linked yet (see ~/.hermes/inbox.env)."]:
        out.append("Email:")
        out += ["  " + l for l in mail]
        if any("couldn't sign in" in l for l in mail):
            needs.append("Inbox triage couldn't sign in - check the app password in ~/.hermes/inbox.env.")
    return out


TRIP = Path.home() / "Documents/Trip Planning/My Trip/00 TRIP MASTER.md"


def trip():
    """Countdown, today's trip plan while travelling, weather/rates/flights (trip_tools.py), open to-dos."""
    import datetime as _dt
    start, end = _dt.date(2026, 10, 21), _dt.date(2026, 11, 5)
    today = _dt.date.today()
    if today > end or not TRIP.exists():
        return []
    text = TRIP.read_text(encoding="utf-8", errors="replace")
    todos = [l.strip()[6:] for l in text.splitlines() if l.strip().startswith("- [ ]")]
    out = []
    if today < start:
        out.append(f"{(start - today).days} days to go (leave Wed Oct 21, Dulles 11:15 PM).")
    else:
        key = f"{today:%a} {today:%b} {today.day}"
        day = next((l.strip()[2:] for l in text.splitlines() if l.strip().startswith(f"- {key}")), "")
        if not day:                                   # a range line like "- Mon Nov 2 - Wed Nov 4 - City"
            for l in text.splitlines():
                m = re.match(r"^- \w{3} (\w{3}) (\d{1,2}) - \w{3} (\w{3}) (\d{1,2}) - (.*)", l.strip())
                if m:
                    a = _dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {today.year}", "%b %d %Y").date()
                    b = _dt.datetime.strptime(f"{m.group(3)} {m.group(4)} {today.year}", "%b %d %Y").date()
                    if a <= today <= b:
                        day = "x - " + m.group(5)
        out.append("Today: " + (day.split(" - ", 1)[-1] if day else "see the master plan"))
    if (start - today).days <= 7 and (Path.home() / ".hermes/scripts/trip_tools.py").exists():
        out += [l for l in run_script("trip_tools.py", timeout=120, args=("standup",)) if not l.startswith("(")]
    if todos:
        out.append(f"Open to-dos ({len(todos)}): " + "; ".join((t.split(" (")[0])[:55] for t in todos[:3])
                   + (" ..." if len(todos) > 3 else ""))
    return out


def vlad():
    try:
        con = sqlite3.connect(f"file:{P / 'vlad/cron/executions.db'}?mode=ro", uri=True)
        rows = con.execute("select job_id, status, delivery_outcome, started_at from executions "
                           "where started_at >= ? order by started_at", (str(TODAY),)).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
    jobs = {}
    try:
        for j in json.load(open(P / "vlad/cron/jobs.json"))["jobs"]:
            jobs[j["id"]] = j["name"]
    except (OSError, ValueError, KeyError):
        pass
    brief = [r for r in rows if "Briefing" in jobs.get(r[0], "")]
    if not brief:
        needs.append("Vlad's morning briefing hasn't run today.")
        return ["Morning briefing: not run yet."]
    s = brief[-1]
    ok = s[1] == "completed"
    if not ok:
        needs.append(f"Vlad's briefing ended with status '{s[1]}'.")
    out = [f"Morning briefing: {'sent' if ok else s[1]} (delivery: {s[2]})."]
    log = P / "vlad/briefs/sent.log"
    try:
        today_lines = [l for l in log.read_text().splitlines() if l.startswith(str(TODAY))]
    except OSError:
        today_lines = []
    for name in ("partner", "Nicole"):
        sent = any(f"SENT {name} " in l for l in today_lines)
        out.append(f"{name}'s brief: {'emailed' if sent else 'NOT sent yet'}")
        if not sent and dt.datetime.now().hour >= 8:
            needs.append(f"{name}'s brief wasn't emailed today.")
    return out


def health():
    out = []
    du = shutil.disk_usage("/")
    free = du.free / du.total
    out.append(f"Disk: {du.free / 1e9:.0f} GB free ({free:.0%})")
    if free < 0.15:
        needs.append(f"Mac disk is getting full ({du.free / 1e9:.0f} GB free).")
    try:
        ps = json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5).read())
        names = [m["name"] for m in ps.get("models", [])]
        out.append("Ollama: running" + (f", {', '.join(names)} loaded" if names else ", no model loaded"))
    except Exception:
        out.append("Ollama: NOT RESPONDING")
        needs.append("Ollama isn't responding - the agents can't think until it's restarted.")
    try:
        g = json.load(open(H / "gateway_state.json"))
        down = [k for k, v in g.get("platforms", {}).items()
                if v.get("state") not in ("connected",) and ("telegram" in k or k == "email")]
        out.append(f"Hermes: {g.get('gateway_state')}" + (f"; not connected: {', '.join(down)}" if down else "; all chats connected"))
        if g.get("gateway_state") != "running":
            needs.append("Hermes gateway isn't running.")
    except (OSError, ValueError):
        out.append("Hermes: state unknown")
    return out


def main():
    rows = fleet()
    agents = [
        ("Reginald (jobs)", "Reginald", jobs()),
        ("Athena (school)", "Athena", school()),
        ("Vlad (news)", "Vlad", vlad()),
        ("Sterling (business)", "Sterling", ["nothing scheduled"]),
    ]
    out = [f"MORNING STANDUP - {TODAY:%A, %B %d}", ""]
    day = your_day()
    if day:
        out += ["YOUR DAY"] + ["  " + l for l in day] + [""]
    try:
        hm = trip()
    except Exception:
        hm = []
    if hm:
        out += ["TRIP"] + ["  " + l for l in hm] + [""]
    for title, name, today in agents:
        out.append(title)
        out.append(f"  Yesterday: {yesterday(rows, name)}")
        if today[0].rstrip().endswith(":") or len(today) > 3:
            out.append("  Today:")
            out += ["    " + t for t in today]
        else:
            out.append("  Today: " + today[0])
            out += ["    " + t for t in today[1:]]
        out.append(f"  Blocker: {blocker(rows, name)}")
        out.append("")
    out += section("Donut (Mac mini)", health())
    out += section("Needs the owner", needs) if needs else ["Needs the owner: nothing today."]
    text = "\n".join(out).rstrip()
    print(text)
    try:                                           # also text the owner's iPhone, if switched on in ~/.hermes/textme.json
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from textme import text_robert
        text_robert("standup", text)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print(f"Morning digest failed: {type(ex).__name__}: {ex}")
