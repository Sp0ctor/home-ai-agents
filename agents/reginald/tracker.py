#!/usr/bin/env python3
"""Reginald's application tracker (works on the application ledger).

  tracker.py                        daily check: follow-ups that are due (with drafts) + "did you apply?" reminders
  tracker.py drafts                 the follow-up email drafts for everything due
  tracker.py applied N [N..]        mark jobs as applied today - numbers from the job list the resumes came from
                                    (it finds the list whose job N got a resume emailed; else the latest list)
  tracker.py skip N [N..]           you won't apply to these (stops the "did you apply?" reminder)
  tracker.py pending                resumes emailed but not yet marked applied or skipped
  tracker.py applied "Company"      mark the most recent ledger row for that company as applied today
  tracker.py followed "Company"     record that the owner sent a follow-up today
  tracker.py status "Company" X     X = interview | rejected | offer | withdrawn | no-response
  tracker.py list                   open applications and where each one stands

the owner sends every follow-up himself. This script never sends email.
"""
import csv, datetime as dt, json, re, shutil, sys
from pathlib import Path

JOBS = Path.home() / "Documents/Reginald/jobs"
LEDGER = JOBS / "applications/application_ledger.csv"
DAILY = JOBS / "daily"
FIRST_NUDGE_DAYS, SECOND_NUDGE_DAYS = 7, 10
PENDING_AFTER_DAYS, PENDING_EVERY_DAYS, PENDING_GIVE_UP_DAYS = 2, 2, 14
TODAY = dt.date.today()
FINAL = ("interview", "rejected", "offer", "withdrawn", "no-response")


def load():
    with open(LEDGER, newline="", errors="replace") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def save(fields, rows):
    shutil.copy(LEDGER, LEDGER.with_suffix(".csv.bak"))
    with open(LEDGER, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def status_date(s, word):
    m = re.search(word + r" (\d{4}-\d{2}-\d{2})", s or "")
    return dt.date.fromisoformat(m.group(1)) if m else None


def find(rows, company):
    hits = [r for r in rows if company.lower() in (r.get("employer") or "").lower()]
    if not hits:
        sys.exit(f"No ledger entry for '{company}'. Check the spelling, or use a number from today's list.")
    return hits[-1]


def latest_list():
    files = sorted(DAILY.glob("20*.json"))
    return json.load(open(files[-1])) if files else {"jobs": []}


def recent_lists(days=14):
    out = []
    for f in sorted(DAILY.glob("20*.json"), reverse=True):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f.name):
            continue
        if (TODAY - dt.date.fromisoformat(f.stem)).days > days:
            break
        try:
            out.append(json.load(open(f)))
        except ValueError:
            pass
    return out


def is_pending(row):
    s = row.get("application_status") or ""
    return s.startswith("resume emailed") and "applied" not in s and "skipped" not in s


def resolve_numbers(nums, rows):
    """Number -> (job, list date). Prefer the newest list where that job's resume was emailed and is still pending."""
    lists = recent_lists()
    by_url = {}
    for r in rows:
        by_url[r.get("official_url") or ""] = r
    res = {}
    for n in nums:
        pick = None
        for data in lists:
            j = next((x for x in data.get("jobs", []) if x.get("n") == n), None)
            if j and by_url.get(j["url"]) is not None and is_pending(by_url[j["url"]]):
                pick = (j, data.get("date"))
                break
        if pick is None and lists:
            j = next((x for x in lists[0].get("jobs", []) if x.get("n") == n), None)
            pick = (j, lists[0].get("date")) if j else None
        res[n] = pick
    return res


def mark(args, word):
    fields, rows = load()
    nums = [int(a) for a in args if a.isdigit()]
    names = [a for a in args if not a.isdigit()]
    done = []
    for n, pick in resolve_numbers(nums, rows).items():
        if not pick:
            print(f"#{n} isn't on a recent job list - skipped.")
            continue
        j, date = pick
        row = next((r for r in reversed(rows) if (r.get("official_url") or "") == j["url"]), None)
        if row is None:
            row = {k: "" for k in fields}
            row.update({"timestamp_emitted": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                        "employer": j["company"], "job_title": j["title"], "official_url": j["url"],
                        "board_or_ats": j.get("source", ""), "location": j.get("location", ""),
                        "fit_status": "approved by the owner", "notes": f"daily list {date} #{n}"})
            rows.append(row)
        row["application_status"] = f"{word} {TODAY}"
        done.append(f"#{n} ({date} list) {row['employer']} - {row['job_title']}")
    for name in names:
        row = find(rows, name)
        row["application_status"] = f"{word} {TODAY}"
        done.append(f"{row['employer']} - {row['job_title']}")
    if not done:
        sys.exit(f"Nothing was marked {word}. Use numbers from your job list or a company name from the ledger.")
    save(fields, rows)
    if word == "applied":
        follow = TODAY + dt.timedelta(days=FIRST_NUDGE_DAYS)
        print(f"Marked applied ({TODAY}):\n  " + "\n  ".join(done) + f"\nFollow-up reminder on {follow:%a %b %d} if there's no reply.")
    else:
        print(f"Marked skipped - no more reminders:\n  " + "\n  ".join(done))


def mark_applied(args):
    mark(args, "applied")


def pending_rows(rows):
    out = []
    for r in rows:
        if not is_pending(r):
            continue
        sent = status_date(r.get("application_status"), "resume emailed")
        if sent and (TODAY - sent).days <= PENDING_GIVE_UP_DAYS:
            m = re.search(r"daily list (\d{4}-\d{2}-\d{2}) #(\d+)", r.get("notes") or "")
            out.append((r, sent, m.group(1) if m else "", m.group(2) if m else "?"))
    return sorted(out, key=lambda x: (x[1], int(x[3]) if x[3].isdigit() else 0))


def pending_lines(rows, force=False):
    pend = pending_rows(rows)
    due = [p for p in pend if (TODAY - p[1]).days >= PENDING_AFTER_DAYS and
           ((TODAY - p[1]).days - PENDING_AFTER_DAYS) % PENDING_EVERY_DAYS == 0]
    show = pend if force else due
    if not show:
        return []
    lines = [f"Did you apply? Resumes emailed, not marked yet ({len(show)})"]
    for r, sent, date, n in show:
        lines.append(f"  #{n} {r['employer']} - {r['job_title'][:60]} (resume sent {sent:%b %d})")
    nums = " ".join(p[3] for p in show)
    lines.append(f'Tell Reginald "applied {nums}" (only the ones you did), or "skip <numbers>" for any you won\'t.')
    return lines


def set_status(company, word):
    fields, rows = load()
    row = find(rows, company)
    if word == "followed":
        row["application_status"] = (row.get("application_status") or "") + f"; followed up {TODAY}"
    else:
        if word not in FINAL:
            sys.exit(f"Status must be one of: {', '.join(FINAL)}")
        row["application_status"] = (row.get("application_status") or "") + f"; {word} {TODAY}"
    save(fields, rows)
    print(f"{row['employer']} - {row['job_title']}: {row['application_status']}")


def open_apps(rows):
    out = []
    for r in rows:
        s = r.get("application_status") or ""
        applied = status_date(s, "applied")
        if applied and not any(w in s for w in FINAL):
            out.append((r, applied, status_date(s, "followed up")))
    return out


# Your one-sentence pitch and signature for follow-up drafts (edit these).
PITCH = ("I bring [your clearance/certifications] and hands-on experience with [your key tools] "
         "in [your environment].")
SIGNATURE = "Your Name | +1 555 555 0100 | you@example.com"


def draft(r, applied):
    title, company = r.get("job_title", "the role"), r.get("employer", "your team")
    return (f"Subject: Following up - {title} application\n"
            f"Hello {company} Hiring Team,\n"
            f"I applied for the {title} position on {applied:%B %d} and wanted to confirm my interest. "
            f"{PITCH} "
            f"I'd welcome the chance to talk about how I can help your team.\n"
            f"Thank you,\n{SIGNATURE}")


def daily(drafts=True):
    fields, rows = load()
    due = []
    for r, applied, followed in open_apps(rows):
        if followed is None and (TODAY - applied).days >= FIRST_NUDGE_DAYS:
            due.append((r, applied, "first follow-up"))
        elif followed and (TODAY - followed).days >= SECOND_NUDGE_DAYS:
            due.append((r, applied, "no reply since your follow-up - mark it 'no-response' or send one last note"))
    lines = []
    if due:
        lines += [f"Follow-ups due ({len(due)})", ""]
        for i, (r, applied, kind) in enumerate(due, 1):
            lines += [f"{i}. {r['employer']} - {r['job_title']} (applied {applied:%b %d}, {(TODAY - applied).days} days ago): {kind}",
                      f"   {r.get('official_url', '')}"]
            if kind == "first follow-up" and drafts:
                lines += ["", draft(r, applied), ""]
        if not drafts:
            lines.append("Ask Reginald for \"follow-up drafts\" to get the emails to send.")
        lines += ["When you've sent one, tell Reginald: \"followed up with <company>\".",
                  "Got an answer? Tell him: \"<company> interview\" / \"rejected\" / \"offer\".", ""]
    lines += pending_lines(rows)
    if lines:  # empty output = no message
        print("\n".join(lines).rstrip())


def listing():
    fields, rows = load()
    apps = open_apps(rows)
    if not apps:
        print("No open applications marked as applied yet.")
        return
    print(f"Open applications ({len(apps)}):")
    for r, applied, followed in sorted(apps, key=lambda x: x[1]):
        f = f", followed up {followed:%b %d}" if followed else ""
        print(f"- {r['employer']} - {r['job_title']}: applied {applied:%b %d} ({(TODAY - applied).days} days){f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            daily()
        elif a[0] == "applied" and len(a) > 1:
            mark_applied(a[1:])
        elif a[0] == "drafts":
            daily(drafts=True)
        elif a[0] == "skip" and len(a) > 1:
            mark(a[1:], "skipped")
        elif a[0] == "pending":
            print("\n".join(pending_lines(load()[1], force=True)) or "Nothing pending - every emailed resume is marked applied or skipped.")
        elif a[0] == "followed" and len(a) > 1:
            set_status(" ".join(a[1:]), "followed")
        elif a[0] == "status" and len(a) > 2:
            set_status(" ".join(a[1:-1]), a[-1].lower())
        elif a[0] == "list":
            listing()
        else:
            print(__doc__)
    except SystemExit:
        raise
    except Exception as ex:
        print(f"TRACKER ERROR: {type(ex).__name__}: {ex}")
        sys.exit(1)
