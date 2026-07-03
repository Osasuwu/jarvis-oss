# Scheduler primitive — RETIRED (#743)

> **Status: retired.** `agents/scheduler.py` (the APScheduler resident run-loop,
> #300) and its service installer were removed in #743 when **`agents/wake_driver.py`**
> replaced them. Milestone #44 (reactive-core) is **event-driven**: the loop
> blocks on a Postgres `LISTEN`/`NOTIFY` wake signal and cold-boots the
> orchestrator per event — "persistent BEHAVIOR, not a persistent PROCESS".
> There is no longer a resident interval poller, no APScheduler, and no
> SQLAlchemy jobstore.

## What replaced it

| Retired | Replacement |
|---|---|
| `agents/scheduler.py` (APScheduler `BackgroundScheduler`, interval ticks) | `agents/wake_driver.py` (LISTEN/NOTIFY loop + watchdog) |
| `python -m agents.scheduler --interval 60` | `python -m agents.wake_driver` |
| `apscheduler` + `sqlalchemy` deps | none (psycopg only, for the LISTEN socket) |
| `scripts/install/install-scheduler-service.ps1` | (not yet re-introduced; wake_driver runs foreground for now) |

The crash-safety property APScheduler gave via its Postgres jobstore is now
provided by the events FSM itself (#739): an event stays `claimed` until the
orchestrator commits `processed`, and a **watchdog** re-claims rows stranded
past a threshold — so a crash mid-tick reprocesses the event rather than
losing it. See `agents/wake_driver.py` and `tests/test_wake_driver.py`.

## Teardown for already-deployed devices

Any device that registered the `jarvis-scheduler` NSSM service must remove it
once — the service would otherwise fail-loop on the deleted module:

```powershell
.\scripts\install\uninstall-scheduler-service.ps1
```

`uninstall-scheduler-service.ps1` is kept solely for this cleanup; once every
device has run it, the script can be deleted too.

## Why event-driven, not interval-poll

The interval poller (`while True: sleep(interval); tick()`) is the anti-pattern
#743 retired: it wakes whether or not there is work, couples latency to the
interval, and is a resident process doing nothing most of the time. LISTEN/NOTIFY
wakes only on a real event (the `notify_events_insert` trigger), drains the
queue one event at a time, and falls back to the watchdog interval only as a
safety timeout. Decisions: `efa255cc` (continuous-loop wake), `2c5384d0`
(substrate verdict — retire APScheduler inside reactive-core slices).
