## Enforcement-primitive cost/risk axis — pre-grill briefing

**Status:** Draft (research, not committed).
**Date:** 2026-05-16.
**Author:** AFK autonomous chain iter:23.
**Audience:** Owner, on return, before deciding adopt-order of enforcement primitives.

Third orthogonal axis on the same problem-space as iter:20 and iter:21:

- **iter:20** = failure-class axis — *which primitive owns which failure class* (#650/#651/#652/#653).
- **iter:21** = harness/source axis — *where each primitive comes from* (cwc / Sonovore / etc.) and **adopt/partial/skip** verdicts.
- **iter:23 (this draft)** = cost/risk axis — *what does each primitive cost to land, and what risk does it carry*. Hour estimates, LOC ballparks, gate-block status, dependency graph, reversibility.

The two prior briefings answered *what* and *why*. This one answers *if you have N hours, which N primitives*. It is the concrete adopt-order artifact the prior two implied but did not produce.

This is a briefing, not a decision. Owner picks the adopt-order after a grill pass.

## 1. Primitives in scope

Twelve primitives, deduped across the two prior briefings (iter:21 §2 and iter:20 §2-§4). Two are out of scope (P-event-store, Continuous-Claude wholesale) per iter:21 Tier D — not re-included.

| ID | Primitive | First mention |
|---|---|---|
| **A1** | `commit-on-stop.sh` Stop-hook | iter:21 Tier A |
| **A2** | `AGENT_STOP` PreToolUse hook (promote from kickoff convention) | iter:21 Tier A |
| **A3** | `STEER.md` PreToolUse hook (read-once-delete) | iter:21 Tier A |
| **B4** | SessionEnd hook → upsert `working_state_<project>` | iter:21 Tier B |
| **B5** | PreCompact hook → externalise reminder | iter:21 Tier B |
| **B6** | Fresh-context evaluator subagent (P-evaluator) | iter:20 evaluator + iter:21 Tier B |
| **B7** | PreToolUse verify-gate hook on Edit/Write | iter:20 P2' + iter:21 Tier B |
| **C8** | Reshape baton to 4-section convention | iter:21 Tier C |
| **C9** | `P-default-fail` AC checkboxes (AFK chain task descriptions) | iter:21 Tier C |
| **C10** | Port Sonovore's `extract-transcript.py` as `/recover` skill | iter:21 Tier C |
| **D11** | Skill-prelude/epilogue (`/delegate`) — P1 in iter:20 | iter:20 P1 |
| **D12** | PR body AC-table + GH Action gate — P3 in iter:20 | iter:20 P3 |
| **D13** | Structured todo schema with `last_verified_at` — P4 in iter:20 | iter:20 P4 |

(Numbering is arbitrary; chosen so the **tier letters preserve adopt-verdict from iter:21**, with D-tier reserved for iter:20-originating primitives that iter:21 didn't tier.)

## 2. Estimate methodology

Each primitive estimated against:

- **LOC** — net new code, including hook + smoke test + docs blurb. ±50% precision. Tests counted at ~2× hook LOC per CLAUDE.md path-filtered-CI rule (`#326`).
- **Hours** — wall clock for one solo developer pass, *with* grill, PR, review, merge. Not raw coding time. Half-day = 4h, day = 8h.
- **Files touched** — count of files in the resulting PR. Used to estimate review surface area.
- **Gate-block status** — does the PR need to edit `.claude/*` (autonomous-blocked, owner-routed PR) or stays in `.claude-userlevel/*` (autonomous-PRable)?
- **Coverage** — which `#65X` trackers does this primitive *close* (full L1+L2) vs *partially address* (L1 only)?
- **Risk vector** — false-positive, regression, infra change, breakage of existing flow.
- **Dependencies** — does this primitive require another to be already shipped?
- **Reversibility** — revert cost if the primitive misbehaves.

## 3. Main cost/risk table

| ID | LOC | Hours | Files | Gate | Coverage (closes / partials) | Risk vector | Deps | Reversibility |
|---|---:|---:|---:|---|---|---|---|---|
| **A1** commit-on-stop | ~20 | 0.5 | 2 | `.claude/*` → owner-PR | (none directly; backstops baton durability for #648) | none observed — `commit -am` on tracked-only | none | trivial (delete hook) |
| **A2** AGENT_STOP hook | ~40 | 1.0 | 3 | `.claude/*` → owner-PR | (operator control; relates to #648 wrapper preservation) | low — file-presence read | none | trivial (delete hook) |
| **A3** STEER.md hook | ~60 | 1.5 | 3 | `.claude/*` → owner-PR | (none directly; new owner-control channel) | low — single-file ops; one-shot read-and-delete | none | trivial (delete hook) |
| **B4** SessionEnd hook → MCP upsert | ~150 | 4-6 | 5 | `.claude/*` → owner-PR | partials #648 (durability); orthogonal to #650-#653 | medium — needs reliable project-root detection across sandcastle / multi-repo; double-write race with `/end` skill | none | reversible (disable hook); upsert is idempotent |
| **B5** PreCompact reminder | ~50 | 2-3 | 3 | `.claude/*` → owner-PR | partials #653 (forces externalise; doesn't block) | low — additive prompt injection | none | trivial (delete hook) |
| **B6** Fresh-context evaluator | ~250 | 12-16 | 6-8 | mixed — `.claude/agents/evaluator.md` + skill rewrite + `.claude-userlevel/skills/verify/SKILL.md` | partials #651, #652 (subagent verification); doesn't cover #650 dispatch-time or #653 main-session | medium — subagent dispatch overhead; cost ($) for separate context window; risk of false-PASS if evaluator inherits same biases | none structurally; better with B7 | hard — restructure of `/verify` semantics, owner must decide replace-vs-parallel |
| **B7** Edit/Write verify-gate hook | ~200 | 12-16 | 5-7 | `.claude/*` → owner-PR | closes #653 (L2 mechanical); partials #651 (false-completion claims gated at write-time) | **high** — false-positive risk on legitimate writes; allow-list maintenance burden; needs careful evidence-read detection | B6 strongly recommended (gate refers evidence to evaluator output) | hard — disabling reopens main-session #653 |
| **C8** 4-section baton reshape | ~100 (markdown) | 2 | 2 | autonomous (`.scratch/handoff.md` + memory content) | (none directly; ergonomic) | low — pure content; baton consumers (kickoff prompts) may need parsing adjustments | none | trivial (revert content) |
| **C9** P-default-fail AC | ~80 | 3-4 | 1 (kickoff prompt) | autonomous (AFK chain kickoff edit only — not skill files) | partials #652 (AC-dodge becomes harder when AC starts false) | low — AFK-chain-only initially; scope creep risk if generalised | none structurally; best with B6 evaluator | trivial (revert kickoff text) |
| **C10** `/recover` skill (extract-transcript) | ~300 | 8-12 | 4-5 | autonomous (`.claude-userlevel/skills/recover/`) | (none directly; recovery channel) | low — read-only on JSONL; JSONL format brittleness | none | trivial (skill removable) |
| **D11** `/delegate` skill epilogue | ~120 | 6-8 | 2-3 | autonomous (`.claude-userlevel/skills/delegate/SKILL.md`) | closes L2 of #650, #651, #652 | medium — false-positive on legitimate empty-diff cases; advisory-vs-mandatory choice per check | none | trivial (revert skill text); each check independently togglable |
| **D12** PR body AC-table + Action | ~150 | 6-8 | 4 (template + action + 2 tests) | `.github/*` → semi-gated; meta-test required (rule #326) | partials #652 (machine-readable AC at PR time, not dispatch time) | medium — strictness vs friction; legitimate empty-AC PRs (docs-only) | none structurally; converges with C9 | reversible (disable Action) |
| **D13** Todo schema `last_verified_at` | ~400 | 16-24 | 8-10 | `.claude/*` (schema-touching) + downstream tool plumbing | partials #653; doesn't cover #650-#652 | **high** — schema migration; cascades into every skill that reads/writes todos; agent compliance is its own variable | B5 (need compaction-signal) | hard — schema migration in-place |

**Reading the cost column:** sum of A-tier = ~3h. Sum of A+C8+C9+D11 (low-risk first-quartile) = ~14h. Full B-tier = ~30h. D13 alone = ~20h. Everything = ~70-90h ≈ 2-3 weeks calendar.

## 4. Cost buckets (sorted for "if I have N hours")

### Bucket 1 — "2 hours on first AFK return" (≤3h total)

- **A1** commit-on-stop (0.5h)
- **A2** AGENT_STOP hook (1.0h)
- **A3** STEER.md hook (1.5h)

All three are `.claude/*` edits → one owner-routed PR. **One PR or three?** Sandcastle subagent fragility (`#650`) suggests three independently-revertible PRs; review surface is small (~6 files total). Recommendation: one PR containing all three hooks, since they're independent files and each is ≤60 LOC. Revert-one-hook is `git revert <commit-touching-that-file>` either way.

**Net value:** owner-control channel (steer + stop) + baton durability backstop. Zero coverage of #650-#653 directly, but unblocks autonomous-day baseline reliability (the run #1 iter:12-24 silent-rc=1 cluster would have been caught at the wrapper level with A1+A2 in place).

### Bucket 2 — "Half-day, picks one tracker closure" (≤8h on top of Bucket 1)

Pick **D11** (`/delegate` epilogue). **Why D11 over B5/B7/D12:**

- **Autonomous-PRable.** Bucket 2 is exactly the AFK-day chain's natural surface — no gate-block, no `.claude/*` blocker. D11 goes in `.claude-userlevel/skills/delegate/SKILL.md`.
- **Closes L2 of three trackers.** #650 + #651 + #652. By coverage breadth, D11 is the single highest-leverage primitive in the table.
- **Reversibility trivial.** Each check is independently togglable inside the skill epilogue.

Alternative if owner has read iter:20 and prefers central enforcement over scoped: **B7** verify-gate hook (12-16h, two buckets). Higher closure cert but blocks autonomous landing and risks false-positives.

### Bucket 3 — "Full day, covers #653 main-session class" (≤16h on top of buckets 1-2)

Two paths:

- **Path A (low-risk):** **B5** PreCompact reminder (2-3h) + **C9** P-default-fail AC (3-4h) + **C8** 4-section baton reshape (2h) ≈ 7-9h. Partial coverage of #653 (B5 nudges externalise) and L1 for #652 (C9 makes dodge harder). No mechanical closure of #653, but landed without `.claude/*` blocker for C8/C9 and only one small hook for B5.
- **Path B (mechanical closure):** **B7** Edit/Write verify-gate hook (12-16h). Closes #653 L2 mechanically. Owner-routed PR (gate-blocked). High false-positive risk requires careful allow-list. Best paired with **B6** (Path C below), not done alone.

Recommendation: **Path A**, defer Path B until evaluator (B6) is at least specced. Path B without B6 lacks the verify-output the gate refers to and degrades to "did you read *something* recently" which is not the same check.

### Bucket 4 — "Calendar week, mechanical closure on three of four classes" (≤40h)

- **B6** fresh-context evaluator (12-16h)
- **B7** verify-gate hook (12-16h)
- **D12** PR body AC-table + Action (6-8h)

Sequence B6 → B7 → D12. B6 establishes the verify pipeline; B7 gates writes on B6's output; D12 catches what slips through at PR-create time. Net coverage: closes L1+L2 for #651 and #652, mechanical L2 for #653, no direct close for #650 (still relies on D11 from Bucket 2 + structural changes deferred to milestone `#534`).

### Bucket 5 — "Two-week + invasive (D13)"

D13 todo-schema work (16-24h) is the only invasive primitive remaining. Cost is high, coverage is narrow (#653 only, and B5+B7 already addresses #653 partially+mechanically). **Recommendation: defer D13** unless future trackers demonstrate that B5+B7 leave residual main-session bugs. Schema migrations are sticky.

## 5. Risk-vector summary

| Risk vector | Primitives carrying this risk | Mitigation |
|---|---|---|
| **False-positive blocks** | B7 verify-gate, D11 mandatory checks, D12 AC-table | Allow-list maintenance; advisory-vs-mandatory toggle per check; staged rollout (advisory first, promote on observed clean rate) |
| **Schema migration** | D13 todo schema | Defer until residual evidence justifies; B5+B7 first |
| **Cost ($)** | B6 evaluator (separate context window) | Cap evaluator invocations per session; opt-in via `/verify --fresh-context` initially |
| **Double-write race** | B4 SessionEnd hook competes with `/end` skill | Last-write-wins acceptable for idempotent upsert; explicit converge step in `/end` skill epilogue |
| **JSONL format brittleness** | C10 `/recover` | Pin JSONL version; degrade gracefully on parse failure |
| **Allow-list bikeshed** | B7 verify-gate | Pre-grill point 4 — owner picks scope (test-results.json only vs working_state_* vs PR bodies) |
| **Gate-block delays autonomous land** | A1, A2, A3, B4, B5, B7 | Bucket 1 + Bucket 2 explicitly separate autonomous-PRable primitives (D11) from gate-blocked (A1-A3) — choose by context |

## 6. Dependency graph (sequencing constraint)

```
A1 (independent)  A2 (independent)  A3 (independent)
                    |
B5 PreCompact ─────┐
                    ├──→ B7 verify-gate (needs B6's evaluator output to gate against)
B6 evaluator ──────┘
                    └──→ D12 PR AC-table (independent but converges with C9)

C8 baton reshape (independent)
C9 P-default-fail (best paired with B6 for verification)
C10 /recover (independent)
D11 /delegate epilogue (independent — only depends on `/delegate` skill existing, which it does)
D13 todo schema (depends on B5 for compaction signal)
```

Hard dependencies: **B7 requires B6**. **D13 requires B5**. Everything else is sequenceable in any order.

Soft pairings: C9 + B6 (default-fail AC becomes mechanically verifiable via evaluator). C9 + D12 (PR-body AC table aligns with kickoff-prompt AC convention).

## 7. Decision points the grill should resolve (cost/risk-axis specific)

Five questions. The iter:20 § 6 (six questions on *class*) and iter:21 § 7 (seven questions on *harness*) cover *what* and *why*. These cover *when and at what cost*:

1. **Bucket 1 as one PR vs three?** Recommendation: one PR (independent files, small surface). Owner may prefer three for granular revert.
2. **Bucket 2 = D11 (autonomous-PRable, scoped) or B7 (gate-blocked, central)?** Recommendation: D11 — autonomous chain can land while owner is AFK on next cycle. B7 needs a manual-confirm PR review.
3. **Bucket 3 Path A vs Path B?** Path A = three small partials covering three trackers (B5 + C9 + C8). Path B = one big closure on #653 (B7). Recommendation: Path A; Path B only after B6 evaluator is specced.
4. **Defer D13 indefinitely or schedule?** Recommendation: defer; mark as "revisit after B5+B7 ship and observe residual #653 incidents for 4 weeks". 
5. **Hours estimates ±50% — does owner trust them enough to plan against?** This is meta-grill: chain has not implemented any of these primitives, so estimates are inherited from cwc reference + Jarvis-skill priors. Owner may want a low-risk Bucket 1 land first as estimate calibration before committing to Bucket 4.

## 8. Convergence with iter:20 + iter:21

The three axes triangulate the same decision space from different angles:

- **iter:20 (failure-class):** "no single primitive covers all 4 — split delegate-class from main-session-class". Recommended P1+P3 for delegate-class; P2' or P4 for main-session.
- **iter:21 (harness-source):** "the primitives Jarvis lacks are exactly what cwc ships. Copy-adapt, don't design fresh." Recommended Tier A free wins in Phase 1; Tier B evaluator+verify-gate in Phase 3.
- **iter:23 (cost-risk, this draft):** "Bucket 1 (3h) lands the free wins. Bucket 2 (8h) closes 3 of 4 trackers via D11 (skill-epilogue). Bucket 3-4 (~30h) closes the remaining one mechanically."

**Convergent ordering:** A1+A2+A3 → D11 → B5 → B6+B7 → D12 → defer D13.

This is *not* the order any single prior briefing recommended — iter:21's Phase 1 was Tier A only; iter:20's Phase 1 was P1 epilogue only. The cost/risk axis surfaces that **A-tier and D11 should land together in the first 1-day window**, even though iter:21 placed them in Phase 1 and Phase 4 respectively, because the cost-bucket analysis shows D11 is autonomous-PRable while A-tier is owner-routed. *They don't block each other and have asymmetric paths to merge.*

## 9. What this synthesis can't decide

- **Whether the chain itself should auto-land any of these primitives.** Bucket 1 is gate-blocked (`.claude/*`), so the chain can only file the PR for owner review. D11 is autonomous-PRable, so the chain could in principle author and merge it — but per AFK rules "no non-LOW PR merges", chain would still park. Owner must decide whether `/delegate` skill-epilogue PRs are pre-authorised for AFK auto-merge.
- **Phase 2 SessionEnd hook's relationship to `/end` skill.** iter:21 §7 point 2 raised this; cost-axis cannot answer. Owner architectural call.
- **Whether mechanical enforcement is the right answer at all.** All three briefings assume yes (memory `verify_before_assuming_implemented` is prose, chain wants mechanical). Owner may reject the premise; in that case all 13 primitives skip and the chain pivots to soft-rule strengthening.

## 10. Recommended owner read order

For owner returning AFK with limited attention:

1. **§3 main table** (this section). One screen. Sorted intuitively.
2. **§4 Bucket 1** (3h, owner-PR). Approve or split.
3. **§4 Bucket 2 D11** (8h, autonomous-PRable). Approve for chain to author next AFK cycle.
4. **§7 decision points 1, 2, 4** — first-pass approval gate.
5. Defer §4 Buckets 3-5 and §6 dependency graph until Bucket 1+2 ship.

Sized for ~5-minute read; main table + Bucket 1-2 are the only sections needed for the first grill.

## 11. Linked artefacts

**Companion synthesis drafts:**
- `docs/research/enforcement-primitive-synthesis-2026-05-16-explore.md` (iter:20, failure-class axis)
- `docs/research/cwc-harness-applicability-2026-05-16-explore.md` (iter:21, harness-source axis)

**Source research:**
- `docs/research/autonomous-day-orchestration-2026-05-16-v2.md` §3 (cwc) + §6 (community implementations)

**Trackers converging:**
- `#650` worktree-isolation
- `#651` subagent fabrication
- `#652` AC-dodge
- `#653` post-compaction premise
- `#654` memory-source enforcement gap (iter:22)
- `#648` wrapper-script preservation (parallel concern)

**Decision UUIDs (chain history, this thread):**
- `c4311238-9102-43bf-bb8a-573af28647a4` — iter:20 synthesis draft (class axis)
- `192c4bc2-4018-40b0-a5f2-9afd458a7f7d` — iter:21 synthesis draft (harness axis)
- (iter:23 decision UUID to be appended on emit)

**End of draft.** Three-axis triangulation complete. Owner can pick adopt-order from §4 buckets without re-reading the failure-class or harness-source briefings.
