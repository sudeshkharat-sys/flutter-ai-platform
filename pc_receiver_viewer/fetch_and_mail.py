"""
fetch_and_mail.py -- "program 3": runs on ANY PC that can open the viewer's
web address AND can send mail. It logs in to the viewer, gets one day's
numbers + the NOT OK Excel, and mails them (uses send_report_mail.py's
settings and sender, keep both files together).

The mail has, for every app: All Results (OK / NOT OK) with Shift A/B/C on the
next lines, All VINs (PASS / FAIL) with Shift A/B/C -- and the attached Excel has
3 tabs: Summary, All VINs (each VIN with its tasks) and NOT OK (failed results).

Usage:
    python fetch_and_mail.py --setup-viewer   (step 1: only the viewer address + password, NO mail needed)
    python fetch_and_mail.py 2026-11-24 --dry-run   (step 1: shows the table, saves the Excel, sends nothing)
    python fetch_and_mail.py --setup     (step 2: viewer address + password, then mail settings)
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


def ask_setup(with_mail=True):
    old = mailer.load_settings()
    url = mailer.ask("Viewer address (e.g. http://10.3.48.175:8766)", old.get("viewerUrl", ""))
    password = mailer.ask("Viewer login password", old.get("viewerPassword", ""))
    settings = dict(old)
    settings.update({"viewerUrl": url.rstrip("/"), "viewerPassword": password})
    mailer.SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    if not with_mail:
        print(f"Viewer settings saved to {mailer.SETTINGS_FILE}")
        print("Next:  python fetch_and_mail.py <date> --dry-run   (shows the table and saves the Excel)")
        return
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
    print(f"Getting the Excel ({summary['notokRows']} NOT OK rows) ...")
    excel = opener.open(base + "/download/daily-excel" + query, timeout=600).read()

    msg = EmailMessage()
    msg["Subject"] = f"Digital Eye Daily Report - {day}"
    msg["From"] = settings.get("sender", "")
    msg["To"] = ", ".join(settings.get("recipients", []))
    msg.set_content(summary["plain"])
    msg.add_alternative(summary["html"], subtype="html")
    msg.add_attachment(excel, maintype=XLSX_TYPE[0], subtype=XLSX_TYPE[1],
                       filename=f"DigitalEye_Daily_{day}.xlsx")
    return msg, excel


def main():
    if "--setup-viewer" in sys.argv:
        ask_setup(with_mail=False)
        return
    if "--setup" in sys.argv:
        ask_setup()
        return

    dry = "--dry-run" in sys.argv
    settings = mailer.load_settings()
    if not settings.get("viewerUrl"):
        print("Not set up yet. Run:  python fetch_and_mail.py --setup-viewer")
        sys.exit(1)
    if not dry and not settings.get("host"):
        print("Mail is not set up yet. Run:  python fetch_and_mail.py --setup")
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--today" in sys.argv:
        day = datetime.now().strftime("%Y-%m-%d")
    elif args:
        day = args[0]
    else:
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        msg, excel = build_day_message(settings, day)
        if dry:
            folder = mailer.Path(settings.get("reportFolder") or mailer.DOWNLOADS)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"DigitalEye_Daily_{day}.xlsx"
            path.write_bytes(excel)
            print("--- DRY RUN: nothing was sent ---\n")
            print(msg.get_body(preferencelist=("plain",)).get_content())
            print(f"Excel saved: {path}")
            return
        mailer.send(settings, msg)
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)
    print(f"Sent to: {msg['To']}")


if __name__ == "__main__":
    main()
