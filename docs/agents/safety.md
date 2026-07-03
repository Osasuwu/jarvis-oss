# Action-agent safety gate

Module: `agents/safety.py`. Ships as **S2-0** (issue #295), the foundation
every Federation & Delegation Sprint 2+ action agent must route its mutations through.
Model memory: `action_agent_safety_gate_model_v1`.

## Three tiers

| Tier | Meaning | Examples |
|------|---------|----------|
| `0 AUTO` | Fires without principal involvement. | `priority:{high,medium,low}`, `area:*`, `needs-research`, `needs-triage`, `status:ready` labels. Insert into `events` / `audit_log`. `goals.progress` append. Memory store with tag `auto-generated`. |
| `1 OWNER_QUEUE` | Default. Gate refuses to fire and flags `queued=True`. | New issue comments, closing issues, merging PRs, `priority:critical`, `pillar:*` labels, any write to a table not on the Sprint-1 whitelist. |
| `2 BLOCKED` | Never runs. `gate()` raises `GateError`. | `.env*`, `.claude/*`, destructive verbs (`delete`, `drop`, `force_push`), impersonation / `send_as_owner`, cross-repo writes, the whole `messaging` area. |

Tier 2 wins over Tier 0 when both match — safety bias is deny-first.

## Typical use

```python
from agents import safety

outcome = safety.gate(
    agent_id="langgraph-dispatcher",
    tool_name="gh_labels",
    action="add_label",
    target="priority:high",
    area="github",
    scope_hash=approved_scope_hash,
    dry_run=False,
    fn=lambda: github_client.add_label(issue=42, label="priority:high"),
)
```

What the gate does for you:

1. Classifies `(tool_name, action, target, area)` into a tier.
2. Computes a deterministic `idempotency_key` via sha256 over
   `agent_id | action | target | scope_hash`.
3. Decides: fire `fn` (Tier 0, live-run), skip `fn` and flag
   `queued=True` (Tier 1), or raise `GateError` (Tier 2).
4. Emits one `audit_log` row per attempt — best-effort, never raises.
   The row carries `tier` and `idempotency_key` in `details`.

The returned `GateOutcome` has `tier`, `fired`, `queued`, `dry_run`,
`idempotency_key`. Keep the key on your queue / outcome row so re-runs
are no-ops.

## Classification-only

If you just want a tier decision and an audit row without running code,
omit `fn`:

```python
outcome = safety.gate(
    agent_id="langgraph-dispatcher",
    tool_name="gh_labels",
    action="add_label",
    target="priority:high",
    area="github",
)
# outcome.tier is Tier.AUTO; audit row outcome="classified"
```

## Dry-run

Pass `dry_run=True` when piloting a new action type. `fn` never runs;
the audit row shows `outcome="dry_run"` (Tier 0) or
`"dry_run_queued"` (Tier 1); Tier 2 still raises.

## Standalone audit

`safety.audit(...)` writes a single audit row without going through the
gate. Useful for legacy paths that classify+execute on their own. Do
**not** call it in addition to `gate()` for the same event — you'll
double-count.

## Adding a new action type

1. Classify before writing code. Where does it land: Tier 0, 1, or 2?
2. If Tier 0 / Tier 2, update both `agents/safety.py` **and**
   `action_agent_safety_gate_model_v1` memory in the same change — the
   two must stay in lockstep.
3. Add a unit test in `tests/test_agents_safety.py` covering the new
   match. Every tier-boundary assertion is the spec.

## Changing tiers

Principal-driven moves only. If the principal says "stop asking about X,
auto-apply it" → move the action to Tier 0 and narrow the rule as much
as possible. If "this was too aggressive" → move to Tier 1. Each move
is a decision memory with the reason. Never expand Tier 0 silently.

## Relation to `task_queue` / `action_queue`

Tier 1 `queued=True` currently means "audited as queued"; the actual
enqueue to a dedicated queue table lands in S2-1 (#296) when
`task_queue` ships. Until then, the dispatcher and other agents can
rely on the audit row + `queued` flag; no agent should fire a Tier 1
action today.
