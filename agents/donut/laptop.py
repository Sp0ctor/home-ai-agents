#!/usr/bin/env python3
"""Donut's controls for the owner's Windows laptop, over SSH: software through winget (Windows' package manager),
plus restart. It can NOT run any other command on the laptop - that's on purpose.

  laptop.py status              is the laptop reachable? winget version
  laptop.py updates             list software with updates available (read-only)
  laptop.py upgrade all         update everything that has an update
  laptop.py upgrade <ID>        update one app (ID from 'updates', e.g. Mozilla.Firefox)
  laptop.py search <words>      find an app's winget ID (read-only)
  laptop.py install <ID>        install one app (machine-wide)   - only when the owner asked for that app
  laptop.py installed           list what's installed (read-only)
  laptop.py restart             restart the laptop in 60 seconds (warns anyone using it)   - only when the owner asked
  laptop.py cancel-restart      cancel a restart that hasn't happened yet
Connection: the 'laptop' entry in ~/.ssh/config (user 'donut', key ~/.ssh/donut_laptop).
"""
import datetime as dt, re, subprocess, sys
from pathlib import Path

HOST = "laptop"
WINGET = r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\donut\winget.ps1"
COMMON = "--accept-source-agreements --disable-interactivity"
PKG = "--accept-package-agreements --silent --scope machine"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,99}$")
LOG = Path.home() / ".hermes/logs/laptop.log"


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def run(args, timeout=900, raw=False):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, args if raw else f"{WINGET} {args}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "Timed out - the laptop may still be working on it."
    out = (r.stdout + r.stderr)
    out = "\n".join(l.rstrip() for l in out.splitlines()
                    if l.strip() and not re.fullmatch(r"[\s\-\\|/█▒]*[\d.]*\s*(%|[KMG]B\s*/\s*[\d.]+\s*[KMG]B)?", l.strip()))
    if r.returncode == 255:
        return 255, ("Can't reach the laptop over SSH - it may be asleep, off, or away from home Wi-Fi. "
                     f"({out.splitlines()[-1] if out else 'no response'})")
    return r.returncode, out


def check_id(pid):
    if not ID_RE.match(pid):
        print(f"FAIL - '{pid}' isn't a valid winget ID (letters, numbers, dots, e.g. Mozilla.Firefox). Use: laptop.py search <name>")
        sys.exit(1)


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "status":
        code, out = run("--version", timeout=60)
        print(f"RESULT: OK - laptop reachable, winget {out.strip()}" if code == 0 else f"RESULT: FAIL - {out}")
    elif cmd == "updates":
        code, out = run(f"upgrade --include-unknown {COMMON}")
        print(out[-6000:] if out else f"FAIL - no answer (code {code})")
    elif cmd == "installed":
        code, out = run(f"list {COMMON}")
        print(out[-8000:])
    elif cmd == "search" and len(a) > 1:
        q = " ".join(a[1:])
        if not re.fullmatch(r"[A-Za-z0-9 .+_-]{2,60}", q):
            print("FAIL - search with plain words only.")
            sys.exit(1)
        code, out = run(f'search "{q}" {COMMON}', timeout=120)
        print(out[-4000:])
    elif cmd == "upgrade" and len(a) > 1:
        if a[1] == "all":
            log("UPGRADE all - start")
            code, out = run(f"upgrade --all --include-unknown {COMMON} {PKG.replace(' --scope machine', '')}", timeout=3600)
        else:
            check_id(a[1])
            log(f"UPGRADE {a[1]} - start")
            code, out = run(f"upgrade --id {a[1]} --exact {COMMON} {PKG.replace(' --scope machine', '')}", timeout=1800)
        log(f"UPGRADE {a[1]} - exit {code}")
        print(("RESULT: OK\n" if code == 0 else f"RESULT: finished with code {code} (some apps may need closing or a reboot)\n")
              + out[-5000:])
    elif cmd == "install" and len(a) > 1:
        check_id(a[1])
        log(f"INSTALL {a[1]} - start")
        code, out = run(f"install --id {a[1]} --exact {COMMON} {PKG}", timeout=1800)
        log(f"INSTALL {a[1]} - exit {code}")
        print(("RESULT: OK - installed\n" if code == 0 else f"RESULT: FAIL (code {code})\n") + out[-4000:])
    elif cmd == "restart":
        log("RESTART requested")
        code, out = run('shutdown /r /t 60 /c "Restarting in 60 seconds - requested by the owner via Donut"', timeout=60, raw=True)
        print("RESULT: OK - the laptop restarts in 60 seconds (it shows a warning first). It's usually back in 2-5 minutes."
              if code == 0 else f"RESULT: FAIL - {out}")
    elif cmd == "cancel-restart":
        code, out = run("shutdown /a", timeout=60, raw=True)
        print("RESULT: OK - restart cancelled." if code == 0 else f"RESULT: nothing to cancel or failed - {out}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
