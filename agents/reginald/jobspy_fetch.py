#!/usr/bin/env python3
"""JobSpy bridge for Reginald's job_scout.py (runs in ~/.local/jobspy/venv).
Reads a JSON request on stdin and prints a JSON list of jobs in job_scout's format:
  {"queries": [...], "sites": ["indeed","linkedin","zip_recruiter","google"], "location": "Your City, ST",
   "distance": 40, "hours_old": 72, "results": 25}
Gentle by design: a few searches a day, short pauses, no job-description fetching from LinkedIn.
"""
import json, math, sys, time


def money(v):
    try:
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else f"${int(v):,}"
    except (TypeError, ValueError):
        return ""


def main():
    req = json.load(sys.stdin)
    from jobspy import scrape_jobs
    out, errors = [], []
    for i, q in enumerate(req.get("queries", [])[:4]):
        if i:
            time.sleep(4)
        try:
            df = scrape_jobs(site_name=req.get("sites", ["indeed", "linkedin", "zip_recruiter", "google"]),
                             search_term=q, google_search_term=f"{q} jobs near {req.get('location', 'Your City, ST')}",
                             location=req.get("location", "Your City, ST"), distance=req.get("distance", 40),
                             results_wanted=req.get("results", 25), hours_old=req.get("hours_old", 72),
                             country_indeed="usa", description_format="markdown", linkedin_fetch_description=False,
                             verbose=0)
        except Exception as ex:
            errors.append(f"{q}: {type(ex).__name__}: {str(ex)[:80]}")
            continue
        for r in df.fillna("").to_dict("records"):
            lo, hi = money(r.get("min_amount") or None), money(r.get("max_amount") or None)
            sal = f"{lo}-{hi}" if lo and hi else (lo or hi)
            if sal and r.get("interval") and r.get("interval") != "yearly":
                sal += f" ({r['interval']})"
            out.append({"source": f"jobspy:{r.get('site', '')}", "company": str(r.get("company") or ""),
                        "title": str(r.get("title") or ""), "location": str(r.get("location") or ""),
                        "url": str(r.get("job_url_direct") or r.get("job_url") or ""),
                        "desc": str(r.get("description") or "")[:6000], "posted": str(r.get("date_posted") or "")[:10],
                        "salary": sal})
    json.dump({"jobs": out, "errors": errors}, sys.stdout)


if __name__ == "__main__":
    main()
