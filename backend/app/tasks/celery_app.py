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
    # On Windows, prefork uses spawn so each child process starts completely
    # fresh. Three things must be set up before fast_trace_task can run:
    #
    # 1. import_default_modules() — imports task modules so tasks are
    #    registered in app._tasks.
    #
    # 2. build_tracer() per task — fast_trace_task calls task.__trace__()
    #    directly without a None-check. The slower trace_task() has a lazy
    #    fallback, but fast_trace_task does not. We must build each tracer
    #    explicitly; without this step the call raises:
    #    TypeError: 'NoneType' object is not callable
    #
    # 3. setup_worker_optimizations() — fills the module-level _loc list as
    #    [tasks, accept_content, hostname] so fast_trace_task can unpack it.
    #    Without this step it raises:
    #    ValueError: not enough values to unpack (expected 3, got 0)
    import socket
    from celery.app.trace import build_tracer, setup_worker_optimizations

    celery_app.loader.import_default_modules()

    hostname = socket.gethostname()
    for task_name, task in celery_app.tasks.items():
        if task.__trace__ is None:
            task.__trace__ = build_tracer(
                task_name, task,
                loader=celery_app.loader,
                hostname=hostname,
                app=celery_app,
            )

    setup_worker_optimizations(celery_app, hostname=hostname)
