import sys
import multiprocessing
from celery import Celery
from app.config import settings

# Required for PyInstaller/frozen EXE: prevents child processes from
# re-executing the entire frozen entry-point on Windows.
if getattr(sys, "frozen", False):
    multiprocessing.freeze_support()

celery_app = Celery(
    "flutter_studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.convert_model", "app.tasks.build_apk"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    # Use 'solo' pool when running inside a frozen EXE so that Celery does not
    # try to fork child processes (which re-runs the EXE entry point on Windows
    # and causes the "worker not found" / infinite-restart loop).
    # Override via CELERYD_POOL env-var when running normally (e.g. prefork).
    worker_pool="solo" if getattr(sys, "frozen", False) else "prefork",
)
