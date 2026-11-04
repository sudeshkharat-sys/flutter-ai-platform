Local launchers for flutter-ai-platform (Windows, no PowerShell needed).

First time only:
  1. conda create -n flutter-ai python=3.11 -y --override-channels -c conda-forge
  2. install-requirements.bat

Daily:
  start-all.bat          (Postgres, Redis, backend, Celery, frontend)
or one by one: start-postgres, start-redis, start-backend, start-celery, start-frontend.
Other: stop-postgres.bat, psql-pg.bat

Paths live in _config.bat. Edit it if folders move.
Note: Redis must be version 6 or newer (older ones fail with "unknown command HELLO").
