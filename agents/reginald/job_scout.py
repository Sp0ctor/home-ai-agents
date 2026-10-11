#!/usr/bin/env python3
"""Reginald's daily job scout.

Pulls openings straight from employers' own job systems (Workday, Greenhouse, Lever,
Ashby) and public remote boards, so every job is real and currently posted. Filters them
for the owner, ranks them against his master resume, and saves a numbered shortlist.

  job_scout.py                 run today's search (used by the scheduled job), print the list
  job_scout.py show N          full details for job N from the latest shortlist
  job_scout.py approve N [N..] mark jobs approved, add them to the application ledger, and
                               start building + emailing their resumes in the background
  job_scout.py sources         test every source and report how many jobs each returned

Settings (sources, titles, places, pay) live in job_scout_config.json next to this file.
Standard library only.
"""
import csv, datetime as dt, html, json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "job_scout_config.json"
JOBS_DIR = Path.home() / "Documents/Reginald/jobs"

# Pre-write guard for file operations
def can_write(path: str | Path) -> bool:
    """Check if we can write to a file (avoiding race-conditions)."""
    from pathlib import Path
    p = Path(path)
    # Check parent directory is writable
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return True
    except PermissionError:
        return False

# Pre-read guard for file operations
def read_first(path: str | Path) -> str | None:
    """Read the first 1KB of a file to avoid write conflicts."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        content = p.read_text(errors="replace")[:1024]
        return content
    except Exception:
        return None
DAILY = JOBS_DIR / "daily"
SEEN = DAILY / "seen.json"
LEDGER = JOBS_DIR / "applications/application_ledger.csv"
MASTER = JOBS_DIR / "resumes/Master_Resume.docx"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TODAY = dt.date.today().isoformat()
PY = Path.home() / ".hermes/hermes-agent/venv/bin/python"
RESUME_TOOL = Path.home() / ".hermes/profiles/reginald/skills/productivity/robert-job-workflow/scripts/resume_tool.py"


# ---------------------------------------------------------------- helpers
def http(url, data=None, timeout=25):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def text(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def cfg():
    return json.load(open(CONFIG))


def job(source, company, title, location, url, desc="", posted="", salary=""):
    return {"source": source, "company": company, "title": text(title), "location": text(location),
            "url": url, "desc": text(desc), "posted": posted, "salary": salary}


# ---------------------------------------------------------------- sources
def workday(src, queries, detail_filter):
    host, tenant, site = src["host"], src["tenant"], src["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    found = {}
    for q in queries:
        for offset in (0, 20):
            try:
                res = http(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
            except Exception:
                break
            posts = res.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if path and path not in found:
                    found[path] = job("workday", src["name"], p.get("title", ""), p.get("locationsText", ""),
                                      f"https://{host}/{site}{path}", posted=p.get("postedOn", ""))
            if len(posts) < 20:
                break
    out = []
    for path, j in found.items():
        if not detail_filter(j):
            continue
        try:
            info = http(f"{base}{path}").get("jobPostingInfo", {})
            j["desc"] = text(info.get("jobDescription", ""))
            j["location"] = text(info.get("location", j["location"])) or j["location"]
            j["posted"] = info.get("startDate", j["posted"])
        except Exception:
            pass
        out.append(j)
    return out


def greenhouse(src, *_):
    res = http(f"https://boards-api.greenhouse.io/v1/boards/{src['token']}/jobs?content=true")
    return [job("greenhouse", src["name"], j.get("title"), (j.get("location") or {}).get("name", ""),
                j.get("absolute_url"), j.get("content", ""), j.get("updated_at", "")[:10]) for j in res.get("jobs", [])]


def lever(src, *_):
    res = http(f"https://api.lever.co/v0/postings/{src['token']}?mode=json")
    out = []
    for j in res:
        sal = j.get("salaryRange") or {}
        salary = f"${sal.get('min', 0):,}-${sal.get('max', 0):,}" if sal.get("max") else ""
        out.append(job("lever", src["name"], j.get("text"), (j.get("categories") or {}).get("location", ""),
                       j.get("hostedUrl"), (j.get("descriptionPlain") or "") + " " + (j.get("additionalPlain") or ""),
                       dt.datetime.fromtimestamp(j.get("createdAt", 0) / 1000).date().isoformat(), salary))
    return out


def ashby(src, *_):
    res = http(f"https://api.ashbyhq.com/posting-api/job-board/{src['token']}?includeCompensation=true")
    out = []
    for j in res.get("jobs", []):
        loc = j.get("location", "") + (" (Remote)" if j.get("isRemote") else "")
        comp = (j.get("compensation") or {}).get("compensationTierSummary", "") or ""
        out.append(job("ashby", src["name"], j.get("title"), loc, j.get("jobUrl"), j.get("descriptionPlain", ""),
                       (j.get("publishedAt") or "")[:10], comp))
    return out


def remotive(src, queries, *_):
    out = []
    for q in queries[:4]:
        try:
            res = http(f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote(q)}&limit=50")
        except Exception:
            continue
        for j in res.get("jobs", []):
            out.append(job("remotive", j.get("company_name"), j.get("title"),
                           "Remote (" + (j.get("candidate_required_location") or "") + ")", j.get("url"),
                           j.get("description", ""), (j.get("publication_date") or "")[:10], j.get("salary", "")))
    return out


def remoteok(src, *_):
    res = http("https://remoteok.com/api")
    return [job("remoteok", j.get("company"), j.get("position"), "Remote (" + (j.get("location") or "") + ")",
                j.get("url"), j.get("description", ""), (j.get("date") or "")[:10],
                f"${j['salary_min']:,}-${j['salary_max']:,}" if j.get("salary_max") else "")
            for j in res if isinstance(j, dict) and j.get("position")]


import urllib.parse  # noqa: E402  (used by remotive)

JOBSPY_PY = Path.home() / ".local/jobspy/venv/bin/python"
JOBSPY_FETCH = Path(__file__).resolve().parent / "jobspy_fetch.py"


def jobspy(src, queries, *_):
    """Indeed / LinkedIn / ZipRecruiter / Google Jobs via JobSpy (its own venv; see install_ai_tools.sh)."""
    import subprocess
    if not JOBSPY_PY.exists():
        raise RuntimeError("JobSpy not installed (run ~/.hermes/scripts/install_ai_tools.sh)")
    req = {"queries": src.get("queries") or queries, "sites": src.get("sites", ["indeed", "linkedin", "zip_recruiter", "google"]),
           "location": src.get("location", "Your City, ST"), "distance": src.get("distance", 40),
           "hours_old": src.get("hours_old", 72), "results": src.get("results", 25)}
    r = subprocess.run([str(JOBSPY_PY), str(JOBSPY_FETCH)], input=json.dumps(req), capture_output=True,
                       text=True, timeout=420)
    data = json.loads(r.stdout or "{}")
    jobs = [job(j["source"], j["company"], j["title"], j["location"], j["url"], j["desc"], j["posted"], j["salary"])
            for j in data.get("jobs", []) if j.get("url")]
    if not jobs and data.get("errors"):
        raise RuntimeError("; ".join(data["errors"])[:150])
    return jobs


KINDS = {"workday": workday, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
         "remotive": remotive, "remoteok": remoteok, "jobspy": jobspy}


# ---------------------------------------------------------------- filtering & ranking
def compile_rules(c):
    word = lambda xs: re.compile(r"\b(" + "|".join(re.escape(x) for x in xs) + r")\b", re.I)
    return {"title": word(c["title_keywords"]), "bad_title": word(c["title_exclude"]),
            "place": word(c["locations"]), "remote": re.compile(r"\bremote\b|\btelework|\bnationwide\b|\banywhere in the (us|u\.s\.|united states)", re.I),
            "foreign": re.compile(r"\b(" + "|".join(c["foreign_exclude"]) + r")\b", re.I)}


def title_ok(j, R):
    return bool(R["title"].search(j["title"])) and not R["bad_title"].search(j["title"])


def place_ok(j, R):
    loc = j["location"]
    if R["place"].search(loc):
        return True
    return bool(R["remote"].search(loc)) and not R["foreign"].search(loc)


def salary_range(j):
    s = j["salary"] + " " + j["desc"][:6000]
    nums = []
    for a, b in re.findall(r"\$\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d)?\s?[kK])(?:[^$\d]{1,12}\$\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d)?\s?[kK]))?", s):
        for v in (a, b):
            if not v:
                continue
            v = v.replace(",", "").strip()
            n = float(v[:-1]) * 1000 if v.lower().endswith("k") else float(v)
            if 30000 <= n <= 400000:
                nums.append(int(n))
    return (min(nums), max(nums)) if nums else None


def clearance(desc):
    d = desc.lower()
    if re.search(r"polygraph|\bci[ /-]?poly|full[ -]scope|\bfsp\b|\bts/sci\s*(w/|with)\s*poly", d):
        return "POLY"
    if re.search(r"ts/sci|top secret/sci|ts / sci|tssci", d):
        return "TS/SCI"
    if re.search(r"top secret|\bts clearance", d):
        return "TS"
    if re.search(r"\bsecret clearance|active secret|\bsecret\b", d):
        return "Secret"
    if re.search(r"public trust|\bmbi\b|suitability", d):
        return "Public Trust"
    return "None"


def master_terms():
    try:
        from docx import Document
        t = " ".join(p.text for p in Document(str(MASTER)).paragraphs)
    except Exception:
        t = ""
    return t


def score(j, c, master):
    d = (j["title"] + " " + j["desc"]).lower()
    s = 0
    for kw, w in c["skill_weights"].items():
        if kw.lower() in d:
            s += w if kw.lower() in master.lower() or not master else w // 2
    if re.search(r"\b(1[0-9]|[89])\+?\s*years", d):
        s -= 4
    if j["clr"] in ("TS/SCI", "TS", "Secret"):
        s += 3
    rng = j.get("pay")
    if rng and rng[1] >= c["pay_target"]:
        s += 3
    return s


def fit(j, c):
    rng = j.get("pay")
    if not rng:
        return "pay not listed"
    lo, hi = rng
    if hi < c["pay_floor"]:
        return "too low"
    return f"${lo//1000}K-${hi//1000}K" + ("" if hi >= c["pay_target"] else " (below target)")


# ---------------------------------------------------------------- memory of what was sent/applied
def ledger_keys():
    keys = set()
    if not LEDGER.exists():
        return keys
    with open(LEDGER, newline="", errors="replace") as f:
        for r in csv.DictReader(f):
            keys.add((r.get("official_url") or "").split("?")[0].rstrip("/"))
            keys.add(((r.get("employer") or "").lower().strip(), (r.get("job_title") or "").lower().strip()))
    return keys


def load_seen():
    try:
        return json.load(open(SEEN))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- commands
def fetch_all(c, R, report=None):
    def one(src):
        t0 = time.time()
        try:
            fn = KINDS[src["kind"]]
            jobs = fn(src, c["search_queries"], lambda j: title_ok(j, R) and place_ok(j, R))
            if report is not None:
                report.append((src["name"], src["kind"], len(jobs), f"{time.time()-t0:.0f}s", ""))
            return jobs
        except Exception as ex:
            if report is not None:
                report.append((src["name"], src["kind"], 0, f"{time.time()-t0:.0f}s", f"{type(ex).__name__}: {str(ex)[:70]}"))
            return []
    with ThreadPoolExecutor(max_workers=8) as ex:
        return [j for batch in ex.map(one, c["sources"]) for j in batch]


def run():
    c = cfg()
    R = compile_rules(c)
    report = []
    jobs = fetch_all(c, R, report)
    master = master_terms()
    applied, seen = ledger_keys(), load_seen()
    cutoff = (dt.date.today() - dt.timedelta(days=c["resend_after_days"])).isoformat()
    stats = {"found": len(jobs), "poly": 0, "low_pay": 0, "dup": 0}
    keep, urls = [], set()
    for j in jobs:
        if not (title_ok(j, R) and place_ok(j, R)):
            continue
        url = (j["url"] or "").split("?")[0].rstrip("/")
        key = (j["company"].lower().strip(), j["title"].lower().strip())
        if not url or url in urls or url in applied or key in applied or seen.get(url, "") > cutoff:
            stats["dup"] += 1
            continue
        urls.add(url)
        j["clr"] = clearance(j["desc"])
        if j["clr"] == "POLY":
            stats["poly"] += 1
            continue
        j["pay"] = salary_range(j)
        if j["pay"] and j["pay"][1] < c["pay_floor"]:
            stats["low_pay"] += 1
            continue
        j["score"] = score(j, c, master)
        keep.append(j)
    keep.sort(key=lambda j: j["score"], reverse=True)
    top = keep[: c["per_day"]]
    for i, j in enumerate(top, 1):
        j["n"] = i
        j["fit"] = fit(j, c)
        seen[(j["url"] or "").split("?")[0].rstrip("/")] = TODAY
    DAILY.mkdir(parents=True, exist_ok=True)
    json.dump({"date": TODAY, "jobs": top, "stats": stats, "sources": report},
              open(DAILY / f"{TODAY}.json", "w"), indent=1)
    json.dump(seen, open(SEEN, "w"))
    lines = [f"Reginald's job list - {dt.date.today():%a %b %d} ({len(top)} new)", ""]
    for j in top:
        clr = "" if j["clr"] == "None" else f" | {j['clr']}"
        lines.append(f"{j['n']}. {j['title'][:60]} - {j['company'][:30]}")
        lines.append(f"   {j['location'][:45]} | {j['fit']}{clr}")
        lines.append(f"   {j['url']}")
    if not top:
        lines.append("No new matching jobs today.")
    lines += ["", f"Checked {stats['found']} openings; skipped {stats['poly']} needing a polygraph, "
              f"{stats['low_pay']} under ${c['pay_floor']//1000}K, {stats['dup']} already seen or applied.",
              "To choose: send Reginald the word approve followed by the numbers of the jobs you want."]
    failed = [r for r in report if r[4]]
    if failed:
        lines.append(f"({len(failed)} of {len(report)} job sources didn't respond today.)")
    print("\n".join(lines))


def latest():
    files = sorted(p for p in DAILY.glob("20*.json"))
    if not files:
        sys.exit("No shortlist yet. Run job_scout.py first.")
    return files[-1], json.load(open(files[-1]))


def show(nums):
    path, data = latest()
    for n in nums:
        j = next((x for x in data["jobs"] if x["n"] == n), None)
        if not j:
            print(f"Job {n} is not on the {data['date']} list.")
            continue
        print(f"#{n} {j['title']} - {j['company']}\nLocation: {j['location']}\nPay: {j['fit']}\n"
              f"Clearance: {j['clr']}\nURL: {j['url']}\nPosted: {j['posted']}\n\n{j['desc'][:4000]}\n")


def approve(nums):
    path, data = latest()
    rows = []
    for n in nums:
        j = next((x for x in data["jobs"] if x["n"] == n), None)
        if not j:
            print(f"Job {n} is not on the {data['date']} list - skipped.")
            continue
        j["already"] = bool(j.get("approved"))
        j["approved"] = j.get("approved") or TODAY
        rows.append(j)
    json.dump(data, open(path, "w"), indent=1)
    if rows and LEDGER.exists():
        with open(LEDGER, newline="", errors="replace") as f:
            header = next(csv.reader(f))
        with open(LEDGER, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            for j in [r for r in rows if not r.get("already")]:
                w.writerow({"timestamp_emitted": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                            "employer": j["company"], "job_title": j["title"], "official_url": j["url"],
                            "board_or_ats": j["source"], "location": j["location"], "published_salary": j["fit"],
                            "clearance_or_access_screen": j["clr"], "fit_status": "approved by the owner",
                            "application_status": "resume requested", "notes": f"daily list {data['date']} #{j['n']}"})
    if not rows:
        print("No jobs approved - check the numbers against the list.")
        return
    import subprocess
    log = DAILY / f"{data['date']}-build.log"
    with open(log, "a") as lf:
        subprocess.Popen([str(PY if PY.exists() else sys.executable), str(RESUME_TOOL), "batch", data["date"]]
                         + [str(j["n"]) for j in rows], stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"Approved {len(rows)} job(s) from the {data['date']} list: " + ", ".join(f"#{j['n']} {j['company']}" for j in rows))
    print(f"Building and emailing the resumes now, in the background (about 1 minute per job). "
          f"the owner will get one email per resume, then a summary email. Log: {log}")


def sources():
    c = cfg()
    R = compile_rules(c)
    report = []
    jobs = fetch_all(c, R, report)
    for name, kind, n, t, err in sorted(report, key=lambda r: -r[2]):
        print(f"{'OK  ' if not err else 'FAIL'} {name:<28} {kind:<10} {n:>4} jobs {t:>4}  {err}")
    ok = [r for r in report if not r[4]]
    print(f"\n{len(ok)}/{len(report)} sources working, {len(jobs)} raw jobs.")


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        if not args:
            run()
        elif args[0] == "show":
            show([int(x) for x in re.findall(r"\d+", " ".join(args[1:]))])
        elif args[0] == "approve":
            approve([int(x) for x in re.findall(r"\d+", " ".join(args[1:]))])
        elif args[0] == "sources":
            sources()
        else:
            print(__doc__)
    except Exception as ex:
        print(f"JOB SCOUT ERROR: {type(ex).__name__}: {ex}")
        sys.exit(1)
