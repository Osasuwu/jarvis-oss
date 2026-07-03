# Research-pass gate

A pre-finalization gate that blocks AC lock / decision emission / artifact publication
unless a recent 4-channel research artifact exists for the current topic.

**Source of truth:** this file. Skills reference this fragment by path — do not
duplicate the logic.

---

## Trigger modes

The calling skill specifies which mode applies:

| Mode | Applies to | Behaviour |
|---|---|---|
| **Unconditional** | `/to-prd`, `/improve-codebase-architecture` | Gate always fires before the protected action |
| **High-stakes only** | `/grill`, `/reason` | Gate fires only when the about-to-emit decision has `reversibility` in `{hard, irreversible}` OR `confidence < 0.7` |

---

## Procedure

### 1. Extract topic keywords

Assemble >=3 keyword sets:

1. **Issue / PRD title verbatim** — the exact title of the work item being finalised
2. **Skill-area tag** — from CLAUDE.md taxonomy, e.g. `area:infrastructure`, `area:skills`
3. **Primary entity name** — the main entity or concept the decision/artifact concerns

### 2. Check for research artifact

Check in order; first match passes the gate.

#### a. Working-state check

```
memory_get(name="working_state_<project>", project="<project>")
```

Accept if the record contains a `research_artifacts` field listing at least one UUID.

#### b. Memory recall

```
memory_recall(type=reference, query=<topic-keywords>, project=<project>, limit=5)
```

Accept any hit where:
- `source_provenance` starts with `skill:research`
- `created_at` is within 60 days of the current date

No sim-score threshold — presence of any qualifying artifact satisfies the gate.

### 3. Resolve

| Condition | Action |
|---|---|
| Artifact found | Gate passes silently. Continue with the skill flow. |
| No artifact AND known infrastructure outage (Firecrawl/WebSearch unreachable) | Record `outcome_record(pattern_tag=["research-waiver", "skill:<name>", "infrastructure-blocked"])`, then proceed. Excluded from `/reflect` drift detection. |
| No artifact AND owner explicitly waives | Record `outcome_record(pattern_tag=["research-waiver", "skill:<name>"], outcome_status="success" if owner-explicit else "partial", lessons=<verbatim waiver reason>)`, then proceed. |
| No artifact AND autonomous mode | **HALT.** Do not auto-waive. Write `memory_store` entry noting the issue needs research before continuing. |
| No artifact AND no waiver | **BLOCK.** Propose invoking `/research` with the extracted topic keywords and the 4-channel protocol (issue #691). |
