"""
PC Receiver -- "Mahindra Digital Eye Vault"

Companion app for the "Send to PC" feature in generated Flutter AI Studio
apps. Run this on the PC. It:

  1. Serves a local web dashboard (opened automatically in your browser) for
     pairing new phones (QR code) and browsing received data -- the
     "Vault" -- per phone and per app.
  2. Advertises itself on the local WiFi/hotspot via mDNS so phones can find
     it by name instead of typing an IP.
  3. Accepts uploads only from phones that completed the QR pairing
     handshake, verified via an HMAC signature -- any other device on the
     same WiFi is rejected with 401, even though it can see this PC.

The dashboard itself (and every /api/* route) only answers requests from
this PC (127.0.0.1) -- it is not exposed to the rest of the WiFi. Only
/pair and /upload, which phones need to reach and which are already
protected by the pairing handshake, listen on the LAN.

No installation needed when packaged with PyInstaller (see README.md) --
just double-click the resulting .exe.
"""

import asyncio
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import secrets
import socket
import os
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

import mimetypes

import qrcode
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from starlette.requests import ClientDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from zeroconf import ServiceInfo, Zeroconf

from assets import FAVICON_PNG_BASE64, LOGO_PNG_BASE64


def _pause_briefly(seconds: int = 15):
    """Waits for a keypress or `seconds`, whichever comes first, before
    returning -- so someone who double-clicked the exe directly still has
    time to read whatever was just printed, but the process still exits
    on its own if nothing responds. A plain blocking input() would
    otherwise leave the watchdog's auto-restart loop (receiver_run_forever.bat)
    stuck waiting forever for a keypress no unattended process can ever
    provide -- the exe would look "stuck"/"not restarting", when it's
    actually just paused on this exact prompt."""
    print(f"(Closing automatically in {seconds}s -- press Enter to close sooner.)")
    try:
        import msvcrt
        end = time.time() + seconds
        while time.time() < end:
            if msvcrt.kbhit():
                msvcrt.getch()
                return
            time.sleep(0.1)
    except ImportError:
        # Not Windows (e.g. running from source during development) --
        # msvcrt doesn't exist, just wait out the same duration.
        time.sleep(seconds)
    except Exception:
        pass


# ── Paths (work both as a plain script and a PyInstaller --onefile exe) ────

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

# config.json and paired_devices.json always live next to the exe -- that
# folder is expected to be an ordinary local, writable location (the app
# will have already refused to run at all if it isn't, since these are
# required). Where received inspection data (photos, zips, per-app
# data.xlsx) is stored is a separate, changeable choice: picked once from
# the dashboard's Settings panel (persisted here), an environment variable
# (PCRECEIVER_DATA_DIR, for scripted/first-run setups), or defaulting to a
# folder next to the exe -- in that priority order. This lets the same exe
# be dropped on any PC/server and pointed at wherever that machine's
# operator wants the (potentially large) received data to actually live,
# e.g. a network/SAN volume, without editing any config file by hand.
CONFIG_FILE = APP_DIR / "config.json"
DEVICES_FILE = APP_DIR / "paired_devices.json"
APP_ALIASES_FILE = APP_DIR / "app_aliases.json"
MASTER_DATA_FILE = APP_DIR / "master_data.json"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _ensure_writable_dir(path: Path) -> str | None:
    """Creates `path` if needed and proves it's actually writable by writing
    and removing a small probe file. Returns None on success, or an error
    message on failure -- never raises, so callers can show it in the UI."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pcreceiver_write_test"
        probe.write_text("ok")
        probe.unlink()
        return None
    except OSError as e:
        return str(e)


def _pick_folder_dialog() -> str | None:
    """Opens a native OS folder-picker on top of everything else. Runs on a
    worker thread (see the /api/settings/browse route) so the blocking Tk
    dialog doesn't stall the server while the user is choosing -- fine on
    Windows, which (unlike macOS) doesn't require Tk to run on the main
    thread. Returns None if the user cancels."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(title="Choose folder to store received inspection data")
    finally:
        root.destroy()
    return chosen or None


_config = _load_config()
DATA_DIR = Path(
    _config.get("dataDir")
    or os.environ.get("PCRECEIVER_DATA_DIR")
    or (APP_DIR / "received_data")
)
_data_dir_error = _ensure_writable_dir(DATA_DIR)
if _data_dir_error:
    # Deliberately non-fatal: the dashboard (which only needs APP_DIR to be
    # writable, checked separately below) stays usable so the folder can be
    # fixed from Settings instead of only from a command line.
    print(f"[warn] Storage folder {DATA_DIR} is not writable: {_data_dir_error}")
    print("       Open the dashboard and use Settings to choose a writable folder.")

_app_dir_error = _ensure_writable_dir(APP_DIR)
if _app_dir_error:
    # Plain sys.exit(message) would print and quit here, but this runs
    # before main()'s own crash-pause wrapper ever gets a chance to catch
    # anything -- on a double-clicked .exe, Windows closes the console the
    # instant the process exits, so the message would flash and vanish
    # just like an unhandled traceback does. Pausing here directly is the
    # only way this particular message stays readable.
    print(
        f"Could not write to {APP_DIR} ({_app_dir_error})\n"
        f"This app needs to run from a folder this Windows account can write "
        f"to (e.g. Desktop, Documents, or a plain local folder) -- not "
        f"Program Files or a read-only network location."
    )
    _pause_briefly()
    sys.exit(1)


# Configurable in case another local app on this PC already uses 8765 --
# the port is embedded in the pairing QR code, so phones always pick up
# whatever port was actually in use at pairing time; nothing on the phone
# side needs to change when this is overridden.
PORT = int(os.environ.get("PCRECEIVER_PORT", "8765"))
SERVICE_TYPE = "_flutteraisync._tcp.local."
PAIRING_TOKEN_TTL = 300  # seconds
SIGNATURE_WINDOW = 300   # seconds -- rejects replayed requests older than this
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

PC_NAME = socket.gethostname()

app = FastAPI()

_lock = threading.Lock()
_paired_devices: dict[str, dict] = {}          # deviceId -> {secret, deviceName, appName, pairedAt}
_pending_token: dict | None = None              # {token, expiresAt}
_pairing_results: dict[str, dict] = {}          # token -> {deviceId, deviceName, appName} once paired
_failed_attempts: dict[str, list[float]] = {}   # client ip -> [failure timestamps]
_recent_events: list[dict] = []                 # recent uploads, for the dashboard's live activity feed
_MAX_RECENT_EVENTS = 50
_last_heartbeat: dict[str, float] = {}          # deviceId -> epoch seconds of its last /heartbeat ping
HEARTBEATS_FILE_NAME = "_heartbeats.json"

# Incremental cache for the flattened data-viewer rows, keyed by device_dir
# path. Batch folders are immutable once written (a "send" never gets
# retroactively edited), so once a batch's manifest.json has been parsed and
# cached, it never needs to be re-read -- only batch dirs that weren't seen
# last time need parsing. Without this, every dashboard open/filter change
# re-read and re-JSON-parsed every manifest.json under a device/app on disk,
# which got slow once a plant's data grew into the thousands of sends.
_rows_cache_lock = threading.Lock()
_rows_cache: dict[str, dict] = {}  # str(device_dir) -> {"batches": set[str], "rows": list[dict], "seen_keys": set}


# ── Persistence ──────────────────────────────────────────────────────────

def _load_devices():
    global _paired_devices
    if DEVICES_FILE.exists():
        try:
            _paired_devices = json.loads(DEVICES_FILE.read_text())
        except Exception:
            _paired_devices = {}


def _merge_devices_from_disk():
    """Pulls in any device on disk that this process doesn't have in
    memory, before saving. Without this, if more than one copy of this
    exe has been running (a crash-restart loop, a stray leftover instance
    from before an update, etc.) each one only knows about whatever was
    in paired_devices.json at the moment *it* started -- an older,
    stale-but-still-alive copy handling a later pairing would otherwise
    save its outdated snapshot straight over the file, silently erasing
    any device paired since that copy started, even one with a
    completely different name that never matched or collided with
    anything. Call this right before every _save_devices()."""
    if not DEVICES_FILE.exists():
        return
    try:
        on_disk = json.loads(DEVICES_FILE.read_text())
    except Exception:
        return
    for did, d in on_disk.items():
        _paired_devices.setdefault(did, d)


def _save_devices():
    try:
        DEVICES_FILE.write_text(json.dumps(_paired_devices, indent=2))
    except OSError as e:
        # Most commonly the exe is sitting in a folder this user account
        # can't write to (Program Files, a read-only network share, etc.)
        # -- surface that plainly instead of letting it bubble up as an
        # opaque 500 with no clue what actually went wrong.
        raise RuntimeError(
            f"Could not write {DEVICES_FILE} -- move the app to a folder "
            f"this account can write to (e.g. Desktop or Documents), not "
            f"Program Files or a read-only network location. ({e})"
        ) from e


# Heartbeats -- a lightweight, no-data liveness ping the phone sends every
# ~45s while the app is open and a PC is paired (see /heartbeat below),
# independent of actual inspection uploads. Data-based "last received"
# alone can't tell "still connected but nothing new to send" apart from
# "actually dropped off WiFi 20 minutes ago" -- this can. Written under
# DATA_DIR (not next to the exe, like paired_devices.json) specifically so
# the separate, read-only pc_receiver_viewer process -- which only ever
# opens DATA_DIR, never talks to this process directly -- can see it too.

def _heartbeats_file() -> Path:
    return DATA_DIR / HEARTBEATS_FILE_NAME


def _load_heartbeats():
    global _last_heartbeat
    try:
        raw = json.loads(_heartbeats_file().read_text())
        _last_heartbeat = {did: v["lastHeartbeatMs"] / 1000.0 for did, v in raw.items()}
    except Exception:
        _last_heartbeat = {}


def _save_heartbeats():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            did: {
                "deviceName": _paired_devices.get(did, {}).get("deviceName", ""),
                "appName": _paired_devices.get(did, {}).get("appName", ""),
                "lastHeartbeatMs": int(ts * 1000),
            }
            for did, ts in _last_heartbeat.items()
        }
        _heartbeats_file().write_text(json.dumps(out, indent=2))
    except OSError:
        pass  # best-effort -- a write hiccup here should never fail the ping response


HIDDEN_DEVICES_FILE_NAME = "_hidden_devices.json"

# Devices/apps an admin has hidden from the read-only Vault viewer -- e.g. a
# test phone whose data shouldn't be permanently deleted (it may still be
# useful on this PC), but also shouldn't be visible to whoever's looking at
# the Vault viewer. Written under DATA_DIR (not next to the exe, like
# paired_devices.json) for the same reason heartbeats are: the separate
# pc_receiver_viewer process only ever reads DATA_DIR, so this is how it
# learns what to filter out. {"deviceSafe|appSafe": true, ...}
_hidden_devices: dict[str, bool] = {}


def _hidden_devices_file() -> Path:
    return DATA_DIR / HIDDEN_DEVICES_FILE_NAME


def _hidden_key(device_name_safe: str, app_name_safe: str) -> str:
    return f"{device_name_safe}|{app_name_safe}"


def _load_hidden_devices():
    global _hidden_devices
    try:
        _hidden_devices = json.loads(_hidden_devices_file().read_text())
    except Exception:
        _hidden_devices = {}


def _save_hidden_devices():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _hidden_devices_file().write_text(json.dumps(_hidden_devices, indent=2))
    except OSError as e:
        raise RuntimeError(f"Could not write {_hidden_devices_file()}: {e}") from e


# App aliases -- for when two builds of what's really the same production
# app ended up with different names (a rename, a typo, a leftover "(Copy)"
# from Duplicate), which otherwise fragments their data into two unrelated
# app buckets since the app name is the merge key everywhere. Rather than
# physically moving/renaming anything on disk (risky on a live production
# folder), an alias just says "treat app A's data as app B's data" --
# resolved wherever an app name is used to group/merge, both for history
# already on disk and for anything uploaded after the alias is added.
# {non-canonical safe app name -> canonical safe app name}
_app_aliases: dict[str, str] = {}


def _load_app_aliases():
    global _app_aliases
    if APP_ALIASES_FILE.exists():
        try:
            _app_aliases = json.loads(APP_ALIASES_FILE.read_text())
        except Exception:
            _app_aliases = {}


def _save_app_aliases():
    APP_ALIASES_FILE.write_text(json.dumps(_app_aliases, indent=2))


def _resolve_app_alias(app_name_safe: str) -> str:
    """Follows the alias chain to its canonical name. Bounded to a handful
    of hops so a corrupt/cyclical aliases file can't hang a request."""
    seen = set()
    current = app_name_safe
    for _ in range(10):
        target = _app_aliases.get(current)
        if not target or target == current or target in seen:
            return current
        seen.add(current)
        current = target
    return current


# Master data -- the same VIN/model-code -> description mapping the app
# bundles into each APK at build time (assets/master_data.json, see
# backend/app/codegen). The APK-side "Model Variant" is only whatever token
# it could regex out of that description at build time (e.g. "(V1)"), which
# is empty for model families that don't tag a variant that way -- so an
# admin can paste/upload that same master data here and have it filled in
# (or corrected) directly on the PC side, matched by Model Code, using the
# raw description as the variant value.
# list of {"modelCode": str, "description": str, "platformName": str}
_master_data: list[dict] = []


def _load_master_data():
    global _master_data
    if MASTER_DATA_FILE.exists():
        try:
            _master_data = json.loads(MASTER_DATA_FILE.read_text())
        except Exception:
            _master_data = []


def _save_master_data():
    MASTER_DATA_FILE.write_text(json.dumps(_master_data, indent=2))


def _normalize_master_data(raw: list) -> list[dict]:
    """Accepts the same shape the app build pipeline exports/imports
    (platform_name/model_code/description, or modelCode/description) and
    normalizes it to one shape. Rows missing a model code or description
    are dropped -- there's nothing to match or fill in from them."""
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        model_code = str(item.get("modelCode") or item.get("model_code") or "").strip()
        description = str(item.get("description") or "").strip()
        platform_name = str(item.get("platformName") or item.get("platform_name") or "").strip()
        if not model_code or not description:
            continue
        out.append({"modelCode": model_code, "description": description, "platformName": platform_name})
    return out


def _master_data_variant_map() -> dict[str, str]:
    return {row["modelCode"].upper(): row["description"] for row in _master_data}


def _reconcile_master_data() -> dict:
    """Applies the current master data to every inspection already on disk:
    for any row whose Model Code matches, the Model Variant is set to the
    master data's description -- overwriting whatever's there if it
    differs. Rewrites manifest.json files (the data-viewer table's source
    of truth) and the per-app master Excel workbooks, then drops the
    in-memory rows cache so the dashboard reflects the correction
    immediately instead of on next restart."""
    variant_map = _master_data_variant_map()
    manifests_updated = 0
    rows_updated = 0

    if variant_map and DATA_DIR.exists():
        for device_dir in DATA_DIR.iterdir():
            if not device_dir.is_dir() or device_dir.name.startswith("_"):
                continue
            for app_dir in device_dir.iterdir():
                if not app_dir.is_dir():
                    continue
                for batch_dir in app_dir.iterdir():
                    if not batch_dir.is_dir():
                        continue
                    manifest_path = batch_dir / "manifest.json"
                    if not manifest_path.exists():
                        continue
                    try:
                        inspections = json.loads(manifest_path.read_text())
                    except Exception:
                        continue
                    dirty = False
                    for insp in inspections:
                        code = str(insp.get("modelCode") or "").upper()
                        new_variant = variant_map.get(code)
                        if new_variant is not None and insp.get("modelVariant") != new_variant:
                            insp["modelVariant"] = new_variant
                            dirty = True
                            rows_updated += 1
                    if dirty:
                        manifest_path.write_text(json.dumps(inspections, indent=2))
                        manifests_updated += 1

        master_root = DATA_DIR / "_master"
        if master_root.exists():
            variant_col = next(i for i, (_l, k) in enumerate(EXCEL_COLUMNS) if k == "modelVariant")
            code_col = next(i for i, (_l, k) in enumerate(EXCEL_COLUMNS) if k == "modelCode")
            for app_dir in master_root.iterdir():
                xlsx_path = app_dir / "data.xlsx"
                if not xlsx_path.exists():
                    continue
                try:
                    wb = load_workbook(xlsx_path)
                    ws = wb.active
                    changed = False
                    for row in ws.iter_rows(min_row=2):
                        code = str(row[code_col].value or "").upper()
                        new_variant = variant_map.get(code)
                        if new_variant is not None and row[variant_col].value != new_variant:
                            row[variant_col].value = new_variant
                            changed = True
                    if changed:
                        wb.save(xlsx_path)
                except Exception as e:
                    print(f"[warn] could not reconcile master data into {xlsx_path}: {e}")

    with _rows_cache_lock:
        _rows_cache.clear()

    return {"manifestsUpdated": manifests_updated, "rowsUpdated": rows_updated, "modelsLoaded": len(variant_map)}


# Columns for both the persistent per-app master workbook and the
# browser's ad-hoc filtered export -- kept in one place so they stay in sync.
# "Device" records which phone sent each row, since the master workbook is
# now shared by every phone paired under the same app (see
# _append_to_master_excel) rather than split one-per-phone.
EXCEL_COLUMNS = [
    ("VIN", "vin"),
    ("Model Code", "modelCode"),
    ("Model Name", "modelName"),
    ("Model Variant", "modelVariant"),
    ("Date", "date"),
    ("Time", "time"),
    ("Shift", "shift"),
    ("Shift Date", "shiftDate"),
    ("Device", "deviceName"),
    ("Task", "taskName"),
    ("Detected", "className"),
    ("Result", "result"),
    ("Batch", "batch"),
]


def _flatten_manifest_rows(inspections: list, batch_name: str, device_name: str = "") -> list[dict]:
    """One row per inspection task -- shared by the live data-viewer table
    and the persistent master-Excel append on each upload. [device_name] is
    stamped onto every row since the master workbook now aggregates every
    phone paired under the same app (see _append_to_master_excel)."""
    rows = []
    for insp in inspections:
        tasks = insp.get("tasks") or [{}]
        for task in tasks:
            rows.append({
                "batch": batch_name,
                "deviceName": device_name,
                "inspectionId": insp.get("inspectionId"),
                "vin": insp.get("vin"),
                "modelCode": insp.get("modelCode"),
                "modelName": insp.get("modelName"),
                "modelVariant": insp.get("modelVariant"),
                "date": insp.get("date"),
                "time": insp.get("time"),
                "shift": insp.get("shift"),
                # shiftDate is normally identical to date (no rollback --
                # Shift C keeps its own actual calendar date); falls back to
                # the raw date for phones running an older build that
                # doesn't send it yet.
                "shiftDate": insp.get("shiftDate") or insp.get("date"),
                "taskName": task.get("taskName"),
                "className": task.get("className"),
                "result": "OK" if task.get("success") else "NOT OK",
                "imagePath": task.get("imagePath"),
                "backupImagePath": task.get("backupImagePath"),
            })
    return rows


def _style_worksheet(ws):
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(EXCEL_COLUMNS))}{max(ws.max_row, 1)}"
    for i, (label, _key) in enumerate(EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(label) + 4)


def _open_or_migrate_master_workbook(xlsx_path: Path):
    """Opens the existing data.xlsx, migrating its header/columns first if a
    newer app build (or a codegen update) changed the column set since it
    was created -- e.g. adding a Model Name column later shouldn't shift
    every subsequent row out of alignment with a file that predates it.
    Existing values are preserved by matching on column label; any brand
    new column is simply blank for the older rows that predate it."""
    current_header = [label for label, _key in EXCEL_COLUMNS]

    if not xlsx_path.exists():
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(current_header)
        return wb, ws

    wb = load_workbook(xlsx_path)
    ws = wb.active
    existing_header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    if existing_header == current_header:
        return wb, ws

    old_rows = [dict(zip(existing_header, row)) for row in ws.iter_rows(min_row=2, values_only=True)]
    migrated = Workbook()
    mws = migrated.active
    mws.title = "Data"
    mws.append(current_header)
    for old_row in old_rows:
        mws.append([old_row.get(label, "") for label in current_header])
    print(f"[info] migrated {xlsx_path} to the current column set ({len(old_rows)} existing row(s) kept)")
    return migrated, mws


def _dedup_key(row: dict) -> tuple:
    """A physical scan event's natural identity -- stable across re-sends
    of the same data, unlike the phone-local inspection id (which resets
    to 1 after an app reinstall, so it can collide with an unrelated
    inspection from a different install). Used to drop rows a phone
    re-sends that this PC already has, e.g. from a manual "Resync All"
    after re-pairing, without duplicating them."""
    return (row.get("vin"), row.get("date"), row.get("time"), row.get("taskName"))


def _app_master_dir(app_name: str) -> Path:
    """Where an app's aggregated master workbook lives -- keyed by app name
    alone, under a folder prefix that can't collide with a real (user-typed)
    phone name, so every phone paired under the same generated app lands in
    the same continuous data.xlsx instead of getting split up one file per
    phone. That split used to also mean a phone that dropped its pairing and
    got re-paired as e.g. "Samsung (2)" would start a brand new, empty
    workbook -- fragmenting one continuous production run across multiple
    files for no reason a phone-side identity hiccup should ever cause.

    Also resolves through _app_aliases, so two differently-named apps that
    are really the same production line share one workbook once an admin
    aliases one to the other -- both new uploads and the Apps-tab merge."""
    return DATA_DIR / "_master" / _resolve_app_alias(_safe_name(app_name))


def _seed_master_from_legacy_device_excels(app_name_safe: str, xlsx_path: Path):
    """One-time migration: the master workbook used to be split one per
    device (<device>/<app>/data.xlsx) before it became one shared file per
    app (_master/<app>/data.xlsx -- see _app_master_dir). The first time
    the new shared file is about to be created, pull in every row from any
    old per-device file for this same app first, so switching over doesn't
    look like production history got reset to zero -- each device's old
    history just continues in the new shared file. Old per-device files
    are left on disk untouched (not deleted), so nothing is destructive
    here even if something above looks wrong later. Must be called with
    _lock already held."""
    if xlsx_path.exists() or not DATA_DIR.exists():
        return
    legacy_rows = []
    for device_dir in DATA_DIR.iterdir():
        if not device_dir.is_dir() or device_dir.name == "_master":
            continue
        legacy_xlsx = device_dir / app_name_safe / "data.xlsx"
        if not legacy_xlsx.exists():
            continue
        try:
            legacy_wb = load_workbook(legacy_xlsx)
            legacy_ws = legacy_wb.active
            header = [cell.value for cell in next(legacy_ws.iter_rows(min_row=1, max_row=1))]
            label_to_key = {label: key for label, key in EXCEL_COLUMNS}
            for values in legacy_ws.iter_rows(min_row=2, values_only=True):
                by_label = dict(zip(header, values))
                mapped = {label_to_key[label]: by_label.get(label, "")
                          for label in header if label in label_to_key}
                # Old per-device files predate the Device column -- the
                # folder name is exactly which phone this history belongs to.
                if not mapped.get("deviceName"):
                    mapped["deviceName"] = device_dir.name
                legacy_rows.append(mapped)
        except Exception as e:
            print(f"[warn] could not read legacy workbook {legacy_xlsx} during migration: {e}")
    if not legacy_rows:
        return
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb, ws = _open_or_migrate_master_workbook(xlsx_path)
    seen = set()
    migrated = 0
    for row in legacy_rows:
        key = _dedup_key(row)
        if key in seen:
            continue
        seen.add(key)
        ws.append([row.get(key2, "") for _label, key2 in EXCEL_COLUMNS])
        migrated += 1
    if migrated:
        _style_worksheet(ws)
        wb.save(xlsx_path)
        print(f"[info] migrated {migrated} row(s) from legacy per-device workbook(s) into {xlsx_path}")


def _append_to_master_excel(app_dir: Path, rows: list[dict]):
    """Every app gets ONE running Excel file (data.xlsx), shared across
    every phone paired under that app, that new rows are appended to on
    each successful upload -- instead of a separate export having to be
    regenerated by hand each time, and instead of each phone fragmenting
    the same production data into its own file. It's always current, and a
    send that previously failed just shows up in it whenever it finally
    lands, no manual re-export needed. Each row's "Device" column records
    which phone it came from.

    Rows matching a (VIN, date, time, task) already present are skipped --
    a phone can legitimately re-send data it already sent before (a forced
    "Resync All", a retried upload, re-pairing after a reinstall), and this
    keeps that from piling up as duplicate rows here."""
    if not rows:
        return
    xlsx_path = app_dir / "data.xlsx"
    with _lock:
        _seed_master_from_legacy_device_excels(app_dir.name, xlsx_path)
        wb, ws = _open_or_migrate_master_workbook(xlsx_path)
        key_idx = {key: i for i, (_label, key) in enumerate(EXCEL_COLUMNS)}
        existing_keys = set()
        for existing_row in ws.iter_rows(min_row=2, values_only=True):
            existing_keys.add((
                existing_row[key_idx["vin"]],
                existing_row[key_idx["date"]],
                existing_row[key_idx["time"]],
                existing_row[key_idx["taskName"]],
            ))
        new_count = 0
        for row in rows:
            if _dedup_key(row) in existing_keys:
                continue
            ws.append([row.get(key, "") for _label, key in EXCEL_COLUMNS])
            new_count += 1
        if new_count:
            _style_worksheet(ws)
            wb.save(xlsx_path)


def _safe_name(name: str) -> str:
    """Sanitize a phone/app-supplied name for use as a folder path segment."""
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned or "unknown"


# ── Local IP discovery (for the QR payload / mDNS registration) ────────────

def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ── Access control: the dashboard/API is for this PC only ──────────────────

def _require_local(request: Request):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Dashboard is only accessible from this PC")


# ── Pairing ──────────────────────────────────────────────────────────────

def _make_qr_base64(payload: dict) -> str:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(json.dumps(payload))
    qr.make(fit=True)
    img = qr.make_image(fill_color="#151923", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@app.post("/api/pair/start")
async def api_pair_start(_: None = Depends(_require_local)):
    global _pending_token
    token = secrets.token_urlsafe(24)
    expires_at = time.time() + PAIRING_TOKEN_TTL
    with _lock:
        _pending_token = {"token": token, "expiresAt": expires_at}
    payload = {"name": PC_NAME, "ip": _local_ip(), "port": PORT, "token": token}
    return {"token": token, "qrImage": _make_qr_base64(payload), "expiresAt": expires_at}


@app.get("/api/pair/status")
async def api_pair_status(token: str, _: None = Depends(_require_local)):
    with _lock:
        result = _pairing_results.get(token)
    if result:
        return {"paired": True, **result}
    return {"paired": False}


@app.post("/pair")
async def pair(request: Request):
    global _pending_token
    body = await request.json()
    token = body.get("token")
    device_name = body.get("deviceName", "unknown-device")
    app_name = body.get("appName", "app")
    install_id = body.get("installId")  # absent on older app builds that predate this field

    with _lock:
        pending = _pending_token
        if not pending or pending["token"] != token:
            raise HTTPException(status_code=400, detail="Invalid or already-used pairing token")
        if time.time() > pending["expiresAt"]:
            _pending_token = None
            raise HTTPException(status_code=400, detail="Pairing token expired -- generate a new QR code")

        # One-shot: consume the token so a screenshot can't be reused.
        _pending_token = None

        # Catch up with anything another process instance may have
        # written to disk since this one started, before reading or
        # changing anything -- see _merge_devices_from_disk()'s docstring.
        _merge_devices_from_disk()

        # Match by the phone's persistent install id, never by the typed
        # device name alone -- two different phones can easily end up
        # with the same typed name (nothing stops it), and matching by
        # name used to silently reuse the first phone's entry in that
        # case, rotating its secret out from under it and breaking its
        # ability to send anything further. An install id can't collide
        # between two different phones, so it's the only safe way to
        # recognize "this is the same phone pairing again".
        existing_id = None
        if install_id:
            existing_id = next(
                (did for did, d in _paired_devices.items() if d.get("installId") == install_id),
                None,
            )

        secret = secrets.token_hex(32)
        if existing_id:
            device_id = existing_id
            _paired_devices[device_id]["secret"] = secret
            _paired_devices[device_id]["pairedAt"] = datetime.now().isoformat()
        else:
            # No install-id match -- either a genuinely new phone, or the
            # same phone re-pairing after a reinstall (which wipes its
            # locally stored install id along with everything else). If
            # the typed name is already taken by another device, don't
            # merge into it (that's the exact collision this is meant to
            # avoid) -- disambiguate instead so both keep working.
            final_name = device_name
            existing_names = {
                d["deviceName"] for d in _paired_devices.values() if d.get("appName") == app_name
            }
            suffix = 2
            while final_name in existing_names:
                final_name = f"{device_name} ({suffix})"
                suffix += 1
            if final_name != device_name:
                print(f"[warn] Device name '{device_name}' is already paired -- this new pairing is "
                      f"stored as '{final_name}' instead so neither one stops working.")
            device_id = str(uuid.uuid4())
            _paired_devices[device_id] = {
                "secret": secret,
                "deviceName": final_name,
                "appName": app_name,
                "installId": install_id,
                "pairedAt": datetime.now().isoformat(),
            }
            device_name = final_name
        try:
            _save_devices()
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        _pairing_results[token] = {"deviceId": device_id, "deviceName": device_name, "appName": app_name}

    print(f"[paired] New device paired: {device_name} / {app_name} ({device_id})")
    return {"deviceId": device_id, "secret": secret}


# ── Upload ───────────────────────────────────────────────────────────────

def _client_locked_out(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) >= MAX_FAILED_ATTEMPTS


def _record_failure(ip: str):
    _failed_attempts.setdefault(ip, []).append(time.time())


@app.post("/upload")
async def upload(
    request: Request,
    x_device_id: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...),
):
    client_ip = request.client.host if request.client else "unknown"

    if _client_locked_out(client_ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts, try again later")

    device = _paired_devices.get(x_device_id)
    if not device:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Unknown device -- not paired with this PC")

    # Anti-replay: reject stale timestamps.
    try:
        ts = int(x_timestamp) / 1000.0
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad timestamp")
    if abs(time.time() - ts) > SIGNATURE_WINDOW:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Request expired")

    try:
        form = await request.form()
        upload_file = form.get("data")
        if upload_file is None:
            raise HTTPException(status_code=400, detail="Missing file field 'data'")
        body_bytes = await upload_file.read()
    except ClientDisconnect:
        # The phone gave up mid-upload -- usually a weak/unstable WiFi
        # connection dropping partway through sending a larger batch
        # (photos make the payload much bigger than just the data rows).
        # Nothing was saved; the phone's own unsynced flag is untouched,
        # so tapping Send again will retry the same data, not skip it.
        print(f"[warn] Upload from '{client_ip}' disconnected before completing -- "
              f"nothing saved, phone will retry on next send.")
        raise HTTPException(status_code=499, detail="Client disconnected before upload completed")

    body_hash = hashlib.sha256(body_bytes).hexdigest()
    expected_payload = f"{x_device_id}:{x_timestamp}:{body_hash}"
    expected_sig = hmac.new(device["secret"].encode(), expected_payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_sig, x_signature):
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid signature")

    device_name = _safe_name(device["deviceName"])
    app_name = _safe_name(device.get("appName", "app"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # received_data/<phone name>/<app name>/<timestamp>/ -- keeps every
    # phone's data, and every generated app's data, in its own folder even
    # when many phones send to the same PC.
    device_dir = DATA_DIR / device_name / app_name
    device_dir.mkdir(parents=True, exist_ok=True)

    out_zip = device_dir / f"{stamp}.zip"
    out_zip.write_bytes(body_bytes)

    extract_dir = device_dir / stamp
    try:
        with zipfile.ZipFile(out_zip) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Corrupt upload")

    print(f"[received] {len(body_bytes)} bytes from '{device['deviceName']}' / '{app_name}' -> {extract_dir}")

    manifest_path = extract_dir / "manifest.json"
    if manifest_path.exists():
        try:
            inspections = json.loads(manifest_path.read_text())
            rows = _flatten_manifest_rows(inspections, stamp, device_name=device["deviceName"])
            _append_to_master_excel(_app_master_dir(app_name), rows)
        except Exception as e:
            print(f"[warn] could not update master Excel for '{device_name}/{app_name}': {e}")

    with _lock:
        _recent_events.append({
            "deviceId": x_device_id,
            "deviceName": device["deviceName"],
            "appName": device.get("appName", "app"),
            "bytes": len(body_bytes),
            "receivedAtMs": int(time.time() * 1000),
        })
        del _recent_events[:-_MAX_RECENT_EVENTS]

    return {"status": "ok", "savedTo": str(extract_dir)}


@app.post("/heartbeat")
async def heartbeat(
    request: Request,
    x_device_id: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...),
):
    """A no-data liveness ping the phone sends every ~45s while its app is
    open and this PC is paired -- lets "Online" reflect real, recent
    reachability instead of only "did data arrive lately" (a phone that's
    connected fine but has nothing new to send would otherwise look
    indistinguishable from one that dropped off WiFi 20 minutes ago). Same
    HMAC scheme as /upload, just signed over deviceId:timestamp since
    there's no body to hash."""
    client_ip = request.client.host if request.client else "unknown"

    if _client_locked_out(client_ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts, try again later")

    device = _paired_devices.get(x_device_id)
    if not device:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Unknown device -- not paired with this PC")

    try:
        ts = int(x_timestamp) / 1000.0
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad timestamp")
    if abs(time.time() - ts) > SIGNATURE_WINDOW:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Request expired")

    expected_payload = f"{x_device_id}:{x_timestamp}"
    expected_sig = hmac.new(device["secret"].encode(), expected_payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, x_signature):
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid signature")

    with _lock:
        _last_heartbeat[x_device_id] = time.time()
        _save_heartbeats()

    return {"status": "ok"}


# ── Dashboard API (localhost only) ──────────────────────────────────────

@app.get("/api/status")
async def api_status(_: None = Depends(_require_local)):
    return {"pcName": PC_NAME, "ip": _local_ip(), "port": PORT, "serverTimeMs": int(time.time() * 1000)}


@app.get("/api/settings")
async def api_get_settings(_: None = Depends(_require_local)):
    error = _ensure_writable_dir(DATA_DIR)
    return {"dataDir": str(DATA_DIR), "writable": error is None, "error": error}


@app.post("/api/settings/browse")
async def api_browse_data_dir(_: None = Depends(_require_local)):
    """Opens a native folder-picker on the PC itself (this only makes sense
    called from a browser running on the same machine, which /api/* already
    requires) and returns the chosen path, if any, for the dashboard to fill
    into the folder field -- no manual typing/copy-pasting a path needed."""
    try:
        path = await asyncio.to_thread(_pick_folder_dialog)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open a folder picker on this PC ({e}). Type the folder path in manually instead.",
        )
    return {"path": path}


@app.post("/api/settings/data-dir")
async def api_set_data_dir(request: Request, _: None = Depends(_require_local)):
    global DATA_DIR, _config
    body = await request.json()
    raw_path = (body.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Folder path is required")

    new_path = Path(raw_path)
    error = _ensure_writable_dir(new_path)
    if error:
        raise HTTPException(status_code=400, detail=f"That folder isn't writable: {error}")

    DATA_DIR = new_path
    _config["dataDir"] = str(new_path)
    _save_config(_config)
    return {"status": "ok", "dataDir": str(DATA_DIR)}


@app.get("/api/events")
async def api_events(since: int = 0, _: None = Depends(_require_local)):
    """Uploads received after [since] (epoch ms) -- polled by the dashboard
    to trigger the "new data received" toast/animation without needing a
    full devices/storage refresh on every tick."""
    with _lock:
        events = [e for e in _recent_events if e["receivedAtMs"] > since]
    return {"events": events, "serverTimeMs": int(time.time() * 1000)}


def _dir_stats(path: Path):
    if not path.exists():
        return 0, 0
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


@app.get("/api/devices")
async def api_devices(_: None = Depends(_require_local)):
    result = []
    with _lock:
        items = list(_paired_devices.items())
    for device_id, d in items:
        device_name_safe = _safe_name(d["deviceName"])
        app_name_safe = _safe_name(d.get("appName", "app"))
        device_dir = DATA_DIR / device_name_safe / app_name_safe
        batch_dirs = sorted([p for p in device_dir.iterdir() if p.is_dir()]) if device_dir.exists() else []
        file_count, total_bytes = _dir_stats(device_dir)
        result.append({
            "deviceId": device_id,
            "deviceName": d["deviceName"],
            "appName": d.get("appName", "app"),
            "pairedAt": d["pairedAt"],
            "batchCount": len(batch_dirs),
            "fileCount": file_count,
            "totalBytes": total_bytes,
            "lastReceivedAt": batch_dirs[-1].name if batch_dirs else None,
            "hidden": _hidden_devices.get(_hidden_key(device_name_safe, app_name_safe), False),
        })
    result.sort(key=lambda d: d["pairedAt"], reverse=True)
    return result


@app.delete("/api/devices/{device_id}")
async def api_remove_device(device_id: str, _: None = Depends(_require_local)):
    with _lock:
        _merge_devices_from_disk()
        if device_id in _paired_devices:
            del _paired_devices[device_id]
            try:
                _save_devices()
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok"}


@app.post("/api/admin/device-data/hide")
async def api_admin_hide_device_data(request: Request, _: None = Depends(_require_local)):
    """Toggles whether [deviceName]/[appName] is hidden from the read-only
    Vault viewer (pc_receiver_viewer). Nothing on disk is touched or
    deleted -- this dashboard (and the Devices/Apps tabs) still shows it
    as normal; only the separate viewer process, which reads the hidden
    list out of DATA_DIR, filters it out. Safer than a hard delete for a
    test device you might still want the data for later."""
    body = await request.json()
    device_name = str(body.get("deviceName", ""))
    app_name = str(body.get("appName", ""))
    hidden = bool(body.get("hidden", True))
    safe_device = _safe_name(device_name)
    safe_app = _safe_name(app_name)
    if not safe_device or not safe_app:
        raise HTTPException(status_code=400, detail="deviceName and appName are required")

    key = _hidden_key(safe_device, safe_app)
    with _lock:
        if hidden:
            _hidden_devices[key] = True
        else:
            _hidden_devices.pop(key, None)
        try:
            _save_hidden_devices()
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "deviceName": device_name, "appName": app_name, "hidden": hidden}


@app.get("/api/admin/master-data")
async def api_admin_get_master_data(_: None = Depends(_require_local)):
    return {"masterData": _master_data}


@app.post("/api/admin/master-data")
async def api_admin_set_master_data(request: Request, _: None = Depends(_require_local)):
    """Accepts the same master-data JSON the app bundles into the APK at
    build time (a list of {platform_name, model_code, description} --
    pasted or uploaded here), and immediately re-applies it to every
    inspection already on disk, so Model Variant is filled in/corrected
    from the description column wherever the Model Code matches -- see
    _reconcile_master_data."""
    body = await request.json()
    raw = body.get("masterData")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="Expected {\"masterData\": [...]}")

    global _master_data
    normalized = _normalize_master_data(raw)
    if not normalized:
        raise HTTPException(status_code=400, detail="No valid rows found (each needs at least modelCode/model_code and description)")

    with _lock:
        _master_data = normalized
        _save_master_data()
        result = _reconcile_master_data()

    return {"status": "ok", "modelsLoaded": len(_master_data), **result}


@app.get("/api/storage")
async def api_storage(_: None = Depends(_require_local)):
    tree = {}
    if not DATA_DIR.exists():
        return tree
    for device_dir in sorted(DATA_DIR.iterdir()):
        if not device_dir.is_dir():
            continue
        if device_dir.name == "_master":
            # The app-wide aggregated workbooks (see _app_master_dir), not
            # a phone's raw per-batch uploads -- has no batch subfolders of
            # its own, so it wouldn't show up here anyway, but skip it
            # explicitly rather than relying on that.
            continue
        apps = {}
        for app_dir in sorted(device_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            batches = []
            for batch_dir in sorted(app_dir.iterdir(), reverse=True):
                if not batch_dir.is_dir():
                    continue
                file_count, size = _dir_stats(batch_dir)
                zip_path = app_dir / f"{batch_dir.name}.zip"
                batches.append({
                    "batch": batch_dir.name,
                    "fileCount": file_count,
                    "sizeBytes": size,
                    "downloadPath": f"{device_dir.name}/{app_dir.name}/{batch_dir.name}.zip" if zip_path.exists() else None,
                })
            if batches:
                apps[app_dir.name] = batches
        if apps:
            tree[device_dir.name] = apps
    return tree


@app.get("/api/storage/download")
async def api_storage_download(path: str, _: None = Depends(_require_local)):
    target = (DATA_DIR / path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target, filename=target.name)


def _resolve_under_data_dir(rel_path: str) -> Path:
    """Resolves a phone/app-relative path safely under DATA_DIR, or raises 404."""
    target = (DATA_DIR / rel_path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return target


@app.get("/api/device-data")
async def api_device_data(deviceId: str, _: None = Depends(_require_local)):
    """Every task-result row this device has ever sent, flattened for the
    data-viewer table -- one row per inspection task, with the image path
    already rewritten into a URL the browser can load directly."""
    with _lock:
        device = _paired_devices.get(deviceId)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown device")

    device_name = _safe_name(device["deviceName"])
    app_name = _safe_name(device.get("appName", "app"))
    device_dir = DATA_DIR / device_name / app_name

    rows = _flatten_device_rows_cached(device_dir, device["deviceName"], device_name, app_name)
    return {"deviceName": device["deviceName"], "appName": device.get("appName", "app"), "rows": rows}


@app.get("/api/apps")
async def api_apps(_: None = Depends(_require_local)):
    """Same idea as /api/devices, but grouped by app instead of by phone --
    every generated app that at least one currently-paired phone belongs
    to, with stats merged across every device paired under it. Apps
    aliased to each other (see _app_aliases) collapse into a single row,
    labeled with whichever app name was paired most recently."""
    with _lock:
        items = list(_paired_devices.values())

    by_app: dict[str, dict] = {}
    for d in items:
        app_name = d.get("appName", "app")
        app_name_safe = _resolve_app_alias(_safe_name(app_name))
        bucket = by_app.setdefault(app_name_safe, {"appName": app_name, "deviceNames": set(), "pairedAt": d["pairedAt"]})
        bucket["deviceNames"].add(d["deviceName"])
        if d["pairedAt"] >= bucket["pairedAt"]:
            bucket["appName"] = app_name  # label from whichever pairing is newest
        bucket["pairedAt"] = max(bucket["pairedAt"], d["pairedAt"])

    result = []
    for app_name_safe, bucket in by_app.items():
        device_dirs = _device_dirs_for_app(app_name_safe)
        batch_count = 0
        total_bytes = 0
        last_received = None
        for device_dir in device_dirs:
            batch_dirs = sorted([p for p in device_dir.iterdir() if p.is_dir()]) if device_dir.exists() else []
            batch_count += len(batch_dirs)
            _, dir_bytes = _dir_stats(device_dir)
            total_bytes += dir_bytes
            if batch_dirs and (last_received is None or batch_dirs[-1].name > last_received):
                last_received = batch_dirs[-1].name
        result.append({
            "appName": bucket["appName"],
            "appNameSafe": app_name_safe,
            "deviceCount": len(bucket["deviceNames"]),
            "pairedAt": bucket["pairedAt"],
            "batchCount": batch_count,
            "totalBytes": total_bytes,
            "lastReceivedAt": last_received,
        })
    result.sort(key=lambda a: a["pairedAt"], reverse=True)
    return result


@app.post("/api/apps/alias")
async def api_apps_alias(request: Request, _: None = Depends(_require_local)):
    """Merges [fromApp]'s data (past and future) into [toApp] -- for when
    two builds of what's really the same production app ended up with
    different names. Doesn't move or delete anything on disk; just makes
    every merge point (the Apps tab, /api/app-data, the master Excel)
    treat fromApp's folders as toApp's from now on."""
    body = await request.json()
    from_app = _safe_name(str(body.get("fromApp", "")))
    to_app = _safe_name(str(body.get("toApp", "")))
    if not from_app or not to_app:
        raise HTTPException(status_code=400, detail="fromApp and toApp are required")
    if from_app == to_app:
        raise HTTPException(status_code=400, detail="Can't merge an app into itself")
    # If [to_app] itself is already aliased elsewhere, point at its final
    # canonical target instead of creating a chain -- keeps every alias a
    # single hop, so nothing downstream needs to follow more than one link.
    to_app = _resolve_app_alias(to_app)
    if from_app == to_app:
        raise HTTPException(status_code=400, detail="These apps are already merged")
    with _lock:
        _app_aliases[from_app] = to_app
        # Anything that already aliased TO from_app now chains through it --
        # repoint those directly at the new canonical target too.
        for k, v in list(_app_aliases.items()):
            if v == from_app:
                _app_aliases[k] = to_app
        _save_app_aliases()
    return {"merged": True, "fromApp": from_app, "toApp": to_app}


@app.get("/api/apps/aliases")
async def api_apps_aliases_list(_: None = Depends(_require_local)):
    return _app_aliases


@app.delete("/api/apps/alias/{from_app}")
async def api_apps_alias_remove(from_app: str, _: None = Depends(_require_local)):
    """Un-merges an app -- its data (already-received and future) goes
    back to being counted under its own name instead of the app it was
    merged into. Nothing on disk moves either way."""
    with _lock:
        removed = _app_aliases.pop(_safe_name(from_app), None)
        if removed is not None:
            _save_app_aliases()
    if removed is None:
        raise HTTPException(status_code=404, detail="No such alias")
    return {"removed": True}


def _device_dirs_for_app(canonical_app_name_safe: str) -> list[Path]:
    """Every <device>/<appName> folder on disk that resolves (through
    _app_aliases, if any) to this canonical app -- not just currently
    -paired devices, so a device that was later unpaired or re-paired
    under a new name still contributes its historical data to the merged
    app view (same scope the master Excel already covers). Matches any
    on-disk app folder whose name aliases to the canonical one, not just
    an exact name match, so an aliased app's existing history is included
    immediately -- no file needs to move."""
    canonical_app_name_safe = _resolve_app_alias(canonical_app_name_safe)
    dirs = []
    if not DATA_DIR.exists():
        return dirs
    for device_dir in DATA_DIR.iterdir():
        if not device_dir.is_dir() or device_dir.name.startswith("_"):
            continue
        for app_dir in device_dir.iterdir():
            if not app_dir.is_dir():
                continue
            if _resolve_app_alias(app_dir.name) == canonical_app_name_safe:
                dirs.append(app_dir)
    return dirs


@app.get("/api/app-data")
async def api_app_data(appName: str, _: None = Depends(_require_local)):
    """Every task-result row every device has ever sent for this app,
    merged into one dataset -- the live-dashboard equivalent of the
    master Excel (_app_master_dir), which already aggregates by app
    rather than by device."""
    app_name_safe = _resolve_app_alias(_safe_name(appName))

    # Best-effort raw device name for display -- falls back to the safe
    # folder name for a device no longer in _paired_devices (unpaired,
    # or re-paired under a different name since).
    with _lock:
        safe_to_raw = {_safe_name(d["deviceName"]): d["deviceName"] for d in _paired_devices.values()}

    rows: list[dict] = []
    for app_dir in _device_dirs_for_app(app_name_safe):
        device_dir_safe = app_dir.parent.name
        device_name_raw = safe_to_raw.get(device_dir_safe, device_dir_safe)
        # app_dir.name (this device's real on-disk app folder) is used for
        # image URLs -- it may differ from the canonical app_name_safe once
        # aliased, and images physically live under the real folder name.
        rows.extend(_flatten_device_rows_cached(app_dir, device_name_raw, device_dir_safe, app_dir.name))

    rows.sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
    return {"appName": appName, "rows": rows}


@app.get("/api/export/master-excel-by-app")
async def api_export_master_excel_by_app(appName: str, _: None = Depends(_require_local)):
    """Same file /api/export/master-excel resolves to for any device paired
    under this app -- exposed without needing a deviceId so the Apps tab's
    "Download Full Excel" doesn't need one on hand."""
    app_name_safe = _safe_name(appName)
    xlsx_path = _app_master_dir(app_name_safe) / "data.xlsx"
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="No data received yet")
    return FileResponse(xlsx_path, filename=f"{app_name_safe}.xlsx")


def _flatten_device_rows_cached(device_dir: Path, device_name_raw: str, device_name_safe: str, app_name_safe: str) -> list[dict]:
    """Same output as re-scanning every batch under [device_dir], but only
    parses batch folders this cache hasn't seen before -- batches are
    immutable once written, so anything already parsed is reused as-is.
    See _rows_cache's declaration for why this exists."""
    cache_key = str(device_dir)
    if not device_dir.exists():
        return []

    batch_dirs = sorted(p for p in device_dir.iterdir() if p.is_dir())

    with _rows_cache_lock:
        entry = _rows_cache.get(cache_key)
        if entry is None:
            entry = {"batches": set(), "rows": [], "seen_keys": set()}
            _rows_cache[cache_key] = entry

        new_batch_dirs = [b for b in batch_dirs if b.name not in entry["batches"]]
        if not new_batch_dirs:
            return list(entry["rows"])

        for batch_dir in new_batch_dirs:
            manifest_path = batch_dir / "manifest.json"
            entry["batches"].add(batch_dir.name)
            if not manifest_path.exists():
                continue
            try:
                inspections = json.loads(manifest_path.read_text())
            except Exception:
                continue

            def _image_url(rel, _batch=batch_dir.name):
                if not rel:
                    return None
                return f"/api/storage/image?path={device_name_safe}/{app_name_safe}/{_batch}/{rel}"

            # A phone can legitimately re-send the same data it sent before
            # (a forced "Resync All", a retried upload, re-pairing after a
            # reinstall) -- each send lands in its own batch folder, so
            # without this the same inspection would show up once per
            # batch it was sent in.
            for row in _flatten_manifest_rows(inspections, batch_dir.name, device_name=device_name_raw):
                key = _dedup_key(row)
                if key in entry["seen_keys"]:
                    continue
                entry["seen_keys"].add(key)
                row["imageUrl"] = _image_url(row.pop("imagePath", None))
                row["backupImageUrl"] = _image_url(row.pop("backupImagePath", None))
                entry["rows"].append(row)

        entry["rows"].sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
        return list(entry["rows"])


@app.get("/api/export/master-excel")
async def api_export_master_excel(deviceId: str, _: None = Depends(_require_local)):
    """Downloads the single running Excel file for this app -- kept
    up to date automatically on every upload, so there's nothing to
    (re)generate: it's always current, including sends that arrived late
    after an earlier failed attempt. Still keyed by the dashboard's
    deviceId (so the existing per-device "Download Excel" buttons need no
    change), but resolved to the app-wide workbook shared by every phone
    paired under that same app -- see _app_master_dir -- rather than a
    file scoped to just this one device, so it's one continuous dataset
    per app regardless of how many phones (or re-pairings) contributed to
    it."""
    with _lock:
        device = _paired_devices.get(deviceId)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown device")
    app_name = _safe_name(device.get("appName", "app"))
    xlsx_path = _app_master_dir(app_name) / "data.xlsx"
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="No data received yet")
    return FileResponse(xlsx_path, filename=f"{app_name}.xlsx")


@app.get("/api/storage/image")
async def api_storage_image(path: str, _: None = Depends(_require_local)):
    target = _resolve_under_data_dir(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


@app.post("/api/export/excel")
async def api_export_excel(request: Request, _: None = Depends(_require_local)):
    body = await request.json()
    rows = body.get("rows") or []
    title = _safe_name(body.get("title", "storage_bank_export"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([label for label, _key in EXCEL_COLUMNS])
    for row in rows:
        ws.append([row.get(key, "") for _label, key in EXCEL_COLUMNS])
    _style_worksheet(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{title}.xlsx"'},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: None = Depends(_require_local)):
    return DASHBOARD_HTML


# ── mDNS advertisement ──────────────────────────────────────────────────

def start_mdns():
    zeroconf = Zeroconf()
    ip = _local_ip()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{PC_NAME}.{SERVICE_TYPE}",
        addresses=[ipaddress.IPv4Address(ip).packed],
        port=PORT,
        properties={"name": PC_NAME},
    )
    try:
        zeroconf.register_service(info)
    except Exception as e:
        # A name collision (e.g. a previous run of this app that didn't
        # shut down cleanly and left a stale registration on the network,
        # or another device already advertising the same name) used to
        # crash the whole app before the dashboard even opened. mDNS is
        # only used for the "PCs visible on this network" convenience
        # discovery feature -- pairing and uploads work fine without it,
        # since the pairing QR code embeds this PC's IP directly -- so a
        # failure here should never stop the app from starting.
        print(f"[warn] mDNS advertising failed ({e}) -- the app will still work, "
              f"but this PC won't show up in phone-side network discovery.")
    return zeroconf


# ── Dashboard HTML (self-contained, no external assets/CDN) ────────────────

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mahindra Digital Eye Vault</title>
<link rel="icon" type="image/png" href="data:image/png;base64,__FAVICON_B64__">
<style>
  :root {
    --crimson: #DC143C;
    --crimson-dark: #B01030;
    --navy: #151923;
    --navy-light: #1f2430;
    --bg: #f7f7fa;
    --card: #ffffff;
    --border: #e6e6ec;
    --text: #1c1f26;
    --muted: #6b7280;
    --orange: #e0821e;
    --green: #1f9d55;
    /* EYE lettering in the logo: E1 red, Y reddish-orange, E2 gold -- the
       app-name plate next to the logo reuses these so each app carries the
       same color as its letter (Receiver = E1, Viewer = Y, next app built
       = E2, reserved). */
    --eye-red: #f30222;
    --eye-orange: #f85813;
    --eye-gold: #fdaf04;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg); color: var(--text); }
  header { background: var(--card); color: var(--text); padding: 14px 24px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--border); }
  header img { height: 88px; margin: 6px 0; }
  header .titles { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
  header .titles p { margin: 0; font-size: 11px; color: var(--muted); max-width: 380px; }
  .app-plate {
    display: inline-flex; align-items: center;
    padding: 4px 12px; border-radius: 5px;
    font-size: 12px; font-weight: 800; letter-spacing: 0.8px;
    color: #fff; white-space: nowrap;
  }
  .plate-recv { background: var(--eye-red); }
  .plate-view { background: var(--eye-orange); }
  .plate-future { background: var(--eye-gold); }

  nav { display: flex; gap: 4px; padding: 12px 24px 0; background: var(--bg); }
  nav button { border: none; background: transparent; padding: 10px 18px; font-size: 13px; font-weight: 600; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; }
  nav button.active { color: var(--crimson); border-bottom-color: var(--crimson); }

  main { padding: 20px 24px 40px; max-width: 980px; margin: 0 auto; }
  .tab { display: none; }
  .tab.active { display: block; }

  .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .toolbar h2 { font-size: 15px; margin: 0; }
  button.primary { background: var(--crimson); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; }
  button.primary:hover { background: var(--crimson-dark); }
  button.ghost { background: transparent; border: 1px solid var(--border); color: var(--muted); padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  button.ghost:hover { border-color: var(--crimson); color: var(--crimson); }
  /* For a .ghost button sitting on the dark navy header (e.g. the
     viewer-page's Back button) -- the default .ghost styling (transparent
     bg, faint border, muted-gray text) was tuned for a light card and all
     but disappeared on that dark background. This gives it an actual
     visible pill instead of just barely-there text. */
  button.ghost-dark { background: rgba(220,20,60,0.18); border: 1px solid var(--crimson); color: #fff; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  button.ghost-dark:hover { background: var(--crimson); border-color: var(--crimson); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  button:disabled:hover { border-color: var(--border); color: var(--muted); }
  button.icon-btn { background: transparent; border: 1px solid var(--border); color: var(--muted); width: 30px; height: 30px; border-radius: 50%; cursor: pointer; font-size: 13px; line-height: 1; }
  button.icon-btn:hover { border-color: var(--crimson); color: var(--crimson); }

  .empty { text-align: center; padding: 48px 12px; color: var(--muted); font-size: 13px; }

  /* Data viewer -- still a fixed full-screen overlay (it sits above the
     dashboard), but the overlay itself scrolls as one normal page now
     instead of stacking header/filters/charts as fixed-height flex items
     above a table-wrap squeezed into whatever height is left over
     (flex:1; overflow:auto). That squeeze meant a bigger charts panel, or
     simply a short window, could shrink the table down to a single
     visible row. Scrolling the whole overlay avoids that regardless of
     how much the charts panel above it grows. */
  .viewer-page { position: fixed; inset: 0; background: var(--bg); z-index: 40; display: none; flex-direction: column; overflow-y: auto; }
  .viewer-page.open { display: flex; }
  .viewer-header { background: var(--navy); color: #fff; padding: 14px 24px; display: flex; align-items: center; gap: 14px; position: sticky; top: 0; z-index: 3; }
  .viewer-header h2 { margin: 0; font-size: 15px; }
  .viewer-header p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  .viewer-header .spacer { flex: 1; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
  .filters input, .filters select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
  .filters input { width: 130px; }
  .filter-count { font-size: 11px; color: var(--muted); margin-left: auto; align-self: center; white-space: nowrap; }
  .table-wrap { padding: 0 24px 24px; }
  table.data-table { width: 100%; border-collapse: collapse; background: var(--card); font-size: 12px; }
  /* No longer position:sticky -- the table's scrolling ancestor is now the
     whole .viewer-page overlay (see above), which also has the sticky
     .viewer-header in it; a sticky thead at the same top:0 would fight the
     header for the same spot. Losing the pinned header while scrolling is
     an acceptable trade for never squeezing the table itself down to a
     sliver. */
  table.data-table thead th { background: #fafafc; border-bottom: 2px solid var(--border); padding: 10px 10px; text-align: left; white-space: nowrap; }
  table.data-table tbody td { padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  table.data-table tbody tr:hover { background: #fbfbfd; }
  .badge-ok { color: var(--green); font-weight: 700; }
  .badge-fail { color: var(--crimson); font-weight: 700; }
  .img-link { color: var(--crimson); cursor: pointer; text-decoration: underline; font-size: 11px; background: none; border: none; padding: 0; }
  .img-link:disabled { color: var(--muted); text-decoration: none; cursor: default; }

  /* Grouped VIN rows with expand/collapse */
  .group-row { cursor: pointer; }
  .group-row .chevron { display: inline-block; transition: transform 0.15s; color: var(--muted); }
  .group-row.expanded .chevron { transform: rotate(90deg); }
  .task-count-ok { color: var(--green); font-weight: 700; }
  .task-count-fail { color: var(--crimson); font-weight: 700; }
  .detail-row { display: none; background: #fafafc; }
  .detail-row.open { display: table-row; }
  .detail-row td { padding: 10px 10px 14px 34px !important; }
  table.mini-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  table.mini-table th { text-align: left; padding: 4px 8px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); }
  table.mini-table td { padding: 5px 8px; border-bottom: 1px solid #f0f0f3; }

  /* Pie chart panel */
  .charts-panel { padding: 4px 24px 4px; max-height: 280px; overflow-y: auto; flex-shrink: 0; border-bottom: 1px solid var(--border); }
  /* Sections (Overall, By Shift, By VIN...) flow left-to-right, centered as
     a group, and only wrap to the next line when they actually run out of
     horizontal room, instead of each one claiming a full-width row
     regardless of how few cards it holds or being pinned to the left edge. */
  .charts-flow { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: center; gap: 22px; }
  .chart-row { margin-bottom: 10px; }
  /* Only sections marked as the start of a new group (task-level Overall
     charts vs. VIN-level charts vs. the By VIN/Day/Month extras) get a
     divider -- not every section, so "Overall Result" and "Overall Result
     by Shift" sit together with no line between them, then one divider,
     then "VIN Result (Pass/Fail)" and "VIN Result by Shift" together. */
  .chart-row.group-start { border-left: 1px dashed var(--border); padding-left: 22px; }
  .chart-row-title { font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.4px; display: flex; align-items: center; gap: 6px; }
  .chart-close { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 12px; padding: 0 2px; line-height: 1; }
  .chart-close:hover { color: var(--crimson); }
  .chart-cards { display: flex; gap: 14px; flex-wrap: wrap; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; text-align: center; width: 118px; }
  /* Headline totals (Overall Result, VIN Result) read bigger than their
     per-shift breakdowns, so the "main" number is visually distinct from
     the supporting detail underneath it. */
  .chart-card.chart-card-lg { width: 138px; padding: 14px; }
  .chart-card.chart-card-lg .chart-label { font-size: 13px; }
  .chart-card.chart-card-lg .chart-meta { font-size: 11px; }
  .chart-card.chart-card-sm { width: 88px; padding: 6px; }
  .chart-card.chart-card-sm .chart-label { font-size: 10px; }
  .chart-card.chart-card-sm .chart-meta { font-size: 9px; }
  .chart-card.clickable-chart { cursor: pointer; transition: box-shadow 0.15s, border-color 0.15s; }
  .chart-card.clickable-chart:hover { border-color: var(--crimson); box-shadow: 0 2px 8px rgba(220,20,60,0.15); }
  .chart-card .chart-label { font-size: 11px; font-weight: 700; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chart-card .chart-meta { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .chart-note { font-size: 12px; color: var(--muted); font-style: italic; max-width: 280px; }
  .chart-hidden-bar { font-size: 11px; color: var(--muted); margin-bottom: 10px; display: flex; align-items: center; gap: 10px; }
  .chart-hidden-bar b { color: var(--text); }
  .chart-hidden-bar button.ghost { padding: 3px 10px; font-size: 11px; }

  .lightbox { position: fixed; inset: 0; background: rgba(10,12,18,0.85); display: none; align-items: center; justify-content: center; z-index: 70; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 90vw; max-height: 85vh; border-radius: 8px; }
  .lightbox .lb-close { position: absolute; top: 20px; right: 28px; color: #fff; font-size: 28px; cursor: pointer; background: none; border: none; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
  .card .avatar { width: 42px; height: 42px; border-radius: 50%; background: #fdeaea; color: var(--crimson); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; flex-shrink: 0; }
  .card .info { flex: 1; min-width: 0; }
  .card .info .name { font-weight: 700; font-size: 14px; }
  .card .info .meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .card .stats { display: flex; gap: 18px; font-size: 11px; color: var(--muted); text-align: center; }
  .card .stats b { display: block; font-size: 14px; color: var(--text); }

  /* Devices grouped by phone, each with one or more paired apps */
  .device-group { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
  .device-group .dg-head { padding: 14px 16px; display: flex; align-items: center; gap: 14px; background: #fafafc; border-bottom: 1px solid var(--border); }
  .device-group .dg-head .name { font-weight: 700; font-size: 14px; }
  .device-group .dg-head .meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .device-group .dg-apps { padding: 10px 16px; }
  .app-row { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; transition: background 0.4s; }
  .app-row:last-child { margin-bottom: 0; }
  .app-row .a-name { font-weight: 600; font-size: 13px; }
  .app-row .a-meta { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .app-row .a-info { flex: 1; min-width: 0; }
  .app-row .a-stats { display: flex; gap: 16px; font-size: 11px; color: var(--muted); text-align: center; }
  .app-row .a-stats b { display: block; font-size: 13px; color: var(--text); }
  .app-row.pulse, .card.pulse { animation: rowPulse 1.8s ease-out; }
  @keyframes rowPulse {
    0%   { background: #ffeef0; box-shadow: 0 0 0 0 rgba(220,20,60,0.35); }
    100% { background: var(--card); box-shadow: 0 0 0 16px rgba(220,20,60,0); }
  }

  .card.clickable { cursor: pointer; }
  .card.clickable:hover { border-color: var(--crimson); }
  .card .chevron { color: var(--muted); font-size: 18px; margin-left: 4px; }

  .breadcrumb { font-size: 12px; color: var(--muted); margin-bottom: 12px; }
  .breadcrumb .crumb-link { color: var(--crimson); cursor: pointer; text-decoration: underline; }
  .breadcrumb .crumb-sep { margin: 0 6px; }
  .breadcrumb .crumb-current { color: var(--text); font-weight: 600; }

  /* Header "receiving" pulse, flashed briefly on new uploads */
  .live-indicator { display: none; align-items: center; gap: 6px; font-size: 11px; color: var(--crimson-dark); margin-left: auto; }
  .live-indicator.show { display: flex; }
  .live-indicator .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--crimson); animation: dotPulse 1s infinite; }
  @keyframes dotPulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.4); } }

  .accordion { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
  .accordion > .head { padding: 12px 16px; font-weight: 700; font-size: 13px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; background: #fafafc; }
  .accordion > .body { padding: 4px 16px 12px; }
  .app-group { margin-top: 8px; }
  .app-group .app-name { font-size: 12px; font-weight: 700; color: var(--crimson); margin: 8px 0 6px; }
  .batch-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; font-size: 12px; }
  .batch-row .b-name { font-weight: 600; flex: 1; }
  .batch-row .b-meta { color: var(--muted); }

  /* Modal */
  .overlay { position: fixed; inset: 0; background: rgba(10,12,18,0.55); display: none; align-items: center; justify-content: center; z-index: 50; }
  .overlay.open { display: flex; }
  .modal { background: #fff; border-radius: 14px; padding: 24px; width: 340px; text-align: center; }
  .modal h3 { margin: 0 0 6px; font-size: 15px; }
  .modal p { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .modal img { width: 220px; height: 220px; border: 1px solid var(--border); border-radius: 8px; }
  .modal .waiting { margin-top: 14px; font-size: 12px; color: var(--muted); display: flex; align-items: center; justify-content: center; gap: 8px; }
  .spinner { width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--crimson); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .modal .success { color: var(--green); font-weight: 700; margin-top: 14px; }
  .modal .close-btn { margin-top: 16px; display: flex; gap: 8px; justify-content: center; }

  .settings-modal { width: 420px; text-align: left; }
  .settings-modal p { text-align: left; }
  .settings-current { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 14px; }
  .settings-current-label { font-size: 11px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
  .settings-current-path { font-size: 13px; font-weight: 600; margin-top: 3px; word-break: break-all; }
  .settings-current-status { font-size: 12px; margin-top: 4px; font-weight: 600; }
  .settings-current-status.ok { color: var(--green); }
  .settings-current-status.bad { color: var(--crimson); }
  .settings-field-label { display: block; font-size: 12px; font-weight: 600; margin-bottom: 6px; }
  .settings-path-row { display: flex; gap: 8px; }
  #settingsPathInput { flex: 1; min-width: 0; box-sizing: border-box; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; }
  #settingsBrowseBtn { white-space: nowrap; }
  .settings-msg { font-size: 12px; margin-top: 10px; min-height: 16px; }
  .settings-msg.ok { color: var(--green); font-weight: 600; }
  .settings-msg.bad { color: var(--crimson); font-weight: 600; }
  .settings-banner { background: #fff4e5; border-bottom: 1px solid #f0c987; color: #8a5a00; font-size: 13px; font-weight: 600; padding: 10px 24px; cursor: pointer; text-align: center; }
  .settings-banner:hover { background: #ffe9c7; }

  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: var(--navy); color: #fff; padding: 10px 18px; border-radius: 8px; font-size: 12px; opacity: 0; pointer-events: none; transition: opacity 0.2s; z-index: 60; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <img src="data:image/png;base64,__LOGO_B64__" alt="Mahindra Digital Eye Vault">
  <div class="titles">
    <span class="app-plate plate-recv">Receiver</span>
    <p>Receives inspection data from paired phones on this WiFi/hotspot</p>
  </div>
  <div class="live-indicator" id="liveIndicator"><span class="dot"></span>Receiving…</div>
  <button class="ghost" style="margin-left:10px;" onclick="openSettingsModal()">⚙ Settings</button>
</header>

<div class="settings-banner" id="settingsBanner" style="display:none;" onclick="openSettingsModal()">
  ⚠ Storage folder isn't writable -- data can't be saved. Click here to choose a different folder.
</div>

<nav>
  <button class="tab-btn" data-tab="devices">Devices</button>
  <button class="tab-btn active" data-tab="apps">Apps</button>
  <button class="tab-btn" data-tab="storage">Vault</button>
  <button class="tab-btn" data-tab="admin">Admin Edit</button>
</nav>

<main>
  <section id="tab-devices" class="tab">
    <div class="toolbar">
      <h2>Paired Devices</h2>
      <button class="primary" onclick="openPairModal()">+ Add New Device</button>
    </div>
    <div class="breadcrumb" id="devicesBreadcrumb"></div>
    <div id="devicesList"><div class="empty">Loading…</div></div>
  </section>

  <section id="tab-apps" class="tab active">
    <div class="toolbar">
      <h2>Apps</h2>
    </div>
    <p style="font-size:12px;color:var(--muted);margin:-6px 0 14px;">Same data as Devices, merged across every phone paired under each app -- use this when multiple phones share one app and you want one continuous dataset instead of switching between devices.</p>
    <div id="appsList"><div class="empty">Loading…</div></div>
    <div id="mergedAppsPanel"></div>
  </section>

  <section id="tab-storage" class="tab">
    <div class="toolbar">
      <h2>Vault</h2>
      <button class="ghost" onclick="loadStorage()">Refresh</button>
    </div>
    <div id="storageList"><div class="empty">Loading…</div></div>
  </section>

  <section id="tab-admin" class="tab">
    <div class="toolbar">
      <h2>Admin Edit</h2>
    </div>

    <div class="card" style="display:block;padding:16px;margin-bottom:18px;">
      <h3 style="margin:0 0 6px;">Master Data (Model Code → Variant)</h3>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px;max-width:640px;">
        Paste or upload the same master data JSON used when building the app
        (a list of <code>{ platform_name, model_code, description }</code>).
        Matching rows already received get their Model Variant filled in --
        or overwritten, if it differs -- from the description column here.
      </p>
      <div id="masterDataStatus" style="font-size:12px;color:var(--muted);margin-bottom:10px;">Loading…</div>
      <textarea id="masterDataInput" rows="8" style="width:100%;max-width:640px;font-family:monospace;font-size:12px;" placeholder='[{"platform_name":"...","model_code":"MC1","description":"..."}]'></textarea>
      <div style="display:flex;gap:10px;align-items:center;margin-top:10px;">
        <input type="file" id="masterDataFile" accept="application/json" onchange="loadMasterDataFile(event)">
        <button class="primary" onclick="applyMasterData()">Apply Master Data</button>
      </div>
    </div>

    <div class="card" style="display:block;padding:16px;">
      <h3 style="margin:0 0 6px;">Hide Device Data</h3>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px;max-width:640px;">
        Hides a phone/app's data from the read-only Vault viewer -- e.g. a
        test device whose data isn't meant for whoever's looking at the
        Vault. Nothing is deleted: it stays here in this dashboard as
        normal, and can be unhidden any time.
      </p>
      <div id="adminDeviceList"><div class="empty">Loading…</div></div>
    </div>
  </section>
</main>

<div class="overlay" id="pairOverlay">
  <div class="modal">
    <h3>Pair a New Phone</h3>
    <p>Open the app → Send to PC → Pair New PC, and scan this code.</p>
    <img id="pairQrImg" src="" alt="QR code">
    <div class="waiting" id="pairWaiting"><div class="spinner"></div> Waiting for phone to scan…</div>
    <div class="success" id="pairSuccess" style="display:none;"></div>
    <div class="close-btn"><button class="ghost" onclick="closePairModal()">Close</button></div>
  </div>
</div>

<div class="overlay" id="settingsOverlay">
  <div class="modal settings-modal">
    <h3>Storage Folder</h3>
    <p>Where received inspection data (photos, zips, per-app Excel files) is saved on this PC. Can be a local folder or a network/shared drive this account has write access to.</p>
    <div class="settings-current">
      <div class="settings-current-label">Current folder</div>
      <div class="settings-current-path" id="settingsCurrentPath">Loading…</div>
      <div class="settings-current-status" id="settingsCurrentStatus"></div>
    </div>
    <label class="settings-field-label" for="settingsPathInput">Change to</label>
    <div class="settings-path-row">
      <input id="settingsPathInput" type="text" placeholder="e.g. C:\PCReceiverData or S:\PCReceiverData">
      <button class="ghost" onclick="browseForFolder()" id="settingsBrowseBtn">Browse…</button>
    </div>
    <div class="settings-msg" id="settingsMsg"></div>
    <div class="close-btn">
      <button class="ghost" onclick="closeSettingsModal()">Close</button>
      <button class="primary" onclick="saveDataDir()">Save &amp; Use This Folder</button>
    </div>
  </div>
</div>

<div class="viewer-page" id="viewerPage">
  <div class="viewer-header">
    <button class="ghost-dark" onclick="closeDataViewer()">← Back</button>
    <div>
      <h2 id="viewerTitle">Device Data</h2>
      <p id="viewerSubtitle"></p>
    </div>
    <div class="spacer"></div>
    <button class="ghost-dark" onclick="downloadFullExcel()">Download Full Excel</button>
    <button class="primary" onclick="downloadExcel()">Download Excel (filtered)</button>
  </div>
  <div class="filters">
    <input id="fVin" list="dlVin" placeholder="Filter VIN…" oninput="renderTable()">
    <datalist id="dlVin"></datalist>
    <input id="fModel" list="dlModel" placeholder="Filter Model Code…" oninput="renderTable()">
    <datalist id="dlModel"></datalist>
    <input id="fModelName" list="dlModelName" placeholder="Filter Model Name…" oninput="renderTable()">
    <datalist id="dlModelName"></datalist>
    <input id="fModelVariant" list="dlModelVariant" placeholder="Filter Variant…" oninput="renderTable()">
    <datalist id="dlModelVariant"></datalist>
    <input id="fDate" type="date" title="Filter Date" onchange="renderTable()">
    <select id="fYear" onchange="renderTable()">
      <option value="">All Years</option>
    </select>
    <select id="fMonth" onchange="renderTable()">
      <option value="">All Months</option>
      <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
      <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
      <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
      <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
    </select>
    <select id="fShift" onchange="renderTable()">
      <option value="">All Shifts</option>
      <option value="A">Shift A</option>
      <option value="B">Shift B</option>
      <option value="C">Shift C</option>
    </select>
    <input id="fTask" list="dlTask" placeholder="Filter Task…" oninput="renderTable()">
    <datalist id="dlTask"></datalist>
    <input id="fClass" list="dlClass" placeholder="Filter Detected…" oninput="renderTable()">
    <datalist id="dlClass"></datalist>
    <select id="fResult" onchange="renderTable()">
      <option value="">All Results</option>
      <option value="OK">OK</option>
      <option value="NOT OK">NOT OK</option>
    </select>
    <input id="fBatch" list="dlBatch" placeholder="Filter Send/Batch…" oninput="renderTable()">
    <datalist id="dlBatch"></datalist>
    <select id="fDevice" onchange="renderTable()">
      <option value="">All Devices</option>
    </select>
    <button class="ghost" onclick="clearFilters()">Clear Filters</button>
    <div class="filter-count" id="filterCount"></div>
  </div>
  <div class="charts-panel" id="chartsPanel"></div>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th></th><th>VIN</th><th>Model Code</th><th>Model Name</th><th>Variant</th><th>Date</th><th>Time</th>
          <th>Shift</th><th>Tasks</th><th>Result</th><th>Batch</th><th>Device</th>
        </tr>
      </thead>
      <tbody id="viewerRows"></tbody>
    </table>
  </div>
</div>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <button class="lb-close" onclick="closeLightbox()">✕</button>
  <img id="lightboxImg" src="" alt="Inspection image">
</div>

<div class="toast" id="toast"></div>

<script>
let pairPollTimer = null;
let currentToken = null;

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'storage') loadStorage();
    if (btn.dataset.tab === 'apps') loadApps();
    if (btn.dataset.tab === 'admin') { loadMasterDataStatus(); loadAdminDeviceList(); }
  });
});

function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function initials(name) {
  return (name || '?').trim().slice(0, 2).toUpperCase();
}

// Batch folder names are "YYYYMMDD_HHMMSS" -- parse into a real Date so we
// can show/live-tick a relative "x minutes ago" instead of a raw timestamp.
function parseBatchTimestamp(name) {
  if (!name || name.length < 15) return null;
  const y = +name.slice(0, 4), mo = +name.slice(4, 6) - 1, d = +name.slice(6, 8);
  const h = +name.slice(9, 11), mi = +name.slice(11, 13), s = +name.slice(13, 15);
  const dt = new Date(y, mo, d, h, mi, s);
  return isNaN(dt.getTime()) ? null : dt;
}

function timeAgo(date) {
  if (!date) return 'no data received yet';
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 5) return 'just now';
  if (secs < 60) return secs + 's ago';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  return Math.floor(hrs / 24) + 'd ago';
}

let lastDevicesData = [];
// null = showing the phone list; a phone name = drilled into that phone's apps.
let currentPhone = null;

async function loadDevices() {
  try {
    const r = await fetch('/api/devices');
    lastDevicesData = await r.json();
    renderDevices();
  } catch (e) {
    document.getElementById('devicesList').innerHTML = '<div class="empty">Could not load devices.</div>';
  }
}

function groupByPhone() {
  const byPhone = {};
  for (const d of lastDevicesData) {
    (byPhone[d.deviceName] = byPhone[d.deviceName] || []).push(d);
  }
  return byPhone;
}

function openPhone(phone) {
  currentPhone = phone;
  renderDevices();
}

function backToPhones() {
  currentPhone = null;
  renderDevices();
}

// Devices → Phone → Apps → (View Data button) → actual data table.
function renderDevices() {
  const el = document.getElementById('devicesList');
  const crumb = document.getElementById('devicesBreadcrumb');

  if (!lastDevicesData.length) {
    el.innerHTML = '<div class="empty">No phones paired yet. Tap "+ Add New Device" to pair one.</div>';
    crumb.innerHTML = '';
    return;
  }

  const byPhone = groupByPhone();
  if (currentPhone && !byPhone[currentPhone]) currentPhone = null; // phone fully removed

  if (!currentPhone) {
    // Level 1: phone list.
    crumb.innerHTML = '<span class="crumb-current">All Phones</span>';
    el.innerHTML = Object.keys(byPhone).sort().map(phone => {
      const apps = byPhone[phone];
      const totalSends = apps.reduce((a, d) => a + d.batchCount, 0);
      const totalBytes = apps.reduce((a, d) => a + d.totalBytes, 0);
      const lastTs = apps.map(d => parseBatchTimestamp(d.lastReceivedAt)).filter(Boolean).sort((a, b) => b - a)[0];
      return `
        <div class="card clickable" data-phone="${phone}" onclick="openPhone('${phone.replace(/'/g, "\\'")}')">
          <div class="avatar">${initials(phone)}</div>
          <div class="info">
            <div class="name">${phone}</div>
            <div class="meta">${apps.length} app${apps.length === 1 ? '' : 's'} • Last received ${timeAgo(lastTs)}</div>
          </div>
          <div class="stats">
            <div><b>${totalSends}</b>sends</div>
            <div><b>${fmtBytes(totalBytes)}</b>size</div>
          </div>
          <span class="chevron">›</span>
        </div>
      `;
    }).join('');
    return;
  }

  // Level 2: apps for the selected phone.
  const apps = byPhone[currentPhone];
  crumb.innerHTML = `<span class="crumb-link" onclick="backToPhones()">All Phones</span><span class="crumb-sep">›</span><span class="crumb-current">${currentPhone}</span>`;
  el.innerHTML = apps.map(d => `
    <div class="app-row" data-device-id="${d.deviceId}">
      <div class="a-info">
        <div class="a-name">${d.appName}</div>
        <div class="a-meta">Paired ${new Date(d.pairedAt).toLocaleDateString()} • Last received ${timeAgo(parseBatchTimestamp(d.lastReceivedAt))}</div>
      </div>
      <div class="a-stats">
        <div><b>${d.batchCount}</b>sends</div>
        <div><b>${fmtBytes(d.totalBytes)}</b>size</div>
      </div>
      <button class="primary" onclick="openDataViewer('${d.deviceId}')">View Data</button>
      <button class="ghost" onclick="window.location='/api/export/master-excel?deviceId=${d.deviceId}'" ${d.batchCount === 0 ? 'disabled' : ''}>Download Excel</button>
      <button class="icon-btn" title="Remove pairing" onclick="removeDevice('${d.deviceId}', '${currentPhone} — ${d.appName}')">✕</button>
    </div>
  `).join('');
}

// Re-render every 20s from the already-fetched data so "Last received: Xm
// ago" keeps ticking without hitting the server again.
setInterval(renderDevices, 20000);

let lastAppsData = [];

async function loadApps() {
  try {
    const r = await fetch('/api/apps');
    lastAppsData = await r.json();
    renderApps();
  } catch (e) {
    document.getElementById('appsList').innerHTML = '<div class="empty">Could not load apps.</div>';
  }
}

function renderApps() {
  const el = document.getElementById('appsList');
  if (!lastAppsData.length) {
    el.innerHTML = '<div class="empty">No apps yet -- pair a phone under the Devices tab first.</div>';
    document.getElementById('mergedAppsPanel').innerHTML = '';
    return;
  }
  el.innerHTML = lastAppsData.map(a => `
    <div class="app-row" data-app-name="${a.appNameSafe}">
      <div class="a-info">
        <div class="a-name">${a.appName}</div>
        <div class="a-meta">${a.deviceCount} device${a.deviceCount === 1 ? '' : 's'} paired • Last received ${timeAgo(parseBatchTimestamp(a.lastReceivedAt))}</div>
      </div>
      <div class="a-stats">
        <div><b>${a.batchCount}</b>sends</div>
        <div><b>${fmtBytes(a.totalBytes)}</b>size</div>
      </div>
      <button class="primary" onclick="openAppDataViewer('${a.appNameSafe.replace(/'/g, "\\'")}', '${a.appName.replace(/'/g, "\\'")}')">View Data</button>
      <button class="ghost" onclick="window.location='/api/export/master-excel-by-app?appName=${encodeURIComponent(a.appNameSafe)}'" ${a.batchCount === 0 ? 'disabled' : ''}>Download Excel</button>
      <button class="icon-btn" title="Merge this app's data into another app" onclick="mergeApp('${a.appNameSafe.replace(/'/g, "\\'")}', '${a.appName.replace(/'/g, "\\'")}')">⇄</button>
    </div>
  `).join('');
  loadMergedApps();
}

// Two apps really being the same production line but paired under
// different names (a rename, a typo, a leftover "(Copy)") otherwise never
// merge, since the app name is the merge key everywhere. This lets an
// admin say "treat A's data as B's" without moving/deleting anything on
// disk -- see /api/apps/alias.
async function mergeApp(fromAppSafe, fromAppDisplay) {
  const others = lastAppsData.filter(a => a.appNameSafe !== fromAppSafe);
  if (!others.length) { alert('No other app to merge into yet.'); return; }
  const optionsText = others.map(a => a.appName).join('", "');
  const typed = prompt(
    `Merge "${fromAppDisplay}"'s data (past and future) into which app?\n\nType the exact name: "${optionsText}"`, ''
  );
  if (!typed || !typed.trim()) return;
  const target = others.find(a => a.appName.toLowerCase() === typed.trim().toLowerCase());
  if (!target) { alert('No app with that exact name -- nothing changed.'); return; }
  if (!confirm(`Merge "${fromAppDisplay}" into "${target.appName}"?\n\nNothing on disk moves -- this can be undone later from the same "⇄" menu.`)) return;
  try {
    const r = await fetch('/api/apps/alias', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fromApp: fromAppSafe, toApp: target.appNameSafe }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.status); }
    showToast(`Merged "${fromAppDisplay}" into "${target.appName}"`);
    loadApps();
  } catch (e) {
    alert('Merge failed: ' + e.message);
  }
}

async function loadMergedApps() {
  const panel = document.getElementById('mergedAppsPanel');
  try {
    const r = await fetch('/api/apps/aliases');
    const aliases = await r.json();
    const entries = Object.entries(aliases);
    if (!entries.length) { panel.innerHTML = ''; return; }
    const nameFor = (safe) => (lastAppsData.find(a => a.appNameSafe === safe)?.appName) || safe;
    panel.innerHTML = `
      <div class="toolbar" style="margin-top:18px;"><h2 style="font-size:14px;">Merged Apps</h2></div>
      ${entries.map(([from, to]) => `
        <div class="app-row">
          <div class="a-info"><div class="a-name">${nameFor(from)} → ${nameFor(to)}</div>
          <div class="a-meta">Data from "${nameFor(from)}" counts under "${nameFor(to)}"</div></div>
          <button class="ghost" onclick="unmergeApp('${from.replace(/'/g, "\\'")}')">Undo</button>
        </div>
      `).join('')}
    `;
  } catch (e) {
    panel.innerHTML = '';
  }
}

async function unmergeApp(fromAppSafe) {
  if (!confirm('Undo this merge? The app goes back to being counted under its own name.')) return;
  await fetch('/api/apps/alias/' + encodeURIComponent(fromAppSafe), { method: 'DELETE' });
  loadApps();
}

setInterval(() => { if (lastAppsData.length) loadApps(); }, 20000);

async function removeDevice(id, name) {
  if (!confirm(`Remove pairing for "${name}"? The phone will need to scan a new QR code to send data again.`)) return;
  await fetch('/api/devices/' + id, { method: 'DELETE' });
  showToast('Removed ' + name);
  loadDevices();
}

async function loadMasterDataStatus() {
  const el = document.getElementById('masterDataStatus');
  try {
    const r = await fetch('/api/admin/master-data');
    const d = await r.json();
    const data = d.masterData || [];
    el.textContent = data.length ? `${data.length} model(s) currently loaded.` : 'No master data loaded yet.';
    if (data.length && !document.getElementById('masterDataInput').value) {
      document.getElementById('masterDataInput').value = JSON.stringify(data, null, 2);
    }
  } catch (e) {
    el.textContent = 'Could not load master data status.';
  }
}

function loadMasterDataFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => { document.getElementById('masterDataInput').value = reader.result; };
  reader.readAsText(file);
}

async function applyMasterData() {
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById('masterDataInput').value);
  } catch (e) {
    alert('Not valid JSON: ' + e.message);
    return;
  }
  if (!Array.isArray(parsed)) {
    alert('Expected a JSON array of { platform_name, model_code, description }.');
    return;
  }
  try {
    const r = await fetch('/api/admin/master-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ masterData: parsed }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    showToast(`Applied ${d.modelsLoaded} model(s) -- ${d.rowsUpdated} row(s) updated.`);
    loadMasterDataStatus();
  } catch (e) {
    alert('Could not apply master data: ' + e.message);
  }
}

async function loadAdminDeviceList() {
  const el = document.getElementById('adminDeviceList');
  try {
    const r = await fetch('/api/devices');
    const devices = await r.json();
    if (!devices.length) {
      el.innerHTML = '<div class="empty">No paired devices.</div>';
      return;
    }
    el.innerHTML = devices.map(d => `
      <div class="app-row">
        <div class="a-info">
          <div class="a-name">${d.deviceName} — ${d.appName}</div>
          <div class="a-meta">${d.batchCount} send(s) • ${fmtBytes(d.totalBytes)}${d.hidden ? ' • Hidden from Vault viewer' : ''}</div>
        </div>
        <button class="ghost" onclick="toggleHideDeviceData('${d.deviceName.replace(/'/g, "\\'")}', '${d.appName.replace(/'/g, "\\'")}', ${!d.hidden})">${d.hidden ? 'Unhide' : 'Hide from Viewer'}</button>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Could not load devices.</div>';
  }
}

async function toggleHideDeviceData(deviceName, appName, hidden) {
  try {
    const r = await fetch('/api/admin/device-data/hide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deviceName, appName, hidden }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    showToast((hidden ? 'Hidden ' : 'Unhidden ') + deviceName + ' from the Vault viewer');
    loadAdminDeviceList();
  } catch (e) {
    alert('Could not update: ' + e.message);
  }
}

async function loadStorage() {
  const el = document.getElementById('storageList');
  try {
    const r = await fetch('/api/storage');
    const tree = await r.json();
    const deviceNames = Object.keys(tree);
    if (!deviceNames.length) {
      el.innerHTML = '<div class="empty">No data received yet.</div>';
      return;
    }
    el.innerHTML = deviceNames.map(dev => `
      <div class="accordion">
        <div class="head" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'">
          <span>${dev}</span>
          <span style="color:var(--muted);font-weight:400;">${Object.keys(tree[dev]).length} app(s)</span>
        </div>
        <div class="body">
          ${Object.keys(tree[dev]).map(appName => `
            <div class="app-group">
              <div class="app-name">${appName}</div>
              ${tree[dev][appName].map(b => `
                <div class="batch-row">
                  <span class="b-name">${b.batch}</span>
                  <span class="b-meta">${b.fileCount} files • ${fmtBytes(b.sizeBytes)}</span>
                  ${b.downloadPath ? `<button class="ghost" onclick="window.location='/api/storage/download?path=${encodeURIComponent(b.downloadPath)}'">Download</button>` : ''}
                </div>
              `).join('')}
            </div>
          `).join('')}
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Could not load storage.</div>';
  }
}

async function openPairModal() {
  document.getElementById('pairOverlay').classList.add('open');
  document.getElementById('pairWaiting').style.display = 'flex';
  document.getElementById('pairSuccess').style.display = 'none';
  document.getElementById('pairQrImg').src = '';
  try {
    const r = await fetch('/api/pair/start', { method: 'POST' });
    const d = await r.json();
    if (!r.ok || !d.qrImage) {
      // A server-side failure here (e.g. a missing dependency) used to
      // leave the modal stuck on a broken image and an endless "Waiting
      // for phone to scan..." spinner -- surface it as a real error and
      // close the modal instead.
      showToast(d.detail || 'Could not generate a pairing QR code');
      closePairModal();
      return;
    }
    currentToken = d.token;
    document.getElementById('pairQrImg').src = 'data:image/png;base64,' + d.qrImage;
    pollPairStatus();
  } catch (e) {
    showToast('Could not start pairing');
    closePairModal();
  }
}

function pollPairStatus() {
  clearInterval(pairPollTimer);
  pairPollTimer = setInterval(async () => {
    if (!currentToken) return;
    const r = await fetch('/api/pair/status?token=' + encodeURIComponent(currentToken));
    const d = await r.json();
    if (d.paired) {
      clearInterval(pairPollTimer);
      document.getElementById('pairWaiting').style.display = 'none';
      const s = document.getElementById('pairSuccess');
      s.style.display = 'block';
      s.textContent = `✓ Paired with ${d.deviceName} (${d.appName})`;
      loadDevices();
    }
  }, 2000);
}

function closePairModal() {
  document.getElementById('pairOverlay').classList.remove('open');
  clearInterval(pairPollTimer);
  currentToken = null;
}

// ── Settings (storage folder) ───────────────────────────────────────────

async function checkStorageBanner() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    document.getElementById('settingsBanner').style.display = d.writable ? 'none' : 'block';
  } catch (e) { /* dashboard still usable even if this check fails */ }
}

async function openSettingsModal() {
  document.getElementById('settingsOverlay').classList.add('open');
  document.getElementById('settingsPathInput').value = '';
  document.getElementById('settingsMsg').textContent = '';
  document.getElementById('settingsMsg').className = 'settings-msg';
  await refreshSettingsCurrent();
}

async function refreshSettingsCurrent() {
  const pathEl = document.getElementById('settingsCurrentPath');
  const statusEl = document.getElementById('settingsCurrentStatus');
  pathEl.textContent = 'Loading…';
  statusEl.textContent = '';
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    pathEl.textContent = d.dataDir;
    if (d.writable) {
      statusEl.textContent = '✓ Writable -- data is being saved here';
      statusEl.className = 'settings-current-status ok';
    } else {
      statusEl.textContent = '✕ Not writable: ' + (d.error || 'unknown error');
      statusEl.className = 'settings-current-status bad';
    }
    document.getElementById('settingsBanner').style.display = d.writable ? 'none' : 'block';
  } catch (e) {
    pathEl.textContent = '(could not load)';
  }
}

async function browseForFolder() {
  const btn = document.getElementById('settingsBrowseBtn');
  const msg = document.getElementById('settingsMsg');
  btn.disabled = true;
  btn.textContent = 'Waiting…';
  msg.textContent = 'A folder picker window opened -- check behind the browser if not visible.';
  msg.className = 'settings-msg';
  try {
    const r = await fetch('/api/settings/browse', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) {
      msg.textContent = d.detail || 'Could not open the folder picker.';
      msg.className = 'settings-msg bad';
    } else if (d.path) {
      document.getElementById('settingsPathInput').value = d.path;
      msg.textContent = 'Folder selected -- click Save & Use This Folder to confirm.';
    } else {
      msg.textContent = '';
    }
  } catch (e) {
    msg.textContent = 'Could not reach the app.';
    msg.className = 'settings-msg bad';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Browse…';
  }
}

function closeSettingsModal() {
  document.getElementById('settingsOverlay').classList.remove('open');
}

async function saveDataDir() {
  const input = document.getElementById('settingsPathInput');
  const msg = document.getElementById('settingsMsg');
  const path = input.value.trim();
  if (!path) {
    msg.textContent = 'Enter a folder path first.';
    msg.className = 'settings-msg bad';
    return;
  }
  msg.textContent = 'Checking...';
  msg.className = 'settings-msg';
  try {
    const r = await fetch('/api/settings/data-dir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const d = await r.json();
    if (!r.ok) {
      msg.textContent = d.detail || 'Could not use that folder.';
      msg.className = 'settings-msg bad';
      return;
    }
    msg.textContent = '✓ Saved -- now storing data here.';
    msg.className = 'settings-msg ok';
    input.value = '';
    await refreshSettingsCurrent();
    loadDevices();
  } catch (e) {
    msg.textContent = 'Could not reach the app.';
    msg.className = 'settings-msg bad';
  }
}

// ── Data viewer ──────────────────────────────────────────────────────────
let viewerRows = [];
let viewerDeviceLabel = '';
let currentViewerDeviceId = null;
let currentViewerAppNameSafe = null;
// Bumped on every openDataViewer/openAppDataViewer call so a slow,
// superseded fetch can tell it's no longer the latest request and drop its
// response instead of overwriting the screen with stale data from a
// device/app the user already clicked away from.
let viewerRequestSeq = 0;

async function openDataViewer(deviceId) {
  const seq = ++viewerRequestSeq;
  currentViewerDeviceId = deviceId;
  currentViewerAppNameSafe = null;
  pinnedDay = null;
  document.getElementById('viewerPage').classList.add('open');
  document.getElementById('viewerTitle').textContent = 'Loading…';
  document.getElementById('viewerRows').innerHTML = '';
  try {
    const r = await fetch('/api/device-data?deviceId=' + encodeURIComponent(deviceId));
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (seq !== viewerRequestSeq) return;
    viewerRows = d.rows;
    viewerDeviceLabel = `${d.deviceName} — ${d.appName}`;
    document.getElementById('viewerTitle').textContent = viewerDeviceLabel;
    document.getElementById('viewerSubtitle').textContent = `${viewerRows.length} task result(s) across all sends`;
    populateFilterSuggestions();
    clearFilters();
  } catch (e) {
    if (seq === viewerRequestSeq) document.getElementById('viewerTitle').textContent = 'Could not load data';
  }
}

async function openAppDataViewer(appNameSafe, appNameDisplay) {
  const seq = ++viewerRequestSeq;
  currentViewerDeviceId = null;
  currentViewerAppNameSafe = appNameSafe;
  pinnedDay = null;
  document.getElementById('viewerPage').classList.add('open');
  document.getElementById('viewerTitle').textContent = 'Loading…';
  document.getElementById('viewerRows').innerHTML = '';
  try {
    const r = await fetch('/api/app-data?appName=' + encodeURIComponent(appNameSafe));
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (seq !== viewerRequestSeq) return;
    viewerRows = d.rows;
    viewerDeviceLabel = `${appNameDisplay} (all devices)`;
    document.getElementById('viewerTitle').textContent = viewerDeviceLabel;
    document.getElementById('viewerSubtitle').textContent = `${viewerRows.length} task result(s) merged across every device paired under this app`;
    populateFilterSuggestions();
    clearFilters();
  } catch (e) {
    if (seq === viewerRequestSeq) document.getElementById('viewerTitle').textContent = 'Could not load data';
  }
}

function closeDataViewer() {
  document.getElementById('viewerPage').classList.remove('open');
}

// Fills each filter's <datalist> with the unique values actually present in
// this app's data, so typing a couple of characters suggests real matches
// (e.g. typing "MA3E" in the VIN filter offers the full VINs starting with
// that) instead of the user having to type an exact value from memory.
function populateFilterSuggestions() {
  const fields = [
    ['dlVin', 'vin'], ['dlModel', 'modelCode'], ['dlModelName', 'modelName'],
    ['dlModelVariant', 'modelVariant'],
    ['dlTask', 'taskName'], ['dlClass', 'className'], ['dlBatch', 'batch'],
  ];
  fields.forEach(([listId, key]) => {
    const unique = [...new Set(viewerRows.map(r => r[key]).filter(Boolean))].sort();
    document.getElementById(listId).innerHTML = unique.map(v => `<option value="${v}"></option>`).join('');
  });

  // Year dropdown: only years that actually have data, newest first.
  const years = [...new Set(viewerRows.map(r => (r.date || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  const yearSelect = document.getElementById('fYear');
  const keepYear = yearSelect.value;
  yearSelect.innerHTML = '<option value="">All Years</option>' + years.map(y => `<option value="${y}">${y}</option>`).join('');
  yearSelect.value = years.includes(keepYear) ? keepYear : '';

  // Device dropdown: only meaningfully different from "All Devices" in the
  // merged Apps view, but harmless (and a single option) in the per-device
  // view too -- so it's always populated the same way.
  const devices = [...new Set(viewerRows.map(r => r.deviceName).filter(Boolean))].sort();
  const deviceSelect = document.getElementById('fDevice');
  const keepDevice = deviceSelect.value;
  deviceSelect.innerHTML = '<option value="">All Devices</option>' + devices.map(d => `<option value="${d}">${d}</option>`).join('');
  deviceSelect.value = devices.includes(keepDevice) ? keepDevice : '';
}

function clearFilters() {
  ['fVin', 'fModel', 'fModelName', 'fModelVariant', 'fTask', 'fClass', 'fBatch'].forEach(id => document.getElementById(id).value = '');
  ['fDate', 'fYear', 'fMonth', 'fShift', 'fResult', 'fDevice'].forEach(id => document.getElementById(id).value = '');
  pinnedDay = null;
  renderTable();
}

// Date/Shift Date columns always show the row's real, unmodified date --
// but for grouping/filtering "by day", Shift C (00:00-07:00) is really the
// tail end of the previous day's shift (which started with that day's A/B),
// so it's counted against the previous day here without changing anything
// that's actually displayed or stored.
function shiftGroupDate(row) {
  if (row.shift !== 'C' || !row.date) return row.date;
  const [y, m, d] = row.date.split('-').map(Number);
  if (!y || !m || !d) return row.date;
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() - 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function getFilteredRows() {
  const vin = document.getElementById('fVin').value.toLowerCase();
  const model = document.getElementById('fModel').value.toLowerCase();
  const modelName = document.getElementById('fModelName').value.toLowerCase();
  const modelVariant = document.getElementById('fModelVariant').value.toLowerCase();
  const date = document.getElementById('fDate').value;
  const year = document.getElementById('fYear').value;
  const month = document.getElementById('fMonth').value;
  const shift = document.getElementById('fShift').value;
  const task = document.getElementById('fTask').value.toLowerCase();
  const cls = document.getElementById('fClass').value.toLowerCase();
  const result = document.getElementById('fResult').value;
  const batch = document.getElementById('fBatch').value.toLowerCase();
  const device = document.getElementById('fDevice').value;

  return viewerRows.filter(row => {
    const rowGroupDate = shiftGroupDate(row) || '';
    const rowYear = rowGroupDate.slice(0, 4);
    const rowMonth = rowGroupDate.slice(5, 7);
    return (!vin || (row.vin || '').toLowerCase().includes(vin)) &&
      (!model || (row.modelCode || '').toLowerCase().includes(model)) &&
      (!modelName || (row.modelName || '').toLowerCase().includes(modelName)) &&
      (!modelVariant || (row.modelVariant || '').toLowerCase().includes(modelVariant)) &&
      (!date || shiftGroupDate(row) === date) &&
      (!year || rowYear === year) &&
      (!month || rowMonth === month) &&
      (!pinnedDay || shiftGroupDate(row) === pinnedDay) &&
      (!shift || row.shift === shift) &&
      (!task || (row.taskName || '').toLowerCase().includes(task)) &&
      (!cls || (row.className || '').toLowerCase().includes(cls)) &&
      (!result || row.result === result) &&
      (!batch || (row.batch || '').toLowerCase().includes(batch)) &&
      (!device || row.deviceName === device);
  });
}

// Groups flat task rows into one row per inspection (VIN scanned once, may
// have several task results) so an "integrated" multi-task VIN shows as a
// single expandable row instead of N flat rows repeating the same VIN.
function groupRowsByInspection(rows) {
  const groups = {};
  const order = [];
  rows.forEach((r, idx) => {
    const key = `${r.batch}|${r.inspectionId ?? ''}|${r.vin}|${r.date}|${r.time}`;
    if (!groups[key]) {
      groups[key] = {
        key, vin: r.vin, modelCode: r.modelCode, modelName: r.modelName, modelVariant: r.modelVariant,
        date: r.date, time: r.time, shift: r.shift, batch: r.batch, device: r.deviceName, tasks: [],
      };
      order.push(key);
    }
    groups[key].tasks.push(r);
  });
  return order.map(k => groups[k]);
}

const _expandedGroups = new Set();

function toggleGroup(key) {
  const opening = !_expandedGroups.has(key);
  if (opening) _expandedGroups.add(key); else _expandedGroups.delete(key);
  renderTable();
  if (opening) {
    // With the charts panel taking up space above the table, a row's
    // expanded task list (and its "View" image buttons) can render below
    // the currently-visible/scrolled area, making it look like the image
    // option is missing rather than just scrolled out of sight. Bring it
    // into view automatically instead of requiring a manual scroll.
    const row = document.querySelector(`tr.detail-row[data-key="${CSS.escape(key)}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
  }
}

function renderTable() {
  const filtered = getFilteredRows();
  document.getElementById('filterCount').textContent = `${filtered.length} of ${viewerRows.length} row(s)`;
  renderCharts(filtered);

  const tbody = document.getElementById('viewerRows');
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;color:var(--muted);padding:24px;">No rows match these filters.</td></tr>';
    return;
  }

  const groups = groupRowsByInspection(filtered);
  tbody.innerHTML = groups.map(g => {
    const okCount = g.tasks.filter(t => t.result === 'OK').length;
    const failCount = g.tasks.length - okCount;
    const expanded = _expandedGroups.has(g.key);
    const taskSummary = g.tasks.length === 1
      ? `<span class="${okCount ? 'task-count-ok' : 'task-count-fail'}">${g.tasks[0].result}</span>`
      : `<span class="task-count-ok">${okCount} OK</span>${failCount ? ` / <span class="task-count-fail">${failCount} NOT OK</span>` : ''} (${g.tasks.length} tasks)`;
    // A VIN passes only if every one of its tasks is OK -- any single NOT
    // OK task (or more) fails the whole VIN.
    const vinResult = failCount === 0 ? 'PASS' : 'FAIL';

    const detailRows = g.tasks.map(t => `
      <tr>
        <td>${t.taskName || ''}</td>
        <td>${t.className || ''}</td>
        <td class="${t.result === 'OK' ? 'badge-ok' : 'badge-fail'}">${t.result || ''}</td>
        <td>${t.imageUrl ? `<button class="img-link" onclick="openLightbox('${t.imageUrl}')">View</button>` : '<button class="img-link" disabled>—</button>'}</td>
      </tr>
    `).join('');

    return `
      <tr class="group-row ${expanded ? 'expanded' : ''}" onclick="toggleGroup('${g.key.replace(/'/g, "\\'")}')">
        <td><span class="chevron">▸</span></td>
        <td>${g.vin || ''}</td>
        <td>${g.modelCode || ''}</td>
        <td>${g.modelName || ''}</td>
        <td>${g.modelVariant || ''}</td>
        <td>${g.date || ''}</td>
        <td>${g.time || ''}</td>
        <td>${g.shift || ''}</td>
        <td>${taskSummary}</td>
        <td class="${vinResult === 'PASS' ? 'badge-ok' : 'badge-fail'}">${vinResult}</td>
        <td>${g.batch || ''}</td>
        <td>${g.device || ''}</td>
      </tr>
      <tr class="detail-row ${expanded ? 'open' : ''}" data-key="${escapeAttr(g.key)}">
        <td colspan="12">
          <table class="mini-table">
            <thead><tr><th>Task</th><th>Detected</th><th>Result</th><th>Image</th></tr></thead>
            <tbody>${detailRows}</tbody>
          </table>
        </td>
      </tr>
    `;
  }).join('');
}

// ── Pie charts (hand-drawn SVG, no external chart library needed) ────────

function pieSvg(ok, fail, size) {
  size = size || 88;
  const total = ok + fail;
  const r = size / 2 - 4, cx = size / 2, cy = size / 2;
  if (total === 0) {
    return `<svg width="${size}" height="${size}"><circle cx="${cx}" cy="${cy}" r="${r}" fill="#eee"/></svg>`;
  }
  const p = ok / total;
  let slices;
  if (p >= 0.999) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#1f9d55"/>`;
  } else if (p <= 0.001) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#DC143C"/>`;
  } else {
    const toXY = (deg) => {
      const rad = (deg - 90) * Math.PI / 180;
      return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
    };
    const angle = p * 360;
    const [sx, sy] = toXY(0);
    const [ex, ey] = toXY(angle);
    const largeArc1 = angle > 180 ? 1 : 0;
    const greenPath = `M${cx},${cy} L${sx},${sy} A${r},${r} 0 ${largeArc1} 1 ${ex},${ey} Z`;
    const [sx2, sy2] = toXY(angle);
    const [ex2, ey2] = toXY(360);
    const largeArc2 = (360 - angle) > 180 ? 1 : 0;
    const redPath = `M${cx},${cy} L${sx2},${sy2} A${r},${r} 0 ${largeArc2} 1 ${ex2},${ey2} Z`;
    slices = `<path d="${greenPath}" fill="#1f9d55"/><path d="${redPath}" fill="#DC143C"/>`;
  }
  // Rounding to a whole percent can display "100%" (or "0%") even when
  // real failures (or passes) exist, once one side is a small enough
  // fraction of a large total -- e.g. 7891 OK / 18 NOT OK rounds to 100%.
  // Clamp away from the misleading extremes whenever the other side is
  // genuinely non-zero, so "100%" always means zero failures.
  let pct = Math.round(p * 100);
  if (fail > 0 && pct >= 100) pct = 99;
  if (ok > 0 && pct <= 0) pct = 1;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    ${slices}
    <circle cx="${cx}" cy="${cy}" r="${r * 0.55}" fill="white"/>
    <text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="#1c1f26">${pct}%</text>
  </svg>`;
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

// section/value make the card clickable -- clicking it applies the
// matching filter (e.g. click the "Shift A" pie to filter the table to
// Shift A). Omit them for a non-clickable card like Overall. okLabel/failLabel
// let a card describe something other than task OK/NOT OK counts (e.g. VIN
// Pass/Fail) while reusing the same green/red pie rendering.
// sizeClass: 'lg' for the single headline totals (Overall Result, VIN
// Result), 'sm' for their per-shift breakdowns -- same information density,
// but visually smaller so the headline number reads as the "main" figure
// and the per-shift ones read as supporting detail underneath it.
const CHART_PIE_PX = { lg: 100, sm: 62, '': 88 };

function chartCard(label, ok, fail, section, value, okLabel, failLabel, sizeClass) {
  okLabel = okLabel || 'OK';
  failLabel = failLabel || 'NOT OK';
  sizeClass = sizeClass || '';
  const sizeClassAttr = sizeClass ? ` chart-card-${sizeClass}` : '';
  const attrs = section
    ? ` data-section="${section}" data-value="${escapeAttr(value)}" class="chart-card${sizeClassAttr} clickable-chart" title="Click to filter to ${escapeAttr(label)}"`
    : ` class="chart-card${sizeClassAttr}"`;
  // Exact percentage (one decimal), shown as text alongside the raw counts
  // -- the donut itself always shows a whole number clamped away from a
  // misleading 100%/0%, so this is where the real, unrounded figure lives.
  const total = ok + fail;
  const exactPct = total > 0 ? (ok / total * 100).toFixed(1) : '0.0';
  return `<div${attrs}>${pieSvg(ok, fail, CHART_PIE_PX[sizeClass])}
    <div class="chart-label" title="${label}">${label}</div>
    <div class="chart-meta">${ok} ${okLabel} / ${fail} ${failLabel} (${exactPct}%)</div>
  </div>`;
}

function bucketize(rows, keyFn) {
  const buckets = {};
  for (const r of rows) {
    const k = keyFn(r);
    if (k === null || k === undefined || k === '') continue;
    if (!buckets[k]) buckets[k] = { ok: 0, fail: 0 };
    if (r.result === 'OK') buckets[k].ok++; else buckets[k].fail++;
  }
  return buckets;
}

// Pies work well for a handful of slices/cards, but not for dozens of
// buckets (e.g. every single day across a year) -- past this many cards in
// one row we show a note asking to narrow the filter instead of rendering
// an unreadable wall of tiny pies.
const MAX_PIE_BUCKETS = 12;

// Which day (shiftDate) a "By Day" pie click has drilled into, on top of
// whatever the regular filters are set to. Cleared via its own chip, not
// by other filter changes, so "this VIN in this exact shift-day" combos
// stay possible.
let pinnedDay = null;

// Chart sections the user has dismissed with the row's "✕" -- remembered
// across restarts (same browser) via localStorage, since this is a local,
// single-user dashboard.
let hiddenChartSections = new Set(JSON.parse(localStorage.getItem('hiddenChartSections') || '[]'));

// The four sections shown by default. Everything else (By VIN, By Day, By
// Month, ...) is folded away behind "Show All Charts" until asked for --
// also remembered across restarts.
const CORE_CHART_TITLES = new Set(['Overall (current filters)', 'Overall Result by Shift', 'VIN Result (Pass/Fail)', 'VIN Result by Shift']);
let extrasVisible = localStorage.getItem('chartExtrasVisible') === '1';

function hideChartSection(title) {
  hiddenChartSections.add(title);
  localStorage.setItem('hiddenChartSections', JSON.stringify([...hiddenChartSections]));
  renderTable();
}

function showAllChartSections() {
  hiddenChartSections.clear();
  localStorage.setItem('hiddenChartSections', '[]');
  extrasVisible = true;
  localStorage.setItem('chartExtrasVisible', '1');
  renderTable();
}

function hideExtraChartSections() {
  extrasVisible = false;
  localStorage.setItem('chartExtrasVisible', '0');
  renderTable();
}

function clearPinnedDay() {
  pinnedDay = null;
  renderTable();
}

// Which visual group a chart section belongs to -- used to draw one
// divider between groups (Overall | VIN Result | extras), not one before
// every single section.
function sectionFamily(title) {
  if (title.startsWith('Overall')) return 'overall';
  if (title.startsWith('VIN Result')) return 'vinresult';
  return 'extras'; // This VIN / By VIN / By Day / By Month
}

function renderCharts(filtered) {
  const el = document.getElementById('chartsPanel');
  if (!filtered.length) {
    el.innerHTML = '';
    return;
  }

  const overallOk = filtered.filter(r => r.result === 'OK').length;
  const overallFail = filtered.length - overallOk;

  const sections = [];
  sections.push({ title: 'Overall (current filters)', cards: [chartCard('All Results', overallOk, overallFail, null, null, null, null, 'lg')] });

  const byShift = bucketize(filtered, r => r.shift);
  const shiftKeys = Object.keys(byShift).sort();
  if (shiftKeys.length) {
    sections.push({ title: 'Overall Result by Shift', cards: shiftKeys.map(k => chartCard('Shift ' + k, byShift[k].ok, byShift[k].fail, 'shift', k, null, null, 'sm')) });
  }

  // VIN Result (Pass/Fail): one Pass/Fail per VIN *scan* (inspection), not
  // deduplicated by VIN string -- the same VIN scanned multiple times counts
  // once per scan. A scan passes only if every one of its tasks is OK.
  const inspectionGroups = groupRowsByInspection(filtered);
  let vinPass = 0, vinFail = 0;
  inspectionGroups.forEach(g => {
    const hasFail = g.tasks.some(t => t.result !== 'OK');
    if (hasFail) vinFail++; else vinPass++;
  });
  sections.push({
    title: 'VIN Result (Pass/Fail)',
    cards: [chartCard('All VIN Scans', vinPass, vinFail, null, null, 'Pass', 'Fail', 'lg')],
  });

  // VIN Result by Shift: the same VIN Pass/Fail-per-scan count as above,
  // but broken out per shift -- how many VINs passed/failed within Shift
  // A vs. B vs. C, not just the combined total.
  const vinResultByShift = {};
  inspectionGroups.forEach(g => {
    const shiftKey = g.shift || 'Unknown';
    if (!vinResultByShift[shiftKey]) vinResultByShift[shiftKey] = { pass: 0, fail: 0 };
    const hasFail = g.tasks.some(t => t.result !== 'OK');
    if (hasFail) vinResultByShift[shiftKey].fail++; else vinResultByShift[shiftKey].pass++;
  });
  const vinShiftKeys = Object.keys(vinResultByShift).sort();
  if (vinShiftKeys.length) {
    sections.push({
      title: 'VIN Result by Shift',
      cards: vinShiftKeys.map(k => chartCard(
        'Shift ' + k, vinResultByShift[k].pass, vinResultByShift[k].fail, 'shift', k, 'Pass', 'Fail', 'sm',
      )),
    });
  }

  const byVin = bucketize(filtered, r => r.vin);
  const vinKeys = Object.keys(byVin);
  if (vinKeys.length === 1) {
    sections.push({ title: 'This VIN', cards: [chartCard(vinKeys[0], byVin[vinKeys[0]].ok, byVin[vinKeys[0]].fail)] });
  } else if (vinKeys.length > 1 && vinKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By VIN', cards: vinKeys.map(k => chartCard(k, byVin[k].ok, byVin[k].fail, 'vin', k)) });
  } else if (vinKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By VIN', note: `${vinKeys.length} VINs in view -- filter down to ${MAX_PIE_BUCKETS} or fewer (e.g. one VIN, one shift, or one day) to see per-VIN pies.` });
  }

  // Uses shiftDate so a post-midnight Shift C scan groups with the shift
  // it belongs to, not the raw calendar date it happened to tick over into.
  const byDay = bucketize(filtered, shiftGroupDate);
  const dayKeys = Object.keys(byDay).sort();
  if (dayKeys.length && dayKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Day (shift-day)', cards: dayKeys.map(k => chartCard(k, byDay[k].ok, byDay[k].fail, 'day', k)) });
  } else if (dayKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Day (shift-day)', note: `${dayKeys.length} days in view -- pick a Month filter to see day-wise pies.` });
  }

  const byMonth = bucketize(filtered, r => (r.date || '').slice(0, 7));
  const monthKeys = Object.keys(byMonth).sort();
  if (monthKeys.length && monthKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Month', cards: monthKeys.map(k => chartCard(k, byMonth[k].ok, byMonth[k].fail, 'month', k)) });
  } else if (monthKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Month', note: `${monthKeys.length} months in view -- pick a Year filter to see month-wise pies.` });
  }

  // Only the four core sections are "in play" by default -- By VIN/Day/
  // Month etc. are folded away until "Show All Charts" is clicked.
  const inPlay = extrasVisible ? sections : sections.filter(s => CORE_CHART_TITLES.has(s.title));
  const foldedCount = extrasVisible ? 0 : sections.length - inPlay.length;
  const visible = inPlay.filter(s => !hiddenChartSections.has(s.title));
  const hidden = inPlay.filter(s => hiddenChartSections.has(s.title));

  let html = '';
  if (hidden.length) {
    html += `<div class="chart-hidden-bar">Hidden: ${hidden.map(s => s.title).join(', ')}
      <button class="ghost" onclick="showAllChartSections()">Show All</button></div>`;
  }
  if (!extrasVisible && foldedCount > 0) {
    html += `<div class="chart-hidden-bar">${foldedCount} more chart${foldedCount === 1 ? '' : 's'} available (By VIN, By Day, By Month...)
      <button class="ghost" onclick="showAllChartSections()">Show All Charts</button></div>`;
  } else if (extrasVisible) {
    html += `<div class="chart-hidden-bar"><button class="ghost" onclick="hideExtraChartSections()">Show Fewer</button></div>`;
  }
  if (pinnedDay) {
    html += `<div class="chart-hidden-bar">Pinned to day: <b>${pinnedDay}</b>
      <button class="ghost" onclick="clearPinnedDay()">✕ Clear</button></div>`;
  }
  html += '<div class="charts-flow">' + visible.map((s, i) => {
    const family = sectionFamily(s.title);
    const prevFamily = i > 0 ? sectionFamily(visible[i - 1].title) : null;
    const groupStart = i > 0 && family !== prevFamily;
    return `
    <div class="chart-row${groupStart ? ' group-start' : ''}">
      <div class="chart-row-title">${s.title}
        <button class="chart-close" title="Hide this chart" onclick="hideChartSection('${s.title.replace(/'/g, "\\'")}')">✕</button>
      </div>
      ${s.note ? `<div class="chart-note">${s.note}</div>` : `<div class="chart-cards">${s.cards.join('')}</div>`}
    </div>
  `;
  }).join('') + '</div>';
  el.innerHTML = html;

  el.querySelectorAll('.chart-card.clickable-chart').forEach(card => {
    card.addEventListener('click', () => {
      const section = card.dataset.section;
      const value = card.dataset.value;
      if (section === 'shift') {
        document.getElementById('fShift').value = value;
      } else if (section === 'vin') {
        document.getElementById('fVin').value = value;
      } else if (section === 'month') {
        const [y, m] = value.split('-');
        document.getElementById('fYear').value = y;
        document.getElementById('fMonth').value = m;
      } else if (section === 'day') {
        pinnedDay = value;
      }
      renderTable();
    });
  });
}

function openLightbox(url) {
  document.getElementById('lightboxImg').src = url;
  document.getElementById('lightbox').classList.add('open');
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightboxImg').src = '';
}

function downloadFullExcel() {
  if (currentViewerAppNameSafe) {
    window.location = '/api/export/master-excel-by-app?appName=' + encodeURIComponent(currentViewerAppNameSafe);
    return;
  }
  if (!currentViewerDeviceId) return;
  window.location = '/api/export/master-excel?deviceId=' + encodeURIComponent(currentViewerDeviceId);
}

async function downloadExcel() {
  const filtered = getFilteredRows();
  if (!filtered.length) { showToast('No rows to export'); return; }
  const r = await fetch('/api/export/excel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: filtered, title: viewerDeviceLabel.replace(/[^a-zA-Z0-9]+/g, '_') }),
  });
  if (!r.ok) { showToast('Export failed'); return; }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = viewerDeviceLabel.replace(/[^a-zA-Z0-9]+/g, '_') + '.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── Live "receiving" activity feed ──────────────────────────────────────
// Polls for uploads that landed since the page opened, then flashes the
// header indicator, toasts, glows the affected app row, and refreshes the
// device list/storage bank so counts and "last received" update live --
// no manual refresh needed while data comes in.
let eventsCursor = Date.now();

async function pollEvents() {
  try {
    const r = await fetch('/api/events?since=' + eventsCursor);
    const d = await r.json();
    eventsCursor = d.serverTimeMs;
    if (d.events && d.events.length) {
      handleNewEvents(d.events);
    }
  } catch (e) {}
}

function handleNewEvents(events) {
  const indicator = document.getElementById('liveIndicator');
  indicator.classList.add('show');
  setTimeout(() => indicator.classList.remove('show'), 2500);

  for (const ev of events) {
    showToast(`📥 New data from ${ev.deviceName} — ${ev.appName}`);
  }

  loadDevices().then(() => {
    // Pulse whichever level is currently showing: the phone card if we're
    // at the top level, or the specific app row if we've drilled into it.
    events.forEach(ev => {
      const selector = currentPhone === null
        ? `.card[data-phone="${CSS.escape(ev.deviceName)}"]`
        : (currentPhone === ev.deviceName ? `.app-row[data-device-id="${ev.deviceId}"]` : null);
      const el = selector && document.querySelector(selector);
      if (el) {
        el.classList.add('pulse');
        setTimeout(() => el.classList.remove('pulse'), 1800);
      }
    });
  });

  if (document.getElementById('tab-storage').classList.contains('active')) {
    loadStorage();
  }
}

loadDevices();
loadApps();
checkStorageBanner();
setInterval(loadDevices, 15000);
setInterval(pollEvents, 3000);
</script>
</body>
</html>
""".replace("__LOGO_B64__", LOGO_PNG_BASE64.replace("\n", "")).replace("__FAVICON_B64__", FAVICON_PNG_BASE64.replace("\n", ""))


def _port_available(port: int) -> bool:
    """Quick check so we don't open a browser tab pointing at a dashboard
    that's about to fail to start -- without this, a crash-restart loop
    (e.g. a stray already-running copy holding the port) opens a fresh
    browser tab on every single failed restart attempt, since the browser
    was previously opened unconditionally before ever trying to bind."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main():
    _load_devices()
    _load_app_aliases()
    _load_master_data()
    _load_heartbeats()
    _load_hidden_devices()
    zeroconf = start_mdns()

    ip = _local_ip()
    url = f"http://127.0.0.1:{PORT}"
    print("\nMahindra Digital Eye Vault")
    print(f"Dashboard (this PC only): {url}")
    print(f"Phones on this WiFi/hotspot send to: {ip}:{PORT}")
    print("Leave this window open while receiving data. Press Ctrl+C to quit.\n")

    if _port_available(PORT):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    else:
        print(f"[warn] Port {PORT} is already in use -- another copy of this app is likely "
              f"already running. Not opening a new browser tab; check Task Manager for a "
              f"second PCReceiver.exe if this keeps happening.")

    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    except OSError as e:
        # Most commonly a second copy of this exe already running and
        # holding the port -- without this, that crashed with a raw
        # traceback and (launched by double-click) the window closed
        # before anyone could read it, looking like the app just silently
        # refused to start at all.
        print(f"\n[error] Could not start on port {PORT}: {e}")
        print("        Check Task Manager for another PCReceiver.exe already running,")
        print(f"        or set PCRECEIVER_PORT to a different port if something else on this PC uses {PORT}.")
        raise
    finally:
        zeroconf.close()


if __name__ == "__main__":
    # Double-clicking the packaged .exe opens a console window that Windows
    # closes the instant this process exits -- whether that's a clean exit
    # or an unhandled exception. Without this, any startup failure (bad
    # storage folder, port already taken, an unexpected error) flashes a
    # traceback for a fraction of a second and vanishes, which looks
    # exactly like the app "crashing" for no visible reason. Catching
    # everything here and pausing for a keypress keeps the window open
    # long enough to actually read what happened.
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        import traceback
        print("\n[fatal] Mahindra Digital Eye Vault stopped unexpectedly:\n")
        traceback.print_exc()
        _pause_briefly()
