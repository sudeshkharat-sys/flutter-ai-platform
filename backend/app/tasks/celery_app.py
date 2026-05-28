"""
Thread-based task executor — no Redis or Celery required.
"""
import threading
import logging

logger = logging.getLogger(__name__)


class _MockRequest:
    id = "local-task"


class SimpleTask:
    def __init__(self, func):
        self.func = func
        self.request = _MockRequest()

    def delay(self, *args, **kwargs):
        def run():
            try:
                self.func(self, *args, **kwargs)
            except Exception as exc:
                logger.error(f"Task error: {exc}", exc_info=True)

        threading.Thread(target=run, daemon=True).start()
        return type("AsyncResult", (), {"id": "local"})()

    def __call__(self, *args, **kwargs):
        return self.func(self, *args, **kwargs)


class _CeleryCompat:
    @staticmethod
    def task(bind=True, name=None):
        def decorator(func):
            return SimpleTask(func)
        return decorator


celery_app = _CeleryCompat()
