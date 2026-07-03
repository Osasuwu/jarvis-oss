# Skill trigger model

Every Jarvis skill is classified by its **primary trigger**:

- **Type 1 — event/cron.** Hook (`Stop`, `SessionStart`, `PreToolUse`) or cron fires the skill in a fresh session. Examples: `/cycle`, `/learn`, `/end`.
- **Type 2 — user or orchestrator intent.** A human at the keyboard, or a sandcastle orchestrator sending a task-prompt, supplies the intent at session start; the model matches the skill's description and invokes. Examples: `/implement`, `/grill`, `/diagnose`.

**Mid-task self-trigger ("Type 3") is explicitly not designed for.** When the model is mid-task, invoking a skill eats the smart-zone budget (~100K tokens) for the current task and rarely fires anyway — the model is biased toward task continuation, not meta-step-out. We accept this and let the current task complete; the orchestrator (or the user) triggers the next skill in a fresh session.

This means **headless mode under sandcastle is Type 2**: the orchestrator sends an intent-shaped prompt at session start, and the skill description must be specific enough to be matched against that intent. Skill descriptions are written for two readers — humans and orchestrators — both supplying the intent at session start, never mid-task.

## Considered options

- **Type 3 retained.** Rejected — empirically the agent does not invoke skills from its own mid-task reasoning, and engineering for it inflates description complexity.
- **Single trigger model (everything goes through skills, no hooks).** Rejected — event-driven things (session end, weekly review) become reliable when fired by hooks/cron, not by hoping the model self-triggers.
