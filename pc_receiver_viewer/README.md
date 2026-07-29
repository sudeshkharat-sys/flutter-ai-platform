# PC Receiver Viewer — read-only "Digital Eye Vault" viewer

A **separate, standalone** companion to `pc_receiver/receiver.py`. It does
not run the phone-pairing/upload pipeline and never writes to inspection
data — it only opens (read-only) the same `received_data/` folder and
per-app `data.xlsx` files receiver.py already produces, and serves them to
other users as a password-protected web page with Excel download.

Why this is a separate app instead of new routes in `receiver.py`:
`receiver.py`'s dashboard is deliberately restricted to `127.0.0.1` only —
it was never meant to be reachable off the PC it runs on. This viewer is
the opposite: it's *meant* to be reached by other machines, so it needs its
own login and its own exposure story, kept completely apart from the
phone-pairing security model. Running it as its own process also means a
bug or restart here can never affect the live phone → PC sync pipeline.

## What it does and doesn't do

- ✅ View received inspections (VIN, model, task results, images) across
  every phone/app that has sent data.
- ✅ Filter by VIN/model text, OK/NOT OK, and date range.
- ✅ Download the full running Excel file, or a filtered export.
- ❌ No pairing, no upload, no delete, no settings, no device management.
  Every route is read-only at the code level (files are only ever opened
  for reading).
- ❌ No access to `receiver.py`'s admin dashboard, config, or device list —
  those stay on the receiver, localhost-only, exactly as today.

## Run from source

```bash
cd pc_receiver_viewer
pip install -r requirements.txt
set VIEWER_DATA_DIR=C:\path\to\receiver\received_data   REM see "Pointing at your data" below
python viewer.py
```

On first run, if you haven't set `VIEWER_PASSWORD`, it generates a random
password, prints it once, and writes it to `FIRST_RUN_PASSWORD.txt` next to
the script — share that password with viewers, then delete the file. To set
your own password instead:

```bash
python viewer.py --set-password
```

(or set the `VIEWER_PASSWORD` environment variable before the very first
run, which skips the auto-generated one).

**Changing the password later:** run the same command again any time —
`python viewer.py --set-password` — it always overwrites whatever password
was set before, there's no limit to how many times you can rotate it.
**One thing to remember:** the running viewer process only reads the
password when it starts up, so if the viewer is currently running, stop it
first, run `--set-password`, then start it again — otherwise it keeps
accepting the old password until it's restarted.

## Pointing at your data

This app doesn't ingest anything itself — it just reads a data folder, and
where that folder is is an **admin-only setting** — there is no route in
the web UI to view or change it, so nobody who opens the shared link can
ever see or touch this setting, no matter what they click.

Set (or change) it with:

```bash
python viewer.py --set-data-dir
```

Run with no path, and it opens a native folder-browse window — click your
way to the receiver's data folder instead of typing a path that could have
a typo. (Needs a graphical session to show that window — if you're running
this from a non-graphical/remote console, pass the path directly instead:
`python viewer.py --set-data-dir "S:\PCReceiverData"`.) Either way, use the
exact folder shown in the receiver's own **⚙ Settings** screen, since
that's what it's actually writing to.

This saves the path permanently in `viewer_config.json` next to the
script/exe — re-run the same command any time to point it at a different
folder instead, it always overwrites the previous value. From then on, just
run `viewer.py` (or the `.exe`) normally, no environment variable needed.
Same restart note as the password: if the viewer is already running, stop
it, change the folder, then start it again.

(`VIEWER_DATA_DIR` as an environment variable still works too, e.g. for a
scripted first-run — it's only used if nothing has been saved via
`--set-data-dir` yet, same priority order the receiver itself uses for its
own data-folder setting.)

- **Running on the same PC as receiver.py** (simplest): point it straight
  at the receiver's own data folder — same path, no copying.
- **Running on a different machine**: point it at a synced/shared copy
  (e.g. a mapped network drive, a scheduled `robocopy`/rsync job, or a
  network share the receiver's `received_data/` is redirected to via its
  own Settings). Setting this up is an infra/network task, not something
  this app does for you.

## Security — read before exposing this beyond your own machine

- **Every page and API route requires login** (the shared password above).
  Failed attempts are rate-limited (5 tries, then a 60s lockout per IP).
- **This runs on plain HTTP by default**, same as receiver.py — but unlike
  receiver.py (which only ever talks to `127.0.0.1`), this app is *meant*
  to be reached from other networks. Sending a password over plain HTTP on
  an untrusted path is a real risk. Before exposing this beyond a small
  trusted LAN, do one of:
  - Put it behind a reverse proxy that terminates HTTPS (ask your network
    team — this is also usually the same piece of infrastructure that
    would make it reachable off-network at all, e.g. from a laptop on
    home WiFi).
  - Or set `VIEWER_TLS_CERT` / `VIEWER_TLS_KEY` to a cert/key pair to have
    this process serve HTTPS directly.
- **It listens on `0.0.0.0`** (all network interfaces), not just
  `127.0.0.1` — that's intentional, since the whole point is to be
  reachable by other machines. Whether anyone outside your immediate LAN
  can actually reach it depends on firewall rules and network routing you
  don't control from this app — see the "how do people reach the server at
  all" discussion with your network/security team before deploying.
- The shared-password model is simple but coarse — everyone who has it can
  see everything, and you can't tell who downloaded what. If you need
  per-person accounts or an audit trail later, that's a bigger change;
  happy to build it if/when you need it.

## Configuration reference

| Setting | Env var | Default |
|---|---|---|
| Data folder to read | `VIEWER_DATA_DIR` | `./received_data` next to the script |
| Port | `VIEWER_PORT` | `8766` (different from receiver's `8765`, so both can run on the same PC) |
| Initial password | `VIEWER_PASSWORD` | auto-generated on first run if unset |
| TLS cert/key (optional) | `VIEWER_TLS_CERT` / `VIEWER_TLS_KEY` | none (plain HTTP) |

`viewer_config.json` (created next to the script) persists the password
hash and the session-signing secret across restarts — keep it private, same
as you would `paired_devices.json` for the receiver.

## Building a single .exe (Windows)

Same approach as `pc_receiver/`:

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --console --name DigitalEyeViewer --icon ..\pc_receiver\icon.ico viewer.py
```

The output is `dist\DigitalEyeViewer.exe` — no Python install required on
the target machine.
