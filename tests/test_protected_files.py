"""Tests for scripts/protected-files.py — protected file detection.

#426 added principal-aware decisions (classify + should_block); legacy
``is_protected`` kept for backwards compat and still tested below.
"""

import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import importlib
protected_files = importlib.import_module("protected-files")

is_protected = protected_files.is_protected
normalize_path = protected_files.normalize_path
classify = protected_files.classify
should_block = protected_files.should_block


@pytest.fixture
def fake_claude_home(tmp_path, monkeypatch):
    """Pin ``JARVIS_CLAUDE_HOME`` to a tmp dir so user-level assertions don't
    depend on the developer's real home."""
    home = tmp_path / ".claude"
    monkeypatch.setenv("JARVIS_CLAUDE_HOME", str(home))
    return home.as_posix().rstrip("/")


# ── Protected files: secret-scanner surface ──────────────────────────
# Policy: file-level blocking is reserved for files whose corruption could
# leak secrets into git history before PR review sees it, plus the enforcement
# scripts themselves (privilege-escalation path).

def test_blocks_gitleaks():
    assert is_protected(".gitleaks.toml")

def test_blocks_pre_commit():
    assert is_protected(".pre-commit-config.yaml")

def test_blocks_secret_scanner():
    assert is_protected("scripts/secret-scanner.py")

def test_blocks_protected_files_script():
    """The enforcement hook itself must be protected against non-live tampering."""
    assert is_protected("scripts/protected-files.py")

def test_blocks_principal_script():
    """Principal-detection script: spoofing it bypasses the whole hook."""
    assert is_protected("scripts/principal.py")


# ── Absolute paths (Windows-style) ──────────────────────────────────

def test_blocks_absolute_windows():
    assert is_protected("C:\\Users\\jdoe\\GitHub\\jarvis\\.gitleaks.toml")

def test_blocks_absolute_forward_slash():
    assert is_protected("C:/Users/jdoe/GitHub/jarvis/scripts/secret-scanner.py")


# ── Non-protected files: allowed ─────────────────────────────────────
# Previously-protected files (SOUL.md, CLAUDE.md, mcp-memory/*, .mcp.json)
# are now handled by PR review + CI only — no file-level block needed.

def test_allows_soul_md():
    assert not is_protected("config/SOUL.md")

def test_allows_claude_md():
    assert not is_protected("CLAUDE.md")

def test_allows_mcp_json():
    assert not is_protected(".mcp.json")

def test_allows_mcp_memory_server():
    assert not is_protected("mcp-memory/server.py")

def test_allows_mcp_memory_handler():
    assert not is_protected("mcp-memory/handlers/events.py")

def test_allows_settings_json():
    assert not is_protected(".claude/settings.json")

def test_allows_regular_python():
    assert not is_protected("scripts/device-info.py")

def test_allows_docs():
    assert not is_protected("docs/PROJECT_PLAN.md")

def test_allows_tests():
    assert not is_protected("tests/test_secret_scanner.py")

def test_allows_skill():
    assert not is_protected(".claude/skills/delegate/SKILL.md")

def test_allows_other_json():
    assert not is_protected("config/device.json")


# ── Normalize path ──────────────────────────────────────────────────

def test_normalize_backslashes():
    assert normalize_path("config\\SOUL.md") == "config/SOUL.md"

def test_normalize_leading_dot_slash():
    assert normalize_path("./CLAUDE.md") == "CLAUDE.md"

def test_normalize_absolute():
    assert normalize_path("C:/Users/jdoe/GitHub/jarvis/CLAUDE.md") == "CLAUDE.md"


# ── User-level (~/.claude/) paths: blocked ───────────────────────────

def test_blocks_user_level_settings(fake_claude_home):
    assert is_protected(f"{fake_claude_home}/settings.json")


def test_blocks_user_level_soul(fake_claude_home):
    assert is_protected(f"{fake_claude_home}/SOUL.md")


def test_blocks_user_level_mcp(fake_claude_home):
    assert is_protected(f"{fake_claude_home}/.mcp.json")


def test_blocks_user_level_skill(fake_claude_home):
    assert is_protected(f"{fake_claude_home}/skills/implement/SKILL.md")


def test_blocks_user_level_skill_backslashes(fake_claude_home):
    # Normalize must handle native Windows separators on the user-level surface too.
    winpath = fake_claude_home.replace("/", "\\") + "\\skills\\delegate\\SKILL.md"
    assert is_protected(winpath)


def test_blocks_user_level_mcp_backslashes(fake_claude_home):
    # Symmetry with SKILL.md — ensure the full user-level surface handles backslashes.
    winpath = fake_claude_home.replace("/", "\\") + "\\.mcp.json"
    assert is_protected(winpath)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only: NTFS is case-insensitive")
def test_blocks_user_level_case_insensitive_on_windows(fake_claude_home):
    # On Windows the FS is case-insensitive, so the guard must be too —
    # otherwise an agent could bypass by lower-casing the drive letter or
    # the ``Users\\<name>`` path segment.
    mixed = fake_claude_home.lower() + "/settings.json"
    assert is_protected(mixed)


# ── User-level: look-alike paths allowed ────────────────────────────

def test_allows_other_project_claude_settings(fake_claude_home, tmp_path):
    """`.claude/settings.json` inside some unrelated repo is NOT user-level —
    and since normalize_path doesn't strip its prefix, it must not match
    the repo-level `.claude/settings.json` entry either."""
    other = (tmp_path / "other-project" / ".claude" / "settings.json").as_posix()
    assert not is_protected(other)


def test_allows_user_level_non_protected(fake_claude_home):
    # Files under ~/.claude/ that aren't on the list (e.g. projects/, history) stay allowed.
    assert not is_protected(f"{fake_claude_home}/projects/some-project/history.jsonl")


def test_allows_user_level_skill_subresource(fake_claude_home):
    # Only SKILL.md itself is protected — co-located scripts or resources aren't.
    assert not is_protected(f"{fake_claude_home}/skills/implement/helper.py")


# ── #426: classification (canonical vs mirror vs none) ──────────────


def test_classify_repo_canonical():
    """Repo-side protected files classify as ``canonical``."""
    assert classify(".gitleaks.toml") == "canonical"
    assert classify(".pre-commit-config.yaml") == "canonical"
    assert classify("scripts/secret-scanner.py") == "canonical"
    assert classify("scripts/protected-files.py") == "canonical"
    assert classify("scripts/principal.py") == "canonical"


def test_classify_user_level_mirror(fake_claude_home):
    """User-level ``~/.claude/*`` files classify as ``mirror``."""
    assert classify(f"{fake_claude_home}/SOUL.md") == "mirror"
    assert classify(f"{fake_claude_home}/settings.json") == "mirror"
    assert classify(f"{fake_claude_home}/.mcp.json") == "mirror"
    assert classify(f"{fake_claude_home}/skills/delegate/SKILL.md") == "mirror"


def test_classify_unprotected_returns_none():
    """Files not on either list classify as None."""
    assert classify("config/SOUL.md") is None
    assert classify("CLAUDE.md") is None
    assert classify(".mcp.json") is None
    assert classify("mcp-memory/server.py") is None
    assert classify("docs/PROJECT_PLAN.md") is None
    assert classify("tests/test_secret_scanner.py") is None


# ── #426: should_block(path, principal) — the matrix ────────────────


@pytest.mark.parametrize(
    "principal", ["live", "autonomous", "subagent", "supervised"]
)
def test_unprotected_never_blocks(principal):
    """Files outside the protected list never block, regardless of principal."""
    assert not should_block("config/SOUL.md", principal)
    assert not should_block("CLAUDE.md", principal)
    assert not should_block(".mcp.json", principal)
    assert not should_block("mcp-memory/server.py", principal)
    assert not should_block("docs/PROJECT_PLAN.md", principal)
    assert not should_block("config/device.json", principal)


def test_canonical_allows_live_principal():
    """Live owner can edit canonical sources — harness asks one-off."""
    assert not should_block(".gitleaks.toml", "live")
    assert not should_block(".pre-commit-config.yaml", "live")
    assert not should_block("scripts/secret-scanner.py", "live")
    assert not should_block("scripts/protected-files.py", "live")
    assert not should_block("scripts/principal.py", "live")


@pytest.mark.parametrize(
    "principal", ["autonomous", "subagent", "supervised"]
)
def test_canonical_blocks_non_live_principals(principal):
    """Anything but live blocks canonical sources."""
    assert should_block(".gitleaks.toml", principal)
    assert should_block(".pre-commit-config.yaml", principal)
    assert should_block("scripts/secret-scanner.py", principal)
    assert should_block("scripts/protected-files.py", principal)
    assert should_block("scripts/principal.py", principal)


@pytest.mark.parametrize(
    "principal", ["live", "autonomous", "subagent", "supervised"]
)
def test_mirror_blocks_all_principals(principal, fake_claude_home):
    """User-level mirrors block ALL principals — even live owner uses installer flow.

    Source of truth lives in the repo; direct edits to ``~/.claude/*`` drift
    on the next ``install.ps1 --apply``. Hook directs everyone to the
    installer rather than letting the harness ask owner to drift the mirror.
    """
    paths = [
        f"{fake_claude_home}/SOUL.md",
        f"{fake_claude_home}/settings.json",
        f"{fake_claude_home}/.mcp.json",
        f"{fake_claude_home}/skills/delegate/SKILL.md",
    ]
    for path in paths:
        assert should_block(path, principal), (
            f"{path} should block {principal} (mirror — use installer flow)"
        )


def test_block_reason_mentions_installer_for_mirror(fake_claude_home):
    """Mirror block message must point users at the installer flow."""
    reason = protected_files._block_reason(
        f"{fake_claude_home}/SOUL.md", "mirror", "live"
    )
    assert "install" in reason.lower()


def test_block_reason_mentions_principal_for_canonical():
    """Canonical block message must explain the principal-side constraint."""
    reason = protected_files._block_reason(
        "config/SOUL.md", "canonical", "autonomous"
    )
    assert "autonomous" in reason.lower()
    assert "owner" in reason.lower() or "live" in reason.lower()
