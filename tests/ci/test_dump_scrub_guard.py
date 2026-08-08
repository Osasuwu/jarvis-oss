"""Meta-test for scripts/dump-decisions-quarterly.py sensitive-content scrub.

Verifies:
1. The `_check_decision` function detects all configured sensitive patterns
2. The scrub is wired into `main()` so it actually runs before writing
3. Clean decisions pass through without false positives

#326 spirit: a scrub that exists but isn't wired in must fail red.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

# Add scripts/ to sys.path so the module can be imported under its dotted name.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

dq = importlib.import_module("dump-decisions-quarterly")

# Re-bind the functions under test for convenience
_check_decision = dq._check_decision
_SENSITIVE_PATTERNS = dq._SENSITIVE_PATTERNS


# ============================================================================
# Structural meta-test: scrub is wired into main()
# ============================================================================


def test_scrub_is_wired_into_main():
    """If _check_decision exists but isn't called from main(), fail red.

    A scrub function that's defined but never invoked is the exact class of
    bug this meta-test catches — the function silently rots as the codebase
    evolves and someone moves the write path without moving the check.
    """
    main_src = inspect.getsource(dq.main)
    assert "_check_decision" in main_src, (
        "_check_decision must be called from main() — "
        "a scrub that exists but isn't wired in must fail red"
    )


# ============================================================================
# Pattern detection tests (synthetic fixtures only)
# ============================================================================


def _make_decision(payload_decision: str) -> dict:
    """Build a minimal decision dict whose rendered output contains the given text."""
    return {
        "id": "test-uuid",
        "actor": "session:test",
        "created_at": "2026-04-01T12:00:00Z",
        "payload": {
            "decision": payload_decision,
            "reversibility": "reversible",
            "rationale": "",
            "alternatives_considered": [],
            "memories_used": [],
            "intentionally_empty": False,
        },
    }


def test_detects_tailnet_ip():
    d = _make_decision("deployed to 100.64.1.1")
    assert _check_decision(d) == "tailnet-ip"


def test_detects_rfc1918_10():
    d = _make_decision("server at 10.0.0.5")
    assert _check_decision(d) == "rfc1918-10"


def test_detects_rfc1918_172():
    d = _make_decision("internal 172.20.0.1")
    assert _check_decision(d) == "rfc1918-172"


def test_detects_rfc1918_192():
    d = _make_decision("lan 192.168.1.100")
    assert _check_decision(d) == "rfc1918-192"


def test_detects_windows_user_path():
    d = _make_decision("config at C:\\Users\\jdoe\\app")
    assert _check_decision(d) == "win-user-path"


def test_detects_posix_home_path():
    d = _make_decision("config at /home/jdoe/.cache")
    assert _check_decision(d) == "nix-user-path"


def test_detects_macos_users_path():
    d = _make_decision("config at /Users/jdoe/Library")
    assert _check_decision(d) == "nix-user-path"


def test_detects_email():
    d = _make_decision("contact via jdoe@example.com")
    assert _check_decision(d) == "email"


def test_clean_decision_passes():
    d = _make_decision("switched to JWT auth for the API layer")
    assert _check_decision(d) is None


def test_ip_in_code_context_passes():
    """A bare number like 100.1.1 that isn't a real IP must not false-trigger."""
    d = _make_decision("bumped version to 100.1.1")
    assert _check_decision(d) is None


def test_patterns_have_labels():
    """Every pattern entry must carry a non-empty label string."""
    import re
    for pat, label in _SENSITIVE_PATTERNS:
        assert isinstance(pat, type(re.compile(""))), f"entry {label} has non-Pattern"
        assert label and isinstance(label, str), "entry has empty or non-str label"


def test_octet_rejects_256():
    """The _OCTET helper must not match numbers above 255."""
    import re
    octet = dq._OCTET
    assert re.fullmatch(octet, "0")
    assert re.fullmatch(octet, "255")
    assert re.fullmatch(octet, "100")
    assert not re.fullmatch(octet, "256")
    assert not re.fullmatch(octet, "999")
