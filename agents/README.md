# agents/

Plan-review pipeline — shared grammar and classification logic for the
planner/critic subagents (`.claude/agents/planner.md`, `critic-*.md`) and the
CI diff-gate.

The reactive-core agent stack (orchestrator, wake_driver, task_dispatch,
sandcastle, Supabase/Ollama bridges) that used to live in this package was
demolished in #1802 — see `git log -- agents/` for the retired modules.

| Module | Role |
|--------|------|
| `plan_review_config.py` | Loader for `config/plan_review.yaml` — `exempt`/class-2/class-3 thresholds & criteria (#1685, #1707) |
| `plan_classifier.py` | `classify()`/`classify_task_row()` — change-set → ordinal `1`/`2`/`3`; `label_for()` maps to `afk:2-plan`/`afk:3-human` (#1685, #1707) |
| `plan_lock.py` | `## Plan` section canonicalize/hash + strict parser (#1685) |
| `implement_plan_gate.py` | `evaluate_trigger()` — `/implement`'s ex-ante plan-gate trigger, `priority:critical` carve-out (#1688) |
| `plan_assumptions.py` | Assumption-extraction helpers for the planner subagent |
| `critic_verdict.py` | Critic verdict grammar shared by the critic panel |

## Running tests

    pytest tests/plan_review/ -x -q

Hermetic — no live services required.
