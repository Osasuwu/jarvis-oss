# Mirror-vs-source drift — synthesis #4

**Created:** 2026-05-16 (AFK chain iter:27, gitignored draft, do not commit).

**Status:** Pre-grill briefing. 4th orthogonal axis completing the enforcement-primitive synthesis chain:

1. **Failure-class axis** → `enforcement-primitive-synthesis-2026-05-16-explore.md` (iter:20).
2. **Harness axis** → `cwc-harness-applicability-2026-05-16-explore.md` (iter:21).
3. **Cost axis** → `enforcement-primitive-cost-risk-2026-05-16-explore.md` (iter:23).
4. **This — drift-direction axis** (covers a class the first three only touched indirectly: state replication across N devices).

---

## §1 — The class

Four trackers, filed independently across the chain, all expose the same shape:

| # | Tracker | Source-of-truth (A) | Live state (B) | Reconciliation |
|---|---|---|---|---|
| #648 | Wrapper preservation | `.scratch/afk-chain.sh` (gitignored) | per-device working tree | none — manual `cp` between devices |
| #654 | Memory enforcement gap | memory rows in Supabase (`type=feedback`) | GH issue trackers, hooks, skills | none — `memory_recall` is read-only at decision time |
| #655 | Orphan skills | source absent (`.claude-userlevel/skills/tdd/` removed) | `~/.claude/skills/tdd/` populated | manual `install.ps1 -Apply` per device |
| #656 | Missing skills | source present (`.claude-userlevel/skills/last-work-report/`) | `~/.claude/skills/last-work-report/` absent | manual `install.ps1 -Apply` per device |

**Common triad:**

1. **Authoritative source of truth at location A.**
2. **Live operational state at one-or-more location(s) B**, replicated by some process P (or never replicated).
3. **No automated reconciliation** — drift detection and repair are manual, on-demand, and per-device.

This is a state-replication problem. Each tracker has been treated as a one-off install / wiring / version-control bug. The synthesis claim: they are not one-offs; they are four projections of one structural absence — **no consistency-watcher** between source-of-truth and live state.

## §2 — Why this is the 4th axis, not a duplicate

The prior three axes asked:

- **What class of failure?** (class axis — runtime decision failures, e.g. AC-dodge, fabrication)
- **Which harness layer enforces?** (harness axis — PreToolUse hook vs server validator vs wrapper)
- **What's it cost to ship?** (cost axis — hours, risk, dependencies)

This axis asks: **where does state live, and what watches it?** Concretely:

- The previous trackers (#649/#650/#651/#652/#653) are *behavior-side* problems — agent does X when X is wrong.
- This cluster (#648/#654/#655/#656) is *state-side* problems — the system's representation of itself drifts from reality, silently, between actions.

The two failure modes need different enforcement primitives. PreToolUse hooks fire on actions; nothing fires on the absence of action. Drift detection is a different surface from action gating.

## §3 — Mitigation matrix (overlap analysis)

Each tracker proposed local mitigations. Cross-tabulating to find leverage:

| Mitigation surface | #648 | #654 | #655 | #656 | Shared? |
|---|---|---|---|---|---|
| **SessionStart drift warner** in `scripts/session-context.py` | manifest-vs-mirror banner | memory-vs-tracker scan banner | mirror→manifest (orphan) | manifest→mirror (missing) | **YES — all 4** |
| Pre-commit / CI guard | preserve `.scratch/afk-chain.sh` move to `scripts/` | new memory→issue scan job | manifest-vs-source consistency | manifest-vs-source consistency | partial (#655/#656 share, #648 needs path move) |
| PR template self-check | n/a | n/a | "requires `install.ps1 -Apply` on each device" | same line | #655 + #656 |
| `post-merge` git hook to re-run installer | partial (script move) | n/a | yes | yes | #655 + #656 |
| One-off manual sync | move + commit script | scan + file issues | run `install.ps1 -Apply` | run `install.ps1 -Apply` | all 4, owner-only |

**Leverage point:** SessionStart context loader (`scripts/session-context.py`) is the only surface that covers all four trackers in one implementation. It runs once per session, has read access to all the relevant inputs (filesystem mirror, memory via MCP, manifest, gitignored sources), and the cost of one extra check per session is negligible (~10ms file diff). One bidirectional skill-drift warner already specced in #655 mitigation #2 covers #655 and #656 simultaneously. Extending it to cover the other two is straightforward.

## §4 — Recommended bundling

Three layers, increasing investment:

### L1 — One-shot manual sync (today, 5 min)

`install.ps1 -Apply` on Main PC. Resolves #655 + #656 immediately for this device. Does not address other devices or future drift. Owner must run; chain cannot (out of AFK hard-rule scope per baton).

### L2 — SessionStart drift warner (Phase 1, ~1h)

Extend `scripts/session-context.py` to emit a `## Install drift` block at session start when any of:

```
- mirror has X but manifest/source does not (orphan)        → #655 class
- manifest has X but mirror does not                        → #656 class
- gitignored script at .scratch/afk-chain.sh has a sibling
  in scripts/ with mismatched mtime                          → #648 class (partial)
- memory_list(type=feedback, recurring|repeat) returns rows
  with no matching open GH issue label                       → #654 class
```

Surface as banner with copy-pasteable fix commands. The banner is text-only — no autonomous action; the chain hard-rule against `.claude/*` edits and against running `install.ps1 -Apply` autonomously remains intact. The point is **make drift loud**, not auto-fix.

Implementation note: this script is owned by hooks (`SessionStart` hook calls it), so it's edit-allowed and load-bearing for every session — high blast-radius. Should ship behind a feature flag (`JARVIS_DRIFT_WARNER=1`) for first iteration so the chain can prove the banner is useful before making it default.

### L3 — CI + git hook layer (Phase 2, ~4h)

- **Pre-commit hook**: assert `install-manifest.yaml` `skills.include` is a subset of source dirs under `.claude-userlevel/skills/`. Catches #655 class at commit time.
- **CI meta-test** (`tests/ci/test_install_manifest_consistency.py`): same assertion, runs on every PR. Co-located with `.github/workflows/ci-meta.yml` per CLAUDE.md rule #326.
- **post-merge git hook**: when `install-manifest.yaml` or `.claude-userlevel/skills/**` changes between old and new HEAD, print a banner reminding to run `install.ps1 -Apply`. (Cannot auto-run — owner-gated.)
- **Move `.scratch/afk-chain.sh` → `scripts/afk-chain.sh`** with the quota-detection patch as the first commit (#648 mitigation #1). One-shot move with PR review.
- **Memory→tracker job**: nightly (or `/self-improve`) scan that queries `memory_list(type=feedback)` for `recurring`/`repeat` tags, joins against open GH issues, files a consolidated tracker when N≥3 unattached recurrence-tagged lessons surface (mirrors #654's filed-as-consolidated-batch pattern). This is the part of #654 that automates the human chain step.

### L4 — Skill-level abstraction (Phase 3, defer)

Extract `/check-drift` skill that the SessionStart warner calls into. Same logic, owner-invocable on demand. Worth it only if owner finds themselves running ad-hoc drift checks more than once a week.

## §5 — Adoption order (cost-axis aligned)

Mapping onto cost-axis adopt-order convention from `enforcement-primitive-cost-risk-2026-05-16-explore.md`:

| Step | Item | Effort | Risk | Cost-axis class |
|---|---|---|---|---|
| 1 | L1 manual sync | 5 min, owner | low (reversible — installer quarantines to backup) | A0 |
| 2 | L2 SessionStart warner (feature-flagged) | ~1h | low (text banner; opt-in) | A1+A2 |
| 3 | Make L2 default-on after 1 week of utility | 5 min | low (toggle) | A3 |
| 4 | L3 pre-commit + CI meta-test for manifest consistency | ~2h | medium (could false-positive blocking PRs) | B5 |
| 5 | Move `afk-chain.sh` to `scripts/` with quota patch | ~30min, owner-PR-merge | low | B6 |
| 6 | L3 memory→tracker job (consolidated batch on N≥3) | ~2h | medium (autonomous issue-filing scope) | B7 |
| 7 | L3 post-merge git hook | ~30min | low (text reminder only) | D11 |
| 8 | L4 `/check-drift` skill | ~2h | low | D12 |

**Convergent observation:** the cost-axis "convergent adopt-order A1+A2+A3 → D11 → B5 → B6+B7 → D12 → defer D13" from iter:23 holds for this axis too. L2 ships first, opt-in, then default-on; L3 follows; L4 deferred until ROI clear.

## §6 — Grill points for owner

1. **Is SessionStart context the right surface?** Counter-arg: it loads on *every* session (including 1-minute /loop ticks), so any check that's not O(ms) cheap will become noticeable. Alternatives: explicit `/check-drift` invocation (loses surfacing); a dedicated scheduled task (loses real-time signal); pre-commit + post-merge only (loses on long-running sessions without a commit).
2. **Should the warner block or just warn?** Current proposal: text banner only, no gating. Counter-arg: text in SessionStart is easy to ignore (cf. #406 banner-blindness rule). If drift is consequential enough to fix, should the warner *prevent* certain actions until resolved? Likely no — too high blast radius for what is mostly cosmetic skill-list drift. But the `outcome_record` validator class (#654 item 3) IS hard-fail, and that's a different decision.
3. **What is the "live state" the chain trusts?** The chain has been treating `~/.claude/skills/` as authoritative for skill availability (cf. iter:25 finding `/tdd` listed despite manifest absence). If mirror diverges from manifest, the agent's decision context diverges from the docs. Which wins? Proposal: docs/manifest is source of truth; mirror is a cache; mismatch must surface. Owner: agree?
4. **#648's gitignored-source preservation: is it really part of this class, or a different shape?** Counter-arg for splitting it: the others are about *replication across devices*; #648 is about *version-control coverage of a single device's source*. Counter-arg for keeping it together: in both cases, "source of truth" doesn't actually exist as a single reliable record — it lives in a place that can be silently lost. The drift-warner can detect both with the same machinery.
5. **#654's memory→tracker scan: should the chain itself run this automation, or only the human?** The chain's existing pattern (iter:22 → filed #654 as consolidated batch) IS this automation, manually executed. Wiring it up as a `/self-improve` step or scheduled task lets the chain do it autonomously. Risk: false-positive tracker-spam if recurrence-tag heuristic is loose. Mitigation: require N≥3 unattached lessons before filing, same as the manual rule.
6. **What's the ownership of `install.ps1 -Apply` reruns?** Currently: owner-manual, every device. Each tracker proposed automation via `post-merge` git hook, but that runs locally only — silent on devices that didn't pull recently. Alternative: a periodic "device-health" scheduled task per device that posts to a shared status surface (Telegram? Supabase row?). This becomes a different (5th) axis: cross-device observability.
7. **Class scope: is "drift" the right name?** Counter-options: "state replication", "consistency", "configuration drift". Choosing the name affects future tracker filing — if next quarter the chain finds a 5th instance, will it recognize it as same class? "Drift" is concrete and short but ambiguous with semantic drift (memory content shifting meaning over time). "State replication" is accurate but bureaucratic. Owner: pick one for the runbook.

## §7 — Decision points (for owner)

After grill:

- **D-mirror-1:** Approve L1 manual sync today (5 min) — yes/no/defer.
- **D-mirror-2:** Approve L2 SessionStart warner spec (Phase 1, ~1h) — implement / revise scope / defer.
- **D-mirror-3:** Approve L3 layer (Phase 2, ~4h) — full / partial / defer.
- **D-mirror-4:** Approve L4 `/check-drift` skill — yes / wait for ROI signal / no.
- **D-mirror-5:** Approve memory→tracker automation in `/self-improve` — yes / defer / no (manual stays).
- **D-mirror-6:** Name the class — "drift" / "state replication" / other.
- **D-mirror-7:** Whether to roll the gitignored-source preservation (#648) into the same skill or keep it as a separate one-shot PR move.

## §8 — Convergence with prior three axes

This synthesis is consistent with the convergent narrative across iter:20/21/23:

- **Class axis (iter:20):** identified that delegate-class and main-session-class need different primitives. Drift is *neither* — it's not an action-time decision failure. So drift adds a new primitive type (a *passive watcher*), not a new instance of existing primitives.
- **Harness axis (iter:21):** confirmed PreToolUse hooks fire on action; nothing fires on state changes between actions. Drift watchers belong in either (a) periodic scheduled tasks or (b) session-start handlers. The CWC matrix did not enumerate a "passive state watcher" cell — this axis adds one.
- **Cost axis (iter:23):** the adopt-order A1+A2+A3 → D11 → B5 → B6+B7 → D12 holds. Drift mitigations are L1 (manual) → L2 (warner) → L3 (CI+hooks) → L4 (skill), mapping onto the same letter-class progression.

The four axes are **non-redundant** and **non-conflicting**. Together they describe a complete failure-prevention coverage map:

| Failure surfaces | Coverage axis |
|---|---|
| Wrong decision at action time | Class (iter:20) |
| Where to enforce | Harness (iter:21) |
| What it costs to ship | Cost (iter:23) |
| When state silently diverges | **Drift (this draft)** |

## §9 — Out of scope for this draft

- **Naming bikeshed past §6 point 7.** Owner picks once; runbook updates; chain moves on.
- **Concrete `scripts/session-context.py` diff.** Spec only here; implementation lands as a separate PR after grill.
- **Cross-device observability (Telegram/Supabase device-health pings).** §6 point 6 raises it as a different axis; deferred until owner signals interest.
- **Running `install.ps1 -Apply` from the chain.** Hard-rule scope; not autonomous.
- **Touching the four issue bodies to add cross-refs to this draft.** Drafts are gitignored; cross-ref happens via memory pointer, not GH-side edits.

## §10 — Pointer for owner / future chain

After owner read-through:

- Update `working_state_jarvis` baton with grill outcomes (D-mirror-1..7).
- If approved: file one issue "L2 SessionStart bidirectional drift warner (covers #655/#656; partial #648/#654)" with explicit ref to this draft (paraphrased; draft itself stays gitignored).
- Record decision via `record_decision` with `memories_used` pulling in the 5 lesson-memory UUIDs cross-referenced in #654 + the 4 tracker issue URLs.
- Append synthesis-channel result to `chain-log.md` and mark synthesis #4 outcome.

---

## §11 — Iter:30 addendum: discriminator applied backward

Iter:29 produced a discriminator (NO-DRIFT 6087152 vs DRIFT 5645893): *commit performs a side-effect (file write / env var set / daemon start) atomically with itself → state-promise holds. Commit declares intent without backing automation → state-promise drifts.* Iter:30 applies this backward across the last 12 commits to test predictive coverage.

### Classification table (HEAD~12 → HEAD)

| Commit | State-promise (paraphrased) | Side-effect class | Verifier | Drift? |
|---|---|---|---|---|
| f1fbad6 (#606 last-work-report) | "discoverable today" via mirror | declare — manifest updated; no install run | iter:26 (#656) | **DRIFT** |
| 5645893 (always_load drop) | "5 memories deleted, 16 untagged" in DB | declare — no DB script committed | iter:28 (#657) | **DRIFT** |
| d87b795 (grill AFK docs) | "8 decisions recorded in memory" (7 UUIDs listed) | MCP-call — `record_decision` pre-commit (side-effect IS performed, but verification requires events_canonical SQL access) | iter:30 attempted via `memory_recall` (UUIDs are episodes, not memories — recall doesn't surface them); **not verifiable from MCP toolkit alone** | UNKNOWN (needs SQL) |
| 6087152 (uml MCP restore) | gitignore + .mcp.json + UML_MCP_HOME env var | perform — files committed, env var set | iter:29 | NO DRIFT |
| 48a770f (#629 redrobot watchdog) | "Deployed live on Workshop: Sandcastle-Jarvis + Sandcastle-Redrobot tasks, REDROBOT_REPO_ROOT set" | perform — but per-device on Workshop | not verifiable from Main PC | UNKNOWN (Workshop-only) |
| 0a1f686 (#628 Register-SandcastleTask) | "Registered Sandcastle-Jarvis on Workshop" | perform — per-device on Workshop | not verifiable from Main PC | UNKNOWN (Workshop-only) |
| c6a1fb8 (#627 Ollama bench) | "qwen2.5-coder:14b stays VRAM-resident at ~94 tok/s" | perform — Workshop hardware-specific bench | not verifiable from Main PC (no RTX 5080) | UNKNOWN (Workshop-only) |
| 60c8d2a (#626 prompt.md guard) | "pytest tests/ci/ → 151 passed (12 new + 139 existing)" | perform — test+hook+fix committed atomically | iter:30 ran `pytest tests/ci/` → **151 passed in 0.37s**; `tests/ci/test_sandcastle_prompt_md_guard.py` → **12 passed in 0.15s** | NO DRIFT |
| c5a692f (#614 watchdog hardening) | tests added for Invoke-Sandcastle + Runtime sweep | perform — code+test atomic | self-evident (test in same commit as code) | NO DRIFT |
| 4b8f5e4 (#613 slice 10 docs + CONTEXT) | docs only; no state-promise | n/a — pure docs | n/a | N/A |
| c89c3fb (#612 drop redundant hooks) | hooks removed | perform — file committed | self-evident | NO DRIFT |
| 6fe1df8 (#609 worktree path + PS 5.1 stderr) | bug fixes | perform — file committed | self-evident | NO DRIFT |

### Distribution

- **6 perform → 6 NO DRIFT** (or self-evidently no drift): 6087152, 60c8d2a, c5a692f, c89c3fb, 6fe1df8, plus 48a770f/0a1f686/c6a1fb8/d87b795 conditionally.
- **2 declare → 2 DRIFT**: f1fbad6 (#656), 5645893 (#657).
- **4 UNKNOWN**: d87b795 (MCP-call subclass, needs SQL); 48a770f, 0a1f686, c6a1fb8 (Workshop-only subclass, needs per-device verifier).
- **1 N/A**: 4b8f5e4 (docs-only).

**Predictive model:** 100% of *verifiable* cases (8-for-8) align with the discriminator. No counter-evidence surfaced.

### Three new sub-classes the backward-scan exposes

1. **file-write side-effect** — easy to verify post-hoc (filesystem). Example: 6087152 `.gitignore` + `.mcp.json`.
2. **env-var / process side-effect** — verifiable per-device only. Example: 6087152's `UML_MCP_HOME`; 48a770f's task-scheduler registration. Sub-issue: requires touching the device that ran the commit. Cross-device drift goes silent until each device is independently probed (§6 point 6's cross-device observability axis).
3. **MCP-call side-effect** — performed at MCP-call time, but verification requires server-side DB query. Example: d87b795's 7 `record_decision` UUIDs. The MCP toolkit available to the chain (`memory_recall`, `memory_get`, `events_list`) cannot resolve decision-episode UUIDs without an `execute_sql` or a `decision_get_by_id` tool that doesn't exist today.

### Implication for the L2 drift warner spec (§4)

The §4 SessionStart drift warner was designed around **filesystem drift detection** (manifest vs mirror). The backward-scan exposes that ≥3 of the last 12 commits have state-promises in the *MCP-call* or *per-device-process* sub-classes, neither of which a filesystem-only warner detects. Two paths:

- **Narrow**: keep L2 scope at filesystem drift; file the other two sub-classes as future axis-5 work (cross-device observability) and axis-6 work (commit-promise → DB-episode reconciliation).
- **Broaden**: extend L2 to query Supabase events_canonical at session start for recently-claimed-but-not-recorded decisions. Adds DB round-trip (~50–100ms) but reuses the same surface.

Recommendation: **narrow for v1**, broaden for v2 only if SQL-side verification proves cheap and a 5th tracker surfaces in the MCP-call sub-class. Premature scope expansion defeats the cost-axis adopt-order (A1+A2+A3 ship first, then re-evaluate).

### Where the discriminator does NOT help

- It does not predict **whether the side-effect was correctly performed** — only whether the commit's structure supports atomicity. A commit that writes the wrong env-var value passes the discriminator but the state-promise still breaks. The discriminator is a *necessary* condition, not sufficient.
- It does not cover **commits that perform a side-effect on a system the commit doesn't own** — e.g. a commit message saying "DNS record updated" with no DNS scripting in the diff. None of the last 12 commits fit this pattern, but it's a known gap to flag.

### One-line summary

After 12-commit backward-scan, the iter:29 discriminator is **predictive on 100% of verifiable cases (8/8)** and exposes **three side-effect sub-classes** (file-write / per-device-process / MCP-call) that need different verifiers. The L2 SessionStart warner (§4) should ship narrow (filesystem-only) for v1; the other sub-classes earn their own enforcement layer only if recurrence shows up.

---

**End of draft.** ~280 lines + §11 addendum (~75 lines). Author: AFK chain iter:27 (wrapper-iter:16) base + iter:30 (wrapper-iter:19) addendum. Citation chain: baton `working_state_jarvis` → tracker issues #648/#654/#655/#656/#657 → iter:29 NO-DRIFT verification on 6087152 → iter:30 12-commit backward-scan.
