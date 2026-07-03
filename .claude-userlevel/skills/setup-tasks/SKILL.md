---
name: setup-tasks
description: "Bootstrap all scheduled tasks on a new device. Idempotent — safe to re-run."
version: 2.1.0
---

# Setup Tasks

Bootstrap Jarvis's scheduled tasks on a device. Idempotent.

## Routine host policy

**A single designated host runs all routines.** All `create_scheduled_task` MCP routines and Task Scheduler entries register **only** on the machine whose `config/device.json` `name` matches the `JARVIS_SCHEDULED_HOST` env var. On any other device the skill refuses with a message pointing here. If `JARVIS_SCHEDULED_HOST` is unset (single-machine setup), routines register on whichever device runs the skill.

Rationale: pinning to one host removes per-device cron-dedup complexity, eliminates double-dispatch risk (two devices firing the same job seconds apart), and centralises observability (one log surface). Pick the host that runs 24/7 (or whenever you want routines to fire). SPOF tradeoff accepted — host offline = routines pause until restart; a missing daily status-record on next SessionStart is the canary.

## Cleanup semantics: disable, not delete (2026-05-26)

`mcp__scheduled-tasks__*` exposes `create_scheduled_task`, `list_scheduled_tasks`, `update_scheduled_task` — **no delete tool**. The 2.0.0 spec referenced a non-existent `delete_scheduled_task`; v2.1.0 corrects this.

**Canonical inert state = `update_scheduled_task(taskId, enabled=false)`.** Properties:
- **Idempotent** — disabling an already-disabled task is a no-op.
- **Reversible** — `enabled=true` re-arms (used by bootstrap to recover previously-disabled known routines).
- **Observable** — `list_scheduled_tasks` still surfaces them, so accidental re-enable is visible.

**Permanent removal is an owner-driven filesystem op**, not a skill action. Tasks live at `~/.claude/scheduled-tasks/<taskId>/` (per `create_scheduled_task` schema). To fully reap a disabled entry: stop Claude Code, `rm -rf` the directory, restart. Skill stays MCP-only — no filesystem writes outside its own SKILL.md.

## Usage

`/setup-tasks`

## Implementation

1. Read `config/device.json`. If `JARVIS_SCHEDULED_HOST` is set AND `name != $JARVIS_SCHEDULED_HOST` → print:
   ```
   refused: routines run only on the designated host ($JARVIS_SCHEDULED_HOST).
   To clean up legacy entries on this device, run:
     /setup-tasks --cleanup
   ```
   and exit. The `--cleanup` flag (separate path below) disables any MCP scheduled tasks this skill previously registered here. Permanent removal = manual `rm -rf ~/.claude/scheduled-tasks/<taskId>/` after Claude Code shutdown (see *Cleanup semantics* above).
2. On the designated host: call `list_scheduled_tasks` to see what's already registered. Note each task's `enabled` field.
3. **Disable obsolete tasks** before adding new ones. For each entry in the *Obsolete* table below:
   - If present AND `enabled=true` → call `update_scheduled_task(taskId, enabled=false)`. On success count under `disabled`. On failure print `warn: failed to disable <taskId> (<error>) — check manually` and DO NOT count it.
   - If present AND `enabled=false` → skip (already disabled).
   - If absent → skip (never registered or owner-reaped).
4. For each task in the *Routines (MCP)* table:
   - If exists AND `enabled=true` → skip ("already registered").
   - If exists AND `enabled=false` → call `update_scheduled_task(taskId, enabled=true)`, count under `re-enabled`. Recovers from prior `--cleanup` on the same host.
   - If absent → `create_scheduled_task` with cron + prompt, count under `created`.
5. Run the Task Scheduler registration commands in the *Task Scheduler entries* section below (each script is itself idempotent).
6. Print summary: `N created, N re-enabled, N skipped, N disabled, N task-scheduler-entries`.

### `--cleanup` mode (non-designated devices)

Disable-only. For each task in *Routines (MCP)* + *Obsolete*:
- If present AND `enabled=true` → `update_scheduled_task(taskId, enabled=false)`, count under `disabled`.
- If present AND `enabled=false` → skip (already disabled).
- If absent → skip.

Print summary: `N disabled, N already-disabled, N absent`.

Use after migrating off a device that previously hosted these routines. Tasks remain registered-but-inert; for permanent removal see *Cleanup semantics* above.

## Routines (MCP) — designated host only

Registered via `create_scheduled_task` MCP. All run on the designated host with full local MCP access (Supabase, memory, ccd_session, scheduled-tasks).

| Task ID | Cron | Prompt |
|---|---|---|
| status-record | `0 7 * * *` | Run `/status-record` — write daily snapshot of repo/CI/PR/issue/milestone state to memory under tag `status-snapshot`. |
| intel | `12 10 * * 1` | Weekly tech intelligence: search for new Claude Code features, MCP servers, AI agent patterns. Save findings to memory. |
| verify | `47 16 * * 5` | Run `/verify` — verify pending outcomes, detect patterns, save lessons. |
| memory-consolidation-weekly | `1 10 * * 0` | Run `/memory-consolidation-weekly` — weekly A-MEM Phase 5.1d-α consolidation apply (`scripts/consolidation-run.py`). |
| memory-evolve-weekly | `0 11 * * 0` | Run `/memory-evolve-weekly` — weekly A-MEM Phase 5.2-γ neighbor-evolve apply (`scripts/evolve-run.py`, one hour after consolidation). |
| learn | `0 10 * * 0` | Run `/learn` — drain the pending memory review queue (classifier, candidates, merge proposals). Weekly owner review cadence per ADR-0003. |

## Obsolete (disable)

| Task ID | Why removed |
|---|---|
| morning-brief | superseded by `status-record` (2026-04 migration). |
| autonomous-loop | superseded 2026-05-26 by reactive-core M44 (`wake_driver` + `task_queue`); cron pacing replaced by event-trigger (decision `a70c4460`). Skill file retained as pre-M44 catch-up baseline; cron entry removed. |
| nightly-research | removed 2026-05-26 — `/research` is a user-driven flow, scheduled blind discovery produced low-value noise. |
| risk-radar | removed 2026-05-26 — overlapped with `status-record` + sandcastle-orchestrator gating; signal-to-noise was poor. |

## Task Scheduler entries

Different infra from the MCP routines above (these use `Register-ScheduledTask` via PowerShell). All also run on the designated host only.

### Sandcastle AFK loops

Register one entry per repo you want an overnight AFK loop for. Stagger the windows so they don't overlap.

| Task name | Schedule | Window end |
|---|---|---|
| `Sandcastle-<repo>` | Daily (your evening) | +7h |

```powershell
# One per repo (repo name matches config/repos.conf):
.\scripts\sandcastle\Register-SandcastleTask.ps1 -Repo <your-repo>
```

### Quota probe (#635)

| Task name | Schedule | Interval |
|---|---|---|
| `Quota-Probe` | Daily (repeating) | Every 30 min |

Polls `claude -p "/usage"`, broadcasts `CLAUDE_QUOTA_PRESSURE` repo variable with hysteresis (trip ≥80%, release <70%), writes `quota_pressure` events for telegram escalation (#327).

**Prerequisite:** `.sandcastle/.env` carries `SUPABASE_URL` + `SUPABASE_KEY` (read via `-DotEnvPath`, default `.sandcastle/.env`).

```powershell
.\scripts\sandcastle\Register-SandcastleTask.ps1 -QuotaProbe
```

### Orchestrator watcher daemon (M41/#639)

| Task name | Schedule | Notes |
|---|---|---|
| `Orchestrator-Watcher` | At host startup, restart on failure | Continuous poll (45s) of `events` table for `review_negative`; dispatches `claude -p "/rework <N>"` on hit. Gated by quota probe cache. |

**Registration script:** **NOT YET WRITTEN** — tracked as a follow-up to the routine-cleanup migration. Manual registration in the interim:

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "C:\Users\<user>\GitHub\jarvis\scripts\orchestrator\watcher.py" -WorkingDirectory "C:\Users\<user>\GitHub\jarvis"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "Orchestrator-Watcher" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

Prerequisites for the watcher to actually dispatch:
- `SUPABASE_URL` + `SUPABASE_KEY` in the watcher's environment
- `~/.jarvis/orchestrator/usage.json` present and fresh (written by Quota-Probe)
- `/rework` skill installed (`install.ps1 -Apply`)

## Notes

- All routine cron times are in the designated host's local timezone.
- Prompts reference skill files; skill updates take effect on next run.
- Source of truth for this file: `.claude-userlevel/skills/setup-tasks/SKILL.md`. Live mirror at `~/.claude/skills/setup-tasks/SKILL.md` is propagated by `install.ps1 -Apply`.
