#!/usr/bin/env python3
"""Email the owner the full trip plan: the master plan as a readable email, plus attachments
(master .md, every city day-plan, the trip calendar .ics, and all ticket/boarding-pass PDFs and images).

  email_plan.py            send it (ONLY ever to the owner's own address)
  email_plan.py preview    build it without sending; prints the size and what's attached

Uses the Mac's email account in ~/.hermes/.env (EMAIL_ADDRESS / EMAIL_PASSWORD / EMAIL_SMTP_HOST).
"""
import datetime as dt, email.utils, html, mimetypes, re, smtplib, sys
from email.message import EmailMessage
from pathlib import Path

H = Path.home() / ".hermes"
TRIP = Path.home() / "Documents/Trip Planning/My Trip"
MASTER = TRIP / "00 TRIP MASTER.md"
ROBERT = "you@example.com"          # HARD LOCK: the only recipient this script will ever use
ATTACH_EXT = {".md", ".ics", ".pdf", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 18 * 1024 * 1024                       # Gmail limit is 25 MB incl. encoding overhead


def load_env():
    env = {}
    for line in (H / ".env").read_text().splitlines():
        m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def md_to_html(md):
    out, in_list, in_table = [], False, False

    def close():
        nonlocal in_list, in_table
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_table:
            out.append("</table>")
            in_table = False

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                close()
                out.append('<table cellpadding="6" style="border-collapse:collapse;font-size:14px;margin:8px 0">')
                in_table = True
                out.append("<tr>" + "".join(f'<th style="border:1px solid #ccc;background:#eef3f2;text-align:left">'
                                            f"{inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f'<td style="border:1px solid #ccc;vertical-align:top">{inline(c)}</td>'
                                            for c in cells) + "</tr>")
            continue
        if in_table:
            close()
        m = re.match(r"(#{1,3}) (.*)", s)
        if m:
            close()
            size = {1: 22, 2: 18, 3: 15}[len(m.group(1))]
            out.append(f'<h{len(m.group(1))} style="font-size:{size}px;margin:18px 0 6px;color:#1f4f4c">'
                       f"{inline(m.group(2))}</h{len(m.group(1))}>")
            continue
        if s.startswith("- "):
            if not in_list:
                out.append('<ul style="margin:4px 0;padding-left:20px">')
                in_list = True
            item = s[2:]
            if item.startswith("[ ] "):
                item = "☐ " + item[4:]
            elif item.startswith("[x] "):
                item = '<span style="color:#888">☑ ' + item[4:] + "</span>"
                out.append(f"<li style='margin:3px 0;list-style:none'>{item}</li>")
                continue
            out.append(f"<li style='margin:3px 0'>{inline(item)}</li>")
            continue
        close()
        if s:
            out.append(f"<p style='margin:6px 0'>{inline(s)}</p>")
    close()
    return ('<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;'
            'color:#1d2b2a;max-width:820px">' + "\n".join(out) + "</div>")


def build():
    md = MASTER.read_text(encoding="utf-8")
    today = dt.date.today()
    days = (dt.date(2026, 10, 21) - today).days
    lead = (f"{days} days to go. " if days > 0 else "") + ("Everything for the trip in one place. "
            "The tickets, boarding passes, day plans and the trip calendar are attached so you have them offline.")
    msg = EmailMessage()
    msg["Subject"] = f"Trip plan - full itinerary ({today:%b %d})"
    msg["To"] = ROBERT
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(lead + "\n\n" + md)
    msg.add_alternative(f"<p style='font-family:Helvetica,Arial;font-size:15px'>{html.escape(lead)}</p>" + md_to_html(md),
                        subtype="html")
    files = [MASTER] + sorted(p for p in TRIP.rglob("*") if p.is_file() and p != MASTER
                              and p.suffix.lower() in ATTACH_EXT and ".old" not in p.name and "backup" not in p.name
                              and not p.name.startswith(".") and "Eiffel_Tower_Calendar_Event" not in p.name)
    total, attached, skipped = 0, [], []
    for p in files:
        size = p.stat().st_size
        if total + size > MAX_BYTES:
            skipped.append(p.name)
            continue
        ctype = mimetypes.guess_type(p.name)[0] or ("text/calendar" if p.suffix == ".ics" else "text/markdown"
                                                     if p.suffix == ".md" else "application/octet-stream")
        maint, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maint, subtype=sub, filename=p.name)
        total += size
        attached.append(p.relative_to(TRIP).as_posix())
    return msg, attached, skipped, total


def main(a):
    msg, attached, skipped, total = build()
    if a and a[0] == "preview":
        print(f"PREVIEW - to {ROBERT}, {len(attached)} attachments, {total / 1e6:.1f} MB:\n  " + "\n  ".join(attached)
              + (f"\nSkipped (too big): {', '.join(skipped)}" if skipped else ""))
        return
    env = load_env()
    if not env.get("EMAIL_ADDRESS") or not env.get("EMAIL_PASSWORD"):
        print("FAILED - no email account set up in ~/.hermes/.env (EMAIL_ADDRESS / EMAIL_PASSWORD).")
        sys.exit(1)
    msg["From"] = f"Donut <{env['EMAIL_ADDRESS']}>"
    try:
        with smtplib.SMTP_SSL(env.get("EMAIL_SMTP_HOST") or "smtp.gmail.com", 465, timeout=90) as s:
            s.login(env["EMAIL_ADDRESS"], env["EMAIL_PASSWORD"])
            s.send_message(msg, to_addrs=[ROBERT])
    except Exception as ex:
        print(f"FAILED - email didn't send: {type(ex).__name__}: {str(ex)[:200]}")
        sys.exit(1)
    print(f"OK - emailed the full trip plan to {ROBERT} with {len(attached)} attachments ({total / 1e6:.1f} MB)."
          + (f" Left out (too big): {', '.join(skipped)}" if skipped else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
