#!/usr/bin/env python3
"""Nightly audit facts for a Hermes agent. Runs as a cron pre-run script.

Collects what actually happened in the last 24 hours from the agent's own records
(conversation database, tool results, error log, scheduled-job ledger, memory files),
checks that files the agent CLAIMED to write really exist, writes the result to
audits/YYYY-MM-DD.md, and prints it so the agent can add a few notes.

The agent's home is the folder above this script's scripts/ folder.
In the main home (~/.hermes, Donut) it also builds the fleet report for the other agents.
No arguments. Standard library only. Read-only except for the audit files it writes.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
MAIN = Path.home() / ".hermes"
FLEET = ["reginald", "sterling", "athena", "vlad"]
NAMES = {"default": "Donut", "reginald": "Reginald", "sterling": "Sterling", "athena": "Athena", "vlad": "Vlad"}
WINDOW_H = 24
NOW = dt.datetime.now().astimezone()
SINCE = NOW - dt.timedelta(hours=WINDOW_H)
WEEK = NOW - dt.timedelta(days=7)
SKIP_DIRS = {"logs", "sessions", "cache", "audio_cache", "image_cache", "cron", "audits", "sandboxes", "runtime",
             "plugins", "node", "platforms", "pairing", "backups", ".curator_backups", "profiles", "hermes-agent",
             "state", "pending", "pending_messages", "persistent", "terminal-sessions", "source-checks", "installs",
             "plugin-update-checks", "images", "pastes", "gateway", "kanban", "shared", "tools", "tests", "bin",
             "home", "skins", "skill_cache", "skin_cache", "__pycache__", ".git", "node_modules", "hooks", "scripts"}
CLAIM_RE = re.compile(r"(?:written|wrote|saved|created|generated|exported|stored|uploaded|delivered|placed)"
                      r"[^\n/]{0,60}?(/Users/[^\s`'\"\)\]\*,]+\.[A-Za-z0-9]{1,5})", re.I)


def profile_of(home):
    return "default" if home.resolve() == MAIN.resolve() else home.name


def q(db, sql, args=()):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def tool_error(content):
    """Return a short error signature if a tool result is a failure, else None."""
    if not content:
        return None
    text = content if isinstance(content, str) else str(content)
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            err = d.get("error")
            if err:
                return str(err)
            if d.get("success") is False:
                return str(d.get("message") or d.get("output") or "success=false")
            code = d.get("exit_code")
            out = str(d.get("output") or "")
            if isinstance(code, int) and code != 0:
                return f"exit {code}: {out[-160:]}"
            for bad in ("command not found", "Traceback (most recent call last)", "No such file or directory",
                        "Permission denied", "ModuleNotFoundError"):
                if bad in out:
                    line = next((l for l in out.splitlines()[::-1] if bad.split()[0] in l or "Error" in l), bad)
                    return line.strip()[:160]
    except (ValueError, TypeError):
        pass
    if text.startswith("Error") or '"error": "' in text[:200]:
        return text[:160]
    return None


def signature(err):
    err = str(err)
    if "Traceback" in err:
        lines = [l.strip() for l in err.splitlines() if l.strip()]
        err = next((l for l in reversed(lines) if re.match(r"[A-Za-z_.]+(Error|Exception)\b", l)), lines[-1])
    s = " ".join(err.split())
    s = re.sub(r"/Users/\S+", "<path>", s)
    s = re.sub(r"\d+", "N", s)
    return s[:110]


def conversations(home):
    db = home / "state.db"
    out = {"sessions": [], "user_msgs": 0, "tool_calls": 0, "tool_errors": Counter(), "tool_errors_week": Counter(),
           "claims_missing": [], "claims_ok": 0, "verifier_failures": 0, "unanswered": 0}
    since, week = SINCE.timestamp(), WEEK.timestamp()
    for sid, source, title, started in q(db, "select id, source, title, started_at from sessions "
                                         "where coalesce(last_activity_at, ended_at, started_at) >= ?", (since,)):
        out["sessions"].append((source, title or "(untitled)"))
    rows = q(db, "select m.session_id, m.role, m.content, m.tool_name, m.timestamp, s.source from messages m "
                 "join sessions s on s.id = m.session_id where m.timestamp >= ? order by m.timestamp", (week,))
    last_role = {}
    for sid, role, content, tool, ts, source in rows:
        recent = ts >= since
        if role == "tool":
            err = tool_error(content)
            if err:
                key = f"{tool}: {signature(err)}"
                out["tool_errors_week"][key] += 1
                if recent:
                    out["tool_errors"][key] += 1
            if recent:
                out["tool_calls"] += 1
        if not recent:
            continue
        if role == "user" and source != "cron":
            out["user_msgs"] += 1
        if role == "assistant" and content:
            if "File-mutation verifier" in content and "FAILED" in content:
                out["verifier_failures"] += 1
            for path in set(CLAIM_RE.findall(content)):
                p = path.rstrip(".:;")
                if os.path.exists(p):
                    out["claims_ok"] += 1
                elif p not in out["claims_missing"]:
                    out["claims_missing"].append(p)
        if role in ("user", "assistant") and source != "cron":
            last_role[sid] = role
    out["unanswered"] = sum(1 for r in last_role.values() if r == "user")
    return out


def cron_runs(home):
    rows = q(home / "cron" / "executions.db",
             "select job_id, status, started_at, finished_at, error, delivery_outcome from executions "
             "where started_at >= ? order by started_at", (SINCE.isoformat(),))
    names = {}
    try:
        data = json.load(open(home / "cron" / "jobs.json"))
        for j in (data.get("jobs", []) if isinstance(data, dict) else data):
            names[j.get("id")] = j.get("name")
    except (OSError, ValueError):
        pass
    return [(names.get(j, j), s, (e or "")[:120], d) for j, s, st, fi, e, d in rows]


def error_log(home):
    f = home / "logs" / "errors.log"
    c = Counter()
    if not f.exists():
        return c
    cutoff = SINCE.strftime("%Y-%m-%d %H:%M:%S")
    with open(f, errors="replace") as fh:
        size = f.stat().st_size
        if size > 3_000_000:
            fh.seek(size - 3_000_000)
            fh.readline()
        for line in fh:
            if line[:19] < cutoff or " ERROR " not in line:
                continue
            msg = line.split(" ERROR ", 1)[1]
            c[signature(msg.strip())[:120]] += 1
    return c


def limits(home):
    limit = {"memory_char_limit": 3200, "user_char_limit": 1375}
    for cfg in (MAIN / "config.yaml", home / "config.yaml"):
        try:
            for k in limit:
                m = re.search(rf"^\s*{k}:\s*(\d+)", cfg.read_text(), re.M)
                if m:
                    limit[k] = int(m.group(1))
        except OSError:
            pass
    return limit


def memory(home):
    lim = limits(home)
    res = {}
    for fname, key in (("MEMORY.md", "memory_char_limit"), ("USER.md", "user_char_limit")):
        f = home / "memories" / fname
        n = len(f.read_text(errors="replace")) if f.exists() else 0
        res[fname] = (n, lim[key])
    return res


def new_files(home, profile):
    roots = [home]
    docs = Path.home() / "Documents" / NAMES.get(profile, profile.title())
    if docs.is_dir():
        roots.append(docs)
    found = []
    since = SINCE.timestamp()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith(".") or fn.endswith((".lock", ".db", ".db-wal", ".db-shm", ".json", ".pid", ".sock",
                                                      ".etag", ".log", ".jsonl")) or "backup" in fn:
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime >= since:
                    found.append((p, st.st_size))
    return sorted(found)


def facts(home):
    profile = profile_of(home)
    name = NAMES.get(profile, profile.title())
    c = conversations(home)
    runs = cron_runs(home)
    errs = error_log(home)
    mem = memory(home)
    files = new_files(home, profile)
    problems = []
    if c["claims_missing"]:
        problems.append(f"{len(c['claims_missing'])} file(s) it said it wrote do NOT exist")
    if c["verifier_failures"]:
        problems.append(f"{c['verifier_failures']} reply(ies) flagged by the file-write verifier as FAILED")
    if c["unanswered"]:
        problems.append(f"{c['unanswered']} conversation(s) ended with the owner's message unanswered")
    repeated = [k for k, n in c["tool_errors"].items() if c["tool_errors_week"][k] > n]
    if repeated:
        problems.append(f"{len(repeated)} tool error(s) that also happened earlier this week")
    for f, (n, lim) in mem.items():
        if lim and n / lim >= 0.9:
            problems.append(f"{f} is {n}/{lim} chars ({100*n//lim}%) — nearly full, new memories may be rejected")
    failed_runs = [r for r in runs if r[1] not in ("completed",)]
    if failed_runs:
        problems.append(f"{len(failed_runs)} scheduled run(s) did not complete")
    human = [s for s in c["sessions"] if s[0] != "cron"]
    status = "PROBLEMS" if problems else ("IDLE" if not human else "OK")

    L = [f"# {name} — nightly audit facts {NOW:%Y-%m-%d}",
         f"_Collected by audit_facts.py at {NOW:%Y-%m-%d %H:%M %Z} from the agent's own records "
         f"(last {WINDOW_H}h). These facts are authoritative; do not restate them as your own checks._", "",
         f"**Status: {status}**", ""]
    if problems:
        L += ["## Problems"] + [f"- {p}" for p in problems] + [""]
    L += ["## Work done",
          f"- Conversations with the owner: {len(human)} ({c['user_msgs']} messages from him)"]
    L += [f"  - {src}: {t[:90]}" for src, t in human[:8]]
    L += [f"- Tool calls: {c['tool_calls']}, failed: {sum(c['tool_errors'].values())}",
          f"- Files it claimed to write: {c['claims_ok']} verified present, {len(c['claims_missing'])} missing"]
    L += [f"  - MISSING: {p}" for p in c["claims_missing"][:10]]
    L += [f"- Files created/changed: {len(files)}"]
    L += [f"  - {p.replace(str(Path.home()), '~')} ({n:,} bytes)" for p, n in files[:12]]
    if c["tool_errors"]:
        L += ["", "## Tool errors (count; * = also happened earlier this week)"]
        for k, n in c["tool_errors"].most_common(8):
            star = " *" if c["tool_errors_week"][k] > n else ""
            L.append(f"- {n}x {k}{star}")
    if errs:
        L += ["", "## Error log (top)"] + [f"- {n}x {k}" for k, n in errs.most_common(5)]
    L += ["", "## Scheduled runs"]
    L += [f"- {n}: {s}{' — ' + e if e else ''} (delivery: {d})" for n, s, e, d in runs] or ["- none in window"]
    L += ["", "## Memory"] + [f"- {f}: {n}/{lim} chars ({100*n//max(lim,1)}%)" for f, (n, lim) in mem.items()]
    return {"name": name, "status": status, "problems": problems, "text": "\n".join(L) + "\n",
            "human": len(human), "files": len(files), "tool_err": sum(c["tool_errors"].values())}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main():
    me = facts(HOME)
    write(HOME / "audits" / f"{NOW:%Y-%m-%d}.md", me["text"])
    print(me["text"])
    if profile_of(HOME) != "default":
        return
    rows, sections = [], []
    for p in FLEET:
        h = MAIN / "profiles" / p
        if not h.is_dir():
            continue
        f = facts(h)
        rows.append(f"| {f['name']} | {f['status']} | {f['human']} | {f['files']} | {f['tool_err']} | "
                    f"{'; '.join(f['problems']) or '—'} |")
        sections.append(f["text"])
    report = [f"# Fleet report {NOW:%Y-%m-%d}", "",
              "| Agent | Status | Conversations | Files | Tool errors | Problems |",
              "|---|---|---|---|---|---|", *rows, "", *sections]
    text = "\n".join(report) + "\n"
    write(HOME / "audits" / "supervisor" / f"{NOW:%Y-%m-%d}.md", text)
    print("\n" + text)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:  # never leave the agent without facts
        print(f"AUDIT SCRIPT ERROR: {type(ex).__name__}: {ex}")
        sys.exit(1)
