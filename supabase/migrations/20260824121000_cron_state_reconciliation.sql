-- Osasuwu/jarvis#1645 — reconcile migration history with the live cron state.
--
-- Two changes were applied directly to the running database as low-risk
-- mitigations before this issue was opened. Both are still live and neither is
-- in migration history, so a fresh instance built from these files would
-- inherit the old, expensive cadence. This file closes that gap and is
-- idempotent against the instance where the changes already exist.
--
--   1. events_last_run_by_actor_mv_refresh: */5 -> */30.
--      20260429145000_create_events_canonical.sql scheduled a CONCURRENTLY
--      refresh every 5 minutes, unconditionally, from 2026-04-29: 32,243 runs
--      and 4.7 GB of WAL to maintain a 1,146-row projection over a ~10k-row
--      table that has no code consumer anywhere in the repo. The view is kept
--      (it is a projection built ahead of its readers, not dead), only the
--      cadence is cut 6x.
--
--   2. cron_job_run_details_purge: new daily retention job.
--      cron.job_run_details had no retention and had grown to 34,943 rows
--      since 2026-04-29. Backlog purged to 2,184 rows out of band; this job
--      keeps it bounded at 7 days.
--
-- Revert:
--   select cron.alter_job(2, schedule => '*/5 * * * *');
--   select cron.unschedule('cron_job_run_details_purge');

create extension if not exists pg_cron;

-- cron.schedule() upserts on jobname, so this is safe to re-run and also
-- correct on an instance that never had the */5 version.
select cron.schedule(
  'events_last_run_by_actor_mv_refresh',
  '*/30 * * * *',
  $$REFRESH MATERIALIZED VIEW CONCURRENTLY events_last_run_by_actor_mv$$
);

select cron.schedule(
  'cron_job_run_details_purge',
  '17 3 * * *',
  $$delete from cron.job_run_details where end_time < now() - interval '7 days'$$
);
