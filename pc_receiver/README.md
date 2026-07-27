# PC Receiver — "Mahindra Digital Eye Vault"

Companion app for the "Send to PC" feature in Flutter AI Studio-generated
apps. Runs on the PC, opens a local web dashboard for pairing phones and
browsing received data, and receives inspection data (SQLite DB + images)
sent from phones over the local WiFi/hotspot, with password-equivalent
authentication (HMAC pairing) so only a phone that scanned this PC's QR code
can push data to it.

## Run from source (for development)

```bash
cd pc_receiver
pip install -r requirements.txt
python receiver.py
```

It prints something like:

```
Mahindra Digital Eye Vault
Dashboard (this PC only): http://127.0.0.1:8765
Phones on this WiFi/hotspot send to: 192.168.1.42:8765
```

...and automatically opens the dashboard in your default browser. From there:

- **Devices tab** — tap **"+ Add New Device"** to show a QR code; scan it
  from the app's *Send to PC → Pair New PC* screen. Pairing is one-time per
  phone; after that the phone can just tap "Send" going forward. Each paired
  phone is listed with how much data it's sent and when it last sent.
- **Vault tab** — browse received data grouped by **phone name →
  app name → each send**, with file counts, sizes, and a **Download** button
  per batch (grabs the original zip).

The dashboard and all `/api/*` routes only answer requests from the PC
itself (127.0.0.1) — they're not reachable by other devices on the WiFi.
Only the `/pair` and `/upload` endpoints, which phones need to reach, listen
on the network, and both are protected by the pairing handshake.

Received files land in `./received_data/<phone name>/<app name>/<timestamp>/`
(the original zip sits alongside the extracted folder).

## Building a single no-install .exe (Windows)

Build this **on a Windows machine** (PyInstaller doesn't cross-compile):

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --console --name PCReceiver --icon icon.ico receiver.py
```

The `--icon icon.ico` gives the exe the Digital Eye logo as its file/taskbar
icon instead of the default Python icon, so it looks like a real app rather
than a script. `icon.ico` is already in this folder (generated from the logo
you gave us) — just run PyInstaller from inside `pc_receiver/` as shown so it
finds it, or pass a full path otherwise.

The output is `dist\PCReceiver.exe` — a single file with no Python
installation required on the target PC. Double-click to run.

### Add a desktop shortcut (with the logo)

Since the exe already has the Digital Eye icon baked in, any shortcut to it
automatically shows that logo too. Two ways to create one:

- **Automatic:** from inside `dist\`, run:
  ```powershell
  powershell -ExecutionPolicy Bypass -File ..\create_desktop_shortcut.ps1
  ```
  (or copy `create_desktop_shortcut.ps1` next to `PCReceiver.exe` first).
  Creates a "Mahindra Digital Eye Vault" shortcut on the Desktop.
- **Manual:** right-click `PCReceiver.exe` → *Send to* → *Desktop (create
  shortcut)`. The logo appears automatically since it's part of the exe.

Notes:
- Windows Defender Firewall will prompt to allow network access the first
  time it runs — **allow it** (both private/public network as needed),
  otherwise the phone won't be able to reach it.
- Windows SmartScreen may warn "unknown publisher" since the exe is
  unsigned — this is expected for an unsigned binary and doesn't cost
  anything to ship; a paid code-signing certificate would remove the
  warning but isn't required.
- `paired_devices.json` and `received_data/` are created next to the exe.
  Keep the exe in a writable folder (not `C:\Program Files`, not a
  read-only/permission-restricted network or SAN drive) unless you redirect
  `received_data/` per below.

## Storing received data on a different drive (e.g. a large network/SAN volume)

The exe itself should still run from an ordinary local, writable folder --
that's where the small `paired_devices.json` pairing state always lives.
But the received inspection data (photos, zips, per-app `data.xlsx`) can be
much bigger, so its location is separately overridable via
`PCRECEIVER_DATA_DIR`:

```powershell
set PCRECEIVER_DATA_DIR=S:\PCReceiverData
PCReceiver.exe
```

Requirements:
- The folder (create it first, or let the app create it) must be writable
  by whatever Windows account runs the exe -- if it's on a shared/SAN drive,
  that usually means asking whoever administers that drive to grant this
  account **Modify** permission on that specific folder (not necessarily the
  whole drive). If it isn't writable, the app prints a clear error and exits
  on startup rather than failing later mid-pairing.
- Don't point this at a drive/folder reserved for something else (e.g. a
  database's own data volume) without checking with whoever manages it --
  unrelated app data mixed in there can complicate that system's backups,
  space accounting, or maintenance.

## Running alongside another local app on the same PC

This only ever reads/writes inside its own folder (`received_data/`,
`paired_devices.json`, per-app `data.xlsx`) -- it can't touch another app's
files or database. The one thing that *can* clash with another local app is
the port: if something else on the PC already uses `8765`, override it:

```powershell
set PCRECEIVER_PORT=9090
PCReceiver.exe
```

The port is embedded in the pairing QR code, so phones always pick up
whatever port was actually running when they paired -- no phone-side change
needed if you change this.

## Security model

- Any device on the same WiFi/hotspot can technically *see* this PC (mDNS +
  ARP make IPs discoverable on a LAN regardless of what this app does) —
  that's normal for local networks and not something software can hide.
- What actually gates access is the **pairing secret**: `/upload` requires
  an HMAC-SHA256 signature keyed on a secret exchanged only during the
  one-time QR pairing handshake (`/pair`, gated by a single-use, 5-minute
  token). A device that hasn't paired gets `401 Unauthorized`, even though
  it can see this PC on the network.
- Requests older than 5 minutes are rejected (anti-replay), and repeated
  auth failures from the same IP are locked out for 60 seconds.
- Data is sent as plain HTTP on the local network (not HTTPS) for
  simplicity — the LAN traffic isn't encrypted in transit, only
  authenticated. If you need confidentiality too (e.g. untrusted WiFi with
  other tenants), add TLS with a self-signed cert pinned by the app, or
  restrict this to a private hotspot.
