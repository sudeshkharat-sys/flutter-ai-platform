from celery import Celery
from celery.signals import worker_process_init
from app.config import settings

celery_app = Celery(
    "flutter_studio",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
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


@worker_process_init.connect
def _init_worker_process(sender=None, **kwargs):
    from celery.app.trace import setup_worker_optimizations
    setup_worker_optimizations(celery_app)
