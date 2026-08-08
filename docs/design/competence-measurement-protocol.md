# Competence Measurement Protocol — construct C (code-review judgment)

**Status:** pre-registered, series 1 not yet started.
**Closes:** #1249 (AC1).
**Decisions:** grill chain 2026-08-03/04 — `491cb4a5` (method pivot: live review cycle over a seeded-defect bank), `4bc5c5a3` (slice boundary: measure-and-journal only), `7b6c8930` (scoring semantics), `32badce3` (capture mechanics).

This protocol is pre-registered **before the first verdict is recorded**. Changing it after data collection starts invalidates the series in progress — start a new series instead of editing this doc mid-run.

## 1. What this measures

Construct C: the principal's ability to spot a real defect in a diff, without relying on self-report. `owner_competence_profile` (memory) documents a systematic *underestimation* pattern — this protocol produces an empirical read instead. Constructs A (project-map / drift cadence, #1372) and B (code reading, #1373) are out of scope for this slice.

## 2. Capture mechanism — live review cycle

Subagent PRs open as **drafts**. `.github/workflows/code-review.yml` triggers on `[opened, synchronize, reopened, ready_for_review]`, but drafts are skipped at the plugin's own step-1 eligibility check (see the workflow's job-level comment) — the draft window is structurally blind to the bot. This is what makes draft-time verdicts uncontaminated by the bot's output.

Sequence, order enforced by GitHub server timestamps (not by discipline):

1. PR opens as draft.
2. While still draft, the principal posts a verdict comment (format below) — zero or more per PR, any time before the flip.
3. The PR is flipped to ready for review (`ready_for_review` timeline event).
4. The code-review bot runs and posts findings.

Any verdict comment whose `created_at` is **not strictly before** the PR's `ready_for_review` event timestamp does not exist for scoring purposes — it is discarded, not scored as a miss. There is no journal file: the PR (comments + timeline events + commits) *is* the record. The scoring script (§5, AC2) reads live from GitHub each time it runs.

### Verdict-comment format

```
вердикт: чисто
```
or, one line per claimed defect:
```
вердикт: дефект <file>:<line> — <confidence 0-100>
вердикт: дефект <file>:<line> — <confidence 0-100>
```
`<confidence>` is the principal's stated confidence (0-100) that this specific finding is a real defect. A comment with zero `дефект` lines and a `чисто` line is a clean verdict for the whole PR. Multiple verdict comments on the same PR before `ready_for_review` are allowed; the scoring script pools all claims from all pre-flip comments.

## 3. Ground truth — ranked sources

1. **Post-merge `fix:` commit touching the same lines** (matched by file + hunk, not just file — see AC2). Highest-ranked: an actual bug that needed a subsequent fix is the strongest evidence a defect existed.
2. **Code-review bot verdict** (posted at `ready_for_review`, per §2). Used where no post-merge fix exists yet, or to corroborate.

### The "claimed, not confirmed" bucket

A principal claim of `дефект <file>:<line>` that the bot does **not** flag, and that no post-merge fix later touches, does not collapse into a false alarm. It goes into a separate bucket — **claimed, not confirmed** — and is adjudicated by a third independent reader: a fresh agent, shown the pre-review diff only, with no access to the principal's comments or the bot's verdict. This adjudication runs **only on case-5 PRs** (PRs that have at least one bucket item) — not on every PR, to keep third-reader cost bounded.

The bucket is never silently folded into hit/false-alarm at scoring time. It is reported alongside the core matrix. If bucket size exceeds core (confirmed) size for a series, see the stop rule in §6.

## 4. Scoring — 2×2 matrix, both quantities computed

Per PR, per claim, the classification is principal-claimed-defect × ground-truth-defect (from §3), giving the standard 2×2 (hit / miss / false-alarm / correct-rejection), with the bucket in §3 held aside from false-alarm until adjudicated.

Both **sensitivity** (hit rate given a real defect exists) and **criterion** (bias toward claiming a defect at all — the direct operationalization of the underestimation pattern) are computed from the matrix. **Series 1's conclusion is criterion-only** — sensitivity is reported but not used to draw a conclusion this series, because ground truth is skewed toward the bot's own detection ceiling (§7 pre-registered bias).

## 5. Scoring script (AC2)

`scripts/competence_scoring.py` recomputes the 2×2 matrix + bucket from live GitHub data on every run — there is no separate state file to fall out of sync. It:

- Fetches PR timeline events, comments, and commits for a given PR (or a list of PRs) via the GitHub API.
- Parses verdict comments per §2's format and applies the `created_at < ready_for_review_at` gate.
- Applies the ranked ground-truth sources in §3 (post-merge fix by file+hunk, then bot verdict).
- Computes the 2×2 matrix, the bucket, sensitivity, and criterion.
- Reads thresholds (minimum verdict count, horizon) from **this document**, not from independently hardcoded constants — the script is downstream of the protocol, never the reverse.

## 6. Stop rules

- **Bucket > core.** If the "claimed, not confirmed" bucket (post third-reader adjudication) is larger than the confirmed core, the series draws **no conclusion** — the ground truth is too unreliable to score against.
- **Minimum 10 verdicts.** A series with fewer than 10 scored verdict-comments (after the denominator filter, §7) is **void** — report as insufficient data, not as a weak result.

## 7. Horizon and denominator rule

- **Horizon:** 60 days, or 100 merged PRs — whichever comes first.
- **Denominator rule:** a PR whose bot review never ran (draft never flipped, plugin skipped for an unrelated reason, workflow failure) drops out of the denominator entirely. It is not counted as a non-verdict; it simply does not exist for this series.

## 8. Pre-registered biases

Recorded honestly, before data collection, per the grill chain:

- **Ground truth is skewed toward the bot's own detection ceiling** — anything the bot structurally cannot catch (and that never gets a post-merge fix) will read as a false alarm or a bucket item even if it is a real defect. This is why sensitivity is not used for series-1 conclusions (§4).
- **The third reader is correlated with the bot** — both are drawing on the same class of automated-plus-fresh-eyes review judgment, not an independent human baseline.
- **PR selection is not addressed** — this protocol does not control for which PRs get drafted, how large they are, or what area of the codebase they touch. Any pattern found may reflect PR mix, not competence.

## 9. Out of scope for this slice

- Publishing a "band"/rating from this data — gated on ≥2 completed series, tracked in #1375 (band boundaries and freshness horizon live there, not here).
- Constructs A (#1372) and B (#1373).
- The staleness sweep of CLAUDE.md/SOUL.md/DOCTRINE.md/CONTEXT.md/`always_load` memory — tracked separately in #1374 (AC4), not part of this protocol.
- `redrobot`'s own construct-C series — tracked in #1377, blocked on this series completing first (not a comparison run).
