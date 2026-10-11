#!/usr/bin/env python3
"""Athena's study coach: oral-exam and quiz practice, one question at a time.

  study.py quiz COURSE [week N]      next practice question (weak ones first, then new ones)
  study.py check ID                  the key points a good answer covers + a likely follow-up question
  study.py mark ID got|partly|missed record how the owner did (drives what comes up next)
  study.py progress [COURSE]         how many questions practiced per week, and the weak list
  study.py add COURSE WEEK "question" "point one; point two; point three"
  study.py reset COURSE              forget progress (keeps the questions)

Question banks: ~/.hermes/profiles/athena/school/study/<COURSE>.json
Progress:       ~/.hermes/profiles/athena/school/study/progress.json
"""
import datetime as dt, json, random, re, sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "school" / "study"
PROG = DIR / "progress.json"
RANK = {"missed": 0, "partly": 1, "got": 2}


def course_name(c):
    c = c.upper().replace(" ", "-")
    if not re.search(r"-\d", c):
        c = re.sub(r"(\D)(\d)", r"\1-\2", c, count=1)
    return c


def bank(c):
    f = DIR / f"{course_name(c)}.json"
    if not f.exists():
        have = ", ".join(p.stem for p in DIR.glob("*.json") if p.stem != "progress") or "none"
        sys.exit(f"No question bank for {course_name(c)}. Banks: {have}")
    return json.load(open(f)), f


def all_questions():
    out = {}
    for f in DIR.glob("*.json"):
        if f.stem == "progress":
            continue
        for q in json.load(open(f))["questions"]:
            out[q["id"]] = (f.stem, q)
    return out


def load_prog():
    try:
        return json.load(open(PROG))
    except (OSError, ValueError):
        return {}


def save_prog(p):
    DIR.mkdir(parents=True, exist_ok=True)
    json.dump(p, open(PROG, "w"), indent=1)


def quiz(c, week=None):
    b, _ = bank(c)
    prog = load_prog()
    qs = [q for q in b["questions"] if week is None or q["week"] == week]
    if not qs:
        sys.exit(f"No questions for week {week}.")
    last = prog.get("_last")

    def key(q):
        h = prog.get(q["id"], {}).get("history", [])
        if not h:
            return (1, random.random())                 # never asked
        r = RANK[h[-1]["result"]]
        return (0 if r < 2 else 2, r, h[-1]["date"])   # weak first, mastered last
    qs = sorted(qs, key=key)
    q = next((x for x in qs if x["id"] != last), qs[0])
    prog["_last"] = q["id"]
    save_prog(prog)
    done = sum(1 for x in b["questions"] if prog.get(x["id"]))
    print(f"QUESTION {q['id']} (week {q['week']} - {q['topic']})\n\n{q['q']}\n")
    print(f"Answer as you would out loud: 60-90 seconds, main point first, one example, why it matters to an IT manager.")
    print(f"(Practiced {done} of {len(b['questions'])} questions in {b['course']}.)")
    print(f"\nAthena: after the owner answers, run  study.py check {q['id']}")


def check(qid):
    allq = all_questions()
    if qid not in allq:
        sys.exit(f"No question with id {qid}.")
    c, q = allq[qid]
    print(f"KEY POINTS for {qid} ({q['topic']}):")
    for i, p in enumerate(q["points"], 1):
        print(f"  {i}. {p}")
    if q.get("followup"):
        print(f"\nLikely follow-up from the professor: {q['followup']}")
    print("\nAthena: tell the owner which points he covered and which he missed (quote his words), give a one-line "
          "stronger version of his answer, then run  study.py mark " + qid + " got|partly|missed")


def mark(qid, result):
    if result not in RANK:
        sys.exit("Use got, partly or missed.")
    allq = all_questions()
    if qid not in allq:
        sys.exit(f"No question with id {qid}.")
    prog = load_prog()
    prog.setdefault(qid, {"history": []})["history"].append({"date": str(dt.date.today()), "result": result})
    save_prog(prog)
    print(f"Recorded {qid}: {result}. Next question: study.py quiz {allq[qid][0]}")


def progress(c=None):
    prog = load_prog()
    courses = [course_name(c)] if c else sorted(p.stem for p in DIR.glob("*.json") if p.stem != "progress")
    for name in courses:
        b, _ = bank(name)
        print(f"{name}:")
        weeks = sorted({q["week"] for q in b["questions"]})
        for w in weeks:
            qs = [q for q in b["questions"] if q["week"] == w]
            res = [prog[q["id"]]["history"][-1]["result"] for q in qs if prog.get(q["id"])]
            print(f"  Week {w}: {len(res)}/{len(qs)} practiced - {res.count('got')} solid, "
                  f"{res.count('partly')} partly, {res.count('missed')} missed")
        weak = [q for q in b["questions"] if prog.get(q["id"]) and prog[q["id"]]["history"][-1]["result"] != "got"]
        if weak:
            print("  Work on: " + "; ".join(f"{q['id']} {q['topic']}" for q in weak))


def add(c, week, question, points):
    b, f = bank(c) if (DIR / f"{course_name(c)}.json").exists() else ({"course": course_name(c), "questions": []}, DIR / f"{course_name(c)}.json")
    pts = [p.strip() for p in re.split(r";|\n", points) if p.strip()]
    if len(pts) < 2:
        sys.exit("Give at least two key points, separated by semicolons.")
    n = sum(1 for q in b["questions"] if q["week"] == int(week)) + 1
    qid = f"w{int(week)}-{n}"
    while any(q["id"] == qid for q in b["questions"]):
        n += 1
        qid = f"w{int(week)}-{n}"
    b["questions"].append({"id": qid, "week": int(week), "topic": question[:40], "q": question, "points": pts,
                           "source": "added by Athena " + str(dt.date.today())})
    DIR.mkdir(parents=True, exist_ok=True)
    json.dump(b, open(f, "w"), indent=1)
    print(f"Added {qid} to {b['course']}.")


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if not a:
            print(__doc__)
        elif a[0] == "quiz" and len(a) > 1:
            wk = int(a[3]) if len(a) > 3 and a[2] == "week" else None
            quiz(a[1], wk)
        elif a[0] == "check" and len(a) > 1:
            check(a[1])
        elif a[0] == "mark" and len(a) > 2:
            mark(a[1], a[2].lower())
        elif a[0] == "progress":
            progress(a[1] if len(a) > 1 else None)
        elif a[0] == "add" and len(a) > 4:
            add(a[1], a[2], a[3], a[4])
        elif a[0] == "reset" and len(a) > 1:
            p = load_prog()
            b, _ = bank(a[1])
            for q in b["questions"]:
                p.pop(q["id"], None)
            save_prog(p)
            print("Progress cleared.")
        else:
            print(__doc__)
    except SystemExit:
        raise
    except Exception as ex:
        print(f"STUDY ERROR: {type(ex).__name__}: {ex}")
        sys.exit(1)
