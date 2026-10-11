# Finances (kept by Sterling)

- **Dashboard:** http://127.0.0.1:5055 on the Mac (from the laptop: `ssh -L 5055:127.0.0.1:5055 you@192.168.1.10`, then open that address).
- **Add statements:** export CSV from each bank/card site and drop it in `Inbox/<account folder>/`, then tell Sterling "import my statements".
  PDF-only statements go in `Statements/` - Sterling can read them.
- **Monthly report:** Sterling sends it on the 1st (spending vs budget, bills, rental + notary profit, net worth).
- Sterling only reads and records. He never logs into banks, pays, moves money or changes accounts.

Files: `main.beancount` (the books), `accounts.beancount` (categories), `budgets.beancount`, `rules.csv` (auto-categorizing),
`bills.csv` (bills + due days), `accounts.csv` (which Inbox folder is which account), `transactions/` (one file per import).
