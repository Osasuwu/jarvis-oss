"""Tests for scripts/record-decision-gate.py — Tier 2 hook (#524)."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
gate = importlib.import_module("record-decision-gate")

_HOOK = Path(__file__).resolve().parent.parent / "scripts" / "record-decision-gate.py"


# ── Pure evaluator ───────────────────────────────────────────────────


def test_blocks_when_memories_used_missing():
    assert gate.evaluate("mcp__memory__record_decision", {}) is True


def test_blocks_when_memories_used_empty_list():
    assert (
        gate.evaluate("mcp__memory__record_decision", {"memories_used": []}) is True
    )


def test_blocks_when_memories_used_none():
    assert (
        gate.evaluate("mcp__memory__record_decision", {"memories_used": None}) is True
    )


def test_passes_when_memories_used_has_uuids():
    uuids = ["11111111-1111-1111-1111-111111111111"]
    assert (
        gate.evaluate("mcp__memory__record_decision", {"memories_used": uuids})
        is False
    )


def test_passes_when_intentionally_empty_true_and_empty_list():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": [], "intentionally_empty": True},
        )
        is False
    )


def test_passes_when_intentionally_empty_true_and_missing():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"intentionally_empty": True},
        )
        is False
    )


def test_intentionally_empty_false_does_not_bypass():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": [], "intentionally_empty": False},
        )
        is True
    )


def test_other_tool_never_blocks():
    assert gate.evaluate("Bash", {"command": "ls"}) is False
    assert gate.evaluate("mcp__memory__memory_store", {}) is False


def test_intentionally_empty_overrides_even_with_full_list():
    # Flag is checked first; non-empty list also passes — both routes work.
    uuids = ["11111111-1111-1111-1111-111111111111"]
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": uuids, "intentionally_empty": True},
        )
        is False
    )


# ── End-to-end: subprocess with stdin JSON ───────────────────────────


def _run_hook(input_obj: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(input_obj),
        text=True,
        capture_output=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout


def test_subprocess_blocks_empty_with_deny_json():
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": []},
        }
    )
    assert rc == 2
    payload = json.loads(out)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "deny"
    assert "memories_used" in inner["permissionDecisionReason"]
    assert "CLAUDE.md" in inner["permissionDecisionReason"]
    assert "intentionally_empty" in inner["permissionDecisionReason"]


def test_subprocess_passes_with_uuids():
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {
                "memories_used": ["11111111-1111-1111-1111-111111111111"]
            },
        }
    )
    assert rc == 0
    assert out == ""


def test_subprocess_passes_with_intentionally_empty():
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [], "intentionally_empty": True},
        }
    )
    assert rc == 0
    assert out == ""


def test_subprocess_silent_on_other_tool():
    rc, out = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    )
    assert rc == 0
    assert out == ""


def test_subprocess_silent_on_malformed_stdin():
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="not json at all",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_subprocess_silent_on_empty_stdin():
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0
