"""
fetch_and_mail.py -- "program 3": runs on ANY PC that can open the viewer's
web address AND can send mail. It logs in to the viewer, gets one day's
numbers + the NOT OK Excel, and mails them (uses send_report_mail.py's
settings and sender, keep both files together).

The mail has, for every app: All Results (OK / NOT OK), the same by Shift A/B/C,
All VINs (PASS / FAIL), the same by Shift A/B/C -- and the attached Excel
holds only the NOT OK results of that day (same columns as the viewer table).

Usage:
    python fetch_and_mail.py --setup     (first time: viewer address + password, then mail settings)
    python fetch_and_mail.py             (YESTERDAY -> mail; best for the 8 AM run)
    python fetch_and_mail.py --today     (today so far -> mail)
    python fetch_and_mail.py 2026-11-24  (any date, e.g. for testing)
    add --dry-run to any of them: shows the mail, sends nothing

Run it every morning with Windows Task Scheduler (see README).
"""

import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage

import send_report_mail as mailer

XLSX_TYPE = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def ask_setup():
    old = mailer.load_settings()
    url = mailer.ask("Viewer address (e.g. http://10.3.48.175:8766)", old.get("viewerUrl", ""))
    password = mailer.ask("Viewer login password", old.get("viewerPassword", ""))
    settings = dict(old)
    settings.update({"viewerUrl": url.rstrip("/"), "viewerPassword": password})
    mailer.SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    print("Viewer settings saved. Now the mail settings:\n")
    mailer.setup()


def login(settings):
    """Returns an opener that carries the viewer's login cookie."""
    base = settings["viewerUrl"]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"password": settings["viewerPassword"]}).encode()
    reply = opener.open(base + "/login", data=data, timeout=60)
    if "/login" in reply.geturl():          # the viewer sent us back to the login page
        raise RuntimeError("Viewer login failed (wrong password, or locked out for a minute).")
    return opener


def build_day_message(settings, day):
    base = settings["viewerUrl"]
    opener = login(settings)
    query = "?day=" + day

    print(f"Getting the numbers for {day} ...")
    summary = json.loads(opener.open(base + "/api/daily-summary" + query, timeout=600).read())
    print(f"Getting the NOT OK Excel ({summary['notokRows']} rows) ...")
    excel = opener.open(base + "/download/daily-notok" + query, timeout=600).read()

    msg = EmailMessage()
    msg["Subject"] = f"Digital Eye Daily Report - {day}"
    msg["From"] = settings["sender"]
    msg["To"] = ", ".join(settings["recipients"])
    msg.set_content(summary["plain"])
    msg.add_alternative(summary["html"], subtype="html")
    msg.add_attachment(excel, maintype=XLSX_TYPE[0], subtype=XLSX_TYPE[1],
                       filename=f"DigitalEye_NOT_OK_{day}.xlsx")
    return msg


def main():
    if "--setup" in sys.argv:
        ask_setup()
        return

    settings = mailer.load_settings()
    if not settings.get("viewerUrl") or not settings.get("host"):
        print("Not set up yet. Run:  python fetch_and_mail.py --setup")
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--today" in sys.argv:
        day = datetime.now().strftime("%Y-%m-%d")
    elif args:
        day = args[0]
    else:
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        msg = build_day_message(settings, day)
        if "--dry-run" in sys.argv:
            print("--- DRY RUN: not sent ---")
            print(f"To: {msg['To']}\nSubject: {msg['Subject']}\n")
            print(msg.get_body(preferencelist=("plain",)).get_content())
            return
        mailer.send(settings, msg)
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)
    print(f"Sent to: {msg['To']}")


if __name__ == "__main__":
    main()
