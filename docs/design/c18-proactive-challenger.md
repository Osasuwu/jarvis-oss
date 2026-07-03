# C18 — Proactive Challenger (Design 1-pager)

**Status:** design-locked 2026-04-29 (Sprint #36, [#483](https://github.com/Osasuwu/jarvis/issues/483)). Detection engine → #C18.2 (TBD). Surfacing renderer → #C18.3 (TBD).
**Parent:** [`jarvis-v2-redesign.md` § C18](jarvis-v2-redesign.md#c18--proactive-challenger) (L2), with L3 leans at [§C18 L3](jarvis-v2-redesign.md#c18--proactive-challenger-1).
**Substrate dependency:** reads from C17 [`events_canonical`](c17-events-substrate.md), C2 `goals`, C3 `memories`. Emits `recalibration_proposed` events back to C17.

This doc locks the drift-signal taxonomy, threshold-config schema, emit shape, surfacing routing, bootstrap protocol, and false-positive calibration loop so #C18.2 (detection engine) and #C18.3 (surfacing renderer) can land without re-design. **No SQL** in this doc — query shapes are logical only.

---

## 1. Drift-signal taxonomy

C18 detects 5 signal classes. Each has a fixed reads-from substrate, a **primary threshold** that controls firing, optional **auxiliary parameters** (gating filters, per-brief caps, baseline minima), and a payload contract for the emitted `recalibration_proposed` event. All parameter names below match the YAML dotted form in §2 verbatim; the `threshold_snapshot.param` field of an emitted event (see §3) carries the same dotted string. Substrate-readiness column dictates which signals #C18.2 ships in the first wave vs. defers until C5 / C16 land.

| `signal_class` | Reads from | Primary threshold (YAML dotted) | Auxiliary params | Payload extras | Substrate readiness |
|---|---|---|---|---|---|
| `goal_neglect` | `goals` (active) + `events_canonical` (`action='decision_made'` grouped by `payload->>'goal_slug'`) | `signals.goal_neglect.threshold_days` (default `14`, units: days since last linked decision) | — | `goal_id`, `goal_slug`, `last_activity_ts`, `days_since` | **ready** — C2 + C17 shipped (Sprint #35) |
| `direction_vs_action` | `goals` (P0/P1 active) + `events_canonical` (`action IN ('decision_made','tool_call')` grouped by goal vs principal-stated priority) | `signals.direction_vs_action.gap_ratio_threshold` (default `0.30`, units: fraction of work hours on lower-prio goals while top-prio starves) | `signals.direction_vs_action.min_history_days` (`30`, baseline gate) | `top_priority_goal_id`, `time_share_actual`, `time_share_expected`, `gap_ratio` | **ready** — needs ≥30 days of `decision_made` history (per §5 bootstrap) |
| `stale_assumption` | `memories` (matching idle + low-confidence filter, `archived = false`) | `signals.stale_assumption.min_idle_days` (default `90`, units: days since last access) | `signals.stale_assumption.min_confidence` (`0.5`, AND-gate filter), `signals.stale_assumption.cap_per_brief` (`3`, top-N by score) | `memory_id`, `memory_name`, `confidence`, `last_accessed_at`, `last_used_in_decision` | **ready** — C3 lifecycle columns shipped (Phase 0/1 of #185) |
| `calibration_drift` | C5 calibrator outputs (Brier per memory type, per-class FP/FN — table TBD by C5) | `signals.calibration_drift.brier_ceiling_default` (default `0.20`, units: per-class Brier score) | `signals.calibration_drift.per_class_overrides` (map `memory_class → ceiling`, default empty) | `memory_class`, `current_brier`, `target_brier`, `sample_size` | **blocked-on-C5** — C5 calibrator emits not yet defined |
| `principal_override` | C16 events (`action='principal_override'` grouped by `payload->>'classifier_class'`) | `signals.principal_override.freq_threshold` (default `3`, units: overrides within window) | `signals.principal_override.window_days` (`30`, rolling window length) | `classifier_class`, `override_count`, `window_days`, `last_override_event_id` | **blocked-on-C16** — C16 verification arm not yet shipped |

**Primary vs auxiliary:** auto-tighten (§6) acts on the **primary** threshold only — auxiliary params are owner-tuned via YAML PR. `stale_assumption` triggers when `min_idle_days` AND `min_confidence` both gate-pass; auto-tighten only widens `min_idle_days`, never the confidence filter.

**Why one primary threshold per class:** keeps the auto-tighten loop targetable to a single dimension per signal. Per-class branching (e.g. different thresholds per memory type) lives inside `per_class_overrides` (`calibration_drift`) or inside the SQL detection query — not as additional siblings of the primary threshold.

**Why `signal_class` is a string not enum:** C18 is open to new signal classes (e.g. future "goal-vs-pillar drift", "tool-failure cluster"). Open vocabulary; new values added by PR alongside their threshold parameter and payload contract.

**Excluded by design:**
- Real-time anomaly detection (single-event triggers) — C18 detects *patterns* over windows; one-off anomalies are C16 territory.
- Memory-mutation suggestions — C5 owns mutations. C18 only surfaces; C5 picks up if the surfacing implies a `record_decision`-class belief is stale.
- Action recommendations — `proposed_action` payload field is *advisory text* (e.g. "run /reflect on last 10 decisions"), not an executable instruction.

---

## 2. Threshold config schema

Lives at `config/recalibration_thresholds.yaml`. Loaded at C18 detector startup. Reload-on-change is **out of scope** for #C18.2 (process restart on YAML edit is fine for current cadence; weekly cron tick already restarts).

### Shape

```yaml
# recalibration_thresholds.yaml
# C18 Proactive Challenger detection thresholds.
# Edit class: M2-strong — reviewer + smoke required (per redesign §C18 L3).
# Wrong thresholds = surfacing storm; protect against bypass.

surfacing_enabled: false   # bootstrap gate, see §5

signals:
  goal_neglect:
    enabled: true
    threshold_days: 14
    severity_per_overshoot: 0.1   # severity = min(1.0, (days_since - threshold_days) * factor)

  direction_vs_action:
    enabled: true
    gap_ratio_threshold: 0.30
    min_history_days: 30          # required baseline before signal can fire

  stale_assumption:
    enabled: true
    min_confidence: 0.5
    min_idle_days: 90
    cap_per_brief: 3              # don't flood — top-N stale memories by score

  calibration_drift:
    enabled: false                # blocked on C5 calibrator
    brier_ceiling_default: 0.20
    per_class_overrides: {}       # e.g. {decision: 0.15, reference: 0.30}

  principal_override:
    enabled: false                # blocked on C16
    freq_threshold: 3
    window_days: 30
```

### Edit class: M2-strong

Per [redesign §C18 L3](jarvis-v2-redesign.md#c18--proactive-challenger-1) — **wrong thresholds cause a surfacing storm**, which trains the principal to dismiss without reading (the exact failure mode #C18 is meant to prevent). Treat threshold edits as M2-strong:

- PR with reviewer (Copilot or human) + smoke run (#C18.2 ships smoke as `scripts/c18-dryrun.py` — replays last 30 days of events through current YAML, prints surfacing count by class).
- One-line owner override allowed via direct commit if a surfacing storm is already in progress (incident-response, not steady state).

### Why YAML not DB

Threshold drift across devices is a feature, not a bug — owner can experiment locally before committing. Single source of truth in the repo; portable across the 3 devices via the existing `.mcp.json` portability rule (relative paths only).

---

## 3. `recalibration_proposed` event shape

Extends C17 action vocabulary ([c17-events-substrate.md §5](c17-events-substrate.md)). New reserved values:

| `action` | Emitted by | Wave |
|---|---|---|
| `recalibration_proposed` | C18 detector | Sprint 36 (#C18.2) |
| `surfacing_outcome` | C12 surfacing widget | Sprint 36 (#C18.3) |

### Row contract

| Column | Value | Notes |
|---|---|---|
| `actor` | `c18-detector` | Single actor; specific signal in `payload.signal_class`. |
| `action` | `recalibration_proposed` | |
| `trace_id` | new uuid per detection run | Each cron tick = its own trace, like scheduled tasks per [c17 §3 rule 2](c17-events-substrate.md). |
| `parent_event_id` | NULL | Detection runs are not nested under a calling event. |
| `outcome` | `success` on emit; `failure` if SQL detection threw | Even an empty result-set is `success` (detection ran, found nothing). |
| `cost_tokens` / `cost_usd` | NULL on detection emit | Surfacing renderer (C18.3) emits a separate `tool_call` event for the Haiku call with these populated. |
| `payload` | shape below | |

### Payload shape

```jsonc
{
  "signal_class": "goal_neglect",                // see §1 taxonomy
  "severity": 0.65,                              // 0.0-1.0; per-class formula in YAML
  "evidence_ids": ["uuid", "uuid", ...],         // back-pointers — kind disambiguated by evidence_kind below
  "evidence_kind": "goal_id",                    // "event_id" | "goal_id" | "memory_id" — tells the consumer which table evidence_ids[] dereferences against
  "proposed_action": "Goal `jarvis-v2-memory` last decision 21d ago. Re-prioritize or update.",
  "dismissable_until": "2026-05-13T00:00:00Z",   // cooldown — see §4
  "threshold_snapshot": {                        // what the threshold was when fired (audit, not enforcement)
    "param": "signals.goal_neglect.threshold_days",
    "value": 14
  }
}
```

**Why `evidence_ids` not `evidence_event_ids`:** events/goals/memories share UUID type but live in different tables. The previous name implied "always event_ids" which contradicts the documented multi-kind dereferencing. `evidence_ids` is kind-neutral; `evidence_kind` is the discriminator.

**Why `evidence_kind` not just polymorphic uuid array:** without a kind discriminator, downstream consumers can't dereference the array. One field, three valid values, no JOINs needed at read time.

**Why `threshold_snapshot.param` uses the YAML dotted form:** matches §1 taxonomy and §2 schema verbatim. `/reflect` queries can group by `payload->'threshold_snapshot'->>'param'` without translating between flat names and YAML paths.

**Why `proposed_action` is text not structured:** the action is advisory, rendered to the principal via C12. Structured action would require an action vocabulary that doesn't exist yet (and may never — actions are situational). Text is good enough.

**Why `threshold_snapshot` is captured:** lets `/reflect` retroactively answer "did we lower the threshold and start surfacing more?" without re-reading historical YAML versions from git. Audit trail for the calibration loop in §6.

---

## 4. Surfacing routing contract

C18 emits to `events_canonical`. **C12 batched-brief picks up.** No new transport.

### Aggregator query shape (logical)

C12's batched-brief aggregator (existing) extends to read C18 events:

```
SELECT * FROM events_canonical e
WHERE e.action = 'recalibration_proposed'
  AND e.ts > <last_brief_ts_per_principal>
  AND (
        e.payload->>'dismissable_until' IS NULL
     OR (e.payload->>'dismissable_until')::timestamptz > now()
  )
  AND NOT EXISTS (
    SELECT 1 FROM events_canonical s
    WHERE s.action = 'surfacing_outcome'
      AND s.trace_id = e.trace_id
      AND s.payload->>'outcome' IN ('acted', 'dismissed', 'dismissed_with_reason')
  )
ORDER BY (e.payload->>'severity')::numeric DESC
LIMIT 5;
```

(SQL above is illustrative — concrete query is #C18.2. Note: `dismissable_until` lives **inside `payload`**, not as a top-level column — C17 schema reserves only the canonical `event_id`/`trace_id`/`ts`/`actor`/`action`/`outcome`/`cost_*`/`redacted`/`degraded`/`payload` columns. Cooldown joins on `trace_id` because §6 specifies that `surfacing_outcome` events **inherit** the originating `recalibration_proposed.trace_id` — that's the authoritative key for matching a surfacing back to its detection event.)

### Cooldown rule

Same `signal_class` + same primary evidence (e.g. same `goal_id` for `goal_neglect`) is **not surfaced twice within `dismissable_until`**. Default cooldown:

| Signal | Default `dismissable_until` |
|---|---|
| `goal_neglect` | 14 days from emit (one cycle of the goal-neglect threshold) |
| `direction_vs_action` | 7 days |
| `stale_assumption` | 30 days |
| `calibration_drift` | 14 days |
| `principal_override` | 14 days |

Cooldown reset on `surfacing_outcome.action='acted'` — once principal acts, the next occurrence resets the clock fresh.

### Cap per brief

**Max 5 surfacings per batched brief** (per [redesign §C18 L3](jarvis-v2-redesign.md#c18--proactive-challenger-1) — Haiku call budget under C13).

If detection produces more than 5 candidates after cooldown filter:
1. Sort by `severity` desc.
2. Take top 5.
3. Surplus rows remain in `events_canonical` — next batched brief picks them up if still un-cooled.

### No real-time interrupt

Per [redesign §C18 rejected list](jarvis-v2-redesign.md#rejected-2) — "real-time interrupt on first signal — drowns the principal in noise". C18 detection writes to `events_canonical`; **C12 batched brief is the only delivery path.** No webhook, no Telegram push, no PreToolUse interrupt.

If a future signal class requires real-time delivery, that's a redesign change, not a YAML edit.

---

## 5. Bootstrap protocol

### Why bootstrap matters

C18 is a *pattern detector*. Patterns need history. Firing on Day 1 of detection deployment guarantees false positives — the substrate has no baseline. Worse: surfacing storms during bootstrap train the principal to dismiss reflexively, then real signals get dismissed too.

### Gate: `surfacing_enabled: false` by default

Default YAML ships with `surfacing_enabled: false`. Detection still runs (writes `recalibration_proposed` to `events_canonical`), **C12 reads only when flag is true**. This produces a dry-run log without disturbing the principal.

### Per-signal baseline requirements

| Signal | Minimum baseline |
|---|---|
| `goal_neglect` | 0 days — fires on day 1 (goals already have last-activity timestamps) |
| `direction_vs_action` | ≥30 days of `decision_made` events with `goal_slug` populated |
| `stale_assumption` | ≥60 days of `last_accessed_at` updates on memories |
| `calibration_drift` | ≥4 weeks of C5 calibrator outputs |
| `principal_override` | ≥30 days of C16 `principal_override` events (matches cooldown window) |

#C18.2 **must** check baseline at runtime: insufficient baseline → skip that signal class for this run, log a structured warning. Do not fire false positives during bootstrap.

### Owner flip-the-flag protocol

Once #C18.2 is deployed:
1. Detection runs nightly in dry-run mode.
2. After 4 weeks, owner reviews dry-run output:
   - `SELECT signal_class, COUNT(*), AVG(severity) FROM events_canonical WHERE action='recalibration_proposed' GROUP BY signal_class`
   - Spot-check 5 random events per class. False-positive rate >50% → tune thresholds, leave flag off.
3. When dry-run output looks calibrated → owner edits YAML to `surfacing_enabled: true` via PR (reviewer-required edit per §2).

### Why not auto-flip after baseline elapses

Auto-flip removes the principal's calibration step. The exact failure mode of "alarm fatigue" starts here. The flip is intentionally a manual decision — once done, it's rarely revisited; 30 seconds of owner time saves months of dismissed surfacings.

---

## 6. False-positive calibration loop

Per [redesign §C18 measurement](jarvis-v2-redesign.md#how-measured-2) — surfacing acceptance rate is C18's primary success metric. Below-target acceptance is a signal that thresholds are wrong, not that the principal is ignoring a real problem.

### Outcome events

C12 surfacing widget emits `surfacing_outcome` after every recalibration is shown. Schema:

| Field | Value |
|---|---|
| `actor` | `principal` (when widget click) or `c12-aggregator` (when timeout-dismissed) |
| `action` | `surfacing_outcome` |
| `trace_id` | inherits from the originating `recalibration_proposed` event |
| `parent_event_id` | the `recalibration_proposed.event_id` |
| `payload.outcome` | `acted` \| `dismissed` \| `dismissed_with_reason` \| `timeout_dismissed` |
| `payload.signal_class` | mirrored from origin for fast aggregation |
| `payload.reason_text` | optional, principal-typed when `dismissed_with_reason` |

### Auto-tighten policy

Weekly C18 maintenance task (Sprint 36 #C18.2 ships this as `scripts/c18-tune-thresholds.py`):

1. For each `signal_class` with `enabled: true`:
   - `acceptance_rate = count(acted) / count(acted + dismissed*)` over last 4 weeks.
2. If `acceptance_rate < 0.25` AND total surfacings ≥ 10 (sample-size guard):
   - Apply the class's **fixed tighten step** to the primary threshold (table below), **bounded by configured ceiling**.
   - Emit a `decision_made` event recording the auto-tighten with full before/after values.
   - Owner sees the tighten in the next batched brief (one-line: "C18 auto-tightened `signals.goal_neglect.threshold_days` 14 → 17, acceptance 18% over 4w").
3. **Per-class tighten step + ceiling**: each primary threshold has an explicit step formula in its own units (no severity-stddev mixing). Ship in YAML so the owner can tune. Defaults:

| Signal | Primary threshold | Tighten step (each trip) | Ceiling | Direction |
|---|---|---|---|---|
| `goal_neglect` | `signals.goal_neglect.threshold_days` | `× 1.2` (round to int) | `30` days | up = quieter |
| `direction_vs_action` | `signals.direction_vs_action.gap_ratio_threshold` | `+ 0.05` | `0.50` | up = quieter |
| `stale_assumption` | `signals.stale_assumption.min_idle_days` | `× 1.2` (round to int) | `180` days | up = quieter |
| `calibration_drift` | `signals.calibration_drift.brier_ceiling_default` | `+ 0.02` | `0.40` | up = quieter |
| `principal_override` | `signals.principal_override.freq_threshold` | `+ 1` | `10` overrides | up = quieter |

**Why fixed steps not stddev:** primary thresholds carry *unit semantics* (days, ratio, count). `stddev(severity)` is in [0, 1] with no unit, so multiplying it into a threshold mixes units and produces unexplainable adjustments. Per-class explicit steps in native units stay implementable, auditable, and reversible by hand if a tighten goes wrong.

**One step per trip:** the loop tightens by exactly one step per quarterly check that crosses the threshold — never multi-step jumps. Slow corrections give the owner a chance to notice and override before the signal goes mute.

**`stale_assumption.min_confidence` is NOT auto-tuned.** It's an AND-gate filter, not the primary firing threshold. Owner-only via YAML PR.

### What auto-tighten does NOT do

- **Loosen thresholds** — never. Loosening = more surfacings = drowning the principal. Owner-only operation, via YAML PR.
- **Disable a signal class** — `enabled: false` is owner-only.
- **Tune cooldown windows** — separate dimension; out of scope.
- **Tune auxiliary parameters** — only the primary threshold per §1 is in scope.

### Why auto-tighten not auto-disable

Auto-disable would silently remove a signal class without owner awareness — exactly the kind of drift C18 is meant to detect. Auto-tighten keeps the signal alive but reduces noise; the owner sees the tightening in the next brief and can override.

---

## 7. What this doc does NOT lock

- **Concrete SQL queries** — covered by #C18.2.
- **Severity formulas per signal class** — only the parameter shape (`severity_per_overshoot`, etc.) is locked here; specific math lives in #C18.2.
- **Surfacing widget UI / message phrasing** — covered by #C18.3 (Haiku-rendered prose).
- **C12 batched-brief plumbing** — separate component; #C18.3 wires C18's signals into the existing aggregator.
- **C5 calibrator output schema** — defined by C5 sprint when it lands. Until then, `calibration_drift` signal stays `enabled: false`.
- **C16 `principal_override` event payload** — defined by C16 sprint. Until then, `principal_override` signal stays `enabled: false`.
- **Cross-trace surfacing dedup** — current cooldown is per-trace per-class; cross-class dedup (e.g. don't surface both `goal_neglect` and `direction_vs_action` for the same goal in the same brief) is a future optimization, not a Day-1 requirement.

---

## 8. Acceptance check (this issue)

- [x] Drift-signal taxonomy locked in §1 with substrate-readiness column.
- [x] Threshold config schema in §2 with M2-strong edit class.
- [x] `recalibration_proposed` payload shape unambiguous in §3 (extends C17 vocabulary; `surfacing_outcome` also reserved).
- [x] Surfacing routing contract in §4 (C12 picks up; cooldown rule; max 5/brief; no real-time).
- [x] Bootstrap protocol in §5 (gate flag; per-signal baseline minima; owner flip protocol).
- [x] False-positive calibration loop in §6 (auto-tighten policy with bounded ceilings; never auto-loosen).
- [x] Forward-deferrals in §7 so #C18.2/.3 don't expand scope.
- [x] Cross-link from `jarvis-v2-redesign.md` C18 section (added in same PR, mirrors C17 pattern).

---

## References

- [`jarvis-v2-redesign.md` §C18](jarvis-v2-redesign.md#c18--proactive-challenger) — full L1+L2 spec.
- [`jarvis-v2-redesign.md` §C18 L3 leans](jarvis-v2-redesign.md#c18--proactive-challenger-1) — SQL detection, YAML thresholds, Haiku surfacing renderer, C12 routing.
- [`c17-events-substrate.md`](c17-events-substrate.md) — events table contract; C18 is the second writer (after `record_decision` in #477).
- [`c17-events-substrate.md` §5](c17-events-substrate.md) — action vocabulary; C18 extends with `recalibration_proposed` + `surfacing_outcome`.
- [`jarvis-v2-redesign.md` §Methodology — Tier-A migration order](jarvis-v2-redesign.md#methodology) — C18 sequenced after C5/C16; this design 1-pager is forward-locking and ships ahead, but #C18.2 implementation respects the dependency by gating `calibration_drift` and `principal_override` signals behind `enabled: false` until C5/C16 land.
- Memory `c18_proactive_challenger_2026_04_28` (decision/jarvis) — owner-stated motivation and L1 framing.
