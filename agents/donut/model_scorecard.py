#!/usr/bin/env python3
"""Compare how well an agent's model does its job, per model, from its own conversation history.

  model_scorecard.py [PROFILE] [DAYS]     default: sterling, last 14 days

For each model it counts, over Telegram conversations only:
  replies     - turns where the agent answered the owner
  stalls      - replies that announce work ("let me", "I'll", "now I'm going to") but made no tool call
                in that turn - the "said it, didn't do it" failure
  tool calls / failed tool calls
  bad calls   - tool calls rejected for malformed arguments
Read-only: it opens state.db read-only and changes nothing.
"""
import datetime as dt, json, re, sqlite3, sys
from collections import defaultdict
from pathlib import Path

prof = sys.argv[1] if len(sys.argv) > 1 else "sterling"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 14
home = Path.home() / ".hermes" / ("" if prof == "donut" else f"profiles/{prof}")
db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True)
since = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
ANNOUNCE = re.compile(r"\b(let me|i'll|i will|i'm going to|now i'll|next,? i|i am going to|let's)\b", re.I)
FAIL = re.compile(r'"success": false|"error": "|"status": "error"|BLOCKED|Traceback')
BADARG = re.compile(r"must be|invalid|missing required|Unknown ref|not valid JSON|unexpected keyword", re.I)

stats = defaultdict(lambda: defaultdict(int))
days_seen = defaultdict(set)
sessions = db.execute("select id, coalesce(model,'?'), started_at from sessions where source='telegram' and started_at>=?",
                      (since,)).fetchall()
for sid, model, started in sessions:
    s = stats[model]
    days_seen[model].add(dt.date.fromtimestamp(started))
    rows = db.execute("select role, content, tool_calls from messages where session_id=? order by id", (sid,)).fetchall()
    turn_tools = 0
    for role, content, tc in rows:
        if role == "user":
            turn_tools = 0
        elif role == "assistant":
            if tc:
                n = len(json.loads(tc))
                s["tool calls"] += n
                turn_tools += n
            elif content and content.strip():
                s["replies"] += 1
                tail = content.strip()[-300:]
                if ANNOUNCE.search(tail) and turn_tools == 0:
                    s["stalls"] += 1
                elif ANNOUNCE.search(tail) and tail.rstrip().endswith(":"):
                    s["stalls"] += 1          # ended the turn on "Let me check...:" after some tools
        elif role == "tool":
            txt = (content or "")[:600]
            if FAIL.search(txt):
                s["failed tool calls"] += 1
                if BADARG.search(txt):
                    s["bad calls"] += 1

print(f"Model scorecard - {prof}, last {days} days (Telegram conversations)\n")
for model, s in stats.items():
    if not s["replies"] and not s["tool calls"]:
        continue
    r = max(s["replies"], 1)
    t = max(s["tool calls"], 1)
    print(f"{model}  ({len(days_seen[model])} day(s) used)")
    print(f"  replies {s['replies']}, stalls {s['stalls']} ({100 * s['stalls'] / r:.0f}% of replies)")
    print(f"  tool calls {s['tool calls']}, failed {s['failed tool calls']} ({100 * s['failed tool calls'] / t:.0f}%), "
          f"malformed {s['bad calls']}")
    print()
