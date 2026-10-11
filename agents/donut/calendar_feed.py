#!/usr/bin/env python3
"""Reads the owner's calendar(s) from private iCal links - READ ONLY, nothing is ever changed.

  calendar_feed.py            today's events (used by the morning standup)
  calendar_feed.py days N     the next N days
  calendar_feed.py check      test each saved calendar link

Links live in ~/.hermes/calendar_feeds.txt, one per line, optionally "Label | https://...":
  Personal | https://calendar.google.com/calendar/ical/.../basic.ics
Repeating events (weekly meetings, classes) are expanded; times are shown in Eastern.
"""
import datetime as dt, re, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

FEEDS = Path.home() / ".hermes" / "calendar_feeds.txt"
TZ = ZoneInfo("America/New_York")
TODAY = dt.datetime.now(TZ).date()


def feeds():
    out = []
    if not FEEDS.exists():
        return out
    for line in FEEDS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        url = url.strip()
        url = "https://" + url[len("webcal://"):] if url.startswith("webcal://") else url
        out.append((label.strip() or "Calendar", url))
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 calendar-reader"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def _parse_dt(val, params):
    v = val.strip()
    if re.fullmatch(r"\d{8}", v) or "VALUE=DATE" in params.upper():
        return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8])), True
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", v)
    if not m:
        return None, False
    t = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    if m.group(2):
        return t.replace(tzinfo=dt.timezone.utc), False
    tzid = re.search(r"TZID=([^;:]+)", params)
    try:
        return t.replace(tzinfo=ZoneInfo(tzid.group(1).strip('"')) if tzid else TZ), False
    except Exception:
        return t.replace(tzinfo=TZ), False


def parse(text):
    text = re.sub(r"\r?\n[ \t]", "", text)
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        f, multi = {}, {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            name, _, params = key.partition(";")
            name = name.upper()
            multi.setdefault(name, []).append((val, params))
            f.setdefault(name, (val, params))
        if "DTSTART" not in f:
            continue
        start, allday = _parse_dt(*f["DTSTART"])
        if start is None:
            continue
        end = _parse_dt(*f["DTEND"])[0] if "DTEND" in f else None
        if f.get("STATUS", ("", ""))[0].upper() == "CANCELLED":
            status = "cancelled"
        else:
            status = "ok"
        summ = f.get("SUMMARY", ("(no title)", ""))[0].replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
        exdates = set()
        for val, params in multi.get("EXDATE", []):
            for v in val.split(","):
                d, _ = _parse_dt(v, params)
                if d is not None:
                    exdates.add(d if isinstance(d, dt.date) and not isinstance(d, dt.datetime) else d.astimezone(TZ).replace(tzinfo=None))
        rid = _parse_dt(*f["RECURRENCE-ID"])[0] if "RECURRENCE-ID" in f else None
        events.append({"uid": f.get("UID", ("", ""))[0], "title": summ, "start": start, "end": end, "allday": allday,
                       "rrule": f.get("RRULE", ("", ""))[0], "exdates": exdates, "rid": rid, "status": status,
                       "location": f.get("LOCATION", ("", ""))[0].replace("\\,", ",")})
    return events


def occurrences(ev, first, last):
    """Yield (start, end) occurrences that fall on dates first..last (inclusive)."""
    dur = (ev["end"] - ev["start"]) if ev["end"] is not None else (dt.timedelta(days=1) if ev["allday"] else dt.timedelta(0))
    if not ev["rrule"]:
        d = ev["start"] if ev["allday"] else ev["start"].astimezone(TZ).date()
        if first <= d <= last:
            yield ev["start"], ev["start"] + dur
        return
    from dateutil.rrule import rrulestr
    if ev["allday"]:
        base = dt.datetime.combine(ev["start"], dt.time())
        rule = rrulestr(ev["rrule"], dtstart=base, ignoretz=True)
        for o in rule.between(dt.datetime.combine(first, dt.time()) - dt.timedelta(seconds=1),
                              dt.datetime.combine(last, dt.time(23, 59, 59)), inc=True):
            if o.date() not in ev["exdates"]:
                yield o.date(), o.date() + dur
        return
    start_local = ev["start"].astimezone(ev["start"].tzinfo)
    naive = start_local.replace(tzinfo=None)
    rr = re.sub(r"UNTIL=(\d{8}T\d{6})Z", lambda m: "UNTIL=" + dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
                .replace(tzinfo=dt.timezone.utc).astimezone(start_local.tzinfo).strftime("%Y%m%dT%H%M%S"), ev["rrule"])
    rule = rrulestr(rr, dtstart=naive, ignoretz=True)
    lo = dt.datetime.combine(first, dt.time()) - dt.timedelta(days=1)
    hi = dt.datetime.combine(last, dt.time(23, 59, 59)) + dt.timedelta(days=1)
    for o in rule.between(lo, hi, inc=True):
        aware = o.replace(tzinfo=start_local.tzinfo)
        local = aware.astimezone(TZ)
        if first <= local.date() <= last and local.replace(tzinfo=None) not in ev["exdates"] and o not in ev["exdates"]:
            yield aware, aware + dur


def agenda(first, last):
    items, errors = [], []
    for label, url in feeds():
        try:
            evs = parse(fetch(url))
        except Exception as ex:
            errors.append(f"{label}: couldn't read the calendar ({type(ex).__name__})")
            continue
        overrides = {(e["uid"], (e["rid"].astimezone(TZ).replace(tzinfo=None) if isinstance(e["rid"], dt.datetime) else e["rid"]))
                     for e in evs if e["rid"] is not None}
        for e in evs:
            if e["rid"] is None and e["status"] == "cancelled":
                continue
            for s, en in occurrences(e, first, last):
                key = s.astimezone(TZ).replace(tzinfo=None) if isinstance(s, dt.datetime) else s
                if e["rid"] is None and (e["uid"], key) in overrides:
                    continue          # this occurrence was moved/edited; the override event covers it
                if e["status"] == "cancelled":
                    continue
                items.append((label, e, s, en))
    def sortkey(x):
        s = x[2]
        return (s if isinstance(s, dt.date) and not isinstance(s, dt.datetime) else s.astimezone(TZ).date(),
                0 if not isinstance(s, dt.datetime) else 1, s.astimezone(TZ).time() if isinstance(s, dt.datetime) else dt.time())
    return sorted(items, key=sortkey), errors


def fmt(label, e, s, en, show_cal):
    cal = f"  ({label})" if show_cal else ""
    loc = f" @ {e['location'][:40]}" if e["location"] else ""
    if not isinstance(s, dt.datetime):
        return f"All day - {e['title']}{loc}{cal}"
    a, b = s.astimezone(TZ), en.astimezone(TZ) if isinstance(en, dt.datetime) else None
    t = f"{a:%-I:%M %p}" + (f"-{b:%-I:%M %p}" if b and b > a else "")
    return f"{t} - {e['title']}{loc}{cal}"


def show(days=1):
    if not feeds():
        print("No calendar linked yet.")
        return
    first, last = TODAY, TODAY + dt.timedelta(days=days - 1)
    items, errors = agenda(first, last)
    show_cal = len(feeds()) > 1
    if days == 1:
        print("\n".join(fmt(*i, show_cal) for i in items) or "Nothing on the calendar today.")
    else:
        cur = None
        for i in items:
            d = i[2] if not isinstance(i[2], dt.datetime) else i[2].astimezone(TZ).date()
            if d != cur:
                print(f"{d:%a %b %-d}")
                cur = d
            print("  " + fmt(*i, show_cal))
        if not items:
            print(f"Nothing on the calendar in the next {days} days.")
    for e in errors:
        print(e)


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            show(1)
        elif a[0] == "days" and len(a) > 1:
            show(int(a[1]))
        elif a[0] == "check":
            if not feeds():
                print("No calendar links saved in ~/.hermes/calendar_feeds.txt")
            for label, url in feeds():
                try:
                    n = len(parse(fetch(url)))
                    print(f"OK - {label}: {n} events read")
                except Exception as ex:
                    print(f"FAILED - {label}: {type(ex).__name__}: {ex}")
        else:
            print(__doc__)
    except Exception as ex:
        print(f"Calendar error: {type(ex).__name__}: {ex}")
        sys.exit(1)
