"""
send_report_mail.py -- mails a report Excel file you already downloaded.

Standalone and simple: it does not touch the viewer or the receiver.

Usage:
    python send_report_mail.py --setup               (first time: asks mail settings)
    python send_report_mail.py                       (mails the newest report in Downloads)
    python send_report_mail.py "C:\\path\\report.xlsx" (mails that file)
    python send_report_mail.py --dry-run             (shows the mail, sends nothing)

Settings are saved in mail_settings.json next to this script.
"""

import getpass
import json
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SETTINGS_FILE = APP_DIR / "mail_settings.json"
DOWNLOADS = Path.home() / "Downloads"


def load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {}


def ask(label, default=""):
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def setup():
    old = load_settings()
    print("Mail settings (press Enter to keep the value in brackets)")
    host = ask("SMTP host (e.g. smtp.gmail.com)", old.get("host", ""))
    port = int(ask("SMTP port (587 = STARTTLS, 465 = SSL, 25 = plain)", old.get("port", 587)))
    security = ask("Security (starttls / ssl / none)", old.get("security", "starttls")).lower()
    username = ask("Login user (blank if no login needed)", old.get("username", ""))
    sender = ask("Sender address (From)", old.get("sender", username))
    recipients = ask("Recipients (separate with commas)", ", ".join(old.get("recipients", [])))
    recipients = [r.strip() for r in recipients.replace(";", ",").split(",") if r.strip()]
    folder = ask("Folder to look for reports", old.get("reportFolder", str(DOWNLOADS)))

    settings = dict(old)   # keep other keys (like the viewer address used by fetch_and_mail.py)
    settings.update({"host": host, "port": port, "security": security, "username": username,
                     "sender": sender, "recipients": recipients, "reportFolder": folder})

    if username:
        save = ask("Save the password in the settings file? (y/n)", "n").lower()
        if save == "y":
            settings["password"] = getpass.getpass("Password / app password: ")
        elif "password" in old:
            settings["password"] = old["password"]

    if not host or not sender or not recipients:
        print("Host, sender and at least one recipient are needed. Nothing saved.")
        sys.exit(1)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    print(f"Saved to {SETTINGS_FILE}")


def newest_report(folder):
    files = list(Path(folder).glob("DigitalEye_Report_*.xlsx"))
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def read_summary(xlsx_path):
    """Reads the 'ALL APPS' row of the Summary sheet. Returns a dict, or {} if
    anything is missing (the mail is still sent, just without numbers)."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx_path, read_only=True, data_only=True)
        rows = list(wb["Summary"].iter_rows(values_only=True))
        headers = rows[0]
        for row in rows[1:]:
            if row and row[0] == "ALL APPS":
                return dict(zip(headers, row))
    except Exception as e:
        print(f"(Could not read summary numbers: {e})")
    return {}


def build_message(settings, xlsx_path):
    summary = read_summary(xlsx_path)
    lines = [f"Digital Eye report: {xlsx_path.name}", ""]
    for key in ("Total scans", "Unique VINs", "Duplicate VINs", "Scans PASS", "Scans FAIL", "Scan pass %"):
        if summary.get(key) not in (None, ""):
            lines.append(f"{key}: {summary[key]}")
    lines += ["", "Full report is attached (Excel).", f"Sent on {datetime.now():%Y-%m-%d %H:%M}."]

    msg = EmailMessage()
    msg["Subject"] = f"Digital Eye Report - {datetime.now():%Y-%m-%d}"
    msg["From"] = settings["sender"]
    msg["To"] = ", ".join(settings["recipients"])
    msg.set_content("\n".join(lines))
    msg.add_attachment(xlsx_path.read_bytes(), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       filename=xlsx_path.name)
    return msg


def send(settings, msg):
    port = int(settings.get("port", 587))
    security = settings.get("security", "starttls")
    password = os.environ.get("VIEWER_MAIL_PASSWORD") or settings.get("password", "")
    if settings.get("username") and not password:
        password = getpass.getpass(f"Password for {settings['username']}: ")

    if security == "ssl":
        server = smtplib.SMTP_SSL(settings["host"], port, timeout=60)
    else:
        server = smtplib.SMTP(settings["host"], port, timeout=60)
    try:
        if security == "starttls":
            server.starttls()
        if settings.get("username"):
            server.login(settings["username"], password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--setup" in sys.argv:
        setup()
        return

    settings = load_settings()
    if not settings:
        print("No settings yet. Run:  python send_report_mail.py --setup")
        sys.exit(1)

    if args:
        xlsx_path = Path(args[0])
    else:
        xlsx_path = newest_report(settings.get("reportFolder", DOWNLOADS))
    if not xlsx_path or not xlsx_path.exists():
        print("No report file found. Download one from the viewer (Generate Report) "
              "or pass the file path.")
        sys.exit(1)

    print(f"Report file: {xlsx_path}")
    msg = build_message(settings, xlsx_path)

    if "--dry-run" in sys.argv:
        print("--- DRY RUN: nothing sent ---")
        print(f"To: {msg['To']}\nSubject: {msg['Subject']}")
        print(msg.get_body().get_content())
        return

    try:
        send(settings, msg)
    except Exception as e:
        print(f"FAILED to send: {e}")
        sys.exit(1)
    print(f"Sent to: {msg['To']}")


if __name__ == "__main__":
    main()
