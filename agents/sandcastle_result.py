"""Sandcastle result-file reader + completion classifier (S2, #1120).

Mirror of the TS writer ``.sandcastle/completion.mts``. The supervisor writes
an unconditional result file at run end (schemaVersion=2); this module reads
and validates it, reconstructs the completion-event payload, and mirrors the
watchdog's failure classifier + fail-loud scrubber for the S4 sweeper (which
re-emits a completion event from a durable result file when the supervisor's
own emission failed).

The contract is pinned on BOTH sides by the shared fixture
``tests/fixtures/sandcastle-result.json``: the TS writer's ``buildResultFile``
and this reader's ``validate_result_file`` must both accept it.

Usage::

    data = read_result_file(".sandcastle/runtime/<runId>/result.json")
    payload = build_completion_payload(data)
    severity = completion_severity(payload["event_type"], data["prEvidence"])
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

# Same field set as completion.mts RESULT_FILE_REQUIRED_FIELDS. A result file
# missing any of these is malformed and must be rejected — a truncated write
# (disk full, crash mid-write) must never be mistaken for a real completion.
REQUIRED_FIELDS = (
    "schemaVersion",
    "runId",
    "taskId",
    "lineageKey",
    "attempt",
    "tier",
    "goal",
    "outcome",
    "failureClass",
    "exit",
    "branch",
    "commits",
    "pr",
    "prEvidence",
    "failureReason",
    "completionSignal",
    "logFilePath",
    "preservedWorktreePath",
    "iterations",
)

OUTCOMES = {
    "SUCCESS": "success",
    "AGENT_FAULT": "agent_fault",
    "INFRA_FAULT": "infra_fault",
}

FAILURE_CLASSES = {
    "OOM": "oom",
    "PROVIDER_BILLING": "provider_billing",
    "ZERO_COMMITS": "zero-commits-on-windows-host",
    "MISSING_BRANCH": "missing-pinned-branch",
}

# Exact class-name tokens from node_modules/@ai-hero/sandcastle/dist/errors.js —
# matched verbatim (same casing as the TS side / watchdog).
SANDBOX_ERROR_CLASSES = (
    "AgentIdleTimeoutError",
    "ContainerStartTimeoutError",
    "MergeToHostTimeoutError",
    "WorktreeError",
    "DockerError",
    "SyncError",
    "SessionCaptureError",
    "PromptError",
    "AgentError",
    "ExecError",
)

# Classes that mean the run never got a fair shot — infra fault, burns no
# agent attempt, never escalates tier.
INFRA_ERROR_CLASSES = frozenset(
    {
        "ContainerStartTimeoutError",
        "MergeToHostTimeoutError",
        "WorktreeError",
        "DockerError",
        "SyncError",
        "SessionCaptureError",
    }
)

# Watchdog $script:OOMSignatures (case-insensitive substring match).
OOM_SIGNATURES = (
    "out of memory",
    "oom",
    "cuda out of memory",
    "model requires more system memory",
    "model load failed",
    "failed to load model",
    "unable to allocate",
)

# Watchdog $script:ProviderBillingSignatures.
BILLING_SIGNATURES = (
    "insufficient balance",
    "payment required",
    "insufficient funds",
)

# The two narrow HTTP-402 branches from the watchdog (#956 review) — a context
# word followed by ONLY structured separators before 402, or the HTTP status
# line itself. Ported exactly so a signature change stays one edit away on
# either side.
_BILLING_402_CONTEXT = re.compile(r"\b(?:http|status|error|code)[ \t:=\"'{} ,]{0,6}\b402\b", re.IGNORECASE)
_BILLING_402_STATUS_LINE = re.compile(r"\bhttp/\d[\d.]*\s+402\b", re.IGNORECASE)

# Secret token shapes redacted by the scrubber (Protect-LogTail port). The
# generic sk- pattern must run AFTER sk-ant- to avoid partial artifacts.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<GH-TOKEN-REDACTED>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "<GH-TOKEN-REDACTED>"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "<ANTHROPIC-KEY-REDACTED>"),
    (re.compile(r"sk-[A-Za-z0-9\-_]{32,}"), "<API-KEY-REDACTED>"),
)

# Fail-loud residual check skips secrets shorter than this — a short value
# would false-positive on ordinary prose. The supervisor only ever passes long
# values (URLs, API keys, tokens).
_RESIDUAL_CHECK_MIN_LEN = 8


# -- Classifier (mirror of completion.mts classifyCompletion) ----------------


def test_oom(text: str, exit_code: int | None) -> bool:
    """True when the run was killed by memory pressure (watchdog Test-IsOOM)."""
    if exit_code == 137:  # SIGKILL on Linux OOM
        return True
    if not text:
        return False
    lowered = text.lower()
    return any(sig in lowered for sig in OOM_SIGNATURES)


def test_provider_billing(text: str) -> bool:
    """True when a provider account is out of balance (watchdog Test-IsProviderBilling)."""
    if not text:
        return False
    lowered = text.lower()
    if any(sig in lowered for sig in BILLING_SIGNATURES):
        return True
    return bool(_BILLING_402_CONTEXT.search(text) or _BILLING_402_STATUS_LINE.search(text))


def get_sandcastle_error_class(text: str) -> str | None:
    """First tagged-error class token mentioned in ``text``, or None."""
    if not text:
        return None
    for cls in SANDBOX_ERROR_CLASSES:
        if cls in text:
            return cls
    return None


def classify_completion(
    *,
    run_completed: bool,
    exit_code: int | None,
    commits_count: int,
    pinned_branch_exists: bool,
    log_tail: str,
    error_text: str,
) -> dict[str, Any]:
    """Classify a run end into an outcome + failure class (mirror of the TS side).

    Returns ``{outcome, failure_class, event_type, exit, infra}``. ``infra``
    means the fault burns no agent attempt and never escalates tier.
    """
    if run_completed:
        if commits_count > 0 and pinned_branch_exists:
            return {
                "outcome": OUTCOMES["SUCCESS"],
                "failure_class": None,
                "event_type": "task_done",
                "exit": 0,
                "infra": False,
            }
        # Zero commits on the Windows host is an infra fault (upstream
        # mattpocock/sandcastle#855) — the library failed to materialize the
        # branch pin on the host side, not the agent's fault.
        failure_class = (
            FAILURE_CLASSES["MISSING_BRANCH"]
            if not pinned_branch_exists
            else FAILURE_CLASSES["ZERO_COMMITS"]
        )
        return {
            "outcome": OUTCOMES["INFRA_FAULT"],
            "failure_class": failure_class,
            "event_type": "task_failed",
            "exit": exit_code if exit_code is not None else 0,
            "infra": True,
        }

    text = "\n".join(part for part in (error_text, log_tail) if part)
    if test_provider_billing(text):
        return {
            "outcome": OUTCOMES["INFRA_FAULT"],
            "failure_class": FAILURE_CLASSES["PROVIDER_BILLING"],
            "event_type": "task_failed",
            "exit": exit_code if exit_code is not None else 1,
            "infra": True,
        }
    if test_oom(text, exit_code):
        return {
            "outcome": OUTCOMES["INFRA_FAULT"],
            "failure_class": FAILURE_CLASSES["OOM"],
            "event_type": "task_failed",
            "exit": exit_code if exit_code is not None else 137,
            "infra": True,
        }
    error_class = get_sandcastle_error_class(text)
    if error_class is not None:
        infra = error_class in INFRA_ERROR_CLASSES
        return {
            "outcome": OUTCOMES["INFRA_FAULT"] if infra else OUTCOMES["AGENT_FAULT"],
            "failure_class": error_class,
            "event_type": "task_failed",
            "exit": exit_code if exit_code is not None else 1,
            "infra": infra,
        }
    # Unknown throw — fail toward infra (do not burn an agent attempt).
    return {
        "outcome": OUTCOMES["INFRA_FAULT"],
        "failure_class": None,
        "event_type": "task_failed",
        "exit": exit_code if exit_code is not None else 1,
        "infra": True,
    }


# -- Fail-loud scrubber (mirror of completion.mts scrubText/scrubCompletionPayload)


def scrub_text(text: str, known_secrets: list[str]) -> str:
    """Strip known literal secrets + generic token shapes (Protect-LogTail port)."""
    out = text
    for secret in known_secrets:
        if secret:
            out = out.replace(secret, "<SECRET-REDACTED>")
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def scrub_completion_payload(
    payload: dict[str, Any],
    known_secrets: list[str],
) -> tuple[dict[str, Any], bool]:
    """Fail-loud payload scrub — returns ``(scrubbed, safe)``.

    ``safe=False`` when a known secret value survives the scrub; the caller
    must abort emission rather than write an unscrubbed secret into the events
    inbox (#1092 precedent).
    """
    scrubbed = {
        key: scrub_text(value, known_secrets) if isinstance(value, str) else value
        for key, value in payload.items()
    }
    serialized = json.dumps(scrubbed)
    for secret in known_secrets:
        if secret and len(secret) >= _RESIDUAL_CHECK_MIN_LEN and secret in serialized:
            return scrubbed, False
    return scrubbed, True


# -- Result-file read/validate ----------------------------------------------


class SandcastleResultError(ValueError):
    """A result file is malformed or failed a contract check."""


def validate_result_file(data: Any) -> dict[str, Any]:
    """Validate a parsed result file against the schemaVersion=2 contract.

    Raises :class:`SandcastleResultError` on any violation — a truncated write
    or an old-format file must never be mistaken for a real completion. Returns
    the validated dict on success.
    """
    if not isinstance(data, dict):
        raise SandcastleResultError(f"result file must be a JSON object, got {type(data).__name__}")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise SandcastleResultError(
            f"unsupported schemaVersion {data.get('schemaVersion')!r} (expected {SCHEMA_VERSION})"
        )
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise SandcastleResultError(
            f"result file missing required field(s): {', '.join(missing)}"
        )
    if data["outcome"] not in OUTCOMES.values():
        raise SandcastleResultError(f"unknown outcome {data['outcome']!r}")
    if not isinstance(data["commits"], list):
        raise SandcastleResultError("commits must be a list")
    if data["prEvidence"] not in (True, False, None):
        raise SandcastleResultError(f"prEvidence must be boolean or null, got {data['prEvidence']!r}")
    return data


def read_result_file(path: str | Path) -> dict[str, Any]:
    """Load + validate a result file from disk. Raises on any error."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SandcastleResultError(f"result file is not valid JSON: {exc}") from exc
    return validate_result_file(data)


# -- Completion payload reconstruction --------------------------------------


def build_completion_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the completion-event payload from a validated result file.

    Carries the 8-field completion contract plus the routing fields the
    orchestrator reads (pr_evidence / exit_confirmed / goal / failure_reason),
    matching agents/task_dispatch.py poll_completions payload shapes so a
    duplicate host-side emission dedups cleanly on the same dedup key.
    """
    event_type = "task_done" if data["outcome"] == OUTCOMES["SUCCESS"] else "task_failed"
    pr = data["pr"] if data["pr"] is not False else False
    payload: dict[str, Any] = {
        "task_id": data["taskId"] or None,
        "attempt": data["attempt"] if isinstance(data["attempt"], int) else 0,
        "lineage_key": data["lineageKey"] or None,
        "branch": data["branch"] or "",
        "pr": pr,
        "exit": data["exit"] if isinstance(data["exit"], int) else 1,
        "failure_class": data["failureClass"] or None,
        "tier": data["tier"] or None,
        "pr_evidence": data["prEvidence"],
        "exit_confirmed": event_type == "task_failed",
        "goal": data["goal"] or "",
    }
    if event_type == "task_done":
        payload["closing_ref"] = pr
    else:
        payload["failure_reason"] = data["failureReason"] or ""
    return payload


def completion_severity(event_type: str, pr_evidence: bool | None) -> str:
    """Severity for a completion event (mirror of task_dispatch._severity_for)."""
    if event_type == "task_done" and pr_evidence is True:
        return "info"
    return "medium"
