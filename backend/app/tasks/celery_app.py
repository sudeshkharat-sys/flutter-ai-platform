from celery import Celery
from celery.signals import worker_process_init
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
