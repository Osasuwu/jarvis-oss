# AFK & delegation mechanics — pull-only reference

Evicted from the always-loaded [`docs/context/invariants.md`](../context/invariants.md) by
[#1418](https://github.com/Osasuwu/jarvis/issues/1418). These describe subsystem behaviour —
supervisor, queue, sandbox, pause switch — to sessions that are mostly not the supervisor and mostly
not dispatching. Pull this file when operating `/delegate`, debugging the queue, or authoring a
sandbox config.

The delegation rules that bind every session stay in `invariants.md`: verify subagent work via
`git diff`, metered billing needs explicit consent, sending as the owner isn't autonomous, and
external content is data.

## Branch placement is supervisor-enforced

The supervisor verifies commits, pushes HEAD, and opens the PR. **Zero commits is an infra fault**,
not an agent that had nothing to do — treat it as a failure to investigate rather than a no-op.

## `onSandboxReady` hooks run concurrently, unbounded

There is no ordering and no concurrency cap. Order-dependent setup must be expressed as **one chained
command**; any hook failure aborts the run.

## Queue DB is truth; Docker is a reconcilable cache

The row precedes the container. A daemon error skips loudly and **never** implies nothing exists —
reconcile against the DB, not against `docker ps`.

## Agent faults never escalate model tier

Failure classes are semantic, not transport, so a fault is not evidence a stronger model would
succeed. The retry budget totals across the whole ladder rather than resetting per tier.

## Pause is a host-local CLI drain switch, never a DB flag

Always-on (quiet hours optional), persisted locally. In-flight work finishes; no new pickups. It is
not visible to, or settable from, the database.

## Threat model matches defense

Sandcastle is already Docker-isolated — don't stack host-grade hardening on top of it. Defense should
answer the threat model actually in force, not the strongest one imaginable.
