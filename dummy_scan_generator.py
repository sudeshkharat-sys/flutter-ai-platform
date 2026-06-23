"""Dummy Scan Generator for model code: PKN2DCWBS6PSCC5

How it works:
  - Takes model code PKN2DCWBS6PSCC5 (15 chars)
  - Splits it as:  PKN | 2DCWBS | 6PSCC5
  - Builds 17-char VINs by appending 2-digit serial:  PKN2DCWBS6PSCC5 + 01..N
  - Inserts master_model_mappings entry (if missing)
  - Inserts N dummy inspection_results rows

Usage:
  python dummy_scan_generator.py                      # 20 scans, prompts for app_id
  python dummy_scan_generator.py --count 10           # 10 scans
  python dummy_scan_generator.py --app-id <UUID>      # use specific app
  python dummy_scan_generator.py --list-apps          # list available app IDs
"""

import os
import sys
import uuid
import json
import random
import argparse
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.getcwd(), "backend"))

try:
    from app.config import get_settings
    settings = get_settings()
    POSTGRES_CONFIG = {
        "host": settings.POSTGRES_HOST,
        "port": settings.POSTGRES_PORT,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "database": settings.POSTGRES_DB,
    }
except ImportError:
    POSTGRES_CONFIG = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "password"),
        "database": os.getenv("POSTGRES_DB", "flutter_studio"),
    }

# ── Model code to use ────────────────────────────────────────────────────────
MODEL_CODE = "PKN2DCWBS6PSCC5"   # 15 chars

# Split breakdown:
#   PKN    → plant/platform prefix  (positions 1-3)
#   2DCWBS → variant/description    (positions 4-9)
#   6PSCC5 → spec/series code       (positions 10-15)
# VIN = MODEL_CODE + 2-digit serial = 17 chars
MODEL_WMI  = MODEL_CODE[0:3]   # PKN
MODEL_VDS  = MODEL_CODE[3:9]   # 2DCWBS
MODEL_SPEC = MODEL_CODE[9:15]  # 6PSCC5

PLATFORM_NAME = "CD LINE"

# AI inspection tasks (as seen in the app screenshot)
INSPECTION_TASKS = [
    {"taskId": "ZX_RH", "label": "please capture ZX_RH", "aiModel": "camper test"},
    {"taskId": "ZX_LH", "label": "please capture ZX_LH", "aiModel": "camper test"},
    {"taskId": "RX_RH", "label": "please capture RX_RH", "aiModel": "camper test"},
    {"taskId": "RX_LH", "label": "please capture RX_LH", "aiModel": "camper test"},
]

INSPECTOR_NAMES = [
    "Rahul Sharma", "Priya Patil", "Anil Deshmukh",
    "Sneha Joshi",  "Manoj Kadam",  "Pooja Kulkarni",
]


def generate_vin(serial: int) -> str:
    """Build a 17-char VIN from the model code + 2-digit serial.

    Structure:
        PKN  2DCWBS  6PSCC5  01
        WMI  VDS     SPEC    SEQ
    """
    return f"{MODEL_CODE}{serial:02d}"   # e.g. PKN2DCWBS6PSCC501


def build_dummy_results(overall_pass: bool) -> list:
    """Generate realistic task-level inspection results."""
    results = []
    for task in INSPECTION_TASKS:
        if overall_pass:
            passed = random.random() > 0.05   # 95% pass rate per task
        else:
            passed = random.random() > 0.5    # 50% for a failing scan
        confidence = round(random.uniform(0.85, 0.99) if passed else random.uniform(0.45, 0.75), 4)
        results.append({
            "taskId":     task["taskId"],
            "label":      task["label"],
            "aiModel":    task["aiModel"],
            "passed":     passed,
            "confidence": confidence,
        })
    return results


def get_connection():
    import psycopg2
    return psycopg2.connect(**POSTGRES_CONFIG)


def ensure_master_mapping(cursor):
    """Insert PKN2DCWBS6PSCC5 into master_model_mappings if not present."""
    cursor.execute(
        "SELECT id FROM master_model_mappings WHERE model_code = %s",
        (MODEL_CODE,)
    )
    if cursor.fetchone():
        print(f"  master_model_mappings: '{MODEL_CODE}' already exists — skipping insert.")
        return

    new_id = str(uuid.uuid4())
    cursor.execute(
        """
        INSERT INTO master_model_mappings (id, platform_name, model_code, description, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            new_id,
            PLATFORM_NAME,
            MODEL_CODE,
            f"Model {MODEL_WMI}-{MODEL_VDS}-{MODEL_SPEC} (dummy seed)",
            datetime.utcnow(),
        )
    )
    print(f"  master_model_mappings: inserted '{MODEL_CODE}' (id={new_id})")


def list_apps(cursor):
    cursor.execute("SELECT id, name, package_name FROM app_projects ORDER BY created_at DESC")
    rows = cursor.fetchall()
    if not rows:
        print("No app projects found in the database.")
        return
    print("\nAvailable App Projects:")
    print(f"  {'ID':<38}  {'Name':<30}  Package")
    print("  " + "-" * 80)
    for row in rows:
        print(f"  {row[0]:<38}  {row[1]:<30}  {row[2]}")


def insert_dummy_scans(cursor, app_id: str, count: int):
    """Insert `count` dummy inspection_results rows."""
    base_date = datetime.utcnow() - timedelta(days=count)
    inserted = 0
    for i in range(1, count + 1):
        vin = generate_vin(i)
        overall_pass = random.random() > 0.1   # ~90% pass rate
        results      = build_dummy_results(overall_pass)
        all_passed   = all(r["passed"] for r in results)
        inspector    = random.choice(INSPECTOR_NAMES)
        scan_time    = base_date + timedelta(hours=i * 2)

        cursor.execute(
            """
            INSERT INTO inspection_results
                (id, app_id, vin, model_code, results, overall_success, inspector_notes, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                str(uuid.uuid4()),
                app_id,
                vin,
                MODEL_CODE,
                json.dumps(results),
                all_passed,
                f"Inspector: {inspector} | Auto-generated dummy scan #{i}",
                scan_time,
            )
        )
        status = "PASS" if all_passed else "FAIL"
        print(f"  [{i:>3}/{count}]  VIN={vin}  {status}")
        inserted += 1
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Generate dummy inspection scans for PKN2DCWBS6PSCC5")
    parser.add_argument("--count",     type=int, default=20, help="Number of dummy scans to generate (default: 20)")
    parser.add_argument("--app-id",   type=str, default=None, help="App project UUID to attach scans to")
    parser.add_argument("--list-apps", action="store_true",   help="List available app IDs and exit")
    args = parser.parse_args()

    # Dependency check
    try:
        import psycopg2
    except ImportError:
        print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    print(f"Connecting to PostgreSQL at {POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']} ...")
    try:
        conn   = get_connection()
        cursor = conn.cursor()
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)
    print("Connected.\n")

    if args.list_apps:
        list_apps(cursor)
        cursor.close()
        conn.close()
        return

    # Resolve app_id
    app_id = args.app_id
    if not app_id:
        cursor.execute("SELECT id, name FROM app_projects ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            app_id = row[0]
            print(f"No --app-id supplied. Using most recent app: '{row[1]}' ({app_id})")
        else:
            print("No app projects found. Create one via the web UI or pass --app-id.")
            cursor.close()
            conn.close()
            sys.exit(1)

    print(f"Model code : {MODEL_CODE}")
    print(f"  Split    : WMI={MODEL_WMI}  VDS={MODEL_VDS}  SPEC={MODEL_SPEC}")
    print(f"  VIN fmt  : {MODEL_CODE}<NN>  (e.g. {generate_vin(1)})")
    print(f"App ID     : {app_id}")
    print(f"Scans      : {args.count}\n")

    # 1. Ensure model code in master table
    print("Step 1 — master_model_mappings")
    ensure_master_mapping(cursor)
    conn.commit()

    # 2. Insert dummy scans
    print(f"\nStep 2 — inspection_results ({args.count} rows)")
    inserted = insert_dummy_scans(cursor, app_id, args.count)
    conn.commit()

    cursor.close()
    conn.close()
    print(f"\nDone. {inserted} dummy scan(s) created for model code '{MODEL_CODE}'.")


if __name__ == "__main__":
    main()
