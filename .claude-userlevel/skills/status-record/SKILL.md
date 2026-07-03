---
name: status-record
model: claude-haiku-4-5
description: "Periodic state snapshot to memory: per-repo git/CI/PR/issue/milestone state, written as a structured event under tag `status-snapshot`. Type 1 (cron-triggered). Records only — no decisions, no actions. Owner reads back inline via `memory_recall(query=\"status-snapshot\", type=reference)`."
---

# Status Record

Type 1 skill. Fires on cron, scans tracked repos, writes one structured snapshot per run to memory. Replaces the read+output half of the old `/status` skill — the read side is now `memory_recall` over these snapshots.

**Boundary:** this skill is pure recording. No analysis, no proposals, no actions. Decisions and acting on findings belong to the sandcastle orchestrator (#531) and the owner reading the snapshot.

**One carve-out — the L1 contradiction audit (#1016).** Step 3.5 runs the *single* LLM-judged detector in the status-synthesis design (memory↔git contradiction) once, in this morning baseline only, and **records its cached verdicts** into the snapshot. This is still recording, not acting: the skill writes the audit result, it never opens issues, comments, or reworks anything off the back of it. Acting on a surfaced contradiction remains the owner's / orchestrator's job. The audit is L1-only by construction — there is no intraday (L2) re-run, so the cheap delta path never pays for an LLM pass (mirrors `status_engine.analyze()`, which omits the contradiction fold entirely).

## Step 1 — Load repos

Read `$JARVIS_HOME/config/repos.conf` (one `owner/repo` per line, `#` = comment). The skill runs under cron with no guaranteed CWD, so resolve via `JARVIS_HOME` (set by the installer per `install-manifest.yaml` `env_vars`). If `JARVIS_HOME` is unset, fall back to scanning the running process's repo root via `git rev-parse --show-toplevel` only if the CWD is already inside the jarvis repo; otherwise exit non-zero with a clear error.

**Empty file is an error**: after parsing, if zero repo entries remain, exit non-zero with `error: repos.conf has no entries`. A zero-repo run would silently store an empty snapshot and trend queries would show false zeroes.

Local path resolution: read `$JARVIS_HOME/config/device.json` for `{repos_path, name}`. Per-repo local path is `{repos_path}/{repo-name}` where `repo-name` is the segment after `/`. If the directory doesn't exist on this device, skip local git checks gracefully — GitHub-side checks still run. If `device.json` is unreadable, omit `device` from the snapshot YAML and continue (don't fail the run).

## Step 2 — Gather data per repo (parallel)

Run in parallel across repos. For each repo, collect:

**Git state** (if local directory exists; `<path>` always quoted to tolerate spaces):

```bash
git -C "<path>" branch --show-current
git -C "<path>" status --short                                 # empty stdout → clean=true (any output, incl. untracked-only, → clean=false)
git -C "<path>" log --oneline -5                               # prose only — surfaced in markdown body, NOT in YAML
git -C "<path>" for-each-ref --format='%(upstream:track)' refs/heads/ | grep -c '\[gone\]'   # stale branches (locale-independent)
git -C "<path>" stash list | wc -l                             # stashes
```

`git log` output is for the human-readable markdown body only — not part of the v1 YAML contract.

**Preflight** (before any external invocations):

```bash
command -v gh >/dev/null 2>&1 || { echo "error: gh not in PATH" >&2; exit 1; }
gh auth status --hostname github.com >/dev/null 2>&1 || { echo "error: gh not authenticated" >&2; exit 1; }
```

Cron strips PATH; an unauthenticated `gh` silently 401s on every call and produces an empty-but-not-erroring snapshot. Fail fast on either.

**GitHub state:**

```bash
gh pr list --repo <R> --state open --json number,title,createdAt,updatedAt,reviewDecision,isDraft --limit 100
gh issue list --repo <R> --state open --json number,title,labels,updatedAt,body --limit 100
gh run list --repo <R> --json conclusion --limit 10
gh api "repos/<R>/milestones?state=open&per_page=50" --jq '.[] | {number, title, open_issues, closed_issues, due_on}'
gh api "repos/<R>/dependabot/alerts" --jq '[.[] | select(.state=="open")] | length' 2>/dev/null
```

`body` is fetched on open issues (it was not in v1) so blocker edges and decision references can be parsed — see *Richer state for the contradiction audit* below. The body is used only to derive structured edges; it is **not** stored verbatim in the snapshot.

**Recently-closed issues (for the contradiction audit only).** The audit must be able to see "memory still treats #42 as live, but #42 is closed", so additionally fetch issues closed within the audit window (30 days). Skip this call if the morning baseline is not running (it is L1-only):

```bash
# <since> = (generated_at UTC date) − 30 days, ISO date
gh issue list --repo <R> --state closed --search "closed:>=<since>" \
  --json number,title,state,closedAt,labels --limit 100
```

If any list returns exactly its `--limit` size (or `per_page` for `gh api`), set the corresponding `*.truncated: true` field in the YAML and append `partial: <kind> truncated at <limit> for <repo>` to `global.partial`. Limits are sized for current usage (≤100 open issues per repo); a truncation event is itself a signal worth surfacing. A truncated closed-issue search degrades the audit (some closed refs unseen) but never the snapshot — mark `partial: closed-issue search truncated for <repo>` and continue.

### Richer state for the contradiction audit (#1016)

Three structured fields are derived **per open issue** from the gathered `labels` + `body`, used by the Step 3.5 audit (and available to downstream `status_engine.IssueInfo`). Parse, do not store raw bodies:

| Derived field | Source | Rule |
|---|---|---|
| `labels` | gathered directly | already present in v1 |
| `is_blocked` | body + labels | `true` iff the `blocked` label is present **or** the body has a `Blocked by #N` / `Depends on #N` reference (case-insensitive). A `## Blocked by` heading followed by `#N` bullets counts. |
| `blocks` | body | list of `#N` from `Blocks #N` lines, or the issues that name *this* issue under their own `Blocked by` — i.e. the reverse edges. Same `#(\d+)` extraction the engine's `build_contradiction_prefilter` uses. |

`#1016` itself is the canonical example — its body carries a `## Blocked by` section listing `#1013`, `#1015`. The blocker grammar is deliberately the same `#NNN` convention the repo already uses in issue bodies; no new GitHub dependency API is required (keeps the gather portable across all 3 devices).

**Decision references** — the audit also needs the recent decisions whose text references issues. Gather decisions recorded within the prefilter window (14 days) from memory:

```
mcp__memory__memory_recall(query="status-snapshot decision", type="decision", project="jarvis", limit=50)
```

or, equivalently, query the `episodes` table for `decision_made` rows with `created_at >= now-14d`. Each decision's `decision` text is scanned for `#NNN` references; (decision, issue#) pairs where the issue is in the open-or-recently-closed set form the **shortlist** the audit judges. This is exactly `status_engine.build_contradiction_prefilter(decisions, baseline)` — call it rather than re-implementing the ≤14-day + `#NNN` extraction:

```bash
# scripts/ is a plain dir (no package __init__) — put it on PYTHONPATH and
# import the module directly, the same way tests/test_status_engine.py does.
PYTHONPATH="$JARVIS_HOME/scripts" python -c "
import status_engine as se
# ... build DecisionInfo list + Baseline from gathered data, then:
# shortlist = se.build_contradiction_prefilter(decisions, baseline)
# ... after judging each candidate, collect ContradictionVerdict objects:
# cache = se.serialize_contradiction_cache(verdicts, generated_at=<ISO>)
"
```

**Global state** (once, not per repo):

```
mcp__memory__credential_check_expiry(days_ahead=14)
```

## Step 3 — Compute derived counts

Per repo, compute counts only (no rates, no severity tagging, no proposals). Each count maps 1:1 to a YAML field in Step 4:

| YAML field | Derivation |
|------------|------------|
| `ci.failure_count` | runs with `conclusion=failure` in last 10 |
| `ci.cancelled_count` | runs with `conclusion=cancelled` in last 10 |
| `prs.open` | total open PRs |
| `prs.draft` | open PRs with `isDraft=true` |
| `prs.review_pending_2d` | non-draft open PRs with no review and `createdAt` >2 days ago (uses `createdAt` not `updatedAt` — the latter resets on every push/comment/label and would mask genuinely-stale review state) |
| `prs.blocked` | open PRs with `blocked` label or `CHANGES_REQUESTED` review |
| `issues.open` | total open issues |
| `issues.stale_14d` | open issues with `updatedAt` >14 days ago, excluding `blocked` label |
| `issues.blocked` | open issues with `blocked` label |

Thresholds (`>14d`, `>2d`) are policy constants. If a future tweak is needed, change here and bump `schema_version`.

Rates (e.g. failure %) are deliberately not stored — the consumer computes them at recall time so policy lives in the reader, not in N days of frozen snapshots.

## Step 3.5 — Contradiction audit (L1 only, #1016)

The single LLM-judged detector. Runs **only in the morning baseline** — never on an intraday re-run. If this invocation is not the L1 baseline, skip the entire step and carry forward the previous snapshot's `contradiction_cache` block unchanged (it is a cache, not a fresh computation).

**Model.** The deterministic gather/render of this skill runs on the frontmatter model (`claude-haiku-4-5`). The per-candidate contradiction judgment is escalated to **`claude-sonnet-5`** — judgment is the entire value of this lone detector, it runs L1-only over a tiny shortlist (a few candidates/day), so the stronger model costs effectively nothing. (Owner HITL calibration, decision `c72c7383-723c-4827-995a-eb7319d43c38`; model bumped 4.6→5 on the 2026-06-30 Sonnet 5 release, same-tier same-sticker. If sonnet proves weak in practice, escalate the judgment to opus — same single call site.)

**Inputs.** The shortlist from `build_contradiction_prefilter` (Step 2) — `(decision, issue#)` pairs where the referenced issue is **open or closed ≤30d**. For each pair, assemble the issue's current state (open/closed, `updated_at`/`closed_at`, labels, blocker edges) from the gathered data.

**Judgment — run once per candidate, strict factual-conflict semantics only.** Drift and staleness are NOT contradictions here (the deterministic `stale-in-progress` and `decision-without-followthrough` detectors own those). Prompt:

```
You are auditing whether recorded memory contradicts current git/issue
reality, ONE candidate at a time.

Candidate:
- Decision (recorded {age}d ago): "{decision_text}"
- References issue #{issue_number} ({repo})
- Current issue state: {open|closed}, {updated/closed} {n}d ago,
  labels: {labels}, blocker edges: {blocks}/{is_blocked}

Does the decision's CLAIM about #{issue_number} contradict the issue's
ACTUAL current state? Strict factual conflict only.

contradiction — a DIRECT factual conflict, e.g.:
  • decision implies the work shipped / the issue is done, but it is
    still OPEN with no merged PR;
  • decision says #A blocks/is-blocked-by #B, but no such edge exists
    or the named blocker is already closed;
  • decision asserts a fact the issue's current state plainly refutes
    (e.g. "still working on #42" but #42 is closed).
no_contradiction — still-in-progress consistent with the decision;
  benign divergence; normal staleness (NOT a contradiction — a
  deterministic detector handles that); unverifiable from this data.
uncertain — you cannot tell from the given data. Do NOT guess.

Uncertain is DROPPED, not surfaced — a false alarm is worse than a
miss. When in doubt, return uncertain or no_contradiction.

Output (one candidate): verdict ∈ {contradiction|no_contradiction|
uncertain} and a one-sentence rationale citing the specific
conflicting fact.
```

**Fold.** Collect every per-candidate `{decision_id, issue_number, repo, verdict, rationale}`. Only `verdict == "contradiction"` is actionable; `uncertain` and `no_contradiction` are kept in the cache (for auditability) but never surfaced as hits. This is exactly `status_engine.fold_contradiction_verdicts` — the renderer (#1018) calls it over the cached verdicts, so the skill does **not** need to fold; it only records the raw verdicts.

**Serialize.** Produce the cache block with `status_engine.serialize_contradiction_cache(verdicts, generated_at=<ISO>)` (schema `contradiction-cache/v1`). The full verdict set is stored — not just contradictions — so the drop decision stays auditable from the snapshot alone, and the renderer can re-fold without re-running the LLM (AC4).

If the shortlist is empty (no recent decisions reference live issues), write an empty cache (`verdicts: []`) — never omit the block, so consumers can distinguish "audited, nothing found" from "audit didn't run".

## Step 4 — Write the snapshot

One memory per run. The `memories` table has a unique constraint on `(project, name)` and `_handle_store` (in `mcp-memory/handlers/memory.py`) upserts via `on_conflict="project,name"` — same-day re-runs cleanly overwrite. No manual dedup needed.

Schema:

- **`name`**: `status_snapshot_<YYYY-MM-DD>` where the date is derived from `generated_at` in **UTC** (not local wall-clock — avoids divergence between name and `generated_at` for non-UTC devices). One per UTC date; re-runs same day overwrite.
- **`type`**: `reference`
- **`project`**: `jarvis`
- **`tags`**: `["status-snapshot", "auto-generated"]`
- **`source_provenance`**: `skill:status-record`
- **`description`**: `Status snapshot YYYY-MM-DD — N repos, M open PRs, K open issues`

**Content** — YAML front-matter block (machine-parseable) followed by human-readable markdown body. Stable shape:

````markdown
```yaml
schema_version: 1
generated_at: <ISO 8601 UTC>
device: <device.json.name>
repos:
  - name: owner/repo
    branch: main
    clean: true
    ci:
      recent_runs: 10
      failure_count: 0
      cancelled_count: 0
    prs:
      open: 3
      draft: 1
      review_pending_2d: 1
      blocked: 0
    issues:
      open: 17
      stale_14d: 4
      blocked: 0
    milestones:
      - number: 37
        title: Skill set redesign
        open_issues: 7
        closed_issues: 5
        due_on: null
    hygiene:
      stale_branches: 0
      stashes: 0
    security:
      dependabot_open: 0
global:
  credential_expiry:
    - name: voyageai
      expires_at: 2026-06-15
  partial: null   # string reason if any data was skipped/rate-limited; null on a complete run
contradiction_cache:        # L1-only audit (#1016); output of serialize_contradiction_cache
  schema: contradiction-cache/v1
  generated_at: <ISO 8601 UTC>   # same as top-level generated_at on an L1 run
  verdicts:                 # full set kept for auditability; renderer folds to hits via fold_contradiction_verdicts
    - decision_id: c72c7383-723c-4827-995a-eb7319d43c38
      issue_number: 42
      repo: your-username/jarvis
      verdict: contradiction        # contradiction | no_contradiction | uncertain
      rationale: decision claims #42 shipped, but #42 is still open with no merged PR
  # verdicts: [] on an L1 run with an empty shortlist; the whole block is carried
  # forward unchanged on a non-L1 (intraday) run — it is a cache, not recomputed.
```

`contradiction_cache` is a new **optional** top-level field — adding it is not a `schema_version` bump (consumers reading `schema_version: 1` tolerate extra keys, per *Schema versioning* below). Absent on a snapshot written before #1016; an empty `verdicts: []` means "audited, found nothing", distinct from the field being absent ("audit never ran").

# Status snapshot — YYYY-MM-DD

## your-username/jarvis
…human-readable per-repo paragraph for memory_recall consumers…

## your-username/your-second-repo
…
````

Call:

```
memory_store(
  type="reference",
  name="status_snapshot_<YYYY-MM-DD>",
  project="jarvis",
  tags=["status-snapshot", "auto-generated"],
  source_provenance="skill:status-record",
  content=<the markdown block above>,
  description="<one-liner>"
)
```

## Step 5 — Output

Single line, machine-parseable:

```
status_snapshot_<YYYY-MM-DD> stored. <N> repos, <M> open PRs, <K> open issues.
```

If any data was skipped (rate-limit, 404, 403, parse error) append a `partial:` clause so cron monitors can detect:

```
status_snapshot_<YYYY-MM-DD> stored. <N> repos, <M> open PRs, <K> open issues. partial: <reason>
```

That's it. No table, no analysis, no recommendations.

## Failure modes

- `repos.conf` unreadable → log error, exit non-zero. The cron run is logged failed.
- `gh` rate-limit on a single repo → record what was gathered, mark missing fields as `null` in YAML, set `global.partial: <reason>`, **and append `partial: <reason>` to the Step 5 stdout line** so cron monitors can detect.
- `mcp__memory__credential_check_expiry` fails → omit `global.credential_expiry`, set `global.partial: credential_check_expiry unavailable`. Don't fall silent — an expiring credential is exactly the signal this field exists to surface, so absence-of-data deserves a flag.
- `mcp__memory__credential_check_expiry` returns zero results → set `global.credential_expiry: []` (empty list, not omitted) so consumers can skip presence checks.
- `mcp__memory__memory_store` fails → exit non-zero. Don't try alternative storage; the next cron tick will overwrite.
- `dependabot/alerts` returns 403 (non-admin scope) → set `security.dependabot_open: null` (not `0`); document this in the YAML body so consumers don't conflate "no alerts" with "no permission".
- `gh pr list` / `gh issue list` returns 404 (repo gone or renamed) → fall through to the `null`-fields path: emit the repo entry with `prs: null`, `issues: null`, set `global.partial: 404 on owner/repo`.
- `device.json` exists but is malformed JSON → treat identically to "missing": omit the `device` field, continue. Don't fail the whole run on a single broken config file.
- `git -C` exits non-zero on a corrupted local repo → emit the repo entry with `branch: null`, `clean: null`, `hygiene: null`, set `global.partial: corrupted local repo: <name>`. GitHub-side fields still gather.
- **Contradiction audit (Step 3.5) fails for a single candidate** (sonnet call errors/times out) → record that candidate as `verdict: uncertain` (which is dropped on fold — fail toward a miss, never toward a false alarm), append `partial: contradiction audit incomplete: <decision_id>`. The snapshot still writes.
- **Contradiction audit fails wholesale** (model unavailable, prefilter import error) → write `contradiction_cache.verdicts: []`, set `global.partial: contradiction audit skipped: <reason>`, and **still write the snapshot**. The audit is additive; its failure must never block the core state record. Distinguish from "audit not run because intraday" (non-L1), where the prior cache is carried forward rather than emptied.

## Derivations not in the YAML field table

A few fields are computed but their derivation is implicit; spelled out here so consumers don't guess:

- **`clean: bool`** — `true` iff `git status --short` produces empty stdout. Untracked-only state still produces output → `clean=false`. Intentional: an untracked `.lock` file is a hygiene signal, not noise.
- **`branch: string`** — output of `git branch --show-current`; empty on detached-HEAD checkouts.
- **`device: string`** — `device.json.name`, omitted entirely if `device.json` is missing or unparseable.

## Schema versioning

`schema_version: 1` is the current contract. Bump to `2` when:
- Any YAML field is renamed or removed.
- A field's type changes (count → list, scalar → object, etc.).
- A threshold constant changes semantically (e.g. `stale_14d` → `stale_30d`) — same field name with new meaning is a breaking change for trend queries.

Adding new optional fields is **not** a version bump. Consumers reading `schema_version: 1` data must tolerate extra unknown fields.

## Reading these snapshots

Owner / orchestrator reads inline:

```
memory_recall(query="status-snapshot", project="jarvis", type="reference", limit=7)
```

For a specific date: `memory_get(name="status_snapshot_2026-05-10", project="jarvis")`.

For trend analysis (e.g. "milestone 37 burndown over 7 days"): pull last 7 by tag, parse the YAML block.

**Contradiction audit (#1016).** The `contradiction_cache` block holds the L1 memory↔git audit. To surface actionable contradictions without re-running the LLM, fold the cached verdicts:

```bash
PYTHONPATH="$JARVIS_HOME/scripts" python -c "
import status_engine as se, yaml, sys
cache = yaml.safe_load(sys.stdin)['contradiction_cache']
verdicts = se.deserialize_contradiction_cache(cache)
for hit in se.fold_contradiction_verdicts(verdicts):
    print(hit.repo, hit.issue_number, '-', hit.description)
"
```

Only `verdict: contradiction` rows fold to hits; `uncertain`/`no_contradiction` are retained in the cache for auditability but never surfaced (false-negative-over-false-positive posture). This is the renderer path #1018 uses.
