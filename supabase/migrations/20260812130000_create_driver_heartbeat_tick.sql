-- ===========================================================================
-- driver_heartbeat_tick: server-side-`now()` upsert RPC for wake_driver's own
-- tick stamp (#1085 S3-1). Mirrors scrubber_disabled_event_upsert
-- (20260810120000) — a plain `.table("driver_heartbeat").upsert(...)` would
-- use the CLIENT's clock, which drifts across devices; RPC-side `now()` keeps
-- staleness classification (agents/driver_heartbeat.py::classify) trustworthy
-- regardless of which device's wake_driver is ticking.
--
-- Additive migration on top of driver_heartbeat (20260812120000) — no schema
-- change, just the write path the table's own comment already promised.
-- ===========================================================================
create or replace function driver_heartbeat_tick(
  p_driver_name text,
  p_tick        timestamptz default now()
) returns driver_heartbeat
language plpgsql
security invoker
set search_path = public
as $$
declare
  result driver_heartbeat;
begin
  insert into driver_heartbeat as h (driver_name, last_tick)
  values (p_driver_name, p_tick)
  on conflict (driver_name) do update
    set last_tick = excluded.last_tick
  returning h.* into result;
  return result;
end;
$$;
