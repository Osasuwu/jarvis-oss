# Baseline carrier selection — cost table and rationale

Pull-only. Installed to `~/.claude/reference/baseline-carriers.md`, **not** `@import`ed — read
it when you are placing a new behavioral baseline and want the evidence behind the ordering, or
when arguing that a rule deserves a more expensive carrier than the selection order gives it.
Moved out of `DOCTRINE.md` by jarvis#1418: the operative part is the six-step selection order,
which stays inline there; this table is the justification for that order, and justification is
what you read once, not every session.

The carrier is picked by **cost of the rule being violated**, not by how important the rule
feels. Importance without a violation-cost story is how everything ends up `always_load` — the
tag decays into a junk drawer instead of a scarce resource.

Ordered worst-case-first:

| Carrier | Delivery | Compliance | Token cost |
|---|---|---|---|
| Код / CI-гейт | 100% | 100% (mechanical) | 0 |
| PreToolUse deny hook | 100%, incl. subagents/MCP | 100% (mechanical) | 0 |
| File + `@import` (DOCTRINE.md, CLAUDE.md, SOUL.md) | 100% | probabilistic (prompt-level) | always pays |
| `.claude/rules/` + `paths:` filter | 100% when the file matches and 0% otherwise | probabilistic | 0 when not relevant |
| Hook-inject (SessionStart/UserPromptSubmit) | 100%, but absent headless without `~/.claude/` | probabilistic | always pays |
| `--append-system-prompt` | 100%, headless-only | probabilistic | always pays |
| Retrieval / recall | ~50% (situational) | probabilistic | pays only when it fires |
| `always_load` memory tag | 100% delivery, worst prompt position (lost-in-middle) | probabilistic | always pays |

Two properties of this table drive the selection order in `DOCTRINE.md`:

- **The two zero-cost carriers are also the two with mechanical compliance.** A rule that can
  be checked at the tool-call boundary or on the produced artifact should never be prose —
  prose is strictly worse on both axes at once, not a trade-off.
- **`always_load` is the worst carrier on position and cost simultaneously**, and it is
  nonetheless the one content drifts toward, because tagging is the cheapest action for the
  person doing the tagging. That asymmetry is why the cap exists (`DOCTRINE.md` →
  *`always_load` admission criterion*) rather than a guideline.

A carrier that "always pays" pays again after every compaction: compaction summarizes the
conversation but re-delivers the whole always-loaded layer, so a session that compacts three
times pays that layer four times. On a fan-out of N subagents it is paid N+1 times, since each
subagent inherits both `CLAUDE.md` levels and every bare `@import` reachable from them.

Reference: research memory `research_baseline_delivery_carriers_2026_07_30` (project `jarvis`),
superseded by this file plus the selection order in `DOCTRINE.md`.
