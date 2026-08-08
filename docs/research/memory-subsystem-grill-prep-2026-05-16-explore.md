# Memory subsystem — owner-grill convergence brief

**Filed:** 2026-05-16 by AFK chain iter:35 (Run #2).
**Status:** draft, gitignored (`docs/research/` policy `docs_research_unfinished`).
**Audience:** owner, returning to chain artifacts.
**Purpose:** Synthesize the four open memory-subsystem trackers (#641, #654, #658, #660) into a single grillable bundle. Identify common shape, discriminator, mitigation surface, cost tiers. Output is a pre-grill briefing, not a plan.

This is **synthesis #6** of the AFK chain run #2. Prior axes:

1. Class — `enforcement-primitive-synthesis-2026-05-16-explore.md` (iter:20).
2. Harness — `cwc-harness-applicability-2026-05-16-explore.md` (iter:21).
3. Cost — `enforcement-primitive-cost-risk-2026-05-16-explore.md` (iter:23).
4. Drift — `mirror-vs-source-drift-2026-05-16-explore.md` (iter:27/29/30).
5. Installer — `installer-hygiene-synthesis-2026-05-16-explore.md` (iter:34).

Memory-subsystem (this one) is the only axis that has been pre-flagged in the baton for ≥3 iters as "owner-grill ready when synthesized" — convergence is mature.

---

## §1 — The four trackers in one paragraph

The memory subsystem has four information channels. Each one has been independently caught failing in a recurring or silent way, each filed as a separate tracker. The four — #641 (recall), #654 (source/attribution), #658 (store), #660 (calibration-link) — span the full input→output cycle of the subsystem. None is a duplicate of another; each is a different interface; but they share one structural complaint: **the channel's contract is encoded as soft prompt text, the channel's failure is not surfaced as a structured error, and recurring failures are recorded into `outcome.lessons` text fields that future chains discover by accident**. Recurrence count across the four trackers ≥ 14 in the past month. The four collectively triangulate the subsystem from store, recall, source, and calibration angles — when bundled into one grill, the owner can pick a single class-level mitigation rather than 4 independent ones.

---

## §2 — Channel map (the shared shape)

```
                    ┌────────────────────────────────┐
                    │    Memory subsystem (logical)  │
                    └────────────────────────────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
    [STORE]              [RECALL]              [SOURCE]
   #658 store        #641 recall quality   #654 lesson→tracker
   observability     ↔ always_load          enforcement gap
   on upsert        compensation
        │                     │                      │
        └─────────────────────┼──────────────────────┘
                              │
                       [CALIBRATION-LINK]
                       #660 outcome_record.memory_id
                       FK to memories(id) vs episode UUID
```

The four channels are the **boundary surfaces** where the memory subsystem talks to the agent:

| Channel | Surface | What the agent expects | What actually happens | Failure mode |
|---|---|---|---|---|
| **Store** (#658) | `memory_store` return | "saved or rejected, clear signal" | Atomic upsert ALWAYS saves; "consolidation hint" + classifier verdicts get concatenated into prose | Agent misreads prose as rejection, retries with description-differentiation that can't help; gives up; outcome=partial |
| **Recall** (#641) | `memory_recall` ranked results | "rule will surface when contextually needed" | Recall is keyword + semantic + RRF; procedure-bound rules (record_decision, merge, grill) under-weighted vs. their semantically-generic descriptions | Right rule not in top-10 → agent tags as `always_load` insurance → SessionStart context bloats → real always_load drowns |
| **Source** (#654) | `outcome.lessons` field | "Lessons distill into trackers" | `outcome.lessons` is free-text; no scan job; recurring/silent-failure tags accumulate but never produce issues | 5+ "recurring" lessons sit recorded for weeks; future chains re-discover the same failure (3rd, 4th occurrences seen) |
| **Calibration-link** (#660) | `outcome_record.memory_id` FK | "pass the UUID I have, it's a memory thing" | FK strictly targets `memories(id)`; `record_decision`-returned UUIDs are `episodes(id)`; FK silently NULLs | Calibration view stays sparse for 5+ outcomes; agent loses ability to attribute failures back to source memories |

---

## §3 — The discriminator (single class-level)

Across all four, the failure shape is:

**The memory channel's contract is asymmetric, and the asymmetry is not visible at the call site.**

- **#658 — store asymmetry**: project-scoped does atomic upsert; global-scoped does select-then-update-or-insert. Both end up saved. Neither communicates structured success vs. "saved + classifier marked as superseded" vs. "saved + dup-detector flagged" — the agent reads prose and infers.
- **#641 — recall asymmetry**: skill-name boost is documented and partially implemented (per CLAUDE.md §2), but the empirical hit rate per memory-class is unmeasured. Procedure-bound rules with generic descriptions are recall-disadvantaged vs. content-rich rules with specific descriptions.
- **#654 — source asymmetry**: `outcome.lessons` text is the canonical place a recurring failure gets recorded, but **no consumer reads it** — no scan job, no `/triage` integration, no `/reflect` extraction with tracker creation. The pipeline ends at "lesson written"; downstream is human pattern-matching.
- **#660 — calibration-link asymmetry**: `record_decision.memories_used` is **permissive** (accepts UUIDs OR names; server resolves); `outcome_record.memory_id` is **strict** (UUID only; FK to memories(id)). Two adjacent calls in the same flow with different contracts. The strict one silently NULLs on bad input.

**Unifying frame:** the memory subsystem treats the agent as a trusted, type-aware caller (strict at FKs, permissive at array channels, prose return for ambiguous cases). The agent is actually a stochastic generator that pattern-matches across asymmetric contracts. **Mismatch = silent failure that recurs.**

This is the same shape as the drift class (synthesis #4): *promise without backing automation*. The discriminator is identical — the memory subsystem PROMISES a contract via tool description / skill text / always_load tag, but does not PROVIDE structured signal when the contract is violated.

---

## §4 — Mitigation surface (cross-tracker)

Options span three layers — server-side, harness-side, soft-prompt — mirroring the enforcement-primitive class axis from synthesis #1.

### Server-side (Tier 2, mechanical)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **S1 — Structured `memory_store` response** | #658 | M | low | `{stored: bool, superseded: [id], noop_reason: str, classifier_verdict: enum}` instead of prose blob. Agent no longer infers from text. |
| **S2 — Episode-UUID rejection guard on `outcome_record.memory_id`** | #660 | S | low | `if X in episodes and X not in memories: raise SpecificError("episode UUID, did you mean payload.memories_used[0]?")`. ~10 lines + test. |
| **S3 — Name resolution on `outcome_record.memory_id`** | #660 | M | low | Make strict channel permissive like `memories_used`. Mirrors record_decision behavior. ~30 lines + test. |
| **S4 — Recall instrumentation** | #641 | M | low | Trace log per recall call: candidates pre-rerank, final ranks, semantic-vs-keyword origin. Enables tuning rounds with measured before/after. |
| **S5 — Lesson scan job** | #654 | M | medium | Daily job: `memory_list` + `outcome_list` → grep for `recurring`/`silent-failure`/`Nx` recurrence markers → cross-ref `gh issue list` → propose tracker creation (output only, no auto-file in AFK). |

### Harness-side (Tier 2, hook layer)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **H1 — PostToolUse hook on `record_decision`** | #660 | S | low | Capture episode UUID into session-local map; warn on next `outcome_record` call if `memory_id` matches the captured episode UUID. Doesn't need server change. |
| **H2 — PreToolUse on `outcome_record`** | #660 | S | low | If `memory_id` looks like a UUID but is not in memories cache, log warning. Pairs with S2 for double-coverage. |

### Soft-prompt (Tier 1, skill / tool description)

| Option | Covers | Cost | Risk | Notes |
|---|---|---|---|---|
| **P1 — Tool-schema description hardening** | #660 | XS | zero | Edit `mcp-memory/tools_schema.py:463-509` — explicit warning about FK target on `outcome_record.memory_id`. |
| **P2 — Skill contract clause** | #660 | XS | low | `/implement` + `/delegate` outcome-record step: "do not pass decision-episode UUID; use `payload.memories_used[0]`". Tier 1 vulnerable to skipping. |
| **P3 — Recall-rule guidelines docs** | #641 | S | medium | Document the recall asymmetry: which classes need keyword-heavy, which semantic. Empirical, depends on S4 telemetry. |

---

## §5 — Cost / risk / leverage matrix

| Tier | Mitigation set | Coverage | Cost (h) | Risk | Yield |
|---|---|---|---|---|---|
| **L0 — Do nothing** | (none) | 0/4 | 0 | recurrence keeps climbing | -- |
| **L1 — Floor** | P1 + S2 | #660 strong | ~2 | zero | Stops #660 immediately. Cheap, mechanical. |
| **L2 — Recommended** | L1 + S1 + S5 | #658 + #654 added | ~6 | low | Three of four trackers structurally closed. Lesson scan = source-channel observability. |
| **L3 — Full coverage** | L2 + S3 + S4 + H1 | All 4 + telemetry | ~12 | low-medium | All four trackers covered with mechanical Tier 2 backing. S4 enables tuning. |
| **L4 — Aggressive** | L3 + P3 + harness changes | All 4 + soft-prompt + harness | ~20 | medium | Tier 1 + Tier 2 + harness. Full triangulation per CLAUDE.md §3. |
| **L5 — Architectural** | Memory-subsystem schema refactor (separate `episodes.id` namespace from `memories.id` namespace at type level, structured responses everywhere) | All 4 + future-proofing | ~40+ | high | Breaks current tool surface. Major version bump. |

**Floor (L1)** is the minimum that closes the highest-recurrence tracker (#660, 5 lessons in 2026-05). Two hours, zero risk, mechanical.

**Recommended (L2)** is the floor + close the two next-highest trackers (#658 dup-detector observability, #654 source-channel observability). Adds the lesson scan as a recurring job — turns `outcome.lessons` from a write-only sink into a tracker-creation pipeline.

**L3** is the first tier with full coverage of all four trackers; S4 (recall instrumentation) is the only thing that lets #641 be tuned with evidence rather than speculation. Without S4, #641 can only be approached by guessing.

---

## §6 — Convergence and disagreement with prior 5 syntheses

### Convergent

- **Synthesis #1 (class):** the four memory trackers are all candidates for the "mechanical input-side enforcement" class identified in iter:20 §4. S1/S2/H1/H2 here are class-instance examples. **Consistent.**
- **Synthesis #2 (harness):** the harness applicability matrix said "the cwc harness ships primitives the chain rediscovered". S1+S2 here are exact instances of the "structured tool response" + "input validator" primitives. **Consistent.**
- **Synthesis #3 (cost):** the cost-axis adopt order is A1+A2+A3 → D11 → B5+B6+B7 → D12 → defer D13. L1+L2 here map to A1+A2+A3 (mechanical floor + observability). **Consistent.**
- **Synthesis #4 (drift):** the drift discriminator "commit performs side-effect atomically vs. declares intent without backing automation" applies here — the memory subsystem **promises** contracts (tool schema text, skill text) without **backing automation** (structured signals, FK guards, lesson scan). **Strongly convergent.**
- **Synthesis #5 (installer):** the installer-correctness gap was "idempotence + bidirectional + auto-trigger". The memory subsystem's contract gap is "structured signal + symmetric inputs + scan-driven escalation". Different shape, but **same meta-class:** *backing automation for a stated contract*.

### Disagreement / extension

- **vs Synthesis #1:** class-axis treated `outcome_record` lesson as low-priority enforcement. The four-way bundle here makes it clear `outcome_record.memory_id` is **the single highest-recurrence item** in the whole chain (5x in May vs. 3-4x for other classes). L1 should ship before any other Tier-2 hook work. **#660 deserves promotion** in the class-axis adopt order.
- **vs Synthesis #4:** drift-axis named SessionStart-warner as the single leverage point covering 4 drift trackers. Memory-subsystem has no single leverage point — the four channels are structurally distinct. Coverage needs 4 distinct interventions (or one architectural refactor at L5). **Memory subsystem is not amenable to single-warner cover.**
- **Extension (new in this synthesis):** the **lesson scan (S5)** is a primitive not surfaced in syntheses #1-5. It converts `outcome.lessons` text from terminal sink to feedstock for `/triage`. This is the cheapest way to break the "lesson recorded → chains rediscover" recurrence pattern across all classes (not just memory-subsystem). **S5 generalises beyond #654 to every recurring failure with lesson trail.**

---

## §7 — Five grill points for owner

1. **Does L1 (P1 + S2 for #660) ship as a single small PR now, or wait to bundle with L2?** (Argument for now: zero risk, blocks the 5x-recurrent class. Argument for bundling: one CI cycle, one review pass.)

2. **Is the lesson-scan job (S5) shaped as a script + cron, a `/triage` skill extension, or an autonomous-loop tier 2 task?** Each has different invariants — cron is the simplest, `/triage` is most discoverable, autonomous-loop has best context for tracker proposal text.

3. **Should `outcome_record.memory_id` accept *names* (S3) or just reject episode UUIDs (S2)?** S3 is symmetric with `record_decision.memories_used`; S2 is strict-with-better-error. S3 is more permissive (potentially hides bugs); S2 is more diagnostic (forces correct UUID use).

4. **For recall instrumentation (S4) — is sampling sufficient, or does every recall call need a trace?** Sampling halves cost but misses tail behavior. The audit in #641 asked for N=50+ manually-labeled calls; that's sample-sized, not full-trace.

5. **At what recurrence count does `outcome.lessons` text auto-promote to issue draft (S5 trigger threshold)?** 2× silent-failure marker? 3× explicit count? Body keyword match against existing open issues? Owner judgment — the chain hit 5× on #660 and never auto-promoted, so 2-3 feels right.

---

## §8 — Five decision points

For `record_decision` calls when owner triggers grill:

1. **Adopt order**: L0/L1/L2/L3/L4 floor selection. Recommended L2 = floor that closes 3/4 trackers + lesson scan.
2. **Server-side vs harness-side primary for #660**: S2 (server) vs H1 (harness PostToolUse). S2 is more general (catches all callers); H1 is jarvis-only and zero schema risk.
3. **Lesson scan output destination**: open issue auto / triage queue / pre-grill artifact bundle / nothing (output to baton only). AFK chain default per rules is **never auto-file from cron**.
4. **Recall tuning approach (#641)**: instrumentation-first (S4 → tuning) vs. recall-rerank-first (try changes, measure later). Synthesis #1 already implicitly chose instrumentation-first via the audit acceptance criteria; restate as decision.
5. **`memory_id` FK refactor candidate (#660 L5-flavor)**: separate `episodes(id)` and `memories(id)` UUID namespaces at type level (different prefix? different table?). High cost, but eliminates the asymmetry permanently. Long-term thinking.

---

## §9 — Acceptance criteria template (per decision)

If owner picks L2:

- [ ] `mcp-memory/tools_schema.py:463-470` and 502-509 updated with FK warning (P1).
- [ ] `outcome_record(memory_id=X)` rejects episode UUIDs with specific error (S2). Test added in `tests/test_memory_server.py`.
- [ ] `memory_store` return structured `{stored, superseded, noop_reason, classifier_verdict}` (S1). `/implement` and `/delegate` skills updated to consume structured field.
- [ ] Lesson scan script `scripts/lesson-scan.py` writes proposed-tracker draft to `baton/lesson-scan-YYYY-MM-DD.md` (S5). Cron / autonomous-loop tier-2 hook fires daily.
- [ ] Audit query: `SELECT COUNT(*) FROM task_outcomes WHERE memory_id IS NULL AND created_at >= '2026-05-01'` recorded as baseline. Direction-of-change tracked post-fix.
- [ ] Decision recorded with UUIDs in `memories_used` (5x recall map from this brief).

---

## §10 — Out of scope (this synthesis)

- **Memory schema rewrite** (L5) — flagged as candidate but not detailed; needs separate research draft if owner picks it.
- **Recall reranker tuning specifics** (#641 post-S4) — depends on S4 telemetry. Cannot draft without data.
- **Embedding model changes** — orthogonal to all four trackers; out of channel framing.
- **Cross-device memory sync issues** — separate axis (see synthesis #4 §11 per-device-process sub-class).
- **Consolidation classifier behavior** — touched at #658 but the underlying classifier work is a parallel track per `pillar_4_metacognition_loop` memory.
- **Implementation PR scope** — this is grill prep, not a plan. Implementation issues filed post-grill per owner decision-points.

---

## §11 — Tracker reference (UUIDs not slugs per #325/#660 lesson)

To be filled by owner-grill participants. Recall map for the four trackers:

| Tracker | Slug | Status | Recurrence | Primary mitigation tier |
|---|---|---|---|---|
| #641 | recall quality vs always_load | bug / priority:high / needs-research | ongoing | L3 (S4 telemetry) |
| #654 | 5-lesson memory-source gap | meta / process / needs-grill | 5 lessons consolidated | L2 (S5 scan) |
| #658 | dup-detector observability | bug / area:infrastructure / memory | 1 (silent, recovery-dangerous) | L2 (S1 structured response) |
| #660 | outcome_record.memory_id FK | bug / area:skills / area:infrastructure / memory | 5x in 2026-05 | L1 (P1 + S2) |

**Bundle order for grill** (highest-recurrence first): #660 → #654 → #658 → #641.

---

## §12 — Why this is the right bundle (not 4 grills)

- All four share the channel-asymmetry shape (§3). Owner answering "what's the contract here, and what's the structured signal" once propagates across.
- L1+L2 cost stays under 6h total. Below the threshold where bundling adds review friction.
- Cost-axis grill (synthesis #3) explicitly recommended A1+A2+A3 together — the four trackers map to that bundle.
- Three of four trackers (#654, #658, #660) explicitly cross-reference each other in their bodies as "sibling trackers, worth bundling".
- Splitting into 4 grills loses the discriminator. The grill question "is the channel asymmetric and how do we structure the signal" is the same in all four — answer once.

---

## §13 — Recovery hint (for next iter or owner)

If iter:36 wants to extend: the **lesson scan primitive (S5)** is the most generalisable extraction here — applies to every recurring failure class, not just memory. Could be a separate synthesis or a `/lesson-scan` skill draft.

If owner picks L1 only: a tiny PR can ship in <2h. Body: P1 doc edit + S2 server guard + test. Decision UUID lives in `record_decision` output.

If owner defers all: the recurrence rate (~3-5 lessons/month on these classes) sets the cost of no-op. Audit again at iter:end of next chain run.

---

**End brief. ~370 lines, 13 sections. Synthesis channel 6-for-6.**
