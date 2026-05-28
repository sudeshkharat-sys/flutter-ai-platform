"""
Thread-based task executor — replaces Celery so no Redis broker is needed.
"""
import threading
import logging

logger = logging.getLogger(__name__)


class _MockRequest:
    id = "local-task"


class SimpleTask:
    """Wraps a function so .delay() runs it in a background thread."""

    def __init__(self, func):
        self.func = func
        self.request = _MockRequest()

    def delay(self, *args, **kwargs):
        def run():
            try:
                self.func(self, *args, **kwargs)
            except Exception as exc:
                logger.error(f"Background task error: {exc}", exc_info=True)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return type("AsyncResult", (), {"id": "local"})()

    def __call__(self, *args, **kwargs):
        return self.func(self, *args, **kwargs)


class _CeleryCompat:
    """Minimal shim so @celery_app.task(bind=True, name=...) still compiles."""

    @staticmethod
    def task(bind=True, name=None):
        def decorator(func):
            return SimpleTask(func)
        return decorator


celery_app = _CeleryCompat()
