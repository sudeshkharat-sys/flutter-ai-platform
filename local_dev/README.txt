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

One-time: run setup-pgpass.bat so Postgres stops asking for the password.
Command history: cmd/Anaconda Prompt cannot keep history between windows, so use start-all.bat
(double-click it, or put a Desktop shortcut to it) instead of retyping commands.
Using this for a second project: copy this folder into it and edit only the top block of _config.bat.
Two projects on the same ports clash: give each its own BACKEND_PORT / FRONTEND_PORT, and run one stack at a time unless ports differ.
