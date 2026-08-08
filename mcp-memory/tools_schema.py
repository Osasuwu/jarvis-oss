"""MCP tool schemas (#360 split).

Pure data: returns the list of `Tool` objects registered by the
memory MCP server. Kept separate from server.py so the entry stays
thin. Shape is identical to the original @server.list_tools() body —
no schema changes.
"""

from __future__ import annotations

from mcp.types import Tool

# Validation tuples used by enum lists below — duplicated from server.py
# so this module has no runtime dependency on it (avoids circular load).
VALID_TYPES = ("user", "project", "decision", "feedback", "reference")
VALID_GOAL_PRIORITIES = ("P0", "P1", "P2")
VALID_GOAL_STATUSES = ("active", "achieved", "paused", "abandoned")


def tool_definitions() -> list[Tool]:
    return [
        # ---- Goal tools ----
        Tool(
            name="goal_set",
            description=(
                "Create or update a goal (upsert by slug). "
                "Goals are strategic objectives that guide Jarvis's priorities and decisions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Unique identifier (e.g. 'redrobot-demo', 'jarvis-goals-system')",
                    },
                    "title": {"type": "string", "description": "Human-readable goal title"},
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope (e.g. 'redrobot', 'jarvis'). null = cross-project.",
                    },
                    "direction": {
                        "type": ["string", "null"],
                        "description": "Strategic direction this goal belongs to",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(VALID_GOAL_PRIORITIES),
                        "description": "P0 = critical, P1 = important, P2 = nice to have",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(VALID_GOAL_STATUSES),
                    },
                    "why": {"type": "string", "description": "Motivation — why this goal matters"},
                    "success_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of success criteria",
                    },
                    "deadline": {
                        "type": ["string", "null"],
                        "description": "Deadline date (YYYY-MM-DD)",
                    },
                    "progress": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "done": {"type": "boolean"},
                            },
                        },
                        "description": "Progress milestones",
                    },
                    "progress_pct": {
                        "type": "integer",
                        "description": "Overall progress percentage (0-100)",
                    },
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Known risks",
                    },
                    "owner_focus": {
                        "type": "string",
                        "description": "What the owner is working on",
                    },
                    "jarvis_focus": {"type": "string", "description": "What Jarvis should handle"},
                    "parent_id": {
                        "type": ["string", "null"],
                        "description": "Parent goal UUID (for sub-goals)",
                    },
                },
                "required": ["slug", "title"],
            },
        ),
        Tool(
            name="goal_list",
            description=(
                "List goals with optional filters. "
                "Use at session start to load active goals as strategic context."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": list(VALID_GOAL_STATUSES),
                        "description": "Filter by status (default: all)",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Filter by project",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(VALID_GOAL_PRIORITIES),
                        "description": "Filter by priority",
                    },
                },
            },
        ),
        Tool(
            name="goal_get",
            description="Get a specific goal by slug with full details.",
            input_schema={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Goal slug"},
                },
                "required": ["slug"],
            },
        ),
        Tool(
            name="goal_update",
            description=(
                "Partial update of a goal. Only provided fields are updated. "
                "Use to update progress, status, focus, risks, etc."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Goal slug to update"},
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": list(VALID_GOAL_PRIORITIES)},
                    "status": {"type": "string", "enum": list(VALID_GOAL_STATUSES)},
                    "why": {"type": "string"},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "deadline": {"type": ["string", "null"]},
                    "progress": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "done": {"type": "boolean"},
                            },
                        },
                    },
                    "progress_pct": {"type": "integer"},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "owner_focus": {"type": "string"},
                    "jarvis_focus": {"type": "string"},
                    "outcome": {"type": "string", "description": "What happened (for closing)"},
                    "lessons": {"type": "string", "description": "What was learned (for closing)"},
                },
                "required": ["slug"],
            },
        ),
        # ---- Memory tools ----
        Tool(
            name="memory_store",
            description=(
                "Save or update a memory. Upserts by (project, name). "
                "Use for: decisions, user preferences, project context, feedback, references. "
                "Set project=null for cross-project memories."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": list(VALID_TYPES),
                        "description": "Memory category",
                    },
                    "name": {
                        "type": "string",
                        "description": "Unique name within project scope (e.g. 'architecture_split', 'user_work_style')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full memory content. Be specific — this is what future sessions will read.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary for quick relevance matching.",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null = global/cross-project. 'jarvis' = this project.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for filtering (e.g. ['architecture', 'decision'])",
                    },
                    "source_provenance": {
                        "type": "string",
                        "description": (
                            "Where this memory came from. Required as of Phase 2c — "
                            "JTMS attribution, we can't revise what we can't attribute. "
                            "Use a namespaced form: 'session:<id>', 'skill:<name>', "
                            "'hook:<name>', 'user:explicit', 'episode:<episode_id>' "
                            "(Phase 4), or a URL/tool-name when external."
                        ),
                    },
                },
                "required": ["type", "name", "content", "source_provenance"],
            },
        ),
        Tool(
            name="memory_recall",
            description=(
                "Search memories by keyword or semantic meaning. "
                "Uses vector similarity search when available, falls back to keyword matching. "
                "Use at the START of a session to load relevant context, "
                "or when the user references something discussed before."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — natural language or keywords",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Filter by project. null = search all projects.",
                    },
                    "type": {
                        "type": "string",
                        "enum": list(VALID_TYPES),
                        "description": "Filter by memory type.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                        "default": 10,
                    },
                    "include_links": {
                        "type": "boolean",
                        "description": "Include 1-hop linked memories in results",
                        "default": False,
                    },
                    "show_history": {
                        "type": "boolean",
                        "description": (
                            "Include superseded/expired memories. Default false "
                            "(live memory only). Set true for audit/debug to see "
                            "what beliefs were once held."
                        ),
                        "default": False,
                    },
                    "include_unreviewed": {
                        "type": "boolean",
                        "description": (
                            "Include `requires_review=true` rows (Deriver/Dreamer "
                            "candidates pending owner review). Default false — the "
                            "always-gate (#552) hides them from production recall. "
                            "Opt-in for the eval harness and `/learn --status`; "
                            "merge-proposal rows are filtered regardless."
                        ),
                        "default": False,
                    },
                    "brief": {
                        "type": "boolean",
                        "description": (
                            "When true, omit full content — return only name, "
                            "type, project, tags, description, and score. Use to "
                            "preview what's relevant before committing prompt "
                            "budget; call memory_get for full content on hits."
                        ),
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="memory_get",
            description="Get a specific memory by exact name and project.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact memory name",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null = global.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="memory_list",
            description=(
                "List all memories, optionally filtered by project and/or type. "
                "Returns name + description (not full content) for quick overview."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": ["string", "null"],
                        "description": "Filter by project. Omit to list all.",
                    },
                    "type": {
                        "type": "string",
                        "enum": list(VALID_TYPES),
                        "description": "Filter by type.",
                    },
                },
            },
        ),
        Tool(
            name="memory_delete",
            description="Soft-delete a memory by name. Recoverable for 30 days via memory_restore.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Memory name to delete",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null = global.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="memory_restore",
            description="Restore a soft-deleted memory within the 30-day retention window.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Memory name to restore",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null = global.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="memory_mark_stale",
            description=(
                "Hygiene: mark a memory as stale. Host-only (anon/sandcastle refused). "
                "With successor_uuid → stores the UUID in superseded_by; the recall "
                "pipeline filters superseded rows and follows links to the successor. "
                "Without → sets expired_at (belief is wrong/outdated, no replacement). "
                "Use /curate skill for owner-invoked weekly hygiene passes; not for "
                "autonomous calls."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Memory name to mark stale",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null/global = cross-project.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this memory is stale (audit + outcome trail)",
                    },
                    "successor_uuid": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional UUID of the replacement memory. If provided, "
                            "sets superseded_by (NOT expired_at) so recall can walk "
                            "the chain to the successor."
                        ),
                    },
                },
                "required": ["name", "reason"],
            },
        ),
        Tool(
            name="memory_unmark_stale",
            description=(
                "Hygiene inverse: revive a memory by clearing BOTH expired_at and "
                "superseded_by. Host-only. Used when /curate marked the wrong row."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Memory name to revive",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope. null/global = cross-project.",
                    },
                },
                "required": ["name"],
            },
        ),
        # ---- Event tools ----
        Tool(
            name="events_list",
            description=(
                "List events from the event queue. By default returns unprocessed events "
                "sorted by severity. GitHub Actions write events here; the orchestrator reads them."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Filter by repo (e.g. 'your-username/jarvis')",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Filter by event type (e.g. 'ci_failure', 'pr_approved')",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                        "description": "Filter by minimum severity",
                    },
                    "include_processed": {
                        "type": "boolean",
                        "description": "Include already-processed events (default: false)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="events_mark_processed",
            description=(
                "Mark one or more events as processed. "
                "Call after the orchestrator has handled an event."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of event UUIDs to mark as processed",
                    },
                    "processed_by": {
                        "type": "string",
                        "description": "Who processed it (e.g. 'autonomous-loop', 'risk-radar', 'manual')",
                    },
                    "action_taken": {
                        "type": "string",
                        "description": "What was done in response",
                    },
                },
                "required": ["event_ids", "processed_by"],
            },
        ),
        # ---- Event queue FSM tools (#739) ----
        Tool(
            name="event_claim_next",
            description=(
                "Claim the highest-priority pending event for processing. "
                "Atomically transitions a 'pending' event to 'claimed' and returns it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "claimer": {
                        "type": "string",
                        "description": "Who is claiming the event (e.g. 'orchestrator', 'wake_driver')",
                    },
                },
                "required": ["claimer"],
            },
        ),
        Tool(
            name="event_mark_processed_fsm",
            description=(
                "Transition a claimed event to 'processed' via the event queue FSM. "
                "Returns error if event is not in 'claimed' state."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "UUID of the event to mark as processed",
                    },
                    "processor": {
                        "type": "string",
                        "description": "Who processed it (e.g. 'orchestrator', 'wake_driver')",
                    },
                    "action_taken": {
                        "type": "string",
                        "description": "What was done in response",
                    },
                },
                "required": ["event_id", "processor"],
            },
        ),
        Tool(
            name="event_park",
            description=(
                "Park a claimed event that is blocked on a dependency. "
                "Transitions 'claimed' → 'parked'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "UUID of the event to park",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the event is being parked",
                    },
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="event_requeue",
            description=(
                "Re-queue a parked or claimed event back to 'pending'. "
                "Use when a blocked dependency resolves or a claim times out."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "UUID of the event to requeue",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the event is being requeued",
                    },
                },
                "required": ["event_id"],
            },
        ),
        # ---- Outcome tracking tools (Pillar 3) ----
        Tool(
            name="outcome_record",
            description=(
                "Record a task outcome for tracking and learning. "
                "Call after completing a delegation, fix, research, or autonomous action."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "enum": ["delegation", "research", "fix", "review", "autonomous"],
                        "description": "Type of action performed.",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "What was done (concise).",
                    },
                    "outcome_status": {
                        "type": "string",
                        "enum": ["pending", "success", "partial", "failure", "unknown"],
                        "description": "Outcome: success/partial/failure/pending/unknown.",
                    },
                    "outcome_summary": {
                        "type": "string",
                        "description": "What actually happened.",
                    },
                    "goal_slug": {
                        "type": ["string", "null"],
                        "description": "Related goal slug.",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Project scope.",
                    },
                    "issue_url": {"type": "string", "description": "GitHub issue URL."},
                    "pr_url": {"type": "string", "description": "GitHub PR URL."},
                    "tests_passed": {"type": "boolean"},
                    "pr_merged": {"type": "boolean"},
                    "quality_score": {
                        "type": "integer",
                        "description": "Quality 0-100.",
                    },
                    "lessons": {"type": "string", "description": "What was learned."},
                    "pattern_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Pattern tags for learning.",
                    },
                    "memory_id": {
                        "type": ["string", "null"],
                        "description": (
                            "UUID of the primary memory (memories.id) that informed this "
                            "outcome. NOT the episode UUID returned by record_decision — "
                            "that is a different table (episodes.id) and will be rejected. "
                            "Use payload.memories_used[0] from the record_decision call "
                            "this outcome is based on. Omit or pass null if no single "
                            "memory applies."
                        ),
                    },
                },
                "required": ["task_type", "task_description", "outcome_status"],
            },
        ),
        Tool(
            name="outcome_update",
            description=(
                "Update a task outcome after verification. Use to flip status from pending "
                "to success/failure, record verified_at, pr_merged, lessons, etc."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Outcome UUID to update."},
                    "outcome_status": {
                        "type": "string",
                        "enum": ["pending", "success", "partial", "failure", "unknown"],
                    },
                    "outcome_summary": {"type": "string"},
                    "pr_merged": {"type": "boolean"},
                    "tests_passed": {"type": "boolean"},
                    "quality_score": {"type": "integer", "description": "0-100."},
                    "lessons": {"type": "string"},
                    "pattern_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "verified_at": {
                        "type": "string",
                        "description": "ISO timestamp. Defaults to now() if omitted when status changes.",
                    },
                    "memory_id": {
                        "type": ["string", "null"],
                        "description": (
                            "UUID of the primary memory (memories.id) that informed this "
                            "outcome. NOT the episode UUID returned by record_decision — "
                            "that is a different table (episodes.id) and will be rejected. "
                            "Use payload.memories_used[0] from the record_decision call "
                            "this outcome is based on. Omit or pass null if no single "
                            "memory applies. Pass during verification to retro-link "
                            "outcomes whose basis became clear only after the decision "
                            "played out."
                        ),
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="outcome_list",
            description=(
                "List recent task outcomes, optionally filtered by project, goal, status, or pattern_tags. "
                "Use to review what worked and what didn't."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": ["string", "null"],
                        "description": "Filter by project.",
                    },
                    "goal_slug": {
                        "type": ["string", "null"],
                        "description": "Filter by goal slug.",
                    },
                    "outcome_status": {
                        "type": "string",
                        "enum": ["pending", "success", "partial", "failure", "unknown"],
                    },
                    "pattern_tag": {
                        "type": "string",
                        "description": "Filter by pattern tag (outcomes containing this tag).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20).",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="memory_calibration_summary",
            description=(
                "Confidence calibration summary: Brier score of predicted vs actual outcomes, "
                "bucketed by memory type. Reveals systemic over- or under-confidence. "
                "Used by /reflect and /self-improve (#251)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": ["string", "null"],
                        "description": "Optional project filter. null/omitted = global.",
                    },
                },
            },
        ),
        Tool(
            name="fok_calibration_summary",
            description=(
                "FOK (feeling-of-knowing) calibration summary: Brier score of FOK verdicts "
                "against task outcome accuracy. Assesses confidence calibration in memory recall judgments. "
                "Returns n (joined judgments), brier score, verdict breakdown, and drift_signal (#445)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": ["string", "null"],
                        "description": "Optional project filter. null/omitted = global.",
                    },
                },
            },
        ),
        Tool(
            name="record_decision",
            description=(
                "Record a decision made by the agent as a 'decision_made' episode. "
                "Captures decision text, rationale, memory/outcome IDs that informed it, "
                "predicted confidence (0.0-1.0), alternatives, and reversibility. "
                "Feeds the reasoning-trace for later /reflect analysis (#252)."
            ),
            input_schema={
                "type": "object",
                "required": ["decision", "rationale", "reversibility"],
                "properties": {
                    "decision": {
                        "type": "string",
                        "description": "Short statement of what was decided.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-paragraph why — the basis for the choice.",
                    },
                    "memories_used": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memory IDs that informed this decision (from recall).",
                    },
                    "intentionally_empty": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Set true to acknowledge that no memory informed this "
                            "decision. The Tier 2 PreToolUse hook (#524) blocks "
                            "calls with empty memories_used unless this is true. "
                            "Sustained >10% rate is a flag for human review."
                        ),
                    },
                    "outcomes_referenced": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "task_outcomes IDs that informed this decision.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Predicted confidence the decision is correct (0.0-1.0).",
                    },
                    "alternatives_considered": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Options rejected and briefly why.",
                    },
                    "reversibility": {
                        "type": "string",
                        "enum": ["reversible", "hard", "irreversible"],
                        "description": "How easily this decision can be undone.",
                    },
                    "actor": {
                        "type": ["string", "null"],
                        "description": "Source of the decision (e.g. 'skill:delegate', 'session:<id>'). Defaults to 'skill:unknown'.",
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": "Optional project scope for the decision payload.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Harness session id, stamped into the episode payload "
                            "as forensic grouping metadata (#1269). Not the "
                            "recovery key (see cwd, #1423). Normally injected by "
                            "the PreToolUse gate hook (updatedInput) — do not fill "
                            "by hand; invalid values are dropped server-side, "
                            "never fail the write."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory the decision was made in, stamped "
                            "into the episode payload. Recovery-key component "
                            "alongside project+since via decision_list (#1423) — "
                            "survives session_id changes across resume/compaction "
                            "boundaries. Normally injected by the PreToolUse gate "
                            "hook (updatedInput) — do not fill by hand."
                        ),
                    },
                    "llm": {
                        "type": "object",
                        "description": (
                            "Optional LLM call metadata. When present, populates "
                            "OTel GenAI keys in the events_canonical payload "
                            "(gen_ai.request.model, gen_ai.usage.input_tokens, "
                            "gen_ai.usage.output_tokens, gen_ai.usage.cost_usd, "
                            "gen_ai.provider.name, gen_ai.operation.name) and "
                            "fills cost_tokens / cost_usd columns. C17 substrate, #477."
                        ),
                        "properties": {
                            "model": {"type": "string"},
                            "response_model": {"type": "string"},
                            "input_tokens": {"type": "integer", "minimum": 0},
                            "output_tokens": {"type": "integer", "minimum": 0},
                            "cost_usd": {"type": "number", "minimum": 0},
                            "provider": {"type": "string"},
                            "operation": {"type": "string"},
                        },
                    },
                },
            },
        ),
        Tool(
            name="decision_list",
            description=(
                "List decision_made episodes, newest first. Query-based UUID "
                "recovery after context loss (compaction, resume, crash): "
                "returns 'episode_uuid | created_at | decision one-liner' rows. "
                "Recovery key is (project, cwd, since) — session_id is optional "
                "forensic grouping metadata, not required for recovery, because "
                "resume/compaction always mints a new session_id (#1269 broke "
                "across that boundary; #1423 fixes it). Filters combine as AND. "
                "session_id or project is required — never an unfiltered "
                "cross-project scan (this server is shared with redrobot). "
                "Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Optional: narrow to one harness session "
                            "(shape ^[A-Za-z0-9_-]{1,128}$). Not required if "
                            "project is given."
                        ),
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": (
                            "Project filter (payload.project). Required if session_id is not given."
                        ),
                    },
                    "cwd": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional: filter to decisions stamped from this "
                            "working directory (payload.cwd, #1423) — the "
                            "recovery-key component that survives session_id "
                            "changes."
                        ),
                    },
                    "since": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional: only decisions at or after this time. "
                            "Relative ('24h', '7d') or absolute ISO-8601."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results, newest first (default 50).",
                        "default": 50,
                    },
                },
            },
        ),
        # ---- Graph tools ----
        Tool(
            name="memory_graph",
            description=(
                "Explore the memory link graph. "
                "Modes: 'overview' (stats, top connected, orphans), "
                "'links' (all connections for a specific memory by name), "
                "'clusters' (groups of tightly connected memories for consolidation)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["overview", "links", "clusters"],
                        "description": (
                            "overview = link stats + top connected + orphans. "
                            "links = all connections for a memory (requires 'name'). "
                            "clusters = tightly connected groups."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": "Memory name (required for 'links' mode).",
                    },
                },
                "required": ["mode"],
            },
        ),
        # ---- Credential registry tools (Pillar 9) ----
        Tool(
            name="credential_list",
            description=(
                "List registered credentials (metadata only — never returns secret values). "
                "Shows service name, env var name, storage location, expiry, rotation notes."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Filter by scope (e.g. 'jarvis'). null = all.",
                    },
                },
            },
        ),
        Tool(
            name="credential_add",
            description=(
                "Register a credential in the metadata-only registry. "
                "Stores service name, env var name, where it's kept, and rotation info. "
                "NEVER pass actual secret values — the table rejects them."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Service name (e.g. 'Supabase', 'GitHub')",
                    },
                    "env_var": {
                        "type": "string",
                        "description": "Env variable NAME, not value (e.g. 'SUPABASE_KEY')",
                    },
                    "stored_in": {
                        "type": "string",
                        "description": "Where the value lives (e.g. '.env', 'GitHub Actions', 'system env')",
                        "default": ".env",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Project scope (e.g. 'jarvis')",
                        "default": "jarvis",
                    },
                    "expires_at": {
                        "type": ["string", "null"],
                        "description": "Expiry date ISO format (null = no expiry)",
                    },
                    "rotation_notes": {
                        "type": ["string", "null"],
                        "description": "How to rotate (e.g. 'Anthropic Console → API Keys')",
                    },
                    "notes": {"type": ["string", "null"], "description": "Additional notes"},
                },
                "required": ["service", "env_var"],
            },
        ),
        Tool(
            name="credential_check_expiry",
            description=(
                "Check for credentials expiring within N days. "
                "Returns list with service, env var, expiry date, and rotation notes. "
                "Use in morning-brief for proactive alerts."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Alert window in days (default 30)",
                        "default": 30,
                    },
                },
            },
        ),
    ]
