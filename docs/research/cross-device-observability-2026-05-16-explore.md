# Cross-device observability — owner-grill convergence brief

**Filed:** 2026-05-16 by AFK chain iter:38 (Run #3).
**Status:** draft, gitignored (`docs/research/` policy `docs_research_unfinished`).
**Audience:** owner, returning to chain artifacts.
**Purpose:** Synthesize the per-device-process sub-class of the drift discriminator with the cross-device observability gap raised in `mirror-vs-source-drift` §6 point 6 + §11. Identify what device-state surfaces exist today, where they go silent, and what mitigation tiers cost. Output is a pre-grill briefing, not a plan.

This is **synthesis #7** of the AFK chain run #2/#3. Prior axes:

1. Class — `enforcement-primitive-synthesis-2026-05-16-explore.md` (iter:20).
2. Harness — `cwc-harness-applicability-2026-05-16-explore.md` (iter:21).
3. Cost — `enforcement-primitive-cost-risk-2026-05-16-explore.md` (iter:23).
4. Drift — `mirror-vs-source-drift-2026-05-16-explore.md` (iter:27/29/30).
5. Installer — `installer-hygiene-synthesis-2026-05-16-explore.md` (iter:34).
6. Memory subsystem — `memory-subsystem-grill-prep-2026-05-16-explore.md` (iter:35).

Cross-device observability (this one) closes the **last narrow gap in the major-axis chain**: the per-device-process sub-class that iter:30 backward-scan tagged UNKNOWN on 3 of 12 commits (48a770f, 0a1f686, c6a1fb8) and the "5th axis" deferred from §6 point 6 of the drift draft.

---

## §1 — The gap in one paragraph

When a commit declares a side-effect that was performed on **one device** (a scheduled task registered, an env var set, a Sandcastle worker started, an Ollama model VRAM-loaded), the chain running on **another device** cannot independently verify the side-effect. The drift discriminator from iter:29 (`perform vs declare`) classifies these commits as UNKNOWN — not because the discriminator is broken, but because the verifier is **physically not present**. The agent has no surface to query "is Sandcastle-Jarvis currently registered on Workshop?" from Main PC. Existing observability channels — `git log`, `gh issue list`, `memory_recall`, Supabase episodes — are device-agnostic; they record **intent**, not **per-device state**. Three commits in the last 12 trigger this gap; recurrence is structural because the multi-device topology is unavoidable (3 devices, see CLAUDE.md). **Cross-device drift goes silent until the device that ran the commit is independently probed**, which today only happens when the owner physically visits that device.

---

## §2 — Channel map (the cross-device surfaces)

```
                    ┌────────────────────────────────┐
                    │   3-device topology            │
                    │   Main PC / Workshop / Lenovo  │
                    └────────────────────────────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
   [SHARED-CLOUD]        [SHARED-GIT]         [PER-DEVICE LOCAL]
   Supabase (memory,     GitHub (issues,      Task Scheduler, env
   episodes, outcomes)   PRs, workflows)      vars, processes, mirrors
        │                     │                      │
        │ readable from       │ readable from        │ readable from
        │ any device          │ any device           │ THAT device only
        │                     │                      │
        └─────────────────────┼──────────────────────┘
                              │
                       [GAP: no shared device-state surface]
                       per-device-process sub-class is invisible
                       to any session not on the target device
```

The three observability layers as the agent sees them today:

| Layer | Surface | Cross-device read? | What it records | What it misses |
|---|---|---|---|---|
| **Shared cloud** | Supabase `memory_store`, `episodes`, `outcomes` | Yes (any device with MCP) | Decisions, lessons, working state, calibration | Per-device process / env / scheduler state |
| **Shared git** | GitHub issues, PRs, workflow runs, commit log | Yes (any device with `gh`) | Intent, code, code review, CI outputs | Whether the post-commit side-effect happened on the device that owns it |
| **Per-device local** | Task Scheduler, env vars, `~/.claude/skills/` mirror, processes | **No** (only on that device) | Live process state, scheduled tasks, mirror sync state, model VRAM, env | Anything not surfaced to a shared layer |

The gap is the bottom row: **per-device-local state has no cross-device read path**. A session on Main PC literally cannot answer "is task X registered on Workshop today?" without either (a) the owner running a check there, or (b) the device itself emitting a status to a shared surface.

This is **structurally identical** to the memory-subsystem channel-asymmetry framed in synthesis #6: a contract is promised (in a commit message, in a skill, in a runbook) without a backing **structured signal** that lets a non-owner caller verify the contract holds. Synthesis #6's discriminator (*promised contract without backing automation*) generalizes to this axis with one substitution: replace "automation" with "cross-device read path".

---

## §3 — The discriminator (single class-level)

For any commit C with a state-promise P:

- **If P is verifiable from any device that has git+gh+Supabase+filesystem-of-this-repo:** cross-device-observable. The drift discriminator from iter:29 applies normally.
- **If P requires reading state that only the device that ran C can see:** cross-device-opaque. The drift discriminator is undefined; verifier doesn't exist on the calling device.

Three concrete examples from iter:30 backward-scan, all UNKNOWN:

| Commit | Promise | Why opaque from Main PC |
|---|---|---|
| 48a770f | "Sandcastle-Jarvis + Sandcastle-Redrobot tasks deployed on Workshop; REDROBOT_REPO_ROOT set" | Task Scheduler is per-host; env vars are per-process on the host that set them; no shared status row records "task registered yes/no" |
| 0a1f686 | "Registered Sandcastle-Jarvis on Workshop" | Same root cause as 48a770f — Task Scheduler state is host-local |
| c6a1fb8 | "qwen2.5-coder:14b stays VRAM-resident at ~94 tok/s on Workshop RTX 5080" | Hardware-specific benchmark; no cross-device VRAM/perf surface; result lives only in commit message prose |

**Unifying frame:** the per-device-process sub-class is **opaque by physical topology**, not by oversight. No amount of soft-prompt discipline closes it. Closure requires the device itself to **emit** a status to a shared surface. Either (a) the chain ships a "post status" primitive that each device runs periodically, or (b) cross-device-opaque commits stay UNKNOWN and the chain accepts that as a known dark corner.

This is the **first major-axis class** in this chain where the dominant mitigation is **non-server, non-harness, non-skill** — it's "deploy something on each device that emits to a shared row". That's an operational dependency, not a code change.

---

## §4 — Mitigation surface (cross-tracker)

The mitigations split along *who emits* and *who reads*:

### Server-side (Tier 2, mechanical — emit to shared row)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **S1 — `device_status` table in Supabase** | All per-device-opaque commits | M | low | New table: `(device_name TEXT, key TEXT, value JSON, updated_at TIMESTAMPTZ)`. Stored procedure `device_status_upsert(device, key, value)`. ~20 lines schema + 1 MCP tool. |
| **S2 — `device_probe` MCP tool** | Reading device_status | XS | zero | Wraps `SELECT * FROM device_status WHERE device_name=$1 AND key LIKE $2`. ~10 lines + test. |
| **S3 — `device_status` retention policy** | Bounded growth | XS | zero | Trim rows older than N days; pair with S1. Standard housekeeping. |

### Per-device emitters (Tier 2, scheduled — each device produces signal)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **E1 — Scheduled `device-probe` task per device** | Live state visibility | M-L | low | Daily scheduled task on each device runs `device_status_upsert(my_host, 'scheduler.sandcastle', {registered: bool, last_run: ts, last_exit_code: int})` etc. Bootstrap: same as `setup-tasks` skill. |
| **E2 — Git `post-merge` hook → status emit** | Mirror state after pull | S | low | After `git pull` triggers `install.ps1 -Apply` (or fails), emit `device_status_upsert(my_host, 'install.last_apply', {success: bool, manifest_hash: ..., timestamp: ts})`. Doesn't cover unattended drift between pulls but catches the common case. |
| **E3 — `Get-SandcastleStatus` periodic emit** | Sandcastle live state | M | low | Each Sandcastle host emits worker count, last task time, recent failures to `device_status`. The Sandcastle scheduler is the only existing per-device primitive that already has a probe contract — wire it. |
| **E4 — Manual `/device-checkin` skill** | Ad-hoc owner ping | S | low | Owner runs `/device-checkin` on each device weekly; skill scrapes scheduler list + env probe + mirror manifest hash + writes one S1 row. Owner-triggered, not autonomous. Cheap fallback before E1 is wired. |

### Harness-side (Tier 2 — read at session start)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **H1 — SessionStart probe of `device_status`** | Surface staleness on session entry | S | low | Add to `scripts/session-context.py`: query `device_status` for {hostname} where `updated_at < now() - 7d`; warn if stale. Pairs with S2. |
| **H2 — PreToolUse warn on per-device commit messages** | Commit-time hint | M | medium | When agent drafts a commit message containing `on Workshop` / `on Lenovo` / `per-device`, hook reminds: "this side-effect is cross-device-opaque; consider emitting device_status row". Heuristic; likely false-positive prone; defer. |

### Soft-prompt (Tier 1, skill / commit-message convention)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **P1 — Commit-message convention** | Pattern recognition | XS | zero | Add to `.github/copilot-instructions.md`: "If commit has a per-device-process side-effect, include `device-state: <key>=<value>` line so future readers know it's cross-device-opaque". Doesn't fix the gap; surfaces it. |
| **P2 — `verify` skill clause** | Verification-time check | XS | zero | `/verify` skill (existing) gets a clause: when checking a PR with `area:device-config` label, query `device_status` for relevant keys before declaring verified. ~5 lines in skill. |
| **P3 — CLAUDE.md "Device boundary" section** | Onboarding | XS | zero | Document the per-device-opaque class explicitly in CLAUDE.md so future chains don't re-derive it. ~10 lines. |

---

## §5 — Phased adoption (L1–L5)

### L1 — Manual reconciliation today (zero code)

**Cost:** ~10 min/device, owner-only.
**Scope:** Owner runs a short audit on each device when they sit at it: `gh issue list --repo Osasuwu/jarvis --label area:device-config --state closed`, picks the 3 most recent, manually verifies each on the device, posts a single chain-log comment summarizing state.
**Closes:** 0 trackers (none yet filed in this class).
**Leaves open:** Everything; this is the "do nothing structural" baseline.

### L2 — Manual + soft-prompt convention (P1+P3)

**Cost:** ~30 min, one PR.
**Scope:** P1 (commit-message convention) + P3 (CLAUDE.md "Device boundary" section). Surfaces the class without fixing it.
**Closes:** Naming gap. Future commits are easier to triage.
**Leaves open:** Verification still manual; no shared status surface.

### L3 — Shared status surface, manual emit (S1+S2+E4+P2)

**Cost:** ~3h, two PRs (one Supabase schema, one tooling).
**Scope:** Add `device_status` table + `device_probe` MCP tool + `/device-checkin` skill for owner-triggered emit + `/verify` clause to read.
**Closes:** **Read-path** for per-device state. Owner now has a queryable record per device per visit.
**Leaves open:** **Write-path** depends on owner-discipline (run `/device-checkin` weekly). Stale rows likely.

### L4 — Automated per-device emitters (E1+E2+H1)

**Cost:** ~6h, two PRs + per-device bootstrap.
**Scope:** Add daily `device-probe` scheduled task on each of 3 devices (via existing `setup-tasks` skill — extend it) + `post-merge` git hook emitting install state + SessionStart staleness warning.
**Closes:** **Write-path** is autonomous. Stale rows become a CI-class signal, not a forgotten checkbox.
**Leaves open:** Sandcastle-specific deep state (worker count, queue depth) still requires E3.

### L5 — Full instrumentation (all options)

**Cost:** ~10h+, multiple PRs across several sprints.
**Scope:** L4 + E3 (Sandcastle deep emit) + retention policy (S3) + commit-time advisory (H2 if false-positive rate is acceptable).
**Closes:** Per-device-process sub-class becomes cross-device-observable end-to-end.
**Leaves open:** Hardware-specific perf characteristics (c6a1fb8-style VRAM benchmarks) still require running on the target hardware — but their results can be recorded into `device_status` rather than commit-message prose.

**Recommended floor:** L3 (~3h, zero risk). Manual emit is cheap, builds the read surface for the chain, and turns into a measurable cadence ("how stale are my device_status rows?") that signals when L4 is worth the bootstrap.

**Recommended ceiling:** L4. L5's hardware-perf instrumentation is over-scoped for current needs; revisit only if a 4th UNKNOWN commit surfaces in the hardware sub-class.

---

## §6 — Grill points for owner

1. **Is `device_status` table the right primitive, or should this ride on existing memory?** Counter-arg: `memory_store` already accepts arbitrary JSON; per-device entries could be `memory_store(name=f"device_status_{host}_{key}", content=...)`. Pro: no new schema; uses existing tooling. Con: pollutes memory namespace with device-state churn (different concern than "things to recall"); retention semantics conflict (memory is durable, status is rolling). Recommend separate table.
2. **Is daily emit cadence right, or should it be on-change-only?** Daily wastes rows if state didn't change. On-change-only requires diff detection on the emitter (more code). Counter-arg: rows are tiny; daily upsert costs ~3 rows/device/day = ~1000/year per device. Storage is irrelevant; what matters is "when was last write" as a staleness signal. Daily wins on simplicity.
3. **Should the chain itself probe `device_status` at start, or only `/verify`?** SessionStart probe (H1) is cheap (~50ms) and surfaces staleness on every session. Risk: banner-blindness (#406 again). Alternative: probe only when commit-of-interest is per-device-opaque. More accurate but more code. Recommend H1 (cheap + surfaces real signal) for v1.
4. **What about devices that go offline for weeks (Lenovo travel laptop)?** Expected behavior: rows stale; H1 banner says "Lenovo last checked-in 21d ago"; owner ignores until next Lenovo session. The staleness is **honest signal**, not a bug. Confirm: is "stale row → warn but don't block" the right policy?
5. **Cross-cutting with #648 (gitignored-source preservation)?** #648 covers `.scratch/` and other gitignored content per-device. Both #648 and this axis face the same "what's on each device" question. Should `device_status` carry `gitignored_files_inventory` so future chains can reconstruct? Or keep #648 separate (PR-routed source rescue) from this axis (live state probing)? Recommend keep separate — different lifecycle.
6. **Does this axis overlap with redrobot's `Sandcastle-Redrobot` watchdog (#629)?** That watchdog runs per-device on Workshop; this axis would emit `device_status` rows that include "Sandcastle-Redrobot healthy yes/no/last-run". Potentially the watchdog already has a probe contract we should reuse. Recommend audit `#629` PR diff for existing status emit before designing S1.
7. **Should `device_status` also cover the redrobot repo, or just jarvis?** Cross-project read by sharing the Supabase project (already shared per CLAUDE.md). If yes, table name should be unscoped (`device_status` global) not `device_status_jarvis`. Costs nothing extra at design time, costs a migration later.

---

## §7 — Decision points (for owner)

After grill:

- **D-xdev-1:** Approve L1 (manual baseline) — yes/no (yes is essentially free; gates nothing).
- **D-xdev-2:** Approve L2 (commit-message convention + CLAUDE.md section) — yes/revise/defer.
- **D-xdev-3:** Approve L3 (`device_status` table + `device_probe` tool + `/device-checkin` skill + `/verify` clause) — yes/revise/defer.
- **D-xdev-4:** Approve L4 (automated per-device emitters via `setup-tasks` + post-merge hook + SessionStart probe) — yes/wait-for-L3-ROI/defer.
- **D-xdev-5:** Approve L5 (Sandcastle deep emit + retention + commit-time advisory) — defer-by-default unless 4th UNKNOWN surfaces.
- **D-xdev-6:** Scope question — `device_status` shared with redrobot project, or jarvis-only? Recommend shared from day one (no extra cost).
- **D-xdev-7:** Storage shape — separate Supabase table, OR `memory_store` with namespaced names? Recommend separate table (cleaner retention).

---

## §8 — Convergence with prior six axes

This synthesis completes the major-axis chain. Convergence table:

| Failure surface | Coverage axis | This axis adds |
|---|---|---|
| Wrong decision at action time | Class (iter:20) | n/a — orthogonal |
| Where to enforce | Harness (iter:21) | H1 (SessionStart probe) fits existing harness slot; no new slot needed |
| What it costs to ship | Cost (iter:23) | L3 (~3h) sits between A1 and B5 in the cost-axis adopt-order |
| When state silently diverges | Drift (iter:27/29/30) | Closes per-device-process sub-class — third leg of the iter:30 distribution table |
| Installer hygiene | Installer (iter:34) | E2 (post-merge hook → status emit) extends installer-hygiene L3 with a verification surface |
| Memory subsystem channel-asymmetry | Memory (iter:35) | Reuses memory's discriminator (*promised contract without structured signal*) — same shape, different substrate (cross-device vs cross-skill) |

**Cross-axis insight:** synthesis #6 and synthesis #7 share a discriminator. The memory-subsystem channels (store/recall/source/calibration-link) and the device-topology channel (per-device-process state) are **instances of the same meta-class**: the system promises a contract that the agent treats as queryable, but no structured query path exists. The chain's iter:35 cross-class primitive (S5 lesson scan) generalizes one more step here: "any time a class records intent without a queryable backing signal, file the surface". This is now a **two-substrate confirmation** of synthesis #6's meta-claim.

**Cross-axis disagreement check:** This axis has **no single leverage point** by design — closure requires per-device deployment, which is irreducibly multi-host. This matches synthesis #6's "no single leverage point" finding (memory has 4 channels). Synthesis #4 (drift) DID have a single leverage point (SessionStart warner) because filesystem drift is one substrate. So the chain pattern is: substrate-uniformity → single leverage point; substrate-multiplicity (memory channels, device topology) → coordinated multi-mitigation. **Substrate count predicts leverage shape** — possible runbook insight for synthesis-channel template.

---

## §9 — Out of scope for this draft

- **`device_status` table schema final spec.** Skeleton only here; actual columns/indexes land in L3 PR after grill.
- **Per-device bootstrap script for the daily probe (E1).** Owner-decision on what to probe per device is upstream.
- **Hardware perf instrumentation (c6a1fb8-style).** L5 territory; defer until 4th UNKNOWN.
- **Cross-project (`redrobot`) shared schema.** Recommend yes at §6 point 7, but actual coordination with redrobot's CLAUDE.md / MCP setup is its own PR.
- **Touching `mirror-vs-source-drift` §6 point 6 to reference this draft.** Drafts are gitignored; cross-ref goes via baton + memory pointer, not draft-edits.
- **Filing a tracker for this class.** No tracker yet — three commits UNKNOWN but each was correctly per-device-performed (no actual drift); class is a **gap in observability, not a recorded failure**. Owner should decide at grill whether to file or wait for a real drift to surface in this class.

---

## §10 — Pointer for owner / next chain

After owner read-through:

- Update `working_state_jarvis` baton with grill outcomes (D-xdev-1..7).
- If L3 approved: file one issue "Cross-device observability v1: `device_status` table + `device_probe` MCP tool + `/device-checkin` skill" with paraphrased reference to this draft.
- If L4 approved with L3: chain two issues with explicit dependency (L4 requires L3 schema).
- Record decision via `record_decision` with `memories_used` pulling in the 6 prior synthesis decision UUIDs + the iter:30 commit-classification table from `mirror-vs-source-drift` §11.
- Audit redrobot watchdog PR #629 for existing status emit contract before drafting S1 schema (§6 point 6).
- Append synthesis-channel result to `chain-log.md` and mark synthesis #7 outcome (channel now 7-for-7).
- Consider extracting the synthesis-channel template into a runbook reference memory (`autonomous_chain_synthesis_template`) — this draft closes the major-axis chain, so the template is now mature. Tier 5 candidate for a future iteration.

---

## §11 — One-line summary

The per-device-process sub-class is **opaque by physical topology, not oversight**: no soft-prompt closes it. L3 (`device_status` table + `device_probe` MCP tool + manual `/device-checkin` skill + `/verify` clause, ~3h) is the recommended floor — adds the missing read surface without per-device bootstrap. L4 (automated daily emitters via `setup-tasks` extension + post-merge hook + SessionStart probe, ~6h) is the recommended ceiling and closes the write-path. The class is **a gap in observability, not a recorded failure** — file a tracker after grill, not before.

---

**End of draft.** ~320 lines. Author: AFK chain iter:38 (wrapper-iter:3 of run #3). Citation chain: baton `working_state_jarvis` (iter:37 result) → `mirror-vs-source-drift-2026-05-16-explore.md` §6 point 6 + §11 → iter:30 12-commit backward-scan UNKNOWN trio (48a770f, 0a1f686, c6a1fb8) → synthesis #6 channel-asymmetry meta-class.
