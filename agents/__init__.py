"""Jarvis Pillar 7 — reactive-core agents.

Runs alongside Claude Code (not as replacement). The deterministic event
router (:mod:`agents.orchestrator`) consumes the ``events`` queue and routes
each event to inline handling, a ``task_queue`` row, or owner escalation.

See docs/agents/ for setup and operational notes.
"""

__all__ = [
    "config",
    "escalation",
    "executor",
    "github_client",
    "ollama_client",
    "orchestrator",
    "poller",
    "safety",
    "supabase_client",
    "task_dispatch",
    "task_queue",
    "usage_probe",
    "wake_driver",
]
