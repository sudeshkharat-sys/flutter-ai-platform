"""
fetch_and_mail.py -- "program 3": runs on ANY PC that can open the viewer's
web address AND can send mail. It logs in to the viewer, downloads the
report, and mails it (using send_report_mail.py, keep both files together).

Usage:
    python fetch_and_mail.py --setup     (first time: viewer address + password, then mail settings)
    python fetch_and_mail.py             (yesterday's report -> mail)
    python fetch_and_mail.py --all       (the full report, all dates -> mail)
    python fetch_and_mail.py 2026-11-24  (that one day -> mail)

Run it every morning with Windows Task Scheduler (see README).
"""

import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import send_report_mail as mailer


def ask_setup():
    old = mailer.load_settings()
    url = mailer.ask("Viewer address (e.g. http://10.3.48.175:8766)", old.get("viewerUrl", ""))
    password = mailer.ask("Viewer login password", old.get("viewerPassword", ""))
    settings = dict(old)
    settings.update({"viewerUrl": url.rstrip("/"), "viewerPassword": password})
    mailer.SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    print("Viewer settings saved. Now the mail settings:\n")
    mailer.setup()


def download_report(settings, day):
    """Logs in, downloads the report Excel, returns the saved file path."""
    base = settings["viewerUrl"]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    login_data = urllib.parse.urlencode({"password": settings["viewerPassword"]}).encode()
    reply = opener.open(base + "/login", data=login_data, timeout=60)
    if "/login" in reply.geturl():          # the viewer sent us back to the login page
        raise RuntimeError("Viewer login failed (wrong password, or locked out for a minute).")

    url = base + "/download/report"
    if day:
        url += "?day=" + day
    print(f"Downloading {url} (a big report can take a minute) ...")
    data = opener.open(url, timeout=600).read()

    folder = mailer.Path(settings.get("reportFolder") or mailer.DOWNLOADS)
    folder.mkdir(parents=True, exist_ok=True)
    label = day if day else f"ALL_{datetime.now():%Y-%m-%d}"
    path = folder / f"DigitalEye_Report_{label}.xlsx"
    path.write_bytes(data)
    print(f"Saved: {path}")
    return path


def main():
    if "--setup" in sys.argv:
        ask_setup()
        return

    settings = mailer.load_settings()
    if not settings.get("viewerUrl") or not settings.get("host"):
        print("Not set up yet. Run:  python fetch_and_mail.py --setup")
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all" in sys.argv:
        day = ""
    elif args:
        day = args[0]
    else:
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        path = download_report(settings, day)
        msg = mailer.build_message(settings, path)
        if "--dry-run" in sys.argv:
            print("--- DRY RUN: not sent ---")
            print(msg.get_body().get_content())
            return
        mailer.send(settings, msg)
    except (urllib.error.URLError, RuntimeError, OSError, Exception) as e:
        print(f"FAILED: {e}")
        sys.exit(1)
    print(f"Sent to: {msg['To']}")


if __name__ == "__main__":
    main()
