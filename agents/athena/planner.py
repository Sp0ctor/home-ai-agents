#!/usr/bin/env python3
"""Athena's coursework planner.

  planner.py                                    daily check (scheduled job): Monday week plan + 2-day reminders
  planner.py add "COURSE" "ITEM" YYYY-MM-DD [HH:MM]   add one deadline (time optional, default 23:59)
  planner.py add "COURSE" "ITEM" week N         add an item the syllabus only gives a week for (due end of that week)
  planner.py weeks                              the term calendar: which dates each week number covers
  planner.py term YYYY-MM-DD [WEEKS]            set the first day of the term (Fall 2026 = 2026-09-06, 15 weeks)
  planner.py list [all]                         upcoming deadlines (or all, including done/past)
  planner.py week                               what's due in the next 7 days
  planner.py done ID                            mark a deadline finished (no more reminders)
  planner.py remove ID                          delete a deadline added by mistake

Deadlines are stored in deadlines.json in Athena's school folder.
"""
import datetime as dt, json, re, sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent          # Athena's profile folder
STORE = HOME / "school" / "deadlines.json"
TODAY = dt.date.today()
REMIND_DAYS = 2


def load():
    try:
        return json.load(open(STORE))
    except (OSError, ValueError):
        return {"next_id": 1, "items": []}


def save(d):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(d, open(STORE, "w"), indent=1)


def term(d=None):
    t = (d or load()).get("term") or {"name": "Fall 2026", "start": "2026-09-06", "weeks": 15}
    return dt.date.fromisoformat(t["start"]), int(t.get("weeks", 15)), t.get("name", "")


def week_dates(n, d=None):
    start, _, _ = term(d)
    a = start + dt.timedelta(days=7 * (n - 1))
    return a, a + dt.timedelta(days=6)


def week_of(day, d=None):
    start, weeks, _ = term(d)
    n = (day - start).days // 7 + 1
    return n if 1 <= n <= weeks + 1 else None


def due(i):
    return dt.datetime.fromisoformat(f"{i['date']}T{i.get('time') or '23:59'}")


def fmt(i):
    d = due(i)
    left = (d.date() - TODAY).days
    when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days" if left > 0 else f"{-left} days ago"
    t = "" if (i.get("time") or "23:59") == "23:59" else f" {d:%-I:%M %p}"
    wk = f"; week {i['week']}, exact day on course page" if i.get("approx") else ""
    return f"[{i['id']}] {d:%a %b %d}{t} - {i['course']}: {i['item']} ({when}{wk})"


def add(args):
    if len(args) < 3:
        sys.exit('Usage: planner.py add "COURSE" "ITEM" YYYY-MM-DD [HH:MM]')
    course, item, date = args[0].strip(), args[1].strip(), args[2].strip()
    approx, wk = False, None
    if date.lower() == "week":
        if len(args) < 4 or not args[3].strip().isdigit():
            sys.exit('Usage: planner.py add "COURSE" "ITEM" week N')
        wk = int(args[3])
        date, approx, args = str(week_dates(wk)[1]), True, args[:3]
    time = args[3].strip() if len(args) > 3 else "23:59"
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        sys.exit(f"Bad date '{date}'. Use YYYY-MM-DD, e.g. 2026-10-14, or: week N")
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", time):
        sys.exit(f"Bad time '{time}'. Use 24-hour HH:MM, e.g. 23:59.")
    if not (TODAY - dt.timedelta(days=30) <= day <= TODAY + dt.timedelta(days=400)):
        sys.exit(f"Date {day} looks wrong (too far from today). Check the year.")
    d = load()
    for i in d["items"]:
        if i["course"].lower() == course.lower() and i["item"].lower() == item.lower() and i["date"] == str(day):
            print(f"Already listed: {fmt(i)}")
            return
    i = {"id": d["next_id"], "course": course, "item": item, "date": str(day), "time": time,
         "done": False, "reminded": False, "added": str(TODAY)}
    if approx:
        i.update(approx=True, week=wk)
    d["next_id"] += 1
    d["items"].append(i)
    save(d)
    print(f"Added {fmt(i)}")


def upcoming(d, days=None):
    items = [i for i in d["items"] if not i["done"] and due(i).date() >= TODAY]
    if days is not None:
        items = [i for i in items if (due(i).date() - TODAY).days < days]
    return sorted(items, key=due)


def listing(show_all=False):
    d = load()
    items = sorted(d["items"], key=due) if show_all else upcoming(d)
    if not items:
        print("No upcoming deadlines saved. Send Athena a syllabus to add them.")
        return
    print(f"{'All' if show_all else 'Upcoming'} deadlines ({len(items)}):")
    for i in items:
        print(("  DONE " if i["done"] else "  ") + fmt(i))


def week():
    items = upcoming(load(), 7)
    if not items:
        print("Nothing due in the next 7 days.")
        return
    print(f"Due in the next 7 days ({len(items)}):")
    for i in items:
        print("  " + fmt(i))


def set_done(ident, remove=False):
    d = load()
    i = next((x for x in d["items"] if str(x["id"]) == str(ident)), None)
    if not i:
        sys.exit(f"No deadline with id {ident}. Run planner.py list to see ids.")
    if remove:
        d["items"].remove(i)
        print(f"Removed {fmt(i)}")
    else:
        i["done"] = True
        print(f"Marked done: {fmt(i)}")
    save(d)


def weeks_cmd():
    d = load()
    start, n, name = term(d)
    now = week_of(TODAY, d)
    print(f"{name} - week calendar (weeks run Sunday to Saturday; deadlines are usually Saturday 11:59 PM Pacific = Sunday 2:59 AM Eastern)")
    for w in range(1, n + 1):
        a, b = week_dates(w, d)
        print(f"  Week {w:>2}: {a:%a %b %d} - {b:%a %b %d}" + ("   <- this week" if w == now else ""))


def set_term(args):
    d = load()
    try:
        start = dt.date.fromisoformat(args[0])
    except (ValueError, IndexError):
        sys.exit("Usage: planner.py term YYYY-MM-DD [WEEKS]")
    d["term"] = {"name": d.get("term", {}).get("name", "Term"), "start": str(start),
                 "weeks": int(args[1]) if len(args) > 1 else 15}
    save(d)
    weeks_cmd()


def daily():
    d = load()
    out = []
    if TODAY.weekday() == 0:  # Monday
        items = upcoming(d, 7)
        wk = week_of(TODAY, d)
        out.append(f"This week in school ({TODAY:%b %d}-{TODAY + dt.timedelta(days=6):%b %d})" + (f" - Week {wk}" if wk else ""))
        out += ["  " + fmt(i) for i in items] or ["  Nothing due this week."]
        later = [i for i in upcoming(d, 14) if i not in items]
        if later:
            out.append("Next week:")
            out += ["  " + fmt(i) for i in later]
    soon = [i for i in upcoming(d, REMIND_DAYS + 1) if not i["reminded"] and (due(i).date() - TODAY).days <= REMIND_DAYS]
    if soon:
        if out:
            out.append("")
        out.append("Reminder - due soon:")
        for i in soon:
            out.append("  " + fmt(i))
            i["reminded"] = True
        save(d)
    if out:
        out += ["", 'Finished one? Tell Athena "done <id>".']
        print("\n".join(out))


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            daily()
        elif a[0] == "add":
            add(a[1:])
        elif a[0] == "list":
            listing(len(a) > 1 and a[1] == "all")
        elif a[0] == "week":
            week()
        elif a[0] == "weeks":
            weeks_cmd()
        elif a[0] == "term":
            set_term(a[1:])
        elif a[0] in ("done", "remove") and len(a) > 1:
            set_done(a[1], remove=a[0] == "remove")
        else:
            print(__doc__)
    except SystemExit:
        raise
    except Exception as ex:
        print(f"PLANNER ERROR: {type(ex).__name__}: {ex}")
        sys.exit(1)
