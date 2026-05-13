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
)


@worker_process_init.connect
def init_worker(**kwargs):
    # On Windows, worker processes are spawned (not forked), so each child
    # starts with a clean module state. Importing task modules here populates
    # the Celery task registry and triggers setup_worker_optimizations, which
    # fills the _loc list that fast_trace_task unpacks. Without this, every
    # task dispatch raises: ValueError: not enough values to unpack (expected 3, got 0)
    celery_app.loader.import_default_modules()
