#!/usr/bin/env python3
"""Timed reminders that Donut texts to the owner's iPhone (through textme.py, so only ever to the owner).

  reminders.py add "2026-10-29 09:30" "Europe/Paris" "Notre-Dame slot" [--before 5h,10m]
        -> one reminder per --before offset (default: at the time itself). Offsets like 5h, 10m, 1d, 90m.
  reminders.py list               upcoming reminders (in their local time + Eastern)
  reminders.py remove ID          delete one (ID from list)
  reminders.py check              send anything due (also what a bare 'reminders.py' does - the 5-min job)
  reminders.py help               this help
Stored in ~/.hermes/reminders.json.
"""
import datetime as dt, json, re, sys
from pathlib import Path
from zoneinfo import ZoneInfo

H = Path.home() / ".hermes"
STORE = H / "reminders.json"
NY = ZoneInfo("America/New_York")
LATE_OK = dt.timedelta(hours=2)          # if the Mac was asleep, still send up to 2 h late (marked "late")


def load():
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return []


def save(items):
    STORE.write_text(json.dumps(items, indent=1))


def offset(s):
    m = re.fullmatch(r"(\d+)\s*([dhm])", s.strip().lower())
    if not m:
        raise ValueError(f"bad offset '{s}' (use like 5h, 10m, 1d)")
    n = int(m.group(1))
    return dt.timedelta(days=n) if m.group(2) == "d" else dt.timedelta(hours=n) if m.group(2) == "h" else dt.timedelta(minutes=n)


def human(td):
    m = int(td.total_seconds() // 60)
    if m == 0:
        return "now"
    if m % 1440 == 0:
        return f"in {m // 1440} day{'s' if m // 1440 > 1 else ''}"
    if m % 60 == 0:
        return f"in {m // 60} hour{'s' if m // 60 > 1 else ''}"
    return f"in {m} minutes" if m < 60 else f"in {m // 60} h {m % 60} min"


def add(when, tz, text, befores):
    z = ZoneInfo(tz)
    event = dt.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=z)
    items = load()
    nid = max([i["id"] for i in items] or [0]) + 1
    out = []
    for b in befores or ["0m"]:
        off = offset(b)
        at = event - off
        items.append({"id": nid, "at": at.isoformat(), "event": event.isoformat(), "tz": tz, "text": text,
                      "lead": human(off), "sent": False})
        out.append(f"#{nid}: texts you {at:%a %b %d %-I:%M %p} {tz.split('/')[-1]} time "
                   f"({at.astimezone(NY):%-I:%M %p} Eastern) - '{text}' {human(off)}")
        nid += 1
    save(items)
    print("RESULT: OK - reminders set:\n  " + "\n  ".join(out))


def lst():
    items = sorted((i for i in load() if not i["sent"]), key=lambda i: i["at"])
    if not items:
        print("No upcoming reminders.")
        return
    for i in items:
        at = dt.datetime.fromisoformat(i["at"])
        print(f"#{i['id']}  {at:%a %b %d %-I:%M %p} {i['tz'].split('/')[-1]} ({at.astimezone(NY):%-I:%M %p} ET) - "
              f"{i['text']} ({i['lead']})")


def remove(rid):
    items = load()
    keep = [i for i in items if str(i["id"]) != str(rid)]
    save(keep)
    print("RESULT: OK - removed" if len(keep) < len(items) else f"No reminder #{rid}.")


def check():
    items = load()
    now = dt.datetime.now(dt.timezone.utc)
    changed, failed = False, []
    for i in items:
        if i["sent"]:
            continue
        at = dt.datetime.fromisoformat(i["at"])
        if at > now:
            continue
        i["sent"] = True
        changed = True
        if now - at > LATE_OK:
            i["skipped"] = "too late"
            continue
        ev = dt.datetime.fromisoformat(i["event"])
        msg = (f"⏰ Reminder: {i['text']} - {ev:%-I:%M %p} ({i['lead']})"
               + (" [sent late - the Mac was asleep]" if now - at > dt.timedelta(minutes=15) else ""))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from textme import send
            ok, err = send(msg)
        except Exception as ex:
            ok, err = False, f"{type(ex).__name__}: {ex}"
        if not ok:
            failed.append(f"{msg}\n(text failed: {err})")
    if changed:
        save(items)
    if failed:                                     # Telegram gets it only if the text didn't go through
        print("\n\n".join(failed))


def main(a):
    if not a:                                      # the scheduled job runs it with no arguments
        check()
    elif a[0] in ("-h", "--help", "help"):
        print(__doc__)
    elif a[0] == "add" and len(a) >= 4:
        befores = []
        if "--before" in a:
            k = a.index("--before")
            befores = [x for x in a[k + 1].split(",") if x.strip()]
            a = a[:k] + a[k + 2:]
        try:
            add(a[1], a[2], " ".join(a[3:]), befores)
        except Exception as ex:
            print(f"FAIL - {ex}")
    elif a[0] == "list":
        lst()
    elif a[0] == "remove" and len(a) > 1:
        remove(a[1])
    elif a[0] == "check":
        check()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
