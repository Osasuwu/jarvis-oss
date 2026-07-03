"""Deriver — per-session-end implicit-memory pass.

Reads the scrubbed session transcript from the accumulator buffer
(``~/.claude/.deriver-buffer/``), invokes Ollama (routine host, primary) or
DeepSeek (fallback) with the derive prompt, and inserts ≤5 candidate
memories with ``requires_review=true``.

Owner-level scope — fires on every owner session regardless of project.
Candidates self-classify scope: ``user``-type → global; ``feedback`` →
session's project or global per content.
"""
