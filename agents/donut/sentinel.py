#!/usr/bin/env python3
"""Sentinel - watches web pages and tells the owner on Telegram only when something happens. ALERT ONLY:
it never logs in, clicks, buys or submits anything.

  sentinel.py                      check everything (the scheduled job). Prints nothing unless there's news.
  sentinel.py list                 what's being watched and its last state
  sentinel.py add URL [MODE] ["label"] [TEXT]
        MODE = up        alert when the site goes down / comes back (default)
               cert      alert when the HTTPS certificate expires within 14 days
               changed   alert when the page's visible text changes
               contains  alert when TEXT appears on the page (e.g. "Now Hiring", "Available")
  sentinel.py remove N             stop watching item N (from list)
  sentinel.py check                run every check now and show results (nothing is sent)

Watchlist and state: ~/.hermes/sentinel/
"""
import datetime as dt, hashlib, html, json, re, socket, ssl, sys, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlparse

DIR = Path.home() / ".hermes" / "sentinel"
LIST = DIR / "watchlist.json"
STATE = DIR / "state.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CERT_DAYS = 14
DOWN_AFTER = 2          # consecutive failed checks before alerting "down" (avoids one-off blips)
NOW = dt.datetime.now()


def load(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def save(p, data):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    json.dump(data, open(tmp, "w"), indent=1)
    tmp.replace(p)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
        return r.status, body, time.time() - t0


def visible_text(body):
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    # drop things that change on every load (times, counters, tokens)
    text = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM)?\b", " ", text, flags=re.I)
    text = re.sub(r"\b[0-9a-f]{16,}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cert_days_left(url):
    host = urlparse(url).hostname
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=15) as s:
        with ctx.wrap_socket(s, server_hostname=host) as ss:
            exp = dt.datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return (exp - NOW).days, exp


def check_one(item, st):
    """Returns (alert_text or None, status_text). Updates st in place."""
    url, mode, label = item["url"], item.get("mode", "up"), item.get("label") or item["url"]
    try:
        if mode == "cert":
            days, exp = cert_days_left(url)
            status = f"certificate expires {exp:%b %d, %Y} ({days} days)"
            st["ok"] = True
            if days <= CERT_DAYS and st.get("cert_warned") != str(exp.date()):
                st["cert_warned"] = str(exp.date())
                return f"⚠️ {label}: HTTPS certificate expires in {days} days ({exp:%b %d}). Renew it before visitors see a security warning.", status
            return None, status
        code, body, secs = fetch(url)
        if mode == "up":
            status = f"up (HTTP {code}, {secs:.1f}s)"
            was_down = st.get("down", False)
            st.update(fails=0, down=False, ok=True)
            if was_down:
                return f"✅ {label} is back up ({url}).", status
            return None, status
        text = visible_text(body)
        if mode == "contains":
            needle = item.get("text", "")
            found = needle.lower() in text.lower()
            status = f"'{needle}' {'FOUND' if found else 'not on page'}"
            before = st.get("found")
            st.update(found=found, ok=True)
            if found and not before:
                i = text.lower().find(needle.lower())
                snippet = text[max(0, i - 80): i + 160]
                return f"🔔 {label}: \"{needle}\" just appeared.\n…{snippet}…\n{url}", status
            return None, status
        if mode == "changed":
            h = hashlib.sha256(text.encode()).hexdigest()
            prev, prev_text = st.get("hash"), st.get("text", "")
            st.update(hash=h, text=text[:20000], ok=True)
            status = "unchanged" if prev in (None, h) else "CHANGED"
            if prev and prev != h:
                old, new = set(re.split(r"(?<=[.!?])\s+", prev_text)), re.split(r"(?<=[.!?])\s+", text)
                added = [s for s in new if s not in old and len(s) > 20][:3]
                detail = ("\nNew on the page:\n" + "\n".join(f"  • {a[:200]}" for a in added)) if added else ""
                return f"🔔 {label} changed.{detail}\n{url}", status
            return None, status
        return None, f"unknown mode '{mode}'"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError, ValueError) as ex:
        reason = getattr(ex, "reason", None) or getattr(ex, "code", None) or type(ex).__name__
        if isinstance(ex, urllib.error.HTTPError):
            reason = f"HTTP {ex.code}"
        st["fails"] = st.get("fails", 0) + 1
        status = f"FAILED ({reason}) x{st['fails']}"
        if mode == "up" and st["fails"] >= DOWN_AFTER and not st.get("down"):
            st["down"] = True
            return f"🔴 {label} is DOWN ({reason}). Checked {st['fails']} times in a row.\n{url}", status
        return None, status


def run(send=True):
    items = load(LIST, {"items": []})["items"]
    state = load(STATE, {})
    alerts, report = [], []
    for n, item in enumerate(items, 1):
        key = f"{item.get('mode', 'up')}|{item['url']}|{item.get('text', '')}"
        st = state.setdefault(key, {})
        alert, status = check_one(item, st)
        st["last"] = NOW.isoformat(timespec="minutes")
        st["status"] = status
        report.append(f"{n}. [{item.get('mode', 'up')}] {item.get('label') or item['url']}: {status}")
        if alert:
            alerts.append(alert)
    save(STATE, state)
    if send:
        if alerts:
            msg = "SENTINEL\n" + "\n\n".join(alerts)
            print(msg)
            try:                                   # also text the owner's iPhone, if switched on in ~/.hermes/textme.json
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from textme import text_robert
                text_robert("sentinel", msg)
            except Exception:
                pass
    else:
        print("\n".join(report) or "Nothing is being watched yet.")


def add(args):
    if not args or not re.match(r"https?://", args[0]):
        sys.exit('Usage: sentinel.py add URL [up|cert|changed|contains] ["label"] [TEXT]')
    url = args[0]
    mode = args[1] if len(args) > 1 else "up"
    if mode not in ("up", "cert", "changed", "contains"):
        sys.exit("MODE must be up, cert, changed or contains.")
    label = args[2] if len(args) > 2 else urlparse(url).hostname
    item = {"url": url, "mode": mode, "label": label, "added": str(NOW.date())}
    if mode == "contains":
        if len(args) < 4:
            sys.exit('contains needs the TEXT to look for, e.g.: sentinel.py add URL contains "Label" "Now Hiring"')
        item["text"] = args[3]
    if mode == "cert" and not url.startswith("https://"):
        sys.exit("cert checks need an https:// address.")
    data = load(LIST, {"items": []})
    if any(i["url"] == url and i.get("mode") == mode and i.get("text") == item.get("text") for i in data["items"]):
        print("Already watching that.")
        return
    data["items"].append(item)
    save(LIST, data)
    print(f"Watching #{len(data['items'])}: [{mode}] {label} - {url}")
    run(send=False)


def listing():
    data, state = load(LIST, {"items": []}), load(STATE, {})
    if not data["items"]:
        print("Nothing is being watched yet.")
        return
    for n, i in enumerate(data["items"], 1):
        st = state.get(f"{i.get('mode', 'up')}|{i['url']}|{i.get('text', '')}", {})
        extra = f" looking for \"{i['text']}\"" if i.get("text") else ""
        print(f"{n}. [{i.get('mode', 'up')}] {i.get('label')}{extra}\n   {i['url']}\n   last: {st.get('last', 'never')} - {st.get('status', '-')}")


def remove(n):
    data = load(LIST, {"items": []})
    if not (1 <= n <= len(data["items"])):
        sys.exit(f"No item {n}. Run sentinel.py list.")
    gone = data["items"].pop(n - 1)
    save(LIST, data)
    print(f"Stopped watching: {gone.get('label')} ({gone['url']})")


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            run()
        elif a[0] == "list":
            listing()
        elif a[0] == "add":
            add(a[1:])
        elif a[0] == "remove" and len(a) > 1 and a[1].isdigit():
            remove(int(a[1]))
        elif a[0] == "check":
            run(send=False)
        else:
            print(__doc__)
    except SystemExit:
        raise
    except Exception as ex:
        print(f"Sentinel error: {type(ex).__name__}: {ex}")
        sys.exit(1)
