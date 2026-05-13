# AI Vision Platform — Work Log

---

## 12 May 2026

### 1. Nashik Chatbot PQ Project — Z-Stage Changes
- Made configuration changes in the Z-stage pipeline for the Nashik chatbot PQ project.
- Tested end-to-end with a sample file; output verified correct.
- Pushed changes to the deployment branch for release.

### 2. AI Vision App — Celery Unpacking Error During Training (RESOLVED)
**Symptom:** Training task raised a Celery argument-unpacking error on dispatch.

**Root cause:** Mismatch between how the task was invoked (positional vs. keyword argument) and the `bind=True` signature — `self` was not being counted correctly by the caller, causing Celery to unpack arguments into the wrong parameters.

**Fix:** Verified `@celery_app.task(bind=True)` signature and ensured all callers use `.delay(asset_id)` (keyword-safe) rather than positional spreading. Tested with a sample `.pt` model; training completed and TFLite output verified.

### 3. AI Vision EXE — "Worker Not Found" (Identified, continued 13 May)
**Symptom:** The bundled Windows EXE launches the FastAPI server, but every training or APK-build request immediately fails with **"no worker found"** and the queue never clears.

**Root cause analysis (deep):**

| # | Cause | Detail |
|---|-------|--------|
| 1 | **Worker process not started** | `build_apk_task.delay()` and `convert_model_to_tflite.delay()` push tasks onto the Redis queue (`redis://localhost:6379/3`). The Celery worker is a *separate* process (`celery -A app.tasks.celery_app worker …`). The EXE bundles the FastAPI server but never spawns the worker subprocess, so the queue fills up and nothing consumes it. |
| 2 | **PyInstaller multiprocessing loop** | Celery's default `prefork` pool forks child processes via Python's `multiprocessing`. In a frozen EXE on Windows, each child process re-executes the EXE entry-point (because `__main__` resolves to the frozen binary). Without `multiprocessing.freeze_support()` the children loop endlessly or crash immediately, so the worker registers zero concurrency and appears offline. |
| 3 | **Build status stuck `"building"` with no escape** | `export_router.py` sets `build_status = "building"` in the database before dispatching the task. If the worker never starts (causes 1 & 2 above), the status never transitions to `"ready"` or `"error"`. The React frontend polls every 2 s and shows "Processing…" indefinitely — no timeout, no reset path. |

**Status:** Causes identified. Resolution carried forward to 13 May.

---

## 13 May 2026

### 1. AI Vision App — Training Stopped (Same Root Cause as EXE Issue)
**Symptom:** Model training stops part-way; Celery task disappears from the queue without completing.

**Cause:** Same as EXE "worker not found" — Celery `prefork` pool attempts to fork child processes inside the frozen EXE, they crash silently, and the task is dropped.

**Fix applied:**
- Added `multiprocessing.freeze_support()` call guarded by `sys.frozen` check in `celery_app.py`.
- Set `worker_pool = "solo"` when running inside a frozen EXE so Celery executes tasks in-process (no forking). Normal (non-frozen) mode keeps the default `prefork` pool.
- Tested: training ran to completion and produced a valid `.tflite` file.

```python
# celery_app.py — key change
worker_pool="solo" if getattr(sys, "frozen", False) else "prefork",
```

### 2. Flutter APK Build — UI Shows "Processing" but Terminal Log Stopped (RESOLVED)
**Symptom:** Clicking "Build Final APK" sets the status to `"building"` in the DB. After several minutes the terminal log panel stops updating, but the UI button stays greyed out ("Compiling APK…") forever.

**Root cause:** Celery worker crashed mid-task (same EXE pool issue). The DB status remained `"building"` with no recovery path.

**Fix applied — backend (`export_router.py`):**
- Added `_is_worker_available()` helper that pings registered Celery workers with a 2-second timeout before dispatching any build task.
- If no worker responds, the endpoint returns HTTP 503 with a clear message instead of silently setting status to `"building"`.
- Added `POST /apps/{app_id}/build/reset` endpoint to flip a stuck `"building"` or `"error"` status back to `"idle"`.

**Fix applied — frontend (`AppBuilder.js` + `api.js`):**
- Added stale-build detection: tracks the last time `build_log` changed. If the log is unchanged for **5 minutes** while `build_status === 'building'`, a **"Reset Build"** button appears with a warning.
- Added **"Clear Error & Reset"** button that appears whenever `build_status === 'error'`.
- Both buttons call the new `POST …/build/reset` endpoint and refresh the UI.

### 3. AI Vision EXE — Issue Identified and Resolved
**Summary of all changes applied:**

| File | Change |
|------|--------|
| `backend/app/tasks/celery_app.py` | `freeze_support()` + `worker_pool=solo` when frozen |
| `backend/app/api/export_router.py` | Worker availability check + `/build/reset` endpoint |
| `backend/app/api/models_router.py` | Worker availability check before training dispatch |
| `frontend/src/api.js` | Added `resetBuild()` API call |
| `frontend/src/components/AppBuilder.js` | Stale-build timeout + Reset Build / Clear Error buttons |

**Verified:** EXE now starts the worker in solo-pool mode, training completes, APK builds proceed without hanging.

---

### How to Start the Worker Correctly

**Development (non-EXE):**
```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info
```

**Windows EXE / frozen environment:**
```bash
# Pool is automatically set to 'solo' — just start the worker normally:
celery -A app.tasks.celery_app worker --loglevel=info
# or explicitly:
celery -A app.tasks.celery_app worker --pool=solo --loglevel=info
```

**Check worker is alive:**
```bash
celery -A app.tasks.celery_app inspect ping
```
