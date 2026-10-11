#!/usr/bin/env python3
"""Donut's travel tools. Everything is read from the trip MASTER file, so when Donut updates
the master, these follow. Read-only on the internet: no accounts, no logins, nothing is booked or changed.

  trip_tools.py calendar          rebuild "<title>.ics" (flights, hotels, bookings, day plans) in the trip folder
  trip_tools.py weather [CITY] [DATE]   forecast (default: wherever we are today + any day trip). DATE like 2027-05-03 or "oct 25"
  trip_tools.py fx                exchange rates (EUR, GBP, ISK vs USD)
  trip_tools.py holidays          public holidays in each country while we're there
  trip_tools.py route FROM TO [--arrive "HH:MM"|--leave "HH:MM"] [--date YYYY-MM-DD]
                                  public-transport directions (Transitous). FROM/TO: an address or place name, or a
                                  shortcut: hotel (today's hotel) or any key in trip_config.json "places". Times are local to FROM.
  trip_tools.py flight NUMBER     live status of one of our flights (Flightradar24, e.g. BA827)
  trip_tools.py watch             flight watcher (scheduled every 30 min; silent unless something changes)
  trip_tools.py standup           the travel lines for the morning standup
  trip_tools.py selftest          check every service answers
Run with: python3 ~/.hermes/scripts/trip_tools.py ...  (it switches to ~/.local/travel/venv by itself)
"""
import datetime as dt, hashlib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

VENV_PY = Path.home() / ".local/travel/venv/bin/python"
if Path(sys.prefix).resolve() != VENV_PY.parent.parent.resolve() and VENV_PY.exists() and not os.environ.get("TRAVEL_REEXEC"):
    os.environ["TRAVEL_REEXEC"] = "1"                  # run with the venv that has the libraries, whoever called us
    os.execv(str(VENV_PY), [str(VENV_PY), __file__] + sys.argv[1:])

HOME = Path.home()
# Everything trip-specific lives in trip_config.json next to the master plan (see trip_config.example.json).
TRIP = Path(os.environ.get("TRIP_DIR", HOME / "Documents/Trip Planning/My Trip"))
MASTER = TRIP / "00 TRIP MASTER.md"
CFG = json.loads((TRIP / "trip_config.json").read_text()) if (TRIP / "trip_config.json").exists() else \
      json.loads((Path(__file__).with_name("trip_config.example.json")).read_text())
TITLE = CFG.get("title", "My Trip")
ICS = TRIP / f"{TITLE}.ics"
STATE = HOME / ".hermes/trip_state.json"
LOG = HOME / ".hermes/logs/trip_tools.log"
START, END = dt.date.fromisoformat(CFG["start"]), dt.date.fromisoformat(CFG["end"])
YEAR = START.year
HOME_TZ = ZoneInfo(CFG.get("home_tz", "America/New_York"))
NY = HOME_TZ
UA = "TripAssistant/1.0 (personal travel assistant, a few requests a day)"

CITIES = {k: tuple(v) for k, v in CFG["cities"].items()}          # name, lat, lon, time zone, country, currency
CITY_WORDS = CFG.get("city_words", {k: k for k in CITIES})          # words in hotel names/addresses -> city key
DAY_TRIPS = CFG.get("day_trips", {})                                # words in a day plan -> extra weather city
AIRPORTS = {k: tuple(v) for k, v in CFG["airports"].items()}        # IATA -> (name, time zone, city key)
PLACES = {k: tuple(v) for k, v in CFG.get("places", {}).items()}    # direction shortcuts: name, lat, lon
HOTEL_PLACE = CFG.get("hotel_place", {})                            # city key -> PLACES key of that city's hotel
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms + hail",
       99: "thunderstorms + hail"}


def log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")
    except OSError:
        pass


def get_json(url, params=None, timeout=25):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------------ reading the master file
def md_date(s, default_year=YEAR):
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", s, re.I)
    return dt.date(default_year, MON[m.group(1).lower()], int(m.group(2))) if m else None


def clock(s):
    m = re.search(r"(\d{1,2}):(\d{2})\s*([AP])M", s, re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "P" else 0)
    return dt.time(h, int(m.group(2)))


def table(text, heading):
    """Rows (lists of cells) of the markdown table under '## heading'."""
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower().startswith(heading.lower())
            continue
        if inside and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            rows.append(cells)
    return rows[1:] if rows else []                    # drop the header row


def city_of(text):
    t = text.lower()
    for w, c in CITY_WORDS.items():
        if w in t:
            return c
    return None


ROUTE_RE = re.compile(r"([A-Z]{3})(?:\s+(\S+))?\s+(\d{1,2}:\d{2}\s*[AP]M)\s*->\s*([A-Z]{3})(?:\s+(\S+))?\s+"
                      r"(\d{1,2}:\d{2}\s*[AP]M)(?:\s+([A-Z][a-z]{2}\s+\d{1,2}))?")


def norm_flight(f):
    m = re.fullmatch(r"([A-Z0-9]{2})\s*0*(\d+)", f.upper().replace(" ", ""))
    return f"{m.group(1)}{m.group(2)}" if m else f.upper().replace(" ", "")


def master():
    text = MASTER.read_text(encoding="utf-8", errors="replace")
    flights, hotels, acts, days = [], [], [], []
    for c in table(text, "Flights"):
        if len(c) < 3:
            continue
        d, m = md_date(c[0]), ROUTE_RE.search(c[2])
        if not d or not m:
            continue
        fm = re.findall(r"\b([A-Z][A-Z0-9]|[A-Z0-9][A-Z])\s?(\d{1,4})\b", c[1])
        num = re.search(r"\b" + fm[-1][0] + r"\s?" + fm[-1][1] + r"\b", c[1]).group() if fm else c[1].split()[-1]
        o, ot, otime, a, at, atime, adate = m.groups()
        dep = dt.datetime.combine(d, clock(otime), ZoneInfo(AIRPORTS.get(o, ("", "UTC"))[1]))
        arr_d = md_date(adate) if adate else d
        arr = dt.datetime.combine(arr_d, clock(atime), ZoneInfo(AIRPORTS.get(a, ("", "UTC"))[1]))
        if arr <= dep:
            arr += dt.timedelta(days=1)
        flights.append({"num": num, "key": norm_flight(num), "airline": re.split(r"\s+[A-Z0-9]{2}\s?\d", c[1])[0], "from": o,
                        "to": a, "from_term": ot or "", "to_term": at or "", "dep": dep, "arr": arr,
                        "ref": c[3] if len(c) > 3 else ""})
    for c in table(text, "Hotels"):
        if len(c) < 3:
            continue
        m = re.match(r"([A-Za-z]{3})\s+(\d{1,2})\s*-\s*(?:([A-Za-z]{3})\s+)?(\d{1,2})", c[0])
        if not m:
            continue
        a = dt.date(YEAR, MON[m.group(1).lower()], int(m.group(2)))
        b = dt.date(YEAR, MON[(m.group(3) or m.group(1)).lower()], int(m.group(4)))
        hotels.append({"start": a, "end": b, "name": c[1], "addr": c[2], "conf": c[3] if len(c) > 3 else "",
                       "notes": c[4] if len(c) > 4 else "", "city": city_of(c[1] + " " + c[2]) or "home"})
    for c in table(text, "Booked"):
        if len(c) < 2 or "cancel" in c[0].lower():
            continue
        d, t = md_date(c[0]), clock(c[0])
        if d:
            acts.append({"date": d, "time": t, "what": c[1], "ref": c[2] if len(c) > 2 else ""})
    for line in text.splitlines():
        m = re.match(r"^- (\w{3} \w{3} \d{1,2})\b(?: - (\w{3} \w{3} \d{1,2})\b)? - (.*)", line.strip())
        if m:
            a = md_date(m.group(1))
            b = md_date(m.group(2)) if m.group(2) else a
            if a and b:
                days.append({"start": a, "end": b, "text": m.group(3).strip()})
    return {"flights": flights, "hotels": hotels, "acts": acts, "days": days, "text": text}


def city_on(M, d):
    for h in M["hotels"]:
        if h["start"] <= d < h["end"]:
            return h["city"]
    if START < d <= END:          # checkout/travel day not covered: last hotel or the flight's destination
        for f in M["flights"]:
            if f["dep"].date() == d:
                return AIRPORTS.get(f["to"], ("", "", "home"))[2] if len(AIRPORTS.get(f["to"], ())) > 2 else "home"
    return "home"


def morning_city(M, d):
    """Where we wake up on day d (the hotel we check out of on a travel day)."""
    for h in M["hotels"]:
        if h["start"] < d <= h["end"]:
            return h["city"]
    return city_on(M, d)


def day_line(M, d):
    for x in M["days"]:
        if x["start"] <= d <= x["end"]:
            return x["text"]
    return ""


def tz_for_city(c):
    return ZoneInfo(CITIES.get(c, CITIES["home"])[3])


# ------------------------------------------------------------------ calendar
def build_calendar(M):
    from icalendar import Alarm, Calendar, Event
    cal = Calendar()
    cal.add("prodid", f"-//Donut//{TITLE}//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", TITLE)
    cal.add("x-wr-timezone", "America/New_York")
    stamp = dt.datetime(START.year, 1, 1, tzinfo=dt.timezone.utc)     # fixed, so unchanged events stay byte-identical
    seen = []

    def ev(uid, summary, start, end=None, desc="", loc="", alarm_min=None):
        e = Event()
        e.add("uid", hashlib.sha1(uid.encode()).hexdigest()[:20] + "@donut-trip")
        e.add("dtstamp", stamp)
        e.add("summary", summary)
        e.add("dtstart", start)
        if end is not None:
            e.add("dtend", end)
        if desc:
            e.add("description", desc)
        if loc:
            e.add("location", loc)
        if alarm_min:
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", summary)
            a.add("trigger", dt.timedelta(minutes=-alarm_min))
            e.add_component(a)
        cal.add_component(e)
        seen.append(f"{summary} @ {start}")

    for f in M["flights"]:
        fn, tn = AIRPORTS.get(f["from"], (f["from"],))[0], AIRPORTS.get(f["to"], (f["to"],))[0]
        ev(f"flight-{f['key']}-{f['dep'].date()}", f"✈️ {f['num']} {f['from']}→{f['to']}", f["dep"], f["arr"],
           f"{f['airline']} {f['num']}\n{fn}{' ' + f['from_term'] if f['from_term'] else ''} {f['dep']:%a %b %d %I:%M %p} "
           f"(local) → {tn}{' ' + f['to_term'] if f['to_term'] else ''} {f['arr']:%a %b %d %I:%M %p} (local)\n"
           f"{f['ref']}", f"{fn} airport", alarm_min=180)
    for h in M["hotels"]:
        ev(f"hotel-{h['conf'] or h['name']}", f"🏨 {h['name']}", h["start"], h["end"],
           f"Confirmation: {h['conf']}\n{h['addr']}\n{h['notes']}", h["addr"])
    for a in M["acts"]:
        c = city_on(M, a["date"])
        w = a["what"].lower()
        if a["time"]:
            mins = 150 if "lagoon" in w else 120 if ("dinner" in w or "lunch" in w or "tour" in w) else 90
            s = dt.datetime.combine(a["date"], a["time"], tz_for_city(c))
            title = a["what"].split(",")[0][:60]
            if "eiffel" in w:
                title = "Eiffel Tower tour - MEET BY 3:30 PM"
            ev(f"act-{a['ref'] or a['what'][:30]}-{a['date']}", f"⭐ {title}", s, s + dt.timedelta(minutes=mins),
               f"{a['what']}\nRef: {a['ref']}", CITIES.get(c, CITIES['home'])[0], alarm_min=60)
        else:
            ev(f"act-{a['ref'] or a['what'][:30]}-{a['date']}", f"⭐ {a['what'][:60]}", a["date"],
               a["date"] + dt.timedelta(days=1), f"{a['what']}\nRef: {a['ref']}")
    for x in M["days"]:
        short = re.split(r"(?<=[a-z0-9)])[.;] ", x["text"])[0]
        short = (short[:70].rsplit(" ", 1)[0] + "…") if len(short) > 72 else short
        ev(f"day-{x['start']}", f"📍 {short}", x["start"], x["end"] + dt.timedelta(days=1),
           x["text"] + "\n\nFrom the trip master plan (Donut keeps it updated).")
    cal.add_missing_timezones()
    return cal.to_ical(), seen


def cmd_calendar(quiet_if_same=False):
    M = master()
    data, seen = build_calendar(M)
    st = load_state()
    old = st.get("calendar_events", [])
    digest = hashlib.sha1(data).hexdigest()
    if quiet_if_same and digest == st.get("calendar_hash") and ICS.exists():
        return
    ICS.write_bytes(data)
    added = [s for s in seen if s not in old]
    gone = [s for s in old if s not in seen]
    st.update(calendar_hash=digest, calendar_events=seen)
    save_state(st)
    if not old:
        print(f"RESULT: OK - made {ICS.name} with {len(seen)} events ({len(M['flights'])} flights, "
              f"{len(M['hotels'])} hotels, {len(M['acts'])} bookings, {len(M['days'])} day plans).\n{ICS}")
    elif added or gone or not quiet_if_same:
        print(f"TRIP CALENDAR updated: {ICS.name} ({len(seen)} events).")
        for s in added[:8]:
            print(f"  + {s.split(' @ ')[0]}  ({s.split(' @ ')[1][:16]})")
        for s in gone[:8]:
            print(f"  - {s.split(' @ ')[0]}  ({s.split(' @ ')[1][:16]})")
        if added or gone:
            print("To update your phone: Google Calendar on the web > Settings > Import & export > Import that file "
                  "into your trip calendar.")


# ------------------------------------------------------------------ weather / fx / holidays
def f_c(c):
    return f"{round(c * 9 / 5 + 32)}"


def weather(city, d1, d2=None):
    name, lat, lon, tz, *_ = CITIES[city]
    d2 = d2 or d1
    r = get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": tz, "wind_speed_unit": "mph",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "start_date": d1.isoformat(), "end_date": d2.isoformat()})
    D, out = r["daily"], []
    for i, day in enumerate(D["time"]):
        hi, lo = D["temperature_2m_max"][i], D["temperature_2m_min"][i]
        if hi is None:
            continue
        rain = D["precipitation_probability_max"][i]
        wind = D["wind_speed_10m_max"][i]
        out.append(f"{name} {dt.date.fromisoformat(day):%a %b %d}: {WMO.get(D['weather_code'][i], 'mixed')}, "
                   f"{f_c(hi)}/{f_c(lo)}°F ({round(hi)}/{round(lo)}°C)"
                   + (f", rain {rain}%" if rain is not None else "") + (f", wind {round(wind)} mph" if wind else ""))
    return out


def fx_line():
    r = get_json("https://api.frankfurter.dev/v1/latest", {"base": "USD", "symbols": "EUR,GBP,ISK"})
    R = r.get("rates", {})
    parts = []
    if R.get("EUR"):
        parts.append(f"€1 = ${1 / R['EUR']:.2f}")
    if R.get("GBP"):
        parts.append(f"£1 = ${1 / R['GBP']:.2f}")
    if R.get("ISK"):
        parts.append(f"1,000 ISK = ${1000 / R['ISK']:.2f}")
    return f"{' · '.join(parts)}  (ECB rates {r.get('date', '')}; card rates run ~1-3% worse)" if parts else ""


COUNTRY_NAME = {"IS": "Iceland", "IE": "Ireland", "GB": "the UK", "FR": "France", "IT": "Italy", "ES": "Spain",
                "US": "the US"}


def trip_holidays(M, d1=START, d2=END):
    import holidays as H
    out = []
    d = d1
    while d <= d2:
        cities = []
        for cty in (morning_city(M, d), city_on(M, d)):
            if cty not in cities:
                cities.append(cty)
        for cty in cities:
            c = CITIES.get(cty, CITIES["home"])
            cc = c[4]
            try:
                hol = H.country_holidays(cc, subdiv="ENG" if cc == "GB" else None, years=YEAR, language="en_US")
            except Exception:
                try:
                    hol = H.country_holidays(cc, subdiv="ENG" if cc == "GB" else None, years=YEAR)
                except Exception:
                    hol = {}
            if d in hol:
                out.append((d, f"{d:%a %b %d}: {hol.get(d)} in {COUNTRY_NAME.get(cc, cc)} ({c[0]}) - "
                               f"expect Sunday/holiday hours for shops, museums and transport."))
        d += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------ directions (Transitous)
TRANSITOUS = "https://api.transitous.org"


def motis(path, params):
    last = None
    for v in ("v5", "v6", "v4", "v3", "v2", "v1"):
        try:
            return get_json(f"{TRANSITOUS}/api/{v}/{path}", params, timeout=40)
        except urllib.error.HTTPError as ex:
            last = ex
            if ex.code != 404:
                raise
    raise last


def place(M, s, d):
    k = re.sub(r"[^a-z]", "", s.lower())
    if k in ("hotel", "here", "home"):
        k = HOTEL_PLACE.get(city_on(M, d), "")
        if not k:
            raise ValueError("no hotel that day")
    if k in PLACES:
        return PLACES[k]
    c = CITIES[city_on(M, d)]
    params = {"text": s, "place": f"{c[1]},{c[2]}", "placeBias": 5, "numResults": 1}
    try:
        res = get_json(f"{TRANSITOUS}/api/v1/geocode", params, timeout=30)
    except urllib.error.HTTPError:
        res = []
    if not res:
        raise ValueError(f"couldn't find '{s}' - try a fuller address")
    r = res[0]
    return (", ".join(x for x in [r.get("name"), r.get("street"), (r.get("areas") or [{}])[0].get("name")] if x),
            r["lat"], r["lon"])


def hm(iso, tz):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%-I:%M %p")


def route(M, a, b, when=None, arrive=False, d=None):
    d = d or dt.datetime.now(tz_for_city(city_on(M, dt.date.today()))).date()
    tz = tz_for_city(city_on(M, d))
    fa, fb = place(M, a, d), place(M, b, d)
    t = dt.datetime.combine(d, when, tz) if when else dt.datetime.now(tz)
    r = motis("plan", {"fromPlace": f"{fa[1]},{fa[2]}", "toPlace": f"{fb[1]},{fb[2]}",
                       "time": t.isoformat(timespec="seconds"), "arriveBy": "true" if arrive else "false",
                       "numItineraries": 3})
    its = r.get("itineraries") or []
    head = f"{fa[0]} → {fb[0]}, {'arrive by' if arrive else 'leave'} {t:%a %b %d %-I:%M %p}:"
    if not its:
        return [head, "  No public-transport route found (maybe outside coverage) - a taxi/walk may be best."]
    out = [head]
    for i, it in enumerate(its[:3], 1):
        mins = round(it["duration"] / 60)
        out.append(f"  Option {i}: {hm(it['startTime'], tz)} → {hm(it['endTime'], tz)} ({mins} min, "
                   f"{it.get('transfers', 0)} change{'s' if it.get('transfers', 0) != 1 else ''})")
        for L in it["legs"]:
            lm = round(L.get("duration", 0) / 60)
            if L["mode"] in ("WALK", "BIKE", "CAR"):
                if lm >= 2:
                    out.append(f"     walk {lm} min to {L['to'].get('name', '')}".rstrip())
                continue
            line = L.get("displayName") or L.get("routeShortName") or L.get("tripShortName") or L["mode"].title()
            towards = f" toward {L['headsign']}" if L.get("headsign") else ""
            rt = " (live)" if L.get("realTime") else ""
            out.append(f"     {hm(L['startTime'], tz)} {L['mode'].replace('_', ' ').title()} {line}{towards}: "
                       f"{L['from'].get('name', '')} → {L['to'].get('name', '')} ({lm} min){rt}")
    out.append("  (Transitous - check the station boards on the day; holiday/marathon diversions may not show.)")
    return out


# ------------------------------------------------------------------ flights (Flightradar24, unofficial)
def fr24_lookup(f):
    """Find our flight in the departure board of its airport. Returns a dict or None."""
    from FlightRadarAPI import FlightRadar24API
    api = FlightRadar24API(timeout=20)
    for page in range(1, 6):
        res = api.get_airport_details(f["from"], flight_limit=100, page=page)
        deps = (((res.get("airport") or {}).get("pluginData") or {}).get("schedule") or {}).get("departures") or {}
        data = deps.get("data") or []
        for item in data:
            fl = item.get("flight") or {}
            num = (((fl.get("identification") or {}).get("number")) or {}).get("default") or ""
            if norm_flight(num) != f["key"]:
                continue
            T = fl.get("time") or {}
            sched = (T.get("scheduled") or {}).get("departure")
            if sched and abs(sched - f["dep"].timestamp()) > 6 * 3600:
                continue                               # same number, different day
            st = fl.get("status") or {}
            gen = ((st.get("generic") or {}).get("status") or {})
            origin = ((fl.get("airport") or {}).get("origin") or {}).get("info") or {}
            dest = ((fl.get("airport") or {}).get("destination") or {}).get("info") or {}
            return {"status_text": st.get("text") or "", "generic": (gen.get("text") or "").lower(),
                    "sched": sched, "est": (T.get("estimated") or {}).get("departure"),
                    "real": (T.get("real") or {}).get("departure"),
                    "est_arr": (T.get("estimated") or {}).get("arrival") or (T.get("scheduled") or {}).get("arrival"),
                    "gate": origin.get("gate") or "", "terminal": origin.get("terminal") or "",
                    "dest_terminal": dest.get("terminal") or "", "baggage": dest.get("baggage") or ""}
        pages = ((deps.get("page") or {}).get("total")) or 1
        if page >= pages or not data:
            break
        time.sleep(1.5)
    return None


def fmt_ts(ts, code):
    return dt.datetime.fromtimestamp(ts, ZoneInfo(AIRPORTS.get(code, ("", "UTC"))[1])).strftime("%-I:%M %p")


def flight_summary(f, x):
    dep_ts = x.get("real") or x.get("est") or x.get("sched") or f["dep"].timestamp()
    late = round((dep_ts - f["dep"].timestamp()) / 60)
    if x["generic"] in ("canceled", "cancelled"):
        state = "⚠️ CANCELED - open the airline app / call the airline now"
    elif x["generic"] == "diverted":
        state = "⚠️ DIVERTED - " + x["status_text"]
    elif x.get("real"):
        state = f"departed {fmt_ts(x['real'], f['from'])}" + (f" ({late} min late)" if late >= 10 else "")
    elif late >= 10:
        state = f"DELAYED - now leaving ~{fmt_ts(dep_ts, f['from'])} ({late} min late)"
    else:
        state = f"on time ({f['dep']:%-I:%M %p})"
    gate = f"Terminal {x['terminal'] or f['from_term'] or '?'}" + (f", gate {x['gate']}" if x["gate"] else ", gate not posted yet")
    arr = f"; landing ~{fmt_ts(x['est_arr'], f['to'])} local" if x.get("est_arr") else ""
    return f"{f['num']} {f['from']}→{f['to']}: {state}. {gate}{arr}."


def cmd_flight(M, num):
    k = norm_flight(num)
    fs = [f for f in M["flights"] if f["key"] == k]
    if not fs:
        print(f"{num} isn't one of the trip flights. Ours: " + ", ".join(f["num"] for f in M["flights"]))
        return
    f = fs[0]
    if abs((f["dep"] - dt.datetime.now(dt.timezone.utc)).total_seconds()) > 30 * 3600:
        print(f"{f['num']} {f['from']}→{f['to']} is {f['dep']:%a %b %d at %-I:%M %p} local. Live status shows on "
              f"Flightradar24 from about a day before.")
        return
    x = fr24_lookup(f)
    print(flight_summary(f, x) if x else f"{f['num']}: not on {f['from']}'s departure board right now - "
          f"check the airline app.")


def cmd_watch(M):
    now = dt.datetime.now(dt.timezone.utc)
    active = [f for f in M["flights"] if f["dep"] - dt.timedelta(hours=6) <= now <= f["dep"] + dt.timedelta(hours=3)]
    if not active:
        return                                         # silent - nothing to watch
    st = load_state()
    W = st.setdefault("watch", {})
    msgs = []
    for f in active:
        key = f"{f['key']}-{f['dep'].date()}"
        s = W.setdefault(key, {})
        if s.get("done"):
            continue
        try:
            x = fr24_lookup(f)
        except Exception as ex:
            x = None
            log(f"watch {key} error {type(ex).__name__}: {ex}")
        if not x:
            s["fails"] = s.get("fails", 0) + 1
            if s["fails"] == 4 and not s.get("warned"):
                s["warned"] = True
                msgs.append(f"Flight watch can't see {f['num']} on Flightradar24 right now - use the "
                            f"{f['airline']} app for gate and delay news.")
            continue
        s["fails"] = 0
        dep_ts = x.get("est") or x.get("sched") or f["dep"].timestamp()
        bad = x["generic"] in ("canceled", "cancelled", "diverted")
        if "last" not in s:
            msgs.append("FLIGHT WATCH: " + flight_summary(f, x))
        else:
            L = s["last"]
            if bad and L.get("generic") != x["generic"]:
                msgs.append("FLIGHT ALERT: " + flight_summary(f, x))
            elif x.get("real") and not L.get("real"):
                msgs.append("✈️ " + flight_summary(f, x))
                s["done"] = True
            else:
                if abs(dep_ts - L.get("dep_ts", dep_ts)) >= 10 * 60:
                    msgs.append("FLIGHT UPDATE: " + flight_summary(f, x))
                elif x["gate"] and x["gate"] != L.get("gate"):
                    msgs.append(f"GATE {'CHANGE' if L.get('gate') else 'POSTED'}: {f['num']} - terminal "
                                f"{x['terminal'] or f['from_term'] or '?'}, gate {x['gate']}"
                                + (f" (was {L['gate']})" if L.get("gate") else "") + ".")
        s["last"] = {"generic": x["generic"], "dep_ts": dep_ts, "gate": x["gate"], "real": x.get("real")}
    save_state(st)
    if msgs:
        text = "\n".join(msgs)
        print(text)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from textme import text_robert
            text_robert("flight", text)
        except Exception:
            pass


# ------------------------------------------------------------------ standup
def cmd_standup(M):
    today = dt.date.fromisoformat(os.environ["TRIP_TODAY"]) if os.environ.get("TRIP_TODAY") else dt.date.today()
    if today > END or (START - today).days > 7:
        return
    out = []
    if today >= START:
        days = [today, today + dt.timedelta(days=1)]                          # here + tomorrow
    else:
        days = [START + dt.timedelta(days=1), START + dt.timedelta(days=2)]   # before we leave: the first two trip days
    for d in days:
        c = city_on(M, d)
        if c == "home":
            continue
        places = [c] + [v for k, v in DAY_TRIPS.items() if k in day_line(M, d).lower()]
        for p in places:
            try:
                out += weather(p, d)
            except Exception as ex:
                log(f"weather {p} {d} {type(ex).__name__}: {ex}")
    for f in M["flights"]:
        if f["dep"].astimezone(tz_for_city(city_on(M, today))).date() == today or f["dep"].date() == today:
            out.append(f"Flight today: {f['num']} {f['from']} {f['dep']:%-I:%M %p} → {f['to']} {f['arr']:%-I:%M %p} "
                       f"(local times). Flight watch is on.")
    if today >= START - dt.timedelta(days=3):
        try:
            fx = fx_line()
            if fx:
                out.append(fx)
        except Exception as ex:
            log(f"fx {type(ex).__name__}: {ex}")
        try:
            for d, line in trip_holidays(M, max(today, START), min(today + dt.timedelta(days=2), END)):
                out.append("Heads-up: " + line)
        except Exception as ex:
            log(f"holidays {type(ex).__name__}: {ex}")
    print("\n".join(out))


# ------------------------------------------------------------------ state + main
def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str))


def parse_day(s):
    s = s.strip()
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        d = md_date(s)
        if d:
            return d
    raise ValueError(f"don't understand the date '{s}' (use YYYY-MM-DD or 'oct 25')")


def selftest(M):
    ok = True
    print(f"Master file: {len(M['flights'])} flights, {len(M['hotels'])} hotels, {len(M['acts'])} bookings, "
          f"{len(M['days'])} day plans")
    checks = [
        ("Calendar (icalendar)", lambda: f"{len(build_calendar(M)[1])} events"),
        ("Weather (Open-Meteo)", lambda: weather(next(k for k in CITIES if k != "home"), dt.date.today())[0]),
        ("Exchange rates (Frankfurter)", fx_line),
        ("Holidays (holidays)", lambda: "; ".join(l for _, l in trip_holidays(M))[:120] or "none on trip days"),
        ("Directions (Transitous)", lambda: route(M, *list(PLACES)[:2], dt.time(9, 30), False,
                                                  dt.date.today() + dt.timedelta(days=1))[1].strip()),
        ("Flights (Flightradar24)", lambda: (lambda r: f"Home airport board has {len(r['airport']['pluginData']['schedule']['departures']['data'])} departures")(
            __import__("FlightRadarAPI").FlightRadar24API(timeout=20).get_airport_details(next(iter(AIRPORTS)), flight_limit=10))),
    ]
    for name, fn in checks:
        try:
            print(f"  OK   {name}: {fn()}")
        except Exception as ex:
            ok = False
            print(f"  FAIL {name}: {type(ex).__name__}: {str(ex)[:150]}")
    print("RESULT: " + ("OK - all travel tools working" if ok else "some tools failed (see above)"))


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    M = master()
    cmd = a[0]
    if cmd == "calendar":
        cmd_calendar(quiet_if_same="--nightly" in a)
    elif cmd == "weather":
        city, d = None, dt.date.today()
        for x in a[1:]:
            try:
                d = parse_day(x)
            except ValueError:
                city = re.sub(r"[^a-z]", "", x.lower())
        if city and city not in CITIES:
            print(f"Unknown city '{city}'. Known: {', '.join(k for k in CITIES)}")
            return
        places = [city] if city else [city_on(M, d)] + [v for k, v in DAY_TRIPS.items() if k in day_line(M, d).lower()]
        for p in places:
            print("\n".join(weather(p, d)) or f"No forecast yet for {d} (forecasts reach ~16 days ahead).")
    elif cmd == "fx":
        print(fx_line())
    elif cmd == "holidays":
        print("\n".join(l for _, l in trip_holidays(M)) or "No public holidays where we'll be.")
    elif cmd == "route":
        args = a[1:]
        when, arrive, d = None, False, None
        for flag in ("--arrive", "--leave", "--date"):
            if flag in args:
                i = args.index(flag)
                val = args[i + 1]
                del args[i:i + 2]
                if flag == "--date":
                    d = parse_day(val)
                else:
                    when, arrive = clock(val) or dt.time(*map(int, val.split(":"))), flag == "--arrive"
                    m = re.search(r"\d{4}-\d{2}-\d{2}", val)
                    d = d or (dt.date.fromisoformat(m.group()) if m else None)
        if len(args) != 2:
            print('Usage: trip_tools.py route FROM TO [--arrive "HH:MM"|--leave "HH:MM"] [--date YYYY-MM-DD]')
            return
        try:
            print("\n".join(route(M, args[0], args[1], when, arrive, d)))
        except Exception as ex:
            print(f"FAIL - directions didn't work: {type(ex).__name__}: {str(ex)[:200]}")
    elif cmd == "flight" and len(a) > 1:
        try:
            cmd_flight(M, " ".join(a[1:]))
        except Exception as ex:
            print(f"FAIL - Flightradar24 didn't answer ({type(ex).__name__}); check the airline app.")
    elif cmd == "watch":
        cmd_watch(M)
    elif cmd == "standup":
        cmd_standup(M)
    elif cmd == "selftest":
        selftest(M)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
