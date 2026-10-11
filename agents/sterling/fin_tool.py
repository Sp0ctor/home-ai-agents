#!/usr/bin/env python3
"""Sterling's finance tool. Books are plain-text Beancount in ~/Documents/Finances (dashboard: http://127.0.0.1:5055).
READ-AND-RECORD ONLY: nothing here logs into a bank, pays, or moves money.

  fin_tool.py add-account <Account> <inbox-folder> [bank|card|loan|savings] [--balance AMOUNT --as-of YYYY-MM-DD]
        e.g. add-account Assets:Bank:MyBank-Checking MyBank-Checking bank --balance 2450.17 --as-of 2026-09-30
  fin_tool.py import                 import every CSV in Inbox/<folder>/ (dedupes; unknown charges -> Uncategorized, flagged !)
  fin_tool.py undo-import <file>     move one import batch (transactions/<file>) out of the books
  fin_tool.py flip <inbox-folder>    this account's CSV uses + for spending: flip signs (then undo + re-import)
  fin_tool.py review                 list charges still Uncategorized (with their IDs)
  fin_tool.py set <ID> <Account>     categorize one charge (ID from review)
  fin_tool.py rule "PATTERN" <Account>   always categorize matching descriptions; re-applies to Uncategorized now
  fin_tool.py budget <Expenses:...> <monthly amount>
  fin_tool.py bill "<name>" <amount> <due day 1-31> "<paid from>" <autopay yes|no>
  fin_tool.py bills                  bills due in the next 30 days
  fin_tool.py bills-remind           add text reminders (2 days before, 9 AM) for bills due in the next 35 days
  fin_tool.py summary [YYYY-MM]      monthly report (default: last month)
  fin_tool.py check                  validate the books
  fin_tool.py accounts               list our accounts and balances
"""
import csv, datetime as dt, hashlib, io, json, re, subprocess, sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os

VENV_PY = Path.home() / ".local/finance/venv/bin/python"
if Path(sys.prefix).resolve() != VENV_PY.parent.parent.resolve() and VENV_PY.exists() and not os.environ.get("FIN_REEXEC"):
    os.environ["FIN_REEXEC"] = "1"
    os.execv(str(VENV_PY), [str(VENV_PY), __file__] + sys.argv[1:])

FIN = Path(os.environ.get("FIN_DIR", Path.home() / "Documents/Finances"))
MAIN, ACCTS, BUDGETS = FIN / "main.beancount", FIN / "accounts.beancount", FIN / "budgets.beancount"
TX, INBOX, DONE = FIN / "transactions", FIN / "Inbox", FIN / "Imported"
RULES, BILLS, MAP = FIN / "rules.csv", FIN / "bills.csv", FIN / "accounts.csv"
STATE = Path.home() / ".hermes/finance_state.json"
ACCT_RE = re.compile(r"^(Assets|Liabilities|Income|Expenses|Equity)(:[A-Z0-9][A-Za-z0-9-]*)+$")
UNCAT = "Expenses:Uncategorized"
D0 = Decimal("0")


# ------------------------------------------------------------------ helpers
def money(s):
    s = str(s or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("-"):
        neg, s = True, s[:-1]
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def pdate(s):
    s = str(s or "").strip()[:20]
    for f in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d", "%m/%d/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return dt.date.fromisoformat(m.group(1)) if m else None


def q(s):
    return '"' + str(s).replace("\\", "").replace('"', "'").strip()[:120] + '"'


def load_map():
    if not MAP.exists():
        return {}
    return {r["inbox_folder"]: r for r in csv.DictReader(MAP.open())}


def save_map(m):
    with MAP.open("w", newline="") as f:
        w = csv.DictWriter(f, ["inbox_folder", "account", "kind", "flip_sign"])
        w.writeheader()
        for r in m.values():
            w.writerow(r)


def rules():
    out = []
    if RULES.exists():
        for line in RULES.read_text().splitlines()[1:]:
            if "," in line and line.strip():
                pat, acct = line.rsplit(",", 1)
                try:
                    out.append((re.compile(pat, re.I), acct.strip()))
                except re.error:
                    pass
    return out


def categorize(desc, rs):
    for rx, acct in rs:
        if rx.search(desc):
            return acct
    return None


def load():
    from beancount import loader
    return loader.load_file(str(MAIN))


def errors_text(errs):
    return "\n".join(f"  {getattr(e, 'message', e)} ({(getattr(e, 'source', None) or {}).get('filename', '')}:"
                     f"{(getattr(e, 'source', None) or {}).get('lineno', '')})" for e in errs[:10])


def existing_ids():
    ids = set()
    for f in TX.glob("*.beancount"):
        ids.update(re.findall(r'import_id: "([0-9a-f]+)"', f.read_text()))
    return ids


def open_accounts():
    return set(re.findall(r"^\d{4}-\d{2}-\d{2} open (\S+)", ACCTS.read_text(), re.M))


# ------------------------------------------------------------------ CSV reading
DATE_COLS = ("transaction date", "trans date", "date", "posted date", "posting date", "post date")
DESC_COLS = ("description", "payee", "merchant", "name", "details", "memo", "transaction description", "original description")


def read_csv(path, flip=False):
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.splitlines()
    start = 0
    for i, l in enumerate(lines[:30]):                      # skip bank preamble rows above the header
        low = l.lower()
        if "date" in low and ("amount" in low or "debit" in low or "credit" in low):
            start = i
            break
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    if not rows:
        return [], "no rows"
    cols = {c.lower().strip(): c for c in rows[0].keys() if c}
    dc = next((cols[c] for c in DATE_COLS if c in cols), None)
    ec = next((cols[c] for c in DESC_COLS if c in cols), None)
    ac = cols.get("amount") or cols.get("transaction amount") or cols.get("amount (usd)")
    deb = cols.get("debit") or cols.get("withdrawals") or cols.get("withdrawal") or cols.get("debit amount")
    cre = cols.get("credit") or cols.get("deposits") or cols.get("deposit") or cols.get("credit amount")
    if not dc or not ec or not (ac or deb or cre):
        return [], f"couldn't find date/description/amount columns (saw: {', '.join(cols.values())})"
    out = []
    for r in rows:
        d = pdate(r.get(dc))
        if not d:
            continue
        if ac:
            amt = money(r.get(ac))
        else:
            amt = (money(r.get(cre)) or D0) - abs(money(r.get(deb)) or D0)
        if amt is None or amt == 0:
            continue
        if flip:
            amt = -amt
        desc = re.sub(r"\s+", " ", (r.get(ec) or "").strip()) or "(no description)"
        out.append((d, desc, amt.quantize(Decimal("0.01"))))
    return out, ""


# ------------------------------------------------------------------ commands
def cmd_add_account(a):
    if len(a) < 2 or not ACCT_RE.match(a[0]) or not a[0].startswith(("Assets:", "Liabilities:")):
        print("Usage: add-account Assets:Bank:Name-Checking <inbox-folder> [bank|card|loan|savings] [--balance N --as-of YYYY-MM-DD]\n"
              "Account must start Assets: (bank, savings) or Liabilities: (cards, loans); words joined with '-'.")
        sys.exit(1)
    acct, folder = a[0], re.sub(r"[^A-Za-z0-9_-]", "-", a[1])
    kind = a[2] if len(a) > 2 and not a[2].startswith("--") else ("card" if acct.startswith("Liabilities:") else "bank")
    if acct in open_accounts():
        print(f"{acct} is already set up.")
    else:
        with ACCTS.open("a") as f:
            f.write(f"2026-01-01 open {acct} USD\n")
    m = load_map()
    m[folder] = {"inbox_folder": folder, "account": acct, "kind": kind, "flip_sign": m.get(folder, {}).get("flip_sign", "no")}
    save_map(m)
    (INBOX / folder).mkdir(parents=True, exist_ok=True)
    if "--balance" in a and "--as-of" in a:
        bal = money(a[a.index("--balance") + 1])
        asof = dt.date.fromisoformat(a[a.index("--as-of") + 1])
        if acct.startswith("Liabilities:") and bal > 0:
            bal = -bal                                             # money owed is negative in the books
        (TX / f"0000 opening {folder}.beancount").write_text(
            f"{asof - dt.timedelta(days=1)} pad {acct} Equity:Opening-Balances\n{asof} balance {acct} {bal} USD\n")
    errs = load()[1]
    print(f"RESULT: OK - {acct} added. Drop its CSV statements in Finances/Inbox/{folder}/" if not errs
          else "RESULT: FAIL\n" + errors_text(errs))


def cmd_import():
    m, rs, seen = load_map(), rules(), existing_ids()
    if not m:
        print("No accounts set up yet. First: fin_tool.py add-account ...")
        return
    TX.mkdir(exist_ok=True)
    report, any_new = [], False
    for folder, info in m.items():
        files = sorted(p for p in (INBOX / folder).glob("*") if p.suffix.lower() == ".csv")
        for p in files:
            rows, err = read_csv(p, info.get("flip_sign") == "yes")
            if err:
                report.append(f"{folder}/{p.name}: SKIPPED - {err}")
                continue
            acct, out, counts, n_new, n_unc = info["account"], [], defaultdict(int), 0, 0
            for d, desc, amt in sorted(rows):
                base = f"{acct}|{d}|{amt}|{desc}"
                counts[base] += 1
                iid = hashlib.sha1(f"{base}|{counts[base]}".encode()).hexdigest()[:12]
                if iid in seen:
                    continue
                seen.add(iid)
                other = categorize(desc, rs)
                flag = "*" if other else "!"
                other = other or UNCAT
                n_new += 1
                n_unc += other == UNCAT
                out.append(f"{d} {flag} {q(desc)} \"\"\n  import_id: \"{iid}\"\n  {acct}  {amt} USD\n  {other}\n")
            if out:
                batch = TX / f"{dt.date.today()} {folder} {p.stem[:40]}.beancount"
                batch.write_text(f"; imported {dt.datetime.now():%Y-%m-%d %H:%M} from {p.name}\n\n" + "\n".join(out))
                errs = load()[1]
                if errs:
                    batch.rename(batch.with_suffix(".failed"))
                    report.append(f"{folder}/{p.name}: NOT IMPORTED - the books rejected it:\n{errors_text(errs)}")
                    continue
                any_new = True
            dest = DONE / folder
            dest.mkdir(parents=True, exist_ok=True)
            p.rename(dest / f"{dt.date.today()} {p.name}")
            report.append(f"{folder}/{p.name}: {len(rows)} rows, {n_new} new, {len(rows) - n_new} already in the books, "
                          f"{n_unc} need a category")
    print("\n".join(report) or "Inbox is empty - nothing to import.")
    if any_new:
        print("Next: fin_tool.py review  (charges that need a category)")


def cmd_undo(a):
    f = TX / " ".join(a)
    if not f.exists() or f.suffix != ".beancount":
        print("Which batch? Files: " + ", ".join(p.name for p in sorted(TX.glob("*.beancount"))))
        return
    dest = FIN / "Imported" / "_undone"
    dest.mkdir(parents=True, exist_ok=True)
    f.rename(dest / f.name)
    print(f"RESULT: OK - {f.name} taken out of the books (kept in Imported/_undone). Put the CSV back in Inbox to re-import.")


def cmd_flip(a):
    m = load_map()
    if not a or a[0] not in m:
        print("Which inbox folder? " + ", ".join(m))
        return
    m[a[0]]["flip_sign"] = "no" if m[a[0]].get("flip_sign") == "yes" else "yes"
    save_map(m)
    print(f"RESULT: OK - {a[0]} sign flip is now {m[a[0]]['flip_sign']}.")


def txn_blocks():
    for f in sorted(TX.glob("*.beancount")):
        text = f.read_text()
        for mt in re.finditer(r'^(\d{4}-\d{2}-\d{2}) ([*!]) "([^"]*)" ""\n  import_id: "([0-9a-f]+)"\n  (\S+)  (\S+) USD\n  (\S+)\n',
                              text, re.M):
            yield f, mt


def cmd_review():
    items = [(mt.group(1), mt.group(4), mt.group(3), mt.group(6), mt.group(5)) for f, mt in txn_blocks() if mt.group(7) == UNCAT]
    if not items:
        print("Nothing to review - every charge has a category.")
        return
    print(f"{len(items)} charge(s) need a category (amount: - = money out):")
    for d, iid, desc, amt, acct in items[:60]:
        print(f"  {iid}  {d}  {amt:>10}  {desc[:50]}  ({acct.split(':')[-1]})")
    print('Fix one: fin_tool.py set <ID> <Account>   |   always: fin_tool.py rule "WORD" <Account>')


def rewrite(pred_new):
    """pred_new(match) -> new category or None. Rewrites matching transactions in place; returns count."""
    n = 0
    for f in sorted(TX.glob("*.beancount")):
        text = f.read_text()

        def sub(mt):
            nonlocal n
            new = pred_new(mt)
            if not new:
                return mt.group(0)
            n += 1
            return (f'{mt.group(1)} * "{mt.group(3)}" ""\n  import_id: "{mt.group(4)}"\n  {mt.group(5)}  {mt.group(6)} USD\n'
                    f"  {new}\n")
        new_text = re.sub(r'^(\d{4}-\d{2}-\d{2}) ([*!]) "([^"]*)" ""\n  import_id: "([0-9a-f]+)"\n  (\S+)  (\S+) USD\n  (\S+)\n',
                          sub, text, flags=re.M)
        if new_text != text:
            f.write_text(new_text)
    return n


def need_open(acct):
    if not ACCT_RE.match(acct):
        print(f"FAIL - '{acct}' isn't a valid account name (e.g. Expenses:Food:Dining).")
        sys.exit(1)
    if acct not in open_accounts():
        print(f"FAIL - {acct} doesn't exist. Use one from accounts.beancount, or ask the owner before adding a new category.")
        sys.exit(1)


def cmd_set(a):
    if len(a) != 2:
        print("Usage: set <ID> <Account>")
        return
    need_open(a[1])
    n = rewrite(lambda mt: a[1] if mt.group(4) == a[0] else None)
    print(f"RESULT: OK - categorized as {a[1]}." if n else f"No transaction with ID {a[0]} (see: fin_tool.py review).")


def cmd_rule(a):
    if len(a) != 2:
        print('Usage: rule "PATTERN" <Account>')
        return
    need_open(a[1])
    try:
        rx = re.compile(a[0], re.I)
    except re.error:
        print("FAIL - that pattern isn't valid; use plain words.")
        return
    lines = RULES.read_text().splitlines()
    lines.insert(1, f"{a[0]},{a[1]}")                          # new rules win over older ones
    RULES.write_text("\n".join(lines) + "\n")
    n = rewrite(lambda mt: a[1] if mt.group(7) == UNCAT and rx.search(mt.group(3)) else None)
    print(f"RESULT: OK - rule saved; {n} uncategorized charge(s) updated now.")


def cmd_budget(a):
    if len(a) != 2 or not a[0].startswith("Expenses:") or not ACCT_RE.match(a[0]):
        print("Usage: budget Expenses:Food:Groceries 600   (a whole group like Expenses:Food works too)")
        return
    if not any(x == a[0] or x.startswith(a[0] + ":") for x in open_accounts()):
        need_open(a[0])
    amt = money(a[1])
    lines = [l for l in BUDGETS.read_text().splitlines() if f'custom "budget" {a[0]} ' not in l]
    lines.append(f'2026-01-01 custom "budget" {a[0]} "monthly" {amt:.2f} USD')
    BUDGETS.write_text("\n".join(lines) + "\n")
    print(f"RESULT: OK - {a[0]} budget ${amt:,.2f}/month.")


def cmd_bill(a):
    if len(a) != 5:
        print('Usage: bill "Name" <amount> <due day 1-31> "<paid from>" <autopay yes|no>')
        return
    rows = [r for r in csv.DictReader(BILLS.open()) if r["name"].lower() != a[0].lower()] if BILLS.exists() else []
    rows.append({"name": a[0], "amount": f"{money(a[1]):.2f}", "due_day": str(int(a[2])), "paid_from": a[3],
                 "autopay": a[4].lower(), "notes": ""})
    with BILLS.open("w", newline="") as f:
        w = csv.DictWriter(f, ["name", "amount", "due_day", "paid_from", "autopay", "notes"])
        w.writeheader()
        w.writerows(rows)
    print(f"RESULT: OK - {a[0]} saved (${money(a[1]):,.2f}, due on the {int(a[2])}).")


def next_due(day, today):
    import calendar
    for add in range(0, 3):
        y, mth = today.year + (today.month - 1 + add) // 12, (today.month - 1 + add) % 12 + 1
        d = dt.date(y, mth, min(int(day), calendar.monthrange(y, mth)[1]))
        if d >= today:
            return d


def upcoming(days):
    today = dt.date.today()
    out = []
    for r in csv.DictReader(BILLS.open()) if BILLS.exists() else []:
        d = next_due(r["due_day"], today)
        if d and (d - today).days <= days:
            out.append((d, r))
    return sorted(out, key=lambda x: x[0])


def cmd_bills():
    up = upcoming(30)
    if not up:
        print("No bills due in the next 30 days." if BILLS.exists() and BILLS.read_text().count("\n") > 1
              else "No bills saved yet. Add: fin_tool.py bill \"Name\" amount due_day \"paid from\" autopay")
        return
    total = sum(Decimal(r["amount"]) for _, r in up)
    print(f"Bills in the next 30 days (${total:,.2f}):")
    for d, r in up:
        print(f"  {d:%a %b %d}  ${Decimal(r['amount']):>9,.2f}  {r['name']} - from {r['paid_from']}"
              f"{' (autopay)' if r['autopay'] == 'yes' else ' - PAY MANUALLY'}")


def cmd_bills_remind():
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    done = set(st.get("bill_reminders", []))
    added = []
    for d, r in upcoming(35):
        key = f"{r['name']}|{d}"
        if key in done:
            continue
        when = dt.datetime.combine(d - dt.timedelta(days=2), dt.time(9, 0))
        if when < dt.datetime.now():
            continue
        text = (f"Bill due {d:%a %b %d}: {r['name']} ${Decimal(r['amount']):,.2f} from {r['paid_from']}"
                + (" (autopay - just make sure the money's there)" if r["autopay"] == "yes" else " - PAY MANUALLY"))
        res = subprocess.run([sys.executable, str(Path.home() / ".hermes/scripts/reminders.py"), "add",
                              f"{when:%Y-%m-%d %H:%M}", "America/New_York", text], capture_output=True, text=True)
        if "RESULT: OK" in res.stdout:
            done.add(key)
            added.append(f"{when:%b %d} - {r['name']}")
    st["bill_reminders"] = sorted(done)[-200:]
    STATE.write_text(json.dumps(st, indent=1))
    if added:
        print("Bill reminders set:\n  " + "\n  ".join(added))


def balances(entries, until):
    from beancount.core.data import Transaction, Pad, Balance
    bal = defaultdict(lambda: D0)
    for e in entries:
        if isinstance(e, Transaction) and e.date <= until:
            for p in e.postings:
                if p.units and p.units.number is not None:
                    bal[p.account] += p.units.number
    return bal


def cmd_summary(a):
    entries, errs, opts = load()
    from beancount.core.data import Transaction, Custom
    today = dt.date.today()
    if a:
        y, mth = map(int, a[0].split("-"))
    else:
        first = today.replace(day=1) - dt.timedelta(days=1)
        y, mth = first.year, first.month
    start = dt.date(y, mth, 1)
    end = (dt.date(y + mth // 12, mth % 12 + 1, 1) - dt.timedelta(days=1))
    sums, unc = defaultdict(lambda: D0), 0
    for e in entries:
        if isinstance(e, Transaction) and start <= e.date <= end:
            for p in e.postings:
                if p.account.startswith(("Expenses:", "Income:")) and p.units and p.units.number is not None:
                    sums[p.account] += p.units.number
                    unc += p.account == UNCAT
    budgets = {}
    for e in entries:
        if isinstance(e, Custom) and e.type == "budget" and len(e.values) >= 3:
            budgets[str(e.values[0].value)] = Decimal(str(e.values[2].value.number if hasattr(e.values[2].value, "number")
                                                          else e.values[2].value))
    if not any(sums.values()):
        print(f"No transactions recorded for {start:%B %Y} yet.")
        return
    inc = -sum(v for k, v in sums.items() if k.startswith("Income:") and ":Rental-1" not in k and ":Notary" not in k)
    spend = {k: v for k, v in sums.items() if k.startswith("Expenses:") and ":Rental-1" not in k and ":Notary" not in k}
    total_spend = sum(spend.values())
    out = [f"FINANCES - {start:%B %Y}", "",
           f"Household: income ${inc:,.2f} · spending ${total_spend:,.2f} · left over ${inc - total_spend:,.2f}", ""]
    groups = defaultdict(lambda: D0)
    for k, v in spend.items():
        groups[":".join(k.split(":")[:2])] += v
    out.append("Spending by category:")
    for k, v in sorted(groups.items(), key=lambda x: -x[1]):
        out.append(f"  {k.split(':', 1)[1]:<22} ${v:>10,.2f}")
    if budgets:
        out += ["", "Budgets:"]
        for k, b in sorted(budgets.items()):
            used = sum(v for a2, v in spend.items() if a2 == k or a2.startswith(k + ":"))
            flag = "  OVER" if used > b else ("  close" if used > b * Decimal("0.9") else "")
            out.append(f"  {k.split(':', 1)[1]:<22} ${used:>9,.2f} of ${b:,.2f}{flag}")
    for label, key in (("rental rental", ":Rental-1"), ("Notary business", ":Notary")):
        i = -sum(v for k, v in sums.items() if k.startswith("Income:") and key in k)
        x = sum(v for k, v in sums.items() if k.startswith("Expenses:") and key in k)
        if i or x:
            out += ["", f"{label}: income ${i:,.2f} · expenses ${x:,.2f} · profit ${i - x:,.2f}"]
    bal = balances(entries, end)
    assets = sum(v for k, v in bal.items() if k.startswith("Assets:") and k != "Assets:Transfers")
    debts = sum(v for k, v in bal.items() if k.startswith("Liabilities:"))
    out += ["", f"Net worth on {end:%b %d}: ${assets + debts:,.2f} (accounts ${assets:,.2f}, debts ${-debts:,.2f})"]
    tr = bal.get("Assets:Transfers", D0)
    if abs(tr) > 1:
        out.append(f"  Note: ${tr:,.2f} of transfers/card payments don't match up yet (a statement may be missing).")
    if unc:
        out.append(f"\n{unc} charge(s) still need a category - ask Sterling to 'review my charges'.")
    up = upcoming(30)
    if up:
        out += ["", "Bills next 30 days: " + "; ".join(f"{d:%b %d} {r['name']} ${Decimal(r['amount']):,.0f}" for d, r in up)]
    if errs:
        out.append(f"\nWARNING: the books have {len(errs)} problem(s) - run fin_tool.py check.")
    print("\n".join(out))


def cmd_check():
    errs = load()[1]
    print("RESULT: OK - the books are consistent." if not errs else f"RESULT: {len(errs)} problem(s):\n" + errors_text(errs))


def cmd_accounts():
    entries = load()[0]
    bal = balances(entries, dt.date.today())
    m = load_map()
    if not m:
        print("No accounts set up yet.")
        return
    for folder, info in m.items():
        print(f"  {info['account']:<45} ${bal.get(info['account'], D0):>11,.2f}   (Inbox/{folder}, {info['kind']})")


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    c, rest = a[0], a[1:]
    fn = {"add-account": lambda: cmd_add_account(rest), "import": cmd_import, "undo-import": lambda: cmd_undo(rest),
          "flip": lambda: cmd_flip(rest), "review": cmd_review, "set": lambda: cmd_set(rest), "rule": lambda: cmd_rule(rest),
          "budget": lambda: cmd_budget(rest), "bill": lambda: cmd_bill(rest), "bills": cmd_bills,
          "bills-remind": cmd_bills_remind, "summary": lambda: cmd_summary(rest), "check": cmd_check,
          "accounts": cmd_accounts}.get(c)
    if not fn:
        print(__doc__)
        return
    fn()


if __name__ == "__main__":
    main(sys.argv[1:])
