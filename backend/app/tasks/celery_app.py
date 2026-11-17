from celery import Celery
from celery.signals import worker_process_init, worker_ready
from app.config import settings

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
    broker_connection_retry_on_startup=True,
    worker_pool="solo",
    # Keep the broker connection alive through brief Redis hiccups.
    broker_connection_retry=True,
    broker_connection_max_retries=None,
    broker_transport_options={"visibility_timeout": 4 * 3600, "socket_keepalive": True},
    result_backend_transport_options={"retry_on_timeout": True},
    result_expires=24 * 3600,
)

# Windows uses 'spawn' for multiprocessing (not 'fork'), so child worker
# processes start fresh and Celery's setup_worker_optimizations() may not
# run before the first task arrives, leaving _loc=[] and causing:
#   ValueError: not enough values to unpack (expected 3, got 0)
# Explicitly calling it here via worker_process_init guarantees _loc is
# populated in every child process before any task executes.
@worker_process_init.connect
def _init_worker_process(sender=None, **kwargs):
    from celery.app.trace import setup_worker_optimizations
    setup_worker_optimizations(celery_app)


@worker_ready.connect
def _recover_interrupted_jobs(sender=None, **kwargs):
    """A worker that died mid-task (crash, closed tab, reboot) leaves rows stuck
    in 'converting' / 'building' forever. A worker only starts when nothing is
    running (--pool=solo), so anything still in those states was interrupted:
    mark it as failed with a clear message so the user can hit Retry."""
    try:
        from app.connectors.state_db import StateDBConnector
        db = StateDBConnector()
        msg = "Interrupted: the worker stopped before this finished. Please retry."
        n1 = db.execute_update(
            "UPDATE model_assets SET status='error', error_message=:m WHERE status IN ('converting','pending')",
            {"m": msg})
        n2 = db.execute_update(
            "UPDATE app_projects SET build_status='error', build_step='Interrupted' "
            "WHERE build_status IN ('building','pending')")
        print(f"[recovery] marked interrupted jobs: {n1} model(s), {n2} build(s)")
    except Exception as exc:
        print(f"[recovery] skipped: {exc}")
