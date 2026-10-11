#!/usr/bin/env python3
"""Email Vlad's finished morning briefs to the people in recipients.json.

  send_brief.py            send today's brief to every recipient whose brief is ready (the scheduled job)
  send_brief.py NAME       send (or re-send) today's brief to one person
  send_brief.py test NAME  send today's brief for NAME to ROBERT only, to preview it
  send_brief.py list       who gets what

How it works: each recipient has their own Vlad cron job (found by job_name) that writes their brief at ~6:30am
(delivered locally, not to Telegram). This script picks up that job's output from today, cleans it into
plain readable text, and emails it from the Hermes Gmail account. It never invents or edits news.
Prints nothing when everything went out (so the scheduled job stays silent); prints a line for the owner
when something failed or a brief wasn't ready.
"""
import datetime as dt, email.utils, json, re, smtplib, sys
from email.message import EmailMessage
from pathlib import Path

H = Path.home() / ".hermes"
VLAD = H / "profiles/vlad"
CFG = VLAD / "scripts/recipients.json"
LOG = VLAD / "briefs/sent.log"
ROBERT = "you@example.com"
TODAY = dt.date.today()


def load_env():
    env = {}
    for line in (H / ".env").read_text().splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def job_id_for(r):
    if r.get("job_id"):
        return r["job_id"]
    try:
        jobs = json.load(open(VLAD / "cron/jobs.json"))["jobs"]
    except (OSError, ValueError, KeyError):
        return None
    return next((j["id"] for j in jobs if j.get("name", "").lower() == r["job_name"].lower()), None)


def latest_output(job_id):
    if not job_id:
        return None, "its Vlad job doesn't exist yet"
    d = VLAD / "cron/output" / job_id
    files = sorted(d.glob(f"{TODAY:%Y-%m-%d}_*.md")) if d.is_dir() else []
    if not files:
        return None, "no brief written yet today"
    text = files[-1].read_text(errors="replace")
    body = text.split("## Response", 1)[1] if "## Response" in text else ""
    body = body.strip()
    if not body or body.startswith("[SILENT]"):
        return None, "Vlad's run produced no brief"
    if body.startswith("[CRON_FAILURE]") or re.search(r"(?i)search tool (failed|is down)|could not (search|reach)", body[:300]):
        return None, "Vlad reported a search failure: " + body.splitlines()[0][:120]
    return body, None


_LI_HUMAN = Path.home() / ".hermes/profiles/reginald/skills/social-media/li-human"


def unfingerprint(t):
    """Strip invisible characters and AI typography (em dashes, curly quotes) - punctuation only, never words,
    so the news itself is untouched. Uses the li-human tool; if it's missing the text goes out as is."""
    try:
        sys.path.insert(0, str(_LI_HUMAN))
        from li_tool import light
        return light(t)
    except Exception:
        return t


def clean(body):
    """Markdown -> plain text an email reader shows nicely. Removes duplicate source blocks."""
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", body)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?m)^#+\s*", "", t)
    t = re.sub(r"(?m)^\s*[-*]\s+", "• ", t)
    t = re.sub(r"(?m)^—\s*$", "", t)
    blocks = re.split(r"(?m)^(?=Sources:)", t)
    seen, out = set(), []
    for b in blocks:
        key = re.sub(r"\s+", " ", b.strip())
        if key.startswith("Sources:"):
            links = re.findall(r"https?://\S+", b)
            if not links or tuple(links) in seen:
                continue
            seen.add(tuple(links))
            b = "Sources:\n" + "\n".join(f"  {u.rstrip('•').strip()}" for u in dict.fromkeys(links)) + "\n"
        out.append(b)
    t = "".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return unfingerprint(t)


def send(to, subject, text, env):
    msg = EmailMessage()
    msg["From"] = f"Vlad (the owner's news brief) <{env['EMAIL_ADDRESS']}>"
    msg["To"], msg["Subject"] = to, subject
    msg["Reply-To"] = ROBERT
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(text)
    with smtplib.SMTP_SSL(env.get("EMAIL_SMTP_HOST", "smtp.gmail.com"), 465, timeout=60) as s:
        s.login(env["EMAIL_ADDRESS"], env["EMAIL_PASSWORD"])
        s.send_message(msg)


def already_sent(name):
    if not LOG.exists():
        return False
    return any(l.startswith(f"{TODAY} ") and f" {name} " in l and " SENT " in l for l in LOG.read_text().splitlines())


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{TODAY} {dt.datetime.now():%H:%M} {line}\n")


def run(only=None, test=False, force=False):
    cfg = json.load(open(CFG))
    env = load_env()
    problems = []
    for r in cfg["recipients"]:
        if only and r["name"].lower() != only.lower():
            continue
        if not r.get("active", True) and not only:
            continue
        if not force and not test and already_sent(r["name"]):
            continue
        if not r.get("email") and not test:
            problems.append(f"{r['name']} has no email address in recipients.json.")
            continue
        body, why = latest_output(job_id_for(r))
        if not body:
            log(f"SKIP {r['name']} - {why}")
            if only or test or dt.datetime.now().time() >= dt.time(7, 25):   # first try is silent; report on the last one
                problems.append(f"{r['name']}'s brief not sent - {why}.")
            continue
        text = (f"Good morning {r['name']},\n\nHere's your news brief for {TODAY:%A, %B %-d}.\n\n" + clean(body) +
                f"\n\n--\nSent by Vlad, the owner's news assistant. Every story is checked against its source link; "
                f"stories reported by only one outlet are marked UNCONFIRMED.\nTo change topics or stop these emails, just reply.")
        to = ROBERT if test else r["email"]
        subject = ("[TEST for " + r["name"] + "] " if test else "") + f"Morning brief - {TODAY:%a %b %-d}"
        try:
            send(to, subject, text, env)
            log(f"{'TEST' if test else 'SENT'} {r['name']} -> {to}")
            if only or test:
                print(f"RESULT: SENT - {r['name']}'s brief to {to}")
        except Exception as ex:
            problems.append(f"{r['name']}'s brief failed to send: {type(ex).__name__}: {ex}")
            log(f"FAIL {r['name']} - {ex}")
    if problems:
        print("Vlad brief delivery problems:\n" + "\n".join("  " + p for p in problems))


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            run()
        elif a[0] == "list":
            for r in json.load(open(CFG))["recipients"]:
                print(f"{r['name']:<10} {r.get('email') or '(no email yet)':<35} job '{r['job_name']}' ({job_id_for(r) or 'not created'})  "
                      f"{'active' if r.get('active', True) else 'PAUSED'}")
        elif a[0] == "test" and len(a) > 1:
            run(a[1], test=True)
        else:
            run(a[0], force=True)
    except Exception as ex:
        print(f"Vlad brief delivery error: {type(ex).__name__}: {ex}")
        sys.exit(1)
