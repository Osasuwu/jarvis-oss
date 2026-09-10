-- ===========================================================================
-- driver_heartbeat: cross-device liveness signal for wake_driver (#1085).
--
-- Design (grill decision 41f9b77d, #1085): the WRITE (wake_driver stamping
-- its own tick) ships in Slice 3 (S3-1); this migration ships in Slice 2
-- because /dispatch's post-enqueue READ (S2-6) needs real storage to query
-- against — a read with no schema behind it is not a read, it is a stub.
-- No row present (pre-Slice-3, or wake_driver never having ticked) and a row
-- present with a last_tick older than the staleness threshold are both
-- legitimately "stale" from the reader's perspective; the table starting
-- empty is the correct, non-regressing signal until Slice 3's writer exists.
--
-- Additive migration — no change to existing tables. SHARED DB (co-tenant
-- redrobot); nothing here touches memory/events/task tables.
--
-- ceiling: single-row usage (driver_name='wake_driver') under a single-driver
-- assumption — no multi-device/multi-driver fan-in. If a second driver
-- process is ever introduced, each gets its own row keyed by driver_name;
-- the schema already supports that, only the reader's hardcoded name lookup
-- would need to become a parameter.
-- ===========================================================================
create table if not exists driver_heartbeat (
  driver_name text primary key,
  last_tick   timestamptz not null
);

comment on table driver_heartbeat is
  'Cross-device liveness signal for reactive-core drivers (#1085 S2-6/S3-1). One row per driver_name, stamped by the driver on each tick; a missing or stale row means "driver may not be running" to any reader.';

-- RLS: allow-all convention (service_role bypasses; CI/local wake_driver uses
-- the anon key). No sandcastle actor gate — this is a driver-owned surface,
-- symmetric to review_debt's CI-owned surface.
alter table driver_heartbeat enable row level security;
drop policy if exists "Allow all for authenticated" on driver_heartbeat;
drop policy if exists "Allow all for anon" on driver_heartbeat;
create policy "Allow all for authenticated" on driver_heartbeat
  for all using (true) with check (true);
create policy "Allow all for anon" on driver_heartbeat
  for all to anon using (true) with check (true);
