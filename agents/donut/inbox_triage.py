#!/usr/bin/env python3
"""Inbox triage - FLAG ONLY. Reads message headers from the owner's own mailboxes and lists the ones that
look important. It never sends, deletes, moves, marks as read or replies to anything (mailboxes are opened
read-only and only headers are fetched with PEEK, so messages stay unread).

  inbox_triage.py            the last 24 hours (used by the morning standup)
  inbox_triage.py hours N    look back N hours
  inbox_triage.py check      test each saved mailbox login

Logins live in ~/.hermes/inbox.env (chmod 600), written by the owner himself:
  GMAIL_USER=you@example.com
  GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx      (Google account > Security > App passwords)
  ICLOUD_USER=you@icloud.example
  ICLOUD_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx  (account.apple.com > Sign-In and Security > App-Specific Passwords)
Rules for what counts as important: ~/.hermes/inbox_rules.json (created with defaults on first run).
"""
import datetime as dt, email, email.header, email.utils, imaplib, json, re, sys
from pathlib import Path

H = Path.home() / ".hermes"
ENV = H / "inbox.env"
RULES = H / "inbox_rules.json"
CACHE = H / "inbox_known_senders.json"
SERVERS = {"GMAIL": ("imap.gmail.com", ["INBOX"], '"[Gmail]/Sent Mail"'),
           "ICLOUD": ("imap.mail.me.com", ["INBOX"], '"Sent Messages"')}

DEFAULT_RULES = {
    "_note": "Edit freely. Each rule: a label plus words/addresses to match in the sender or subject (case-insensitive).",
    "rules": [
        {"label": "Job / recruiter", "senders": ["myworkday", "workday.com", "greenhouse", "lever.co", "icims", "taleo",
                                                  "smartrecruiters", "clearancejobs", "usajobs", "linkedin.com/jobs",
                                                  "gdit", "caci", "leidos", "boozallen", "saic", "northropgrumman", "anduril"],
         "subject": ["interview", "recruit", "your application", "application received", "next steps", "offer",
                     "clearance", "position", "opportunity", "assessment", "phone screen"]},
        {"label": "School", "senders": ["myschool.edu", "brightspace", "canvas", "blackboard"],
         "subject": ["grade", "feedback", "due", "oral", "exam", "quiz", "announcement"]},
        {"label": "Rental property", "senders": ["appfolio", "propertymanager.example"],
         "subject": ["owner statement", "rent", "maintenance", "work order", "lease", "rental"]},
        {"label": "Notary client", "senders": ["notarize", "proof.com", "snapdocs", "signingorder", "nna"],
         "subject": ["notary", "notarize", "notarization", "signing", "loan signing", "apostille"]},
        {"label": "Money / account security", "senders": [],
         "subject": ["security alert", "new sign-in", "password reset", "verification code", "payment due",
                     "past due", "overdue", "fraud", "suspicious", "unusual activity", "invoice"]},
        {"label": "Clearance / security office", "senders": [".mil", "dcsa", "nbis", "fso"],
         "subject": ["reinvestigation", "eapp", "e-qip", "continuous vetting", "security office", "polygraph"]}
    ],
    "ignore_senders": ["noreply@medium.com", "newsletter", "marketing", "promo", "deals@"],
    "flag_people_you_email": True
}


def load_env():
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def rules():
    if not RULES.exists():
        RULES.write_text(json.dumps(DEFAULT_RULES, indent=1))
    return json.loads(RULES.read_text())


def dec(s):
    out = []
    for part, enc in email.header.decode_header(s or ""):
        out.append(part.decode(enc or "utf-8", "replace") if isinstance(part, bytes) else part)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def accounts(env):
    return [(name, env[f"{name}_USER"], env[f"{name}_APP_PASSWORD"].replace(" ", ""))
            for name in SERVERS if env.get(f"{name}_USER") and env.get(f"{name}_APP_PASSWORD")]


def known_senders(conn, sent_box):
    """Addresses the owner has written to (from his Sent folder), cached for a week."""
    try:
        cache = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    key = sent_box
    if key in cache and cache[key]["date"] >= str(dt.date.today() - dt.timedelta(days=7)):
        return set(cache[key]["addrs"])
    addrs = set()
    try:
        if conn.select(sent_box, readonly=True)[0] == "OK":
            since = (dt.date.today() - dt.timedelta(days=365)).strftime("%d-%b-%Y")
            typ, data = conn.search(None, "SINCE", since)
            ids = data[0].split()[-500:] if typ == "OK" else []
            for i in range(0, len(ids), 100):
                typ, msgs = conn.fetch(b",".join(ids[i:i + 100]), "(BODY.PEEK[HEADER.FIELDS (TO CC)])")
                for m in msgs:
                    if isinstance(m, tuple):
                        h = email.message_from_bytes(m[1])
                        for _, a in email.utils.getaddresses(h.get_all("To", []) + h.get_all("Cc", [])):
                            if a:
                                addrs.add(a.lower())
    except imaplib.IMAP4.error:
        pass
    cache[key] = {"date": str(dt.date.today()), "addrs": sorted(addrs)}
    CACHE.write_text(json.dumps(cache))
    return addrs


def classify(frm_name, frm_addr, subject, rl, people, own_addrs):
    blob_sender = f"{frm_name} {frm_addr}".lower()
    subj = subject.lower()
    if frm_addr.lower() in own_addrs:
        return None
    if any(x.lower() in blob_sender for x in rl.get("ignore_senders", [])):
        return None
    def hit(word, text):          # match at the start of a word: "rent" matches "rental" but not "current"
        return re.search(r"(?<![a-z0-9])" + re.escape(word.lower()), text) is not None
    for r in rl["rules"]:
        if any(hit(s, blob_sender) for s in r.get("senders", [])) or any(hit(w, subj) for w in r.get("subject", [])):
            return r["label"]
    if rl.get("flag_people_you_email") and frm_addr.lower() in people:
        return "Someone you've emailed"
    return None


def scan(hours=24):
    env = load_env()
    accts = accounts(env)
    if not accts:
        return [], ["No mailbox linked yet (see ~/.hermes/inbox.env)."]
    rl = rules()
    own = {u.lower() for _, u, _ in accts} | {env.get("HERMES_EMAIL", "").lower()}
    since_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    flagged, notes = [], []
    for name, user, pw in accts:
        host, boxes, sent_box = SERVERS[name]
        try:
            conn = imaplib.IMAP4_SSL(host, 993, timeout=45)
            conn.login(user, pw)
        except Exception as ex:
            notes.append(f"{name.title()}: couldn't sign in ({type(ex).__name__}) - check the app password in ~/.hermes/inbox.env")
            continue
        try:
            people = known_senders(conn, sent_box) if rl.get("flag_people_you_email") else set()
            for box in boxes:
                if conn.select(box, readonly=True)[0] != "OK":
                    continue
                typ, data = conn.search(None, "SINCE", (since_dt - dt.timedelta(days=1)).strftime("%d-%b-%Y"))
                ids = data[0].split()[-300:] if typ == "OK" else []
                for i in range(0, len(ids), 100):
                    typ, msgs = conn.fetch(b",".join(ids[i:i + 100]), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE)])")
                    for m in msgs:
                        if not isinstance(m, tuple):
                            continue
                        h = email.message_from_bytes(m[1])
                        try:
                            when = email.utils.parsedate_to_datetime(h.get("Date"))
                            when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
                        except Exception:
                            continue
                        if when < since_dt:
                            continue
                        fname, faddr = email.utils.parseaddr(dec(h.get("From")))
                        subject = dec(h.get("Subject")) or "(no subject)"
                        label = classify(fname, faddr, subject, rl, people, own)
                        # bulk mail with an unsubscribe link only counts if a real rule matched
                        if label == "Someone you've emailed" and h.get("List-Unsubscribe"):
                            label = None
                        if label:
                            flagged.append({"acct": name.title(), "when": when, "from": fname or faddr, "addr": faddr,
                                            "subject": subject, "why": label})
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    flagged.sort(key=lambda x: x["when"], reverse=True)
    return flagged, notes


def show(hours=24, limit=12):
    flagged, notes = scan(hours)
    multi = len({f["acct"] for f in flagged}) > 1
    if flagged:
        print(f"{len(flagged)} email(s) worth a look (last {hours}h):")
        for f in flagged[:limit]:
            local = f["when"].astimezone()
            acct = f" [{f['acct']}]" if multi else ""
            print(f"  {local:%a %-I:%M %p} - {f['from'][:30]}: {f['subject'][:70]}  ({f['why']}){acct}")
        if len(flagged) > limit:
            print(f"  ...and {len(flagged) - limit} more")
    elif not notes:
        print(f"Nothing urgent in the last {hours}h.")
    for n in notes:
        print(n)


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            show()
        elif a[0] == "hours" and len(a) > 1:
            show(int(a[1]))
        elif a[0] == "check":
            env = load_env()
            if not accounts(env):
                print("No mailbox linked yet. Create ~/.hermes/inbox.env (see the top of this script).")
            for name, user, pw in accounts(env):
                host = SERVERS[name][0]
                try:
                    c = imaplib.IMAP4_SSL(host, 993, timeout=45)
                    c.login(user, pw)
                    typ, data = c.select("INBOX", readonly=True)
                    print(f"OK - {name.title()} ({user}): signed in, {int(data[0])} messages in Inbox (nothing changed)")
                    c.logout()
                except Exception as ex:
                    print(f"FAILED - {name.title()} ({user}): {type(ex).__name__}: {ex}")
        else:
            print(__doc__)
    except Exception as ex:
        print(f"Inbox triage error: {type(ex).__name__}: {ex}")
        sys.exit(1)
