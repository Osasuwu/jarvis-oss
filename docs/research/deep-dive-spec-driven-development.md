# Deep dive: Spec-Driven Development for Jarvis

Date: 2026-05-06
Author: Jarvis (research skill, deep mode)
Status: design proposal — not yet decided
Background sweep: [`docs/research/agent-dev-practices-sweep-2026-05-06.md`](agent-dev-practices-sweep-2026-05-06.md) §2.1, §2.3

> **Sourcing note.** WebFetch / WebSearch were blocked at execution time. This deep-dive synthesises the SDD landscape from (a) the background sweep entries 2.1, 2.3, 3.5, (b) public knowledge of the four named tools (Spec Kit, Augment, Kiro, OpenSpec) up to assistant cutoff Jan 2026, and (c) the existing Jarvis skill source (`to-prd`, `to-issues`, `verify`, `tdd`). Where a specific factual claim depends on a source we could not re-verify (the Feb-2026 arXiv paper, exact wording on intercode / Microsoft / ranthebuilder posts) it is flagged `[unverified]`. Treat anything not so flagged as either repo-grounded or general SDD canon. The owner should re-fetch the URL list before treating any `[unverified]` claim as load-bearing.

---

## 1. SDD distilled — what makes a spec "executable"

A PRD (the current `/to-prd` output) is **a document for a human reader**. Its consumers are the developer, the reviewer, and the agent treating it as prose context. Verification that the PRD was honoured is *external* — humans inspect the PR, agents run unrelated tests.

A spec, in the SDD sense, is **a machine-checkable contract whose violation is a build failure**. Three properties separate it from a PRD:

1. **Re-executable.** Re-running the spec against the codebase at any time produces a binary pass/fail. A PRD does not — it just sits in the issue body decaying.
2. **Authoritative for behaviour.** The spec, not the implementation, is the source of truth. If they disagree, the implementation is wrong by definition. A PRD has no such standing — drift between PRD and code is "documentation rot", not a bug.
3. **Decomposable into automated gates.** Every claim in the spec ("user can log in via OAuth", "balance never goes negative", "p95 < 200ms") maps to ≥1 executable check (test, type, contract test, property, runtime invariant, lint).

In practice "executable" is a spectrum, not a boolean. The cheapest version is a **spec file the agent re-reads and re-derives tests from on every iteration** (Spec Kit, OpenSpec). The strongest version is a **machine-parseable contract** (OpenAPI, AsyncAPI, JSON-Schema, Gherkin, TLA+, property-based test suites) where validation is mechanical, not LLM-mediated.

Owner's framing — "specs as EXECUTABLE VALIDATION GATES, not human-readable artifacts" — collapses to: **the spec is what /verify checks, not what /implement reads**. Today in Jarvis those are the same artefact (the PRD body); SDD says split them.

The arXiv Feb-2026 paper *"Spec-Driven Development: From Code to Contract in the Age of AI"* `[unverified — could not fetch]` is the canonical academic reference per the sweep §2.1; the framing above is consistent with the abstract circulating in the agentic-engineering community.

**Confidence: 5/5** that the PRD-vs-spec distinction is real. **Confidence: 4/5** on the specific arXiv attribution (depends on re-fetch).

---

## 2. Tool landscape

Four tools called out in the brief. Solo-dev fit reviewed at the end of each.

### 2.1 GitHub Spec Kit

- **What.** Open-source CLI from GitHub (`uvx specify`, `npm i -g @github/spec-kit` `[unverified — exact package name]`) that bolts an SDD workflow onto Claude Code, Copilot CLI, and Cursor. Adds slash commands for the SDD phases and writes spec / plan / task files into `.specify/` (or similar) inside the repo.
- **Workflow stages.** `/specify` → `/plan` → `/tasks` → `/implement` `[unverified — phase names per Microsoft developer blog]`. The output of each stage is a markdown artefact the next stage consumes.
- **License.** MIT (GitHub-published OSS).
- **Solo fit.** **Strong.** No SaaS, no auth, drops files into your repo, works offline, plays with Claude Code. Closest in shape to what Jarvis already does — could be cherry-picked rather than adopted wholesale.

### 2.2 Augment Code

- **What.** Commercial AI dev platform (VSCode extension + agent backend) that built its workflow around SDD and publishes the canonical guide at `augmentcode.com/guides/what-is-spec-driven-development`. Their angle is **context engine + spec**: large-codebase indexing feeds the spec generation, then the agent works against the spec.
- **Phase model.** Augment teaches a multi-phase loop (the brief mentions "seven phases" — Augment's guide is the most likely source). Public version is roughly: *understand → specify → plan → decompose → generate → validate → integrate* `[unverified — exact phase names need re-fetch]`. The "executable" hook lives at *validate* — the spec re-runs as a test pass.
- **License.** Commercial SaaS, paid seat (~$30–60/seat/month at last public pricing `[unverified]`).
- **Solo fit.** **Weak for adoption, strong for stealing the model.** Cost dwarfs the $20/mo external budget; locks you into their IDE. But the seven-phase model is the cleanest written spec of SDD-as-process — read it and reproduce it in skills.

### 2.3 Kiro (AWS)

- **What.** AWS's IDE-style agent (preview, 2025/2026) marketed explicitly as "spec-first". Kiro produces three kinds of artefacts per feature: **requirements**, **design**, and **tasks**, all in markdown, all re-readable by the agent. Explicitly tries to be the SDD reference IDE.
- **License.** AWS-hosted, free in preview, expected paid GA.
- **Solo fit.** **Weak.** AWS lock-in, IDE replacement (would displace Claude Code), still preview. Worth tracking, not adopting. Their three-artefact split (requirements / design / tasks) is itself useful prior art — closer to what Jarvis would produce than Spec Kit's flat "spec.md".

### 2.4 OpenSpec

- **What.** Open-source spec-as-code framework. Specs live alongside code (`specs/<feature>/spec.md` + machine-readable companion). Strong emphasis on ADR-style decision capture and on the spec being version-controlled with the implementation. The intent-driven.dev post (`spec-driven-development-with-adr/`) `[unverified]` is the OpenSpec-flavoured argument that ADRs and specs are complementary, not competing.
- **License.** OSS (MIT/Apache).
- **Solo fit.** **Medium-strong.** Aligns with Jarvis's existing `docs/adr/` discipline (sweep §3.5). Lighter than Augment, less prescriptive than Spec Kit. The ADR↔spec integration story is the best fit for the existing repo conventions.

### 2.5 Tool pick — `[unverified, opinion]`

The ranthebuilder post `[unverified — could not fetch]` reportedly compares three of these and lands on Spec Kit for solo workflows, Augment for teams. This matches the structural analysis above.

**Recommendation: steal the model, do not adopt the tool.** Jarvis already has slash-commands, skills, hooks, and a memory backend. Importing Spec Kit would duplicate the surface; importing Augment is a non-starter on cost. The right move is to evolve `/to-prd` and `/to-issues` to produce SDD-style artefacts, and add a `/spec-validate` skill that becomes the executable gate. **Confidence: 4/5.**

---

## 3. The seven phases (SDD canonical loop)

The brief mentions a "seven phases" formulation including 500-word PRD, decomposition to executable issues, closing the loop with QA. This is most consistent with **Augment Code's published phase model** (could not re-verify exact wording).

Best-faith reconstruction, cross-checked against Spec Kit and OpenSpec:

| # | Phase | Output artefact | What makes it "executable" |
|---|---|---|---|
| 1 | **Understand** | Domain map / glossary update | Re-derivable from repo + memory; CONTEXT.md is the artefact in Jarvis |
| 2 | **Specify** | ~500-word problem & contract spec | Spec re-renders deterministically; agent re-reads on every iteration |
| 3 | **Plan** | Architectural/design plan referencing ADRs | ADR IDs are cited; broken IDs fail CI lint |
| 4 | **Decompose** | Vertical-slice tasks, each with a contract fragment | Each task has its own checkable acceptance contract, not just AC checklist |
| 5 | **Generate** | Implementation (code) | Generated to satisfy the spec's checks, not the prose |
| 6 | **Validate** | Spec re-run as test pass / contract check | This is the executable gate — red = revert |
| 7 | **Integrate** | PR merge, ADR/CHANGELOG update, memory write | Validation outputs become permanent regression assets (sweep §5.5) |

`[unverified — exact phase names per Augment guide; the spirit of the seven-phase loop is consistent across all four tools]`. **Confidence: 3/5** on phase names, **5/5** that *some* loop of this shape is the SDD norm.

---

## 4. Concrete delta — current Jarvis vs SDD-flavoured Jarvis

### 4.1 Current pipeline

```
/grill-me   → record_decision UUIDs, CONTEXT.md updates
   ↓
/to-prd     → markdown PRD on issue tracker (Problem / Solution / User
              Stories / Decisions / Implementation Decisions / Testing
              Decisions / Out of Scope)
   ↓
/to-issues  → vertical-slice issues; each has AC checkboxes + Decisions
              UUIDs (no executable contract)
   ↓
/implement  OR  /delegate
              (agent reads PRD + issue, writes code + tests, opens PR)
   ↓
/verify     → fetches PR state, checks pass/fail, updates outcomes,
              detects patterns
```

**What is "executable" today.** The TDD inner loop (`/tdd`) is genuinely executable — red→green→refactor with real tests. Everything *upstream* of code (PRD, issue, AC) is human-readable prose. `/verify` checks `gh pr view` state, not "does the implementation match the spec" — because there is no spec to match against.

### 4.2 SDD-flavoured pipeline (proposal)

```
/grill-me   → unchanged (decisions stay queryable)
   ↓
/to-prd     → produces TWO artefacts:
              (a) PRD body on issue tracker  — for humans, ≤500 words
              (b) specs/<feature>/spec.md   — machine-checkable contract
                  (Gherkin / OpenAPI fragment / property table /
                  invariant list, depending on layer)
   ↓
/to-issues  → vertical-slice issues, each citing a SPEC FRAGMENT, not
              just AC checkboxes. Issue body has:
                ## Spec fragment
                <link to spec lines this slice satisfies>
                ## Acceptance contract
                <executable checks: test names, schema names, etc.>
   ↓
/implement / /delegate
              (agent reads spec.md as the source of truth; PRD is
              context only. /tdd inner loop already produces tests
              that map 1-to-1 to spec lines.)
   ↓
/spec-validate  ← NEW SKILL. Re-runs spec.md against repo:
              - all Gherkin scenarios pass
              - all OpenAPI examples round-trip
              - all invariants hold (property tests)
              Output: pass/fail per spec line.
   ↓
/verify     → unchanged shape, but now consumes /spec-validate output
              in addition to PR/check state. Outcome rows gain
              spec_validation_status.
```

### 4.3 Where the validation gate lives

**Today:** in CI tests + `/verify` reading `gh pr view` checks. The gate proves "tests passed", not "spec was satisfied" — because spec ≡ PRD prose.

**Under SDD:** the gate is `/spec-validate` running the spec-as-tests, ideally wired as a CI check (`.github/workflows/spec-validate.yml`) that gates PR merge. The PR-Body-Check pattern from the existing repo (CLAUDE.md §"Path-filtered CI guards require a meta-test", #326) is the right precedent — spec validation is just another path-filtered guard, with its own meta-test.

**Confidence: 4/5** that this delta is the right shape. **Confidence: 3/5** on naming `/spec-validate` separately from `/verify`; the alternative is folding it into `/verify` step 0.

### 4.4 What `/to-prd` outputs change

| Section | Today | Under SDD |
|---|---|---|
| Problem Statement | Prose | Prose, ≤100 words (forcing function) |
| Solution | Prose | Prose, ≤200 words |
| User Stories | Numbered list | Numbered list, **each ID-tagged** (`US-7`) so spec scenarios can cite it |
| Decisions | UUIDs from grill | unchanged |
| Implementation Decisions | Prose | Moved to `specs/<feature>/design.md` (Kiro-style separation) |
| Testing Decisions | Prose | **Replaced** by `specs/<feature>/spec.md` (executable) |
| Out of Scope | Prose | unchanged |

The PRD becomes shorter and stops trying to specify behaviour. Behaviour lives in `spec.md`.

### 4.5 What `/to-issues` consumes

Today: PRD body + decision UUIDs.
Under SDD: PRD body + `spec.md` + decision UUIDs. Each emitted issue references a *slice of the spec*, not a slice of the PRD prose. The "Acceptance criteria" checkbox list becomes "Spec scenarios this slice closes" with literal scenario IDs.

### 4.6 ADR boundary (DO NOT REINVENT)

Per brief constraint and sweep §3.5, ADRs are already in use. Boundaries:

- **ADR** = decision that constrains the architecture across features ("we use Supabase for memory", "we never block on remote Claude API in hot paths"). Long-lived, cross-cutting.
- **Spec** = behavioural contract for one feature ("login flow validates X, redirects on Y"). Per-feature, lives with the code.
- **Decision (record_decision episode)** = point-in-time choice, queryable in memory. Often an input to either an ADR or a spec.

intent-driven.dev's argument `[unverified]` is roughly: ADRs explain *why*, specs prove *what*, code is *how*. Jarvis already has the *why* (memory + ADRs) and the *how* (code + tests). SDD adds the *what* as a first-class artefact. **Confidence: 5/5** that adding spec ≠ replacing ADR.

---

## 5. Trade-offs — when does SDD pay off

The core trade is well-summarised in the brief: **SDD slows planning, speeds verification.**

| Work class | Planning cost added | Verification savings | Net |
|---|---|---|---|
| **Net-new feature, multi-slice** | High — must specify behaviour up front | High — agent stops drifting; spec catches "happy-path only" failures (sweep §1.1, §6.1) | **Strong positive.** Pilot here. |
| **Refactor, behaviour-preserving** | Medium — must spec *current* behaviour | Very high — spec becomes regression net during refactor; refactor done = spec still passes | **Strong positive**, but requires golden-path spec extraction first |
| **Bug fix, narrow** | High relative to fix size | Low — one failing test is already the spec | **Negative.** Skip SDD; use `/tdd` red-green directly. |
| **Architectural reshape** | High — spec lifts to ADR + cross-feature invariants | Medium — reshape correctness checked at integration boundary | **Mixed.** Use ADR + selective spec, not full SDD. |
| **Hotfix (`priority:critical`)** | Catastrophic — gates a hotfix on spec writing | Negative — spec written under pressure is wrong | **Strongly negative.** Explicit SDD-skip path required. |
| **Drive-by / `Fix > track` (#428)** | Wildly disproportionate | Zero | **Negative.** Already inline-fixed. |

**Heuristic.** SDD pays off when (a) the change is large enough that the agent will iterate ≥3 times, AND (b) regression risk is non-trivial, AND (c) you expect a future change to touch the same area. Most Jarvis sprint work qualifies for (a) and (c); (b) is variable. **Confidence: 4/5.**

---

## 6. Bootstrap path — minimal pilot that does not blow up the pipeline

**Design constraint:** must be additive, must not break `/grill-me → /to-prd → /to-issues → /implement → /verify`. Owner ships solo across 3 devices; one broken slash command stalls real work.

### Phase 0 — opt-in flag, no contract change

1. Add `specs/` directory to the repo, gitignored-by-default-no, with a stub README.
2. `/to-prd` gains an `--spec` flag (or detects the string "SDD" in the user request). When set, in addition to the PRD it writes `specs/<slug>/spec.md` using a single template (Gherkin recommended — most agent-friendly, no toolchain needed).
3. `/to-issues` gains corresponding `--spec` behaviour: when `specs/<slug>/spec.md` exists, each issue body cites the relevant scenario IDs.

**Artefact cost:** 1 directory, 1 template file, ~30 lines added to two existing skills.

### Phase 1 — pick ONE feature class to pilot

**Recommendation: pilot on `to-prd`-driven sprint features that ship ≥3 issues.** Specifically: the next feature where `/grill-me` produces ≥3 decisions and `/to-issues` would emit ≥3 vertical slices. This filters out trivial work (Fix > track) and hotfixes automatically.

Do NOT pilot on:
- Bug fixes (use `/tdd` directly — already executable)
- Hotfixes (zero tolerance for added gates)
- ADR-grade architectural decisions (those go through Discussions, not the feature pipeline at all per CLAUDE.md)

### Phase 2 — add `/spec-validate`

Once 2–3 features have shipped with `spec.md` artefacts, add the validation skill:

1. Skill reads `specs/<slug>/spec.md`, executes the Gherkin scenarios as pytest-bdd (or jest-cucumber, depending on stack), reports pass/fail per scenario.
2. Wire as optional CI check (`.github/workflows/spec-validate.yml`) — non-blocking initially.
3. After ~5 features, **promote to required check** for any PR that touches `specs/`. Path-filtered guards must ship with a meta-test (CLAUDE.md §"Path-filtered CI guards require a meta-test", #326) — `tests/ci/test_spec_validate_guard.py` ships in the same PR.

### Phase 3 — `/verify` integration

`/verify` step 2 gains a new branch: if the outcome's PR touched `specs/`, also fetch the latest `spec-validate` job result. Outcome row gets `spec_validation_status` column (Supabase migration). Status `partial` if PR merged but spec-validate failed.

### What this pilot deliberately does NOT do

- Does **not** mandate spec for every feature. SDD-skip is the default.
- Does **not** replace `/to-prd` output. PRD body keeps shipping to issue tracker.
- Does **not** introduce a new external tool. No Spec Kit install, no Augment subscription, no Kiro lock-in. Native Claude Code skills + a markdown convention.
- Does **not** touch hotfix path or `Fix > track` path.
- Does **not** touch ADR discipline. ADRs and specs coexist.

**Confidence: 4/5** that this is the lowest-risk way in. Main risk: pilot stalls because writing executable specs is itself a learned skill — first 2 features will be slow.

---

## 7. Open questions for owner

1. **Spec format.** Gherkin (most agent-readable, mature tooling), OpenAPI fragments (only useful when there's an HTTP boundary, which `/jarvis` mostly lacks), property tables (terse, less standard), or LLM-checkable assertions (cheapest, weakest gate)? Recommendation: Gherkin for behavioural slices, JSON-Schema for data shapes. **Decide before Phase 0.**
2. **Spec location.** `specs/<feature>/spec.md` (centralised, easy to grep) vs `<module>/spec.md` (collocated with code, like OpenSpec). The collocated version is harder to enforce but keeps spec drift visible at code-review time. **Trade-off; recommendation: centralised for jarvis, revisit for redrobot which has clearer module boundaries.**
3. **PRD ≤500 words.** Hard limit (CI-enforced) or soft? Augment's "500-word PRD" is a forcing function specifically because behaviour now lives in the spec. If we keep PRDs at current length while also writing specs, we've doubled the work. **Recommendation: soft limit at 500, hard limit at 1000, enforced in `/to-prd` skill body, not CI.**
4. **`/spec-validate` vs folding into `/verify`.** Two skills with clear separation, or one skill with more steps? Recommendation: separate, because `/spec-validate` is invocable mid-implementation by `/tdd`, not only post-PR.
5. **Cross-project applicability.** redrobot has different shape (Python + React + Three.js + MuJoCo). Does the `specs/` convention copy across, or does redrobot need its own? **Probably copy, but pilot in jarvis first.**
6. **Memory schema impact.** Add `spec_validation_status` to `task_outcomes`? Add `spec_id` to `decision_made` episodes so decisions point to the spec lines they shaped? This touches the shared Supabase schema (cross-project impact per CLAUDE.md). **Decide before Phase 3.**
7. **arXiv re-fetch.** The "From Code to Contract" paper is referenced but I could not fetch it. Owner: please pull the actual abstract; if the seven-phase model in §3 is wrong on names, §4–6 still hold structurally but the table in §3 needs editing.
8. **Spec Kit cherry-pick.** Worth running `specify init` in a throwaway clone to see what files it produces, even if we don't adopt? **Cheap experiment, recommend yes.**

---

## File path

`C:\Users\<user>\GitHub\jarvis\docs\research\deep-dive-spec-driven-development.md`

## 5-bullet summary

- SDD's core move is splitting "PRD as prose for humans" from "spec as machine-checkable contract"; today Jarvis fuses them into the `/to-prd` body and pays the cost in agent drift and weak `/verify` semantics.
- Concrete delta: keep `/grill-me`, shrink `/to-prd` to ≤500-word PRD plus a `specs/<feature>/spec.md` executable artefact, have `/to-issues` cite spec scenarios per slice, add a new `/spec-validate` skill that becomes the real validation gate, leave `/verify` consuming its output.
- Tool choice: do not adopt Spec Kit / Augment / Kiro / OpenSpec wholesale — Jarvis already has skills, hooks, MCP, memory; steal the *seven-phase model* (most likely Augment's) and the *ADR-coexistence pattern* (OpenSpec / intent-driven.dev) but keep the implementation native. Confidence 4/5.
- Trade-off shape: SDD pays off on net-new features (≥3 slices) and behaviour-preserving refactors; it actively hurts on hotfixes, narrow bug fixes, and `Fix > track` work — so the pilot must ship with an explicit SDD-skip path.
- Bootstrap: Phase 0 adds `specs/` + `--spec` flag to `/to-prd` and `/to-issues` (additive, ~30 LOC); Phase 1 pilots one ≥3-issue feature; Phase 2 ships `/spec-validate` + meta-test (per CLAUDE.md §"Path-filtered CI guards require a meta-test"); Phase 3 wires Supabase outcome enrichment. Open questions on spec format (Gherkin recommended), location, PRD length cap, and cross-repo applicability — answer before Phase 0.
