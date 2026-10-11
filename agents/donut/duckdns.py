#!/usr/bin/env python3
"""Keeps myhome.duckdns.org pointed at this network's public IP. Runs hourly; silent unless something changed
or went wrong. It only ever updates the domain(s) listed below - never parentsupport or spoctor.

  duckdns.py           the hourly job: compare and update if needed (prints only when it changed something)
  duckdns.py check     show the network's public IP vs what DuckDNS has, change nothing
  duckdns.py force     push the current public IP to DuckDNS now

Token: ~/.hermes/duckdns.env  (one line: DUCKDNS_TOKEN=xxxxxxxx-xxxx-...), chmod 600. Never printed or logged.
"""
import datetime as dt, ipaddress, json, sys, urllib.request
from pathlib import Path

DOMAINS = ["myhome"]
H = Path.home() / ".hermes"
ENV = H / "duckdns.env"
LOG = H / "logs" / "duckdns.log"
STATE = H / "duckdns_state.json"
UA = {"User-Agent": "donut-duckdns/1.0"}
IP_SOURCES = ["https://api.ipify.org", "https://ipv4.icanhazip.com", "https://checkip.amazonaws.com"]


def get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode().strip()


def public_ip():
    """This network's public IPv4, agreed by at least two independent services (else None)."""
    seen = []
    for u in IP_SOURCES:
        try:
            ip = get(u)
            ipaddress.IPv4Address(ip)
            if ip in seen:
                return ip
            seen.append(ip)
        except Exception:
            continue
    return None


def duckdns_ip(domain):
    """What the internet currently resolves the domain to (asked fresh from Cloudflare DNS, no local cache)."""
    try:
        data = json.loads(get(f"https://cloudflare-dns.com/dns-query?name={domain}.duckdns.org&type=A",
                              {"accept": "application/dns-json"}))
        answers = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
        return answers[0] if answers else None
    except Exception:
        return None


def token():
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.strip().startswith("DUCKDNS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def update(domain, ip, tok):
    r = get(f"https://www.duckdns.org/update?domains={domain}&token={tok}&ip={ip}")
    return r.startswith("OK")


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def run(mode="auto"):
    tok = token()
    ip = public_ip()
    st = state()
    if not ip:
        st["fails"] = st.get("fails", 0) + 1
        STATE.write_text(json.dumps(st))
        log("couldn't determine public IP")
        if mode != "auto" or st["fails"] == 3:      # tell the owner once, after 3 hours of failures
            print("DUCKDNS: couldn't find this network's public IP (internet down?). Will keep trying hourly.")
        return
    st["fails"] = 0
    for d in DOMAINS:
        cur = duckdns_ip(d)
        if mode == "check":
            print(f"{d}.duckdns.org -> {cur or 'unknown'}   |   this network's public IP: {ip}   "
                  f"{'(match)' if cur == ip else '(DIFFERENT - the hourly job would update it)'}")
            if not tok:
                print("No token saved yet: create ~/.hermes/duckdns.env (see the top of this script).")
            continue
        if cur == ip and mode != "force":
            continue
        if not tok:
            print(f"DUCKDNS: {d} needs updating ({cur} -> {ip}) but no token is saved in ~/.hermes/duckdns.env.")
            continue
        try:
            ok = update(d, ip, tok)
        except Exception as ex:
            ok = False
            log(f"{d} update error {type(ex).__name__}")
        log(f"{d} {cur} -> {ip} {'OK' if ok else 'FAILED'}")
        if ok:
            print(f"🦆 myhome's home IP changed: {cur or 'unknown'} -> {ip}. DuckDNS updated, so the VPN address "
                  f"{d}.duckdns.org works again (DNS can take a few minutes to catch up)." if d == "myhome" else
                  f"🦆 {d}.duckdns.org updated: {cur} -> {ip}")
        else:
            print(f"DUCKDNS: tried to update {d} ({cur} -> {ip}) but DuckDNS refused it. Check the token in ~/.hermes/duckdns.env.")
    STATE.write_text(json.dumps(st))


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            run("auto")
        elif a[0] in ("check", "force"):
            run(a[0])
        else:
            print(__doc__)
    except Exception as ex:
        print(f"DUCKDNS error: {type(ex).__name__}: {ex}")
        sys.exit(1)
