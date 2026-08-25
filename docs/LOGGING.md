# Logging and outage triage

Where to look when something is broken. Written for whoever (or whatever) is
holding the pager.

## Triage in three commands

```bash
ssh oracle2
pm2 logs GDG-backend --lines 200
```

```bash
# errors only, most recent first
ssh oracle2 'tail -500 ~/.pm2/logs/GDG-backend-error.log'
```

```bash
# everything one request did, by its id
ssh oracle2 'grep "req:6cefc82ee87c" ~/.pm2/logs/GDG-backend-*.log'
```

`oracle2` is the SSH alias for the production VPS. The app runs there under PM2
as **`GDG-backend`** on port **7501**, four uvicorn workers.

## Reading a log line

```
INFO [pid:1996265] [req:6cefc82ee87c] app.middleware: GET /health -> 200 in 3.9ms
└─┬─┘ └──────┬────┘ └───────┬───────┘ └──────┬──────┘ └────────────┬──────────┘
level  which worker    request id        source module          message
```

- **No timestamp in the line.** PM2 is started with `--time` and prefixes one
  itself. Don't add a second.
- **`pid`** matters because four workers share one stdout stream. Lines from
  concurrent requests interleave; the pid and request id are what untangle them.
- **`req`** is `-` for anything outside a request: startup, shutdown, background
  tasks.
- **module** is the Python logger name, so it tells you the file:
  `app.routers.emails` is `app/routers/emails.py`.

## Tracing one request

Every response carries an `X-Request-ID` header. A user reporting a failure can
be asked for it, or you can pull it from the browser's network tab, then:

```bash
ssh oracle2 'grep "req:<id>" ~/.pm2/logs/GDG-backend-out.log'
```

Callers may also **send** `X-Request-ID` and the app will adopt it, so a client
trace and a server trace can share one id.

The same id is set as a Sentry tag, so an error in Sentry links to the exact
log lines.

## PM2 or Sentry?

| Question | Look at |
|---|---|
| What happened, in order, around the failure | PM2 logs |
| What broke, with a stack trace and request context | Sentry |
| Is this endpoint slow | PM2 - the `-> 200 in 3.9ms` summary line |
| Which requests hit this code path | PM2, grep the module name |
| Is this happening to many users | Sentry |

Sentry's `LoggingIntegration` is on by default at `level=INFO` /
`event_level=ERROR`. That means:

- every `logger.info(...)` becomes a **breadcrumb** attached to any error event
  raised later in the same request
- every `logger.error(...)` / `logger.exception(...)` raises a Sentry event on
  its own, even without an exception propagating

So when you open a Sentry issue, the breadcrumbs already contain that request's
log lines. You often don't need to SSH at all.

## Turning up the volume

Level comes from the `LOG_LEVEL` environment variable, default `INFO`:

```python
# app/logging_config.py
resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
```

`DEBUG` additionally emits request bodies (`logger.debug("request body: %s", ...)`)
and the Google Forms payload dumps. **Those contain member PII**, which is why
they are `DEBUG` and not `INFO`. Turn it on deliberately, turn it back off.

Production env vars come from Infisical (`--env=prod --path=/admin-backend`), so
`LOG_LEVEL` has to be set there, then `pm2 restart GDG-backend`.

## Log rotation

`pm2-logrotate` is installed and configured:

| Setting | Value |
|---|---|
| `max_size` | 10M |
| `retain` | 7 |
| `compress` | true |
| `rotateInterval` | daily (`0 0 * * *`) |
| `workerInterval` | 60s |

Rotated files are `~/.pm2/logs/GDG-backend-out__<timestamp>.log.gz`. Worst-case
footprint is bounded at roughly 280M across the four active streams.

```bash
ssh oracle2 'pm2 conf pm2-logrotate'     # current settings
ssh oracle2 'ls -lh ~/.pm2/logs/'        # live files and archives
```

To read a rotated file: `zcat ~/.pm2/logs/GDG-backend-out__*.log.gz | grep ...`

## Gotchas

**`fileConfig` disables loggers.** `logging.config.fileConfig` defaults to
`disable_existing_loggers=True`, which switches off every logger that already
exists. `alembic/env.py` calls it, and the test suite runs migrations
in-process - so before this was fixed, nothing the app logged was capturable
under pytest. If app logging ever goes silent in tests, check that call first.

**Four workers, one stream.** Never assume adjacent lines belong to the same
request. Filter by `req:` first.

**Background tasks log with `req:-`.** Certificate sends and email blasts run in
`BackgroundTasks` after the response is sent, so they are outside the request
context. Grep the module name (`app.routers.emails`) instead of a request id.

## Writing new logging

```python
import logging

logger = logging.getLogger(__name__)   # module-scoped, never the root logger
```

| Use | For |
|---|---|
| `logger.debug` | payloads, request bodies - anything with PII |
| `logger.info` | normal progress worth having as a Sentry breadcrumb |
| `logger.warning` | recovered problems: a retry, a rejected input |
| `logger.error` | a failure, no traceback available |
| `logger.exception` | a failure inside `except:` - includes the traceback |

Do **not** reintroduce `print()`, and do not build a second logging mechanism.
`tests/test_logging.py` fails the build if `print(`, `write_log` or `LogFile`
reappears anywhere in `app/`.

## Historical logs

Everything before the stdlib-logging migration lived in per-request directories
under `~/GDG-Logs` - 48,491 of them, 454M in total, now a 9MB archive. That tree is archived at
`~/GDG-Logs-archive.tar.gz` on the VPS and nothing writes to it any more.

```bash
# what's in there
ssh oracle2 'tar -tzf ~/GDG-Logs-archive.tar.gz | head'

# read one endpoint's logs without unpacking it (--wildcards is required)
ssh oracle2 'tar -xzOf ~/GDG-Logs-archive.tar.gz --wildcards "GDG-Logs/send certificates/*/messages.log"' | head
```

Those files have raw ANSI escape codes in them, so pipe through `cat -v` if the
colour codes get in the way.

## Where the code is

| File | Role |
|---|---|
| `app/logging_config.py` | handler, format, `LOG_LEVEL`, `RequestContextFilter` |
| `app/middleware.py` | request id, summary line, Sentry tag, `X-Request-ID` |
| `app/error_handlers.py` | what gets logged when a request fails |
| `app/main.py` | `configure_logging()` is called from `lifespan` |
