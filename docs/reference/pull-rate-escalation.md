# Pull-rate escalation rule — pull-only reference

Read this when reviewing the output of `scripts/pull-rate-report.py` (#1275) — the on-demand
reader that measures how often the Glossary pull instruction (`.claude-userlevel/CLAUDE.md` →
*Pull-only references*) actually gets used. There is no automatic trigger; a human (or a
skill acting on the human's behalf) runs the script and reads this file to decide what the
number means.

## The two branches

An earlier design had three branches. The first — "pull-rate is effectively zero, treat as a
broken instrument rather than a signal" — was checked arithmetically against the escalation
threshold below and cannot fire: any reachable pull-rate value is either `<= LOW_THRESHOLD` (branch 2
below) or `> LOW_THRESHOLD` (branch 3 below); there is no gap between them for a distinct "zero"
case to occupy. It is dropped rather than shipped as dead code.

What ships is two branches plus a sample-size guard, all evaluated by `evaluate_escalation()`:

1. **`sample_size < MIN_SAMPLE_SIZE` (20 runs) → no action.** Too little data to trust the rate;
   a handful of runs can swing from 0% to 100% on one or two data points. Wait for more self-log
   rows before drawing a conclusion.
2. **`pull_rate <= LOW_THRESHOLD` (0.15, i.e. 15%) → escalate.** The instruction alone isn't
   reaching a workable usage rate. Escalation means moving the Glossary-pull content down one
   level on the baseline-carrier ladder (DOCTRINE.md → *Baseline carrier selection*): from the
   `@import`-carried instruction (level 2 — the agent has to choose to act on it) to a
   `.claude/rules/*.md` + `paths:` file (level 1 — delivery is 100% whenever a matching path is
   read, verified empirically by #1274). Concretely: identify what content the instruction was
   meant to get pulled, and give it a path-scoped rule file instead of relying on the agent
   reading and acting on a prompt-level instruction.
3. **`pull_rate > LOW_THRESHOLD` → no action.** Healthy — the instruction is working at an
   acceptable rate; leave it as a level-2 `@import` instruction.

## Thresholds are a fresh design, not a resurrection

The historical three-branch design's exact numeric thresholds were not recoverable from memory
or issue history by the time this slice implemented it. `LOW_THRESHOLD = 0.15` and
`MIN_SAMPLE_SIZE = 20` (both defined in `scripts/pull-rate-report.py`) are a fresh call for this
slice, not a restored value — flagged for reviewer awareness on the #1275 PR, and open to
revision once real usage data accumulates past the first `MIN_SAMPLE_SIZE` runs.

## Where the numbers come from

`compute_pull_rate()` in `scripts/pull-rate-report.py` produces `{total_runs, runs_with_pull,
pull_rate}`; `evaluate_escalation(pull_rate, sample_size)` applies the branches above. Running
the script's `main()` prints both together. Trigger is a manual reading of this output — never a
staleness stamp evaluated by `session-context.py` on the SessionStart hot path; per DOCTRINE.md's
carrier-selection rule 5, this is retrieval-shaped ("not checkable, situational"), not a baseline
that should recur on every session.
