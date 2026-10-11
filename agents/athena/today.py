#!/usr/bin/env python3
"""Athena's "school today" update - READ ONLY. It never logs in, submits, posts or changes anything
in the school portal; it only reads the portal's calendar feed and Athena's own planner.

  today.py              the morning update (scheduled job): what's due, what's new, what to do today
  today.py feed         refresh from the Brightspace calendar feed and show what it found
  today.py check        test the feed link without changing anything
  today.py rebuild      remove what the portal added to the planner and sync again

Portal feed: the private Brightspace calendar subscription link, saved (by the owner) in
  ~/.hermes/profiles/athena/School/portal_feed_url.txt
New portal items are added to the planner with their exact due date; a planner estimate for the same
item ("week N") is marked replaced. Nothing is ever sent back to the portal.
"""
import datetime as dt, json, re, runpy, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
PROFILE = HERE.parent
SCHOOL = PROFILE / "School"
FEED_URL_FILE = SCHOOL / "portal_feed_url.txt"
EVENTS = SCHOOL / "portal_events.json"
STUDY_PROGRESS = SCHOOL / "study" / "progress.json"
TZ = ZoneInfo("America/New_York")
NOW = dt.datetime.now(TZ)
TODAY = NOW.date()
COURSE_RE = re.compile(r"\b(ITM|AI|MSBA)[\s._-]?(\d{3})\b", re.I)

planner = runpy.run_path(str(HERE / "planner.py"), run_name="today")


# ---------------------------------------------------------------- calendar feed (read only)
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_time(val, params):
    """Returns (date, 'HH:MM' or None) in Eastern time."""
    v = val.strip()
    if re.fullmatch(r"\d{8}", v) or "VALUE=DATE" in params.upper():
        return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8])), None
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", v)
    if not m:
        return None, None
    t = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        t = t.replace(tzinfo=dt.timezone.utc)
    else:
        tzid = re.search(r"TZID=([^;:]+)", params)
        try:
            t = t.replace(tzinfo=ZoneInfo(tzid.group(1)) if tzid else TZ)
        except Exception:
            t = t.replace(tzinfo=TZ)
    t = t.astimezone(TZ)
    return t.date(), t.strftime("%H:%M")


def parse_ics(text):
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold(text), re.S):
        f = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            name, _, params = key.partition(";")
            f.setdefault(name.upper(), (val, params))
        summ = f.get("SUMMARY", ("", ""))[0]
        summ = summ.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
        kind = "available" if re.search(r"-\s*(Available|Availability Starts|Starts)\s*$", summ, re.I) else "due"
        summ = re.sub(r"\s*-\s*(Due|Available|Availability Ends|Availability Starts|Ends|Starts)\s*$", "", summ, flags=re.I)
        date, time = _ical_time(*f["DTSTART"]) if "DTSTART" in f else (None, None)
        if "DUE" in f:
            date, time = _ical_time(*f["DUE"])
        if not date or not summ:
            continue
        loc = f.get("LOCATION", ("", ""))[0]
        desc = f.get("DESCRIPTION", ("", ""))[0].replace("\\n", " ").replace("\\,", ",")
        blob = " ".join([summ, loc, desc, f.get("CATEGORIES", ("", ""))[0]])
        m = COURSE_RE.search(blob)
        course = f"{m.group(1).upper()}-{m.group(2)}" if m else "School"
        events.append({"uid": f.get("UID", (summ + str(date), ""))[0], "title": summ, "course": course,
                       "date": str(date), "time": time, "url": f.get("URL", ("", ""))[0], "kind": kind})
    return events


def fetch_feed():
    if not FEED_URL_FILE.exists():
        return None, "no portal link saved yet"
    url = FEED_URL_FILE.read_text().strip().split()[0] if FEED_URL_FILE.read_text().strip() else ""
    if not url.startswith(("https://", "webcal://")):
        return None, "the saved portal link doesn't look like a calendar link"
    url = "https://" + url[len("webcal://"):] if url.startswith("webcal://") else url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Athena-calendar-reader"})
        with urllib.request.urlopen(req, timeout=40) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as ex:
        return None, f"couldn't read the portal feed ({type(ex).__name__})"
    if "BEGIN:VCALENDAR" not in text:
        return None, "the portal link didn't return a calendar (it may have expired - make a new one in Brightspace)"
    return parse_ics(text), None


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]{3,}", s.lower())} - {"the", "and", "due", "for", "week", "pts", "assignment"}


def sync_feed(verbose=False):
    """Merge feed events into the planner. Returns (new_items, error)."""
    events, err = fetch_feed()
    if err:
        return [], err
    old = {}
    try:
        old = {e["uid"]: e for e in json.load(open(EVENTS))["events"]}
    except (OSError, ValueError, KeyError):
        pass
    d = planner["load"]()
    new = []
    window_start = TODAY - dt.timedelta(days=3)
    first_run = not old
    for e in events:
        day = dt.date.fromisoformat(e["date"])
        is_new = e["uid"] not in old
        if e.get("kind") == "available":          # new course material, not a deadline
            if is_new and not first_run and window_start <= day <= TODAY + dt.timedelta(days=7):
                new.append(dict(e, note="posted"))
            continue
        if day < TODAY or day > TODAY + dt.timedelta(days=150):   # past items: the feed can't tell if they're done
            continue
        changed = not is_new and (old[e["uid"]]["date"], old[e["uid"]].get("time")) != (e["date"], e.get("time"))
        existing = next((i for i in d["items"] if i.get("portal_uid") == e["uid"]), None)
        if existing:
            if changed:
                existing.update(date=e["date"], time=e.get("time") or "23:59", reminded=False)
                new.append(dict(e, note="date changed"))
            continue
        item = {"id": d["next_id"], "course": e["course"], "item": e["title"], "date": e["date"],
                "time": e.get("time") or "23:59", "done": False, "reminded": False, "added": str(TODAY),
                "portal_uid": e["uid"], "source": "portal"}
        d["next_id"] += 1
        d["items"].append(item)
        # an estimated "week N" item for the same work is now superseded by the exact portal date
        ew = _words(e["title"])
        for i in d["items"]:
            if i.get("approx") and not i["done"] and i["course"] == e["course"]:
                gap = abs((dt.date.fromisoformat(i["date"]) - day).days)
                iw = _words(i["item"])
                if gap <= 7 and ew and iw and len(ew & iw) / min(len(ew), len(iw)) >= 0.5:
                    i["done"], i["replaced_by"] = True, item["id"]
        if is_new and not first_run:
            new.append(e)
    planner["save"](d)
    SCHOOL.mkdir(parents=True, exist_ok=True)
    json.dump({"fetched": NOW.isoformat(timespec="minutes"), "events": events}, open(EVENTS, "w"), indent=1)
    if verbose:
        print(f"Portal feed: {len(events)} calendar items read; {len(new)} new or changed.")
    return new, None


# ---------------------------------------------------------------- the morning update
def rebuild():
    """Undo everything the portal added to the planner and sync again from scratch."""
    d = planner["load"]()
    removed = [i for i in d["items"] if i.get("source") == "portal"]
    d["items"] = [i for i in d["items"] if i.get("source") != "portal"]
    for i in d["items"]:
        if i.pop("replaced_by", None) is not None:
            i["done"] = False
    planner["save"](d)
    if EVENTS.exists():
        EVENTS.unlink()
    print(f"Removed {len(removed)} portal items from the planner; syncing again.")
    new, err = sync_feed()
    print(err or "Done. Run today.py to see the update.")


def fmt(i):
    return planner["fmt"](i)


def study_hint(soon):
    oral = next((i for i in soon if re.search(r"oral", i["item"], re.I)), None)
    try:
        prog = json.load(open(STUDY_PROGRESS))
    except (OSError, ValueError):
        prog = {}
    practiced = sum(1 for k, v in prog.items() if not k.startswith("_") and v.get("history"))
    weak = sum(1 for k, v in prog.items() if not k.startswith("_") and v.get("history") and v["history"][-1]["result"] != "got")
    if oral:
        days = (dt.date.fromisoformat(oral["date"]) - TODAY).days
        return (f"Oral midterm {'this week' if days <= 7 else 'coming up'}: practice 3 questions with Athena "
                f"(send \"quiz me for the oral exam\"). So far {practiced} practiced" + (f", {weak} to redo." if weak else "."))
    return None


def morning():
    new, feed_err = sync_feed()
    d = planner["load"]()
    open_items = [i for i in d["items"] if not i["done"]]
    due = lambda i: planner["due"](i).date()
    overdue = sorted((i for i in open_items if due(i) < TODAY and (TODAY - due(i)).days <= 14), key=planner["due"])
    today = [i for i in open_items if due(i) == TODAY]
    tomorrow = [i for i in open_items if due(i) == TODAY + dt.timedelta(days=1)]
    week = sorted((i for i in open_items if TODAY + dt.timedelta(days=1) < due(i) <= TODAY + dt.timedelta(days=7)),
                  key=planner["due"])
    wk = planner["week_of"](TODAY, d)
    out = [f"SCHOOL TODAY - {TODAY:%A, %b %-d}" + (f" (Week {wk})" if wk else ""), ""]
    if new:
        out.append("New from the portal:")
        out += [f"  {e['course']}: {e['title']} - " + ("new material posted" if e.get("note") == "posted" else
                f"due {dt.date.fromisoformat(e['date']):%a %b %-d}" + (f" {e['time']}" if e.get("time") and e["time"] != "23:59" else "")
                + (" (date changed)" if e.get("note") == "date changed" else "")) for e in new[:8]]
        out.append("")
    for title, items in (("Overdue (tell Athena \"done <id>\" if finished)", overdue), ("Due today", today),
                         ("Due tomorrow", tomorrow), ("Later this week", week)):
        if items:
            out.append(title + ":")
            out += ["  " + fmt(i) for i in items]
            out.append("")
    # what to work on today: the nearest real deadlines first
    focus = (overdue + today + tomorrow + week)[:3]
    out.append("What you could do today:")
    if focus:
        for n, i in enumerate(focus, 1):
            out.append(f"  {n}. {i['course']}: {i['item']}")
    else:
        out.append("  Nothing due in the next 7 days - a good day to read ahead or practice for exams.")
    hint = study_hint(overdue + today + tomorrow + week)
    if hint:
        out.append("  - " + hint)
    out.append("")
    if feed_err:
        out.append(f"(Portal: {feed_err}. Dates above come from your syllabi; some are estimates.)")
    else:
        out.append("(Portal checked this morning - read only.)")
    print("\n".join(out).rstrip())


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            morning()
        elif a[0] == "feed":
            new, err = sync_feed(verbose=True)
            print(err or "\n".join(f"  {e['course']}: {e['title']} - {e['date']} {e.get('time') or ''}" for e in new) or "  nothing new")
        elif a[0] == "rebuild":
            rebuild()
        elif a[0] == "check":
            events, err = fetch_feed()
            print(err or f"OK - the portal feed returned {len(events)} calendar items. First few:\n" +
                  "\n".join(f"  {e['date']} {e['course']}: {e['title']}" for e in events[:8]))
        else:
            print(__doc__)
    except Exception as ex:
        print(f"School update error: {type(ex).__name__}: {ex}")
        sys.exit(1)
