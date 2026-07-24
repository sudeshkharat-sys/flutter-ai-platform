# PC Receiver

Companion app for the "Send to PC" feature in Flutter AI Studio-generated
apps. Runs on the PC and receives inspection data (SQLite DB + images) sent
from the phone over the local WiFi/hotspot, with password-equivalent
authentication (HMAC pairing) so only a phone that scanned this PC's QR code
can push data to it.

## Run from source (for development)

```bash
cd pc_receiver
pip install -r requirements.txt
python receiver.py
```

On first run it prints something like:

```
PC Receiver running as 'DESKTOP-ABC123' on 192.168.1.42:8765
Commands: [p]air new phone   [l]ist paired devices   [q]uit
```

Type `p` and press Enter to generate a pairing QR code (ASCII, printed
directly to the console) — scan it from the app's *Send to PC → Pair New PC*
screen. Pairing is one-time per phone; after that, the phone can just tap
"Send" going forward.

Received files land in `./received_data/<phone>_<timestamp>.zip` (also
auto-extracted into a matching folder).

## Building a single no-install .exe (Windows)

Build this **on a Windows machine** (PyInstaller doesn't cross-compile):

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --console --name PCReceiver receiver.py
```

The output is `dist\PCReceiver.exe` — a single file with no Python
installation required on the target PC. Double-click to run.

Notes:
- Windows Defender Firewall will prompt to allow network access the first
  time it runs — **allow it** (both private/public network as needed),
  otherwise the phone won't be able to reach it.
- Windows SmartScreen may warn "unknown publisher" since the exe is
  unsigned — this is expected for an unsigned binary and doesn't cost
  anything to ship; a paid code-signing certificate would remove the
  warning but isn't required.
- `paired_devices.json` and `received_data/` are created next to the exe.
  Keep the exe in a writable folder (not `C:\Program Files`) unless you
  redirect those paths.

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
