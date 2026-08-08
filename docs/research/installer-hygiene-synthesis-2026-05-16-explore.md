# Installer hygiene — synthesis #5

**Created:** 2026-05-16 (AFK chain iter:34, gitignored draft, do not commit).

**Status:** Pre-grill briefing. Sub-class zoom of synthesis #4 (`mirror-vs-source-drift-2026-05-16-explore.md`). Three installer-class trackers (#655, #656, #659) cluster tightly enough to deserve their own decision frame.

**Relation to prior synthesis:** #4 surveyed the *drift class* across 4 surface trackers (wrapper / memory-source / orphan / missing). Two of those (#655, #656) plus one new (#659) all live inside one component — `scripts/install/installer.py` — and break one promise the installer is supposed to keep: *after `install.ps1 -Apply`, the mirror equals the source.* This synthesis treats them as a coherent installer-correctness gap with one bundled fix path, not three separate tickets.

---

## §1 — The three installer-class trackers

| # | Direction | Where the rule lives | Where it broke |
|---|---|---|---|
| **#655** | source REMOVED → mirror retains (orphan) | `installer.py:411-428` *(prune_orphan loop)* | promise honored only when `-Apply` is rerun; no auto-trigger |
| **#656** | source ADDED → mirror lacks (missing) | `installer.py` install loop (templated copy) | same; no auto-trigger |
| **#659** | mirror has `.bak.orphan*` → installer re-orphans into `.bak.orphan.bak.orphan*` | `_backup_dest` (`installer.py:188-194`) + `prune_orphan` loop (411-428) | every subsequent `-Apply` re-quarantines its own quarantines; nesting unbounded |

The three trackers were filed independently, in different iterations, after different evidence paths:

- **#655** (iter:25) — explicit `/tdd` skill orphan, code-reading + cross-ref with #596.
- **#656** (iter:26) — sibling-grep on recent merge #606 ("discoverable today" over-claim).
- **#659** (iter:32) — lesson-literal-grep against `record_decision` rationale: PR #588 had said *"Fix needs separate ticket: detect existing .bak.orphan suffix and skip."* The fix was never tracked. Evidence on disk: `dnd.bak.orphan.bak.orphan`.

They share a component, not just a phenomenon. That distinguishes them from #648 (wrapper preservation — different surface, gitignored .scratch/) and #654 (memory source — different surface, Supabase/MCP).

## §2 — Why this is a coherent sub-class (vs. just three bugs)

The installer holds **one promise**: after `install.ps1 -Apply`, the device-local mirror at `~/.claude/` (or equivalent) equals what the manifest says. Everything else is implementation.

Each tracker is one violation of that promise:

- **#655** = the *eventually* qualifier. Promise is conditional on `-Apply` running. Between manifest change and next `-Apply`, the device contradicts the manifest. There is no fence between the two states.
- **#656** = the *equally* qualifier. The installer's "include and copy" path silently underwrites the promise — *if* you rerun. Same condition as #655 from a different direction.
- **#659** = the *idempotency* qualifier. The installer's own prior actions become inputs to its next run, and the algorithm does not recognize its own quarantine outputs. The promise breaks on the second invocation, not the first.

Stated as one design rule the installer is missing: **the installer must (a) auto-trigger on manifest change, (b) be bidirectional, and (c) be idempotent against its own outputs.** Today it satisfies (b) in plan-time semantics (orphan loop exists) but fails (a) entirely and (c) for the quarantine label.

Behavioral implication: even with a perfect manifest and a perfect device, the live skill set on that device drifts from the manifest **between** human-triggered `-Apply` calls. The drift is silent (no banner, no test failure, no CI signal) and per-device (Main PC may be in sync while laptop is two manifest-revisions behind).

## §3 — The installer's correctness model — what `-Apply` is supposed to guarantee

Reading `installer.py` and `tests/test_installer.py` (1242 lines, 9 prune_orphan tests), the implicit correctness model is:

1. **Build phase.** `build_plan(source, manifest, target)` returns a list of `Action`s. Pure function. Side-effect-free.
2. **Apply phase.** Each `Action` is executed by `apply_action(...)`. Filesystem side-effects. Quarantine-by-rename for orphans (`_backup_dest`).
3. **Idempotence.** Apply N times = apply once, modulo timestamps. (Implicit assumption.)
4. **Convergence.** Build-then-apply, then build-then-apply again, should produce an empty diff. (Implicit assumption.)

#659 falsifies assumption (3) in the worst way: the second apply produces *new* actions (re-orphaning the prior quarantine), so subsequent applies are non-idempotent on the file tree, even when manifest + source haven't moved.

The other side: there's no piece of the installer that observes (4) — convergence. Tests assert single-pass plan correctness; nothing asserts two-pass quiescence.

## §4 — Testing gap analysis

`tests/test_installer.py` covers (search "prune_orphan"):

- `test_build_plan_emits_prune_orphan_for_stale_skill_dir` — single positive case.
- 4 negative cases (when prune_orphan should NOT fire).
- Apply-side quarantine result check (`(target / "skills").glob("deprecated-skill.bak.orphan*")`).

**What's missing (each maps to one tracker):**

| Tracker | Missing test | One-line spec |
|---|---|---|
| #655 | Cross-run drift detection | After plan A then mutation (drop skill from manifest), plan B emits prune_orphan for the dropped skill. |
| #656 | Missing-skill detection | After mutation (add skill to manifest), plan emits a `copy`/`install` action for the new skill. |
| #659 | Two-pass quiescence | `build_plan → apply → build_plan` produces an empty second plan. Currently false on any tree that has `*.bak.orphan` survivors. |

**Strong claim:** the third test (two-pass quiescence) is the structural fix. It would have caught #659 at write time, and it's the assertion form of correctness assumption (4). The other two are useful but incremental.

Naming convention precedent: the meta-test rule (#326) lives under `tests/ci/test_<name>_guard.py`. The installer is not path-filter-guarded, but the same shape applies — tests that pin invariants the human reader is supposed to rely on. Suggest co-locating these under `tests/test_installer_convergence.py` for grep-ability.

## §5 — Mitigation matrix

Each tracker proposed mitigations independently. Cross-tabulating to find leverage:

| Mitigation | #655 | #656 | #659 | Cost | Catches before drift? |
|---|---|---|---|---|---|
| **A. One-shot `install.ps1 -Apply`** | yes | yes | no (re-nests existing) | 5 min, owner only | no (catches after) |
| **B. SessionStart drift warner** in `scripts/session-context.py` | banner | banner | banner (counts `.bak.orphan` survivors) | ~1h | no (catches at session start, after damage) |
| **C. Installer idempotence fix (skip `.bak.<label>$` in orphan loop)** | n/a | n/a | yes | ~10min impl + test | yes (prevents future nesting) |
| **D. Sibling-quarantine refactor (move orphans to `<dest>/.orphans/`)** | n/a | n/a | yes (structurally) | ~2h impl + migration | yes |
| **E. Two-pass quiescence test** in `tests/test_installer.py` | partial | partial | yes | ~30min | yes (catches at PR review) |
| **F. PR template self-check** ("requires `install.ps1 -Apply` on each device") | yes | yes | n/a | trivial | partial (catches forgotten run, after merge) |
| **G. `post-merge` git hook** to re-run installer when manifest changes | yes | yes | partial (still re-nests till C lands) | ~30min impl, dotfile install needed | yes (catches on `git pull`) |

**Leverage stacking:**

- **C + E** are the structural-fix pair for #659. The fix is one-line; the test is the guarantee that it stays fixed.
- **B + G** are the structural-fix pair for #655/#656. The warner catches what slipped; the hook prevents new slip.
- **A** is the one-shot bridge. Necessary regardless; insufficient alone.
- **F** is hygiene around future PRs that touch skills/manifest.

**No mitigation covers all three.** The minimum-viable bundle for all three is **A + C + E + B (or G)**. B is cheaper to implement than G; G is cheaper to forget about once installed.

## §6 — Recommended phasing

### L1 — Today, 15 min, no chain action (owner only)

1. Run `install.ps1 -Apply` on Main PC. Resolves #655 (TODAY) and #656 (TODAY) for this device. **Caveat: re-orphans `dnd.bak.orphan` to `dnd.bak.orphan.bak.orphan-<ts>`** until C lands. Owner judgment whether to live with that for 24h or pause `-Apply` until C ships.

### L2 — One PR, ~1h (idempotence fix for #659)

Mitigation **C + E**. One file changed (`scripts/install/installer.py`), one file added (`tests/test_installer_convergence.py`). Specifically:

- In the orphan loop (lines 411-428), add `if any(child.name.endswith(f".bak.{label}") for label in {"orphan", "pre-jarvis-migration", "post-rename"}): continue`. (Or a less-hardcoded check: a closed list of installer-generated suffixes, maintained in one place.)
- Add convergence test: synthesize a tree with one `dnd.bak.orphan/` survivor, run `build_plan`, assert no `prune_orphan` action for that path.
- Two-pass quiescence test as bonus: `build_plan → apply → build_plan` on a clean target produces an empty second plan.

Risk: low. Pure logic fix. Migration of *existing* nested paths (`dnd.bak.orphan.bak.orphan`) is orthogonal — they stay nested under L2; sweep is a separate one-line `git mv`-style cleanup, owner-judgment.

### L3 — One PR, ~1h-2h (drift detection)

Mitigation **B**. Extend `scripts/session-context.py` to emit a `## Install drift` block on SessionStart when the mirror diverges from the manifest in either direction:

```
## Install drift
- orphan: dnd-prep.bak.orphan (1 leftover)
- orphan: dnd.bak.orphan.bak.orphan (1 leftover, 2-deep — see #659)
- missing: last-work-report (manifest line 107, mirror absent)
Run install.ps1 -Apply to reconcile.
```

Cost: read `~/.claude/skills/`, read `install-manifest.yaml`, set-diff, format. ~30 lines of Python.

Risk: SessionStart latency. Currently negligible (file read + manifest parse + set-diff = <50ms). If the block grows mid-future to scan multiple destinations (skills + commands + hooks), cache by mtime to keep it under 100ms.

### L4 — One PR, ~30min-1h (auto-trigger, optional)

Mitigation **G**. `post-merge` / `post-checkout` git hook that runs `install.ps1 -Apply` when `install-manifest.yaml` or anything under `.claude-userlevel/` changed in the merged range. Belongs in the installer's own setup path (it should install its own git hook).

Defer if L3 is producing the SessionStart warning loud enough that the human is not missing it. Adopt if the warning is noise-blind after the first month.

### L5 — Future, only on recurrence (mitigation D)

Sibling-quarantine refactor (move all orphans to `<dest>/.orphans/`). Heavier than C; only justified if other quarantine-label types start nesting too. Tracker #659's "out of scope" section already says the same.

**Total path L1 → L4 cost: ~3-4 hours** for end-to-end fix + drift detection + auto-trigger. L1 + L2 alone (the floor) is ~1h15min and resolves #659 structurally + #655/#656 on this device.

## §7 — What the prior chain decisions already give us

The drift synthesis #4 (iter:27) recommended SessionStart-warner as the single leverage point covering #648 + #654 + #655 + #656. This synthesis agrees on that leverage point for #655/#656, but **disagrees on whether it suffices**: it doesn't address #659 at all (drift warner catches the symptom — `.bak.orphan.bak.orphan` showing up — but the installer keeps generating new ones every time `-Apply` runs).

The installer-class sub-cluster needs **both** the warner (synthesis #4's recommendation, useful for cross-direction visibility) **and** the in-installer idempotence fix (this synthesis's L2). Either alone leaves a structural hole.

Iter:23 cost-axis synthesis ordered enforcement primitives by "if I have N hours." Mapping this synthesis's L1-L4 onto that frame:

- **15-min slot:** L1 only. Resolves nothing structurally; partial relief.
- **2-hour slot:** L1 + L2. Resolves #659 structurally. #655/#656 still relies on owner remembering to re-Apply.
- **4-hour slot:** L1 + L2 + L3 (or +L4). Drift becomes visible and self-healing.

If the owner asks for the minimum that does meaningful work: **L2 (single PR, ~1h, fixes #659 idempotence + adds two-pass quiescence test).** Everything else is independent — can ship in any order after.

## §8 — Grill points (for when owner returns)

1. **Idempotence fix shape — hardcoded suffix list vs. structural sibling-quarantine?** L2 vs. L5. Hardcoded list (L2) is fast but accumulates technical debt if more `_backup_dest` labels appear; sibling-quarantine (L5) is structurally clean but requires migration. Current `_backup_dest` use-sites: `"orphan"` and `"pre-jarvis-migration"` (line 199). Two labels today; not enough to justify L5 yet. Recommend L2; revisit if 3rd label arrives.
2. **Two-pass quiescence as enforced invariant?** Synthesis #4 §3 named SessionStart-warner as the structural fence; this synthesis names two-pass quiescence as a stronger one. Quiescence is testable (CI), warnable is observable (runtime). Adopt both, or pick one? Recommend: quiescence in CI catches at write time (cheap, durable), warner catches deployment drift (different surface). Not substitutes.
3. **PR template self-check — owner-judgment guardrail or installer auto-trigger?** F vs. G. Self-check trusts human attention; auto-trigger removes the human. Owner has 3 devices, so any human-attention guardrail multiplies by 3. Recommend G if it can be made portable across the device-set (PowerShell hook + dotfile install).
4. **Migration of existing `.bak.orphan.bak.orphan*` paths.** After C lands, the existing nesting on disk stays — does the owner want a one-time `git mv`-equivalent sweep, or live with it? Cosmetic only; the Claude Code skills list still surfaces `dnd.bak.orphan.bak.orphan` while it persists (per #659 sub-mode 2). Recommend one-time manual cleanup script alongside L2 PR.
5. **Sub-class vs. drift class.** Synthesis #4 grouped #655/#656 with #648/#654 under one drift class. Is bundling #655/#656/#659 into a *separate* installer-class PR (this synthesis's path) preferable, or should the broader drift synthesis #4 ship a unified bundle? Recommend separate: #659 is code-bug in installer.py and doesn't need the SessionStart-warner refactor; bundling slows L2.

## §9 — Decision points

Owner should make explicit calls on:

- **DEC-INST-1:** Adopt L2 (idempotence fix + quiescence test) as a standalone PR? Recommended: yes. Smallest unit of structural improvement.
- **DEC-INST-2:** Adopt L3 (bidirectional drift warner) as a separate PR? Or fold into the broader drift-class synthesis #4's recommended SessionStart-warner? Recommend: fold into #4 — one drift-warner implementation covers all 4 trackers and minimizes touchpoints in `session-context.py`.
- **DEC-INST-3:** Adopt L4 (auto-trigger via git hook)? Recommended: defer until L3 ships and runtime observation tells us whether banner blindness is real.
- **DEC-INST-4:** Add `tests/test_installer_convergence.py` as a separate file vs. extending `tests/test_installer.py`? Recommend: separate file (it's a structural invariant, not a unit case).
- **DEC-INST-5:** Migration of existing `.bak.orphan*` tree post-L2 — manual sweep, automated migration in L2 PR, or live-with-it? Recommend: manual sweep (10 lines of shell, ~2 minutes), one-time, alongside L2 merge.

## §10 — What this synthesis does NOT claim

- Does not claim the installer is structurally broken — it's correctly designed for single-pass, single-direction reconciliation, and the gaps are at the convergence + auto-trigger boundaries.
- Does not claim L4 is necessary — `post-merge` hooks have their own portability/maintenance burden, and SessionStart-warner may be sufficient.
- Does not claim this is the highest-leverage area in the codebase. Memory-subsystem 4-way triangulation (#641/#654/#658/#660 per working_state) may be higher-leverage; installer-class is lower-leverage but tighter-scoped and faster-to-ship.

## §11 — Inputs (provenance)

- Tracker bodies: #655, #656, #659 (fetched via `gh issue view`, 2026-05-16).
- Code: `scripts/install/installer.py` (968 lines, read targeted ranges 180-200, 405-440).
- Tests: `tests/test_installer.py` (1242 lines, grep for `prune_orphan` / `bak.orphan` patterns).
- Prior synthesis: `docs/research/mirror-vs-source-drift-2026-05-16-explore.md` (iter:27, §3 mitigation matrix).
- Chain decisions: `9eea45ab` (iter:28), `de8554a5` (iter:29), `b641994b` (iter:30), `fa774852` (iter:31), `67d74eac` (iter:32).
- Working state baton: `working_state_jarvis` (updated 2026-05-16T10:28Z + iter:33).

Filed by AFK chain iter:34. No code touched. Draft only — `docs/research/` is gitignored per `docs_research_unfinished` policy.
