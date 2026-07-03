"""Tests for scripts/install/installer.py — Epic #335 M1."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml


# Add scripts/install/ to sys.path so `import installer` resolves; dataclasses
# need the module registered in sys.modules (__module__ lookup).
import sys as _sys

_install_dir = Path(__file__).resolve().parents[1] / "scripts" / "install"
if str(_install_dir) not in _sys.path:
    _sys.path.insert(0, str(_install_dir))

import installer  # noqa: E402  — path hack is intentional


# ---------- fixtures ----------


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal fake repo with a small .claude/, config/, .mcp.json and git init."""
    repo = tmp_path / "jarvis"
    (repo / ".claude" / "skills" / "implement").mkdir(parents=True)
    (repo / ".claude" / "skills" / "implement" / "SKILL.md").write_text(
        "# implement skill\n", encoding="utf-8"
    )
    (repo / ".claude" / "skills" / "niche").mkdir(parents=True)
    (repo / ".claude" / "skills" / "niche" / "SKILL.md").write_text(
        "# niche skill (should NOT be whitelisted)\n", encoding="utf-8"
    )
    (repo / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python scripts/session-context.py",
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (repo / "config").mkdir()
    (repo / "config" / "SOUL.md").write_text("# SOUL stub\n", encoding="utf-8")
    (repo / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {
                        "command": "python",
                        "args": ["scripts/run-memory-server.py"],
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # git init so current_git_sha() works
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "init",
        ],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def manifest(tmp_path: Path, fake_repo: Path) -> Path:
    target = tmp_path / "claude_home"
    data = {
        "version": 1,
        "target_root": str(target),
        "version_marker": ".jarvis-version",
        "groups": [
            {
                "id": "soul",
                "enabled": True,
                "files": [{"source": "config/SOUL.md", "dest": "SOUL.md", "template": False}],
            },
            {
                "id": "hooks_settings",
                "enabled": True,
                "files": [
                    {
                        "source": ".claude/settings.json",
                        "dest": "settings.json",
                        "template": True,
                    }
                ],
            },
            {
                "id": "mcp_config",
                "enabled": True,
                "files": [{"source": ".mcp.json", "dest": ".mcp.json", "template": True}],
            },
            {
                "id": "skills",
                "enabled": True,
                "directories": [
                    {
                        "source": ".claude/skills",
                        "dest": "skills",
                        "template": False,
                        "include": ["implement"],
                    }
                ],
            },
        ],
        "env_vars": [
            {"name": "JARVIS_HOME", "value": "{repo_root}", "platforms": ["windows", "posix"]}
        ],
        "health_check": {"enabled": False},
        "backup": {"prefix": ".claude.backup-", "retain": 3},
    }
    path = fake_repo / "install-manifest.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# ---------- unit tests ----------


def test_load_manifest_rejects_unknown_version(tmp_path: Path) -> None:
    bad = tmp_path / "m.yaml"
    bad.write_text(yaml.safe_dump({"version": 999}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported manifest version"):
        installer.load_manifest(bad)


def test_detect_state_fresh_when_target_missing(tmp_path: Path) -> None:
    state, prev = installer.detect_state(tmp_path / "missing", "abc123")
    assert state == "fresh"
    assert prev is None


def test_detect_state_current_when_sha_matches(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.mkdir()
    (target / ".jarvis-version").write_text("abc123\n", encoding="utf-8")
    state, prev = installer.detect_state(target, "abc123")
    assert state == "current"
    assert prev == "abc123"


def test_detect_state_outdated_when_sha_differs(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.mkdir()
    (target / ".jarvis-version").write_text("old\n", encoding="utf-8")
    state, prev = installer.detect_state(target, "new")
    assert state == "outdated"
    assert prev == "old"


def test_template_content_json_rewrites_relative_paths(fake_repo: Path) -> None:
    target = fake_repo / ".claude" / "settings.json"
    claude_home = Path("/tmp/not-used")
    rendered = installer.template_content(target, fake_repo, claude_home).decode("utf-8")
    data = json.loads(rendered)
    command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    expected_prefix = fake_repo.as_posix() + "/scripts/"
    assert expected_prefix in command, f"got: {command}"
    assert not command.startswith("python scripts/"), "relative path not rewritten"


def test_template_content_mcp_json_rewrites_args(fake_repo: Path) -> None:
    target = fake_repo / ".mcp.json"
    rendered = installer.template_content(target, fake_repo, Path("/")).decode("utf-8")
    data = json.loads(rendered)
    args = data["mcpServers"]["memory"]["args"]
    assert args[0].startswith(fake_repo.as_posix() + "/scripts/")


def _write_gated_mcp_source(repo: Path) -> Path:
    src = repo / "gated.mcp.json"
    src.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "plain": {"command": "npx", "args": ["plain-server"]},
                    "gated": {
                        "command": "python",
                        "args": ["${GATED_HOME}/server.py"],
                        "x-jarvis-requires-env": "GATED_HOME",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return src


def _planned_server_names(actions: list) -> list[str]:
    return [a.dest for a in actions if a.kind == "register_mcp_user"]


def test_mcp_gate_skips_server_when_required_env_unset(fake_repo: Path, monkeypatch) -> None:
    """A server with x-jarvis-requires-env is skipped where that var is unset."""
    monkeypatch.delenv("GATED_HOME", raising=False)
    src = _write_gated_mcp_source(fake_repo)
    actions = installer._plan_mcp_user_registrations(src, fake_repo, fake_repo / "t")
    planned = _planned_server_names(actions)
    assert "plain" in planned
    assert "gated" not in planned


def test_mcp_gate_registers_server_and_strips_marker_when_env_set(
    fake_repo: Path, monkeypatch
) -> None:
    """When the required env IS set, the server registers and the marker key is
    stripped from the spec so it never reaches `claude mcp add`."""
    monkeypatch.setenv("GATED_HOME", "/opt/gated")
    src = _write_gated_mcp_source(fake_repo)
    actions = installer._plan_mcp_user_registrations(src, fake_repo, fake_repo / "t")
    gated = [a for a in actions if a.kind == "register_mcp_user" and a.dest == "gated"]
    assert len(gated) == 1
    spec = json.loads(gated[0].note)["spec"]
    assert "x-jarvis-requires-env" not in spec
    assert spec["command"] == "python"


def test_build_plan_state_fresh_has_writes_no_backup(manifest: Path, fake_repo: Path) -> None:
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    assert plan.state == "fresh"
    assert plan.backup_path is None  # target doesn't exist yet
    kinds = [a.kind for a in plan.actions]
    assert kinds.count("copy_file") >= 3  # SOUL, settings, mcp
    assert kinds.count("copy_dir") == 1
    assert kinds.count("write_version") == 1
    assert kinds.count("set_env") == 1


def test_apply_plan_creates_files_and_version_marker(manifest: Path, fake_repo: Path) -> None:
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan, m, run_env=None)

    target = plan.target_root
    assert (target / "SOUL.md").exists()
    assert (target / "settings.json").exists()
    assert (target / ".mcp.json").exists()
    assert (target / "skills" / "implement" / "SKILL.md").exists()
    # whitelist excluded
    assert not (target / "skills" / "niche").exists()

    marker = (target / ".jarvis-version").read_text(encoding="utf-8").strip()
    assert marker == plan.current_sha


def test_apply_is_idempotent(manifest: Path, fake_repo: Path) -> None:
    m = installer.load_manifest(manifest)
    plan1 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan1, m, run_env=None)

    plan2 = installer.build_plan(m, fake_repo)
    assert plan2.state == "current"
    assert plan2.actions == []


def test_outdated_triggers_backup(manifest: Path, fake_repo: Path, tmp_path: Path) -> None:
    m = installer.load_manifest(manifest)
    plan1 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan1, m, run_env=None)

    # Pretend the repo moved to a new SHA by overwriting the marker.
    (plan1.target_root / ".jarvis-version").write_text("old-sha-not-real\n", encoding="utf-8")

    plan2 = installer.build_plan(m, fake_repo)
    assert plan2.state == "outdated"
    assert plan2.backup_path is not None
    installer.apply_plan(plan2, m, run_env=None)
    assert plan2.backup_path.exists()
    assert (plan2.backup_path / "SOUL.md").exists()


def test_rollback_restores_target(manifest: Path, fake_repo: Path) -> None:
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan, m, run_env=None)

    backup = plan.target_root.parent / ".claude.backup-manual"
    import shutil

    shutil.copytree(plan.target_root, backup)

    # Corrupt the target.
    (plan.target_root / "SOUL.md").write_text("corrupted\n", encoding="utf-8")
    installer.rollback(plan.target_root, backup)
    assert (plan.target_root / "SOUL.md").read_text(encoding="utf-8") == "# SOUL stub\n"


def test_prune_backups_keeps_latest_n(fake_repo: Path, tmp_path: Path) -> None:
    target_root = tmp_path / "claude_home"
    parent = target_root.parent
    for name in [
        ".claude.backup-20260401-000000",
        ".claude.backup-20260402-000000",
        ".claude.backup-20260403-000000",
        ".claude.backup-20260404-000000",
    ]:
        (parent / name).mkdir()
    dropped = installer.prune_backups(target_root, ".claude.backup-", retain=2)
    assert len(dropped) == 2
    remaining = sorted(p.name for p in parent.iterdir() if p.name.startswith(".claude.backup-"))
    assert remaining == [".claude.backup-20260403-000000", ".claude.backup-20260404-000000"]


def test_health_check_disabled_by_default_returns_ok() -> None:
    status, logs = installer.run_health_check({"health_check": {"enabled": False}}, Path("."))
    assert status == "ok"
    assert logs == []


def test_health_check_failed_command_reports_failure(fake_repo: Path) -> None:
    m = {"health_check": {"enabled": True, "commands": ["python -c exit(1)"]}}
    status, logs = installer.run_health_check(m, fake_repo)
    assert status == "fail"
    assert any("FAIL" in line for line in logs)


@pytest.mark.skipif(
    _sys.platform != "win32",
    reason=(
        "Regression #352 is specific to non-UTF-8 Windows consoles (cp1251/"
        'cp866). The inline `python -c "..."` quoting also relies on cmd.exe '
        "parsing — bash terminates the outer string on the first inner quote."
    ),
)
def test_health_check_handles_utf8_output(fake_repo: Path) -> None:
    """Regression for #352: on non-UTF-8 Windows locales (e.g. cp1251),
    text=True without explicit encoding crashes the reader thread when a
    health-check script emits em-dashes / Cyrillic. The parent call now
    forces encoding='utf-8', errors='replace' — asserting the guarantee
    here locks that in regardless of the host's locale."""
    payload = 'import sys; sys.stdout.buffer.write("before \u2014 после\n".encode("utf-8"))'
    m = {
        "health_check": {
            "enabled": True,
            "commands": [f'python -c "{payload}"'],
        }
    }
    status, logs = installer.run_health_check(m, fake_repo)
    assert status == "ok", logs
    assert any("OK" in line for line in logs)


def test_health_check_timeout_with_grandchild_returns_promptly(
    fake_repo: Path, tmp_path: Path
) -> None:
    """Regression for the 2026-06-12 install wedge: a health command whose
    child spawns a grandchild (session-context.py re-execs into the venv
    python) must not hang run_health_check past its timeout. With the old
    capture_output pipes, the timeout-kill reaped only the direct child; the
    grandchild kept the inherited pipe write-ends open and the parent blocked
    on EOF forever (observed: install.ps1 -Apply wedged 35+ min). The
    file-redirect + tree-kill rewrite bounds the wall clock and reaps the
    grandchild too.

    Paths are passed unquoted — pytest tmp dirs have no spaces, and quoted
    tokens survive shlex(posix=False) on Windows with their quotes attached,
    which list2cmdline then mangles.
    """
    marker = tmp_path / "grandchild.heartbeat"
    grandchild_py = tmp_path / "grandchild.py"
    grandchild_py.write_text(
        "import time\n"
        f"f = open({str(marker)!r}, 'a')\n"
        "for _ in range(600):\n"
        "    f.write('x')\n"
        "    f.flush()\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    # Mirrors session-context.py's bootstrap exactly: blocking subprocess.call
    # of another python, which inherits this process's stdout/stderr handles.
    wrapper_py = tmp_path / "wrapper.py"
    wrapper_py.write_text(
        "import subprocess, sys\n"
        f"sys.exit(subprocess.call([sys.executable, {str(grandchild_py)!r}]))\n",
        encoding="utf-8",
    )
    m = {
        "health_check": {
            "enabled": True,
            "timeout": 5,
            "commands": [f"{_sys.executable} {wrapper_py}"],
        }
    }

    # Run in a daemon thread so a regression FAILS the test instead of
    # hanging pytest until the CI job timeout.
    result: dict[str, tuple[str, list[str]]] = {}

    def target() -> None:
        result["r"] = installer.run_health_check(m, fake_repo)

    t = threading.Thread(target=target, daemon=True)
    start = time.monotonic()
    t.start()
    t.join(timeout=35)
    elapsed = time.monotonic() - start
    assert not t.is_alive(), (
        "run_health_check still blocked after 35s — grandchild pipe-EOF hang regressed"
    )
    assert "r" in result, "run_health_check raised — thread exited without returning a result"
    status, logs = result["r"]
    assert status == "timeout", logs
    # timeout=5 + tree-kill worst case (taskkill 15s + wait 5s + outer wait 10s)
    # ⇒ ~10–12s expected, ~35s theoretical max on Windows. Bound at 30s:
    # catches a slow-kill regression while allowing Windows CI overhead.
    assert elapsed < 30, f"returned after {elapsed:.0f}s — timeout-kill did not bound it"

    # Tree-kill must reach the grandchild: its heartbeat (append-only, so the
    # size can only grow while it lives) stops growing.
    time.sleep(1.0)
    size_before = marker.stat().st_size if marker.exists() else 0
    time.sleep(1.5)
    size_after = marker.stat().st_size if marker.exists() else 0
    assert size_before == size_after, "grandchild still writing after tree-kill"


def test_kill_window_posix_self_exit_nonzero_is_failure() -> None:
    """MAJOR (#963 review): on POSIX a positive returncode means the process
    self-exited non-zero during the kill window — a genuine FAIL, not an
    inconclusive timeout. Classifying it 'timeout' would leave a broken apply
    in place with no rollback."""
    assert installer._kill_window_is_failure(3, "posix") is True


def test_kill_window_posix_signal_kill_is_not_failure() -> None:
    """A negative returncode is our SIGKILL (the real timeout path) — it must
    NOT be read as a self-failure, else a normal timeout triggers a rollback."""
    assert installer._kill_window_is_failure(-9, "posix") is False


def test_kill_window_posix_clean_exit_is_not_failure() -> None:
    """returncode 0 (or still-None) in the kill window is not a failure."""
    assert installer._kill_window_is_failure(0, "posix") is False
    assert installer._kill_window_is_failure(None, "posix") is False


def test_kill_window_windows_stays_timeout() -> None:
    """On Windows TerminateProcess yields exit 1, indistinguishable from a real
    failure — so the kill-window reclassification is POSIX-only and Windows
    stays conservatively a timeout (no false rollback on the target platform)."""
    assert installer._kill_window_is_failure(1, "nt") is False


# ---------- malformed health-check command (#963 review) ----------


def test_health_check_malformed_command_is_fail(fake_repo: Path) -> None:
    """MAJOR (#963 review): an unterminated quote in a health_check command
    raises ValueError from shlex.split — it must be caught as a clean 'fail',
    not propagate as an unformatted traceback out of run_health_check."""
    m = {
        "health_check": {
            "enabled": True,
            "timeout": 5,
            "commands": ["python scripts/foo.py --arg 'bar"],  # unterminated quote
        }
    }
    status, logs = installer.run_health_check(m, fake_repo)
    assert status == "fail"
    assert any("malformed command" in line for line in logs)


# ---------- orphan-skill cleanup (#576) ----------


def test_build_plan_emits_prune_orphan_for_stale_skill_dir(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """Stale skill dir absent from `include` whitelist produces a prune_orphan action."""
    m = installer.load_manifest(manifest)
    # First apply: target gets `implement` skill from whitelist.
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    # Simulate a deprecated skill left behind from a prior install.
    (target / "skills" / "deprecated-skill").mkdir()
    (target / "skills" / "deprecated-skill" / "SKILL.md").write_text("# stale\n", encoding="utf-8")
    # Bump SHA so state goes outdated and actions are re-emitted.
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    orphans = [a for a in plan.actions if a.kind == "prune_orphan"]
    assert len(orphans) == 1
    assert Path(orphans[0].source).name == "deprecated-skill"
    # dest must land in .skills-orphaned/ OUTSIDE skills/ (#927)
    dest = Path(orphans[0].dest)
    assert dest.parent == target / ".skills-orphaned"
    assert dest.name == "deprecated-skill"


def test_apply_plan_quarantines_orphan_skill(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """apply_plan moves orphan dir to .skills-orphaned/ outside skills/, not a sibling rename."""
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    orphan_dir = target / "skills" / "deprecated-skill"
    orphan_dir.mkdir()
    (orphan_dir / "SKILL.md").write_text("# stale\n", encoding="utf-8")
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan, m, run_env=None)

    assert not orphan_dir.exists(), "orphan should have been moved out of skills/"
    # Must land in .skills-orphaned/ — NOT inside skills/ (#927)
    quarantined = target / ".skills-orphaned" / "deprecated-skill"
    assert quarantined.exists(), ".skills-orphaned/deprecated-skill must exist"
    assert (quarantined / "SKILL.md").read_text(encoding="utf-8") == "# stale\n"
    # No .bak.orphan remnant inside skills/
    assert not list((target / "skills").glob("deprecated-skill*"))
    # Whitelisted skill still present.
    assert (target / "skills" / "implement" / "SKILL.md").exists()


def test_dry_run_does_not_remove_orphan(manifest: Path, fake_repo: Path, tmp_path: Path) -> None:
    """Building the plan must not touch orphans — only apply does."""
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    orphan_dir = target / "skills" / "deprecated-skill"
    orphan_dir.mkdir()
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)  # plan-only, no apply
    assert any(a.kind == "prune_orphan" for a in plan.actions)
    assert orphan_dir.exists(), "dry-run must not move the orphan"


def test_no_orphan_actions_when_all_in_whitelist(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """When every dest child is in the whitelist, no prune actions are planned."""
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    assert not any(a.kind == "prune_orphan" for a in plan.actions)


def test_fresh_install_emits_no_orphan_actions(manifest: Path, fake_repo: Path) -> None:
    """Fresh state (target absent) cannot have orphans by definition."""
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    assert plan.state == "fresh"
    assert not any(a.kind == "prune_orphan" for a in plan.actions)


def test_orphan_scan_skips_already_quarantined_dirs(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """Already-quarantined `.bak.orphan` dirs must not be re-quarantined on next install.

    Regression: without this filter, `foo.bak.orphan` becomes
    `foo.bak.orphan.bak.orphan` on every outdated-state apply, growing
    unboundedly. See issue/PR linked in the body.
    """
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    # Two flavours of quarantine name: bare label and label-with-timestamp.
    (target / "skills" / "deprecated-skill.bak.orphan").mkdir()
    (target / "skills" / "deprecated-skill.bak.orphan" / "SKILL.md").write_text(
        "# already quarantined\n", encoding="utf-8"
    )
    (target / "skills" / "other.bak.orphan-20260101-120000").mkdir()
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    orphan_sources = [Path(a.source).name for a in plan.actions if a.kind == "prune_orphan"]
    assert "deprecated-skill.bak.orphan" not in orphan_sources
    assert "other.bak.orphan-20260101-120000" not in orphan_sources


def test_directory_without_include_skips_orphan_check(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """An entry without `include` copies everything → orphan-detection must skip it."""
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for g in data["groups"]:
        if g["id"] == "skills":
            for d in g["directories"]:
                d.pop("include", None)
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    # Without include, the source dir is copied wholesale (both implement +
    # niche). A child not in source/manifest is just a user file — leave it.
    (target / "skills" / "user-extra").mkdir()
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    assert not any(a.kind == "prune_orphan" for a in plan.actions)


def test_existing_bak_orphan_is_not_re_quarantined(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """`.bak.orphan` leftovers from prior runs must not be re-detected as orphans (#659).

    Without the skip, every subsequent install would nest the suffix one level
    deeper: `dnd.bak.orphan` → `dnd.bak.orphan.bak.orphan` → ...
    """
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    # Synthesize the state left by a previous quarantine pass.
    leftover = target / "skills" / "dnd.bak.orphan"
    leftover.mkdir()
    (leftover / "SKILL.md").write_text("# previously quarantined\n", encoding="utf-8")
    # Also a timestamped variant (same-day collision case from `_backup_dest`).
    leftover_stamped = target / "skills" / "dnd-prep.bak.orphan-20260516-120000"
    leftover_stamped.mkdir()
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    orphans = [a for a in plan.actions if a.kind == "prune_orphan"]
    orphan_sources = {Path(a.source).name for a in orphans}
    assert "dnd.bak.orphan" not in orphan_sources, (
        "prior-run quarantine must not be re-quarantined into .bak.orphan.bak.orphan"
    )
    assert "dnd-prep.bak.orphan-20260516-120000" not in orphan_sources, (
        "timestamped quarantine variant must also be skipped"
    )


def test_prune_orphan_dest_is_outside_skills_dir(
    manifest: Path, fake_repo: Path, tmp_path: Path
) -> None:
    """Orphan dest must be outside skills/ so the skill loader ignores it (#927).

    The original bug: dest was computed with path.with_name() which stays inside
    skills/ — Claude Code loads any subdir there regardless of suffix.
    """
    m = installer.load_manifest(manifest)
    installer.apply_plan(installer.build_plan(m, fake_repo), m, run_env=None)
    target = tmp_path / "claude_home"
    (target / "skills" / "stale-skill").mkdir()
    (target / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")

    plan = installer.build_plan(m, fake_repo)
    orphans = [a for a in plan.actions if a.kind == "prune_orphan"]
    assert len(orphans) == 1
    dest = Path(orphans[0].dest)
    skills_dir = target / "skills"
    assert skills_dir not in dest.parents, (
        f"orphan dest {dest} must not be inside skills/ — skill loader picks it up"
    )
    assert dest.parent == target / ".skills-orphaned"


def test_disabled_group_is_skipped(manifest: Path, fake_repo: Path) -> None:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for g in data["groups"]:
        if g["id"] == "soul":
            g["enabled"] = False
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan, m, run_env=None)
    assert not (plan.target_root / "SOUL.md").exists()
    # Others still land.
    assert (plan.target_root / "settings.json").exists()


def test_real_manifest_m4_all_groups_enabled() -> None:
    """Real manifest shape after M4 (#339): all 4 groups enabled.
    soul joins skills + hooks_settings + mcp_config.
    """
    real = Path(__file__).resolve().parents[1] / "install-manifest.yaml"
    assert real.exists(), "install-manifest.yaml must ship in-tree"
    m = installer.load_manifest(real)

    by_id = {g["id"]: g for g in m["groups"]}
    assert by_id["skills"]["enabled"] is True
    assert by_id["skills"]["directories"][0]["source"] == ".claude-userlevel/skills"
    include = by_id["skills"]["directories"][0]["include"]
    assert "sprint-report" not in include
    assert "implement" in include and "delegate" in include

    assert by_id["hooks_settings"]["enabled"] is True
    settings_entry = by_id["hooks_settings"]["files"][0]
    assert settings_entry["source"] == ".claude-userlevel/settings.json"
    assert settings_entry["merge"] is True
    assert settings_entry["template"] is True

    assert by_id["mcp_config"]["enabled"] is True
    mcp_entry = by_id["mcp_config"]["files"][0]
    assert mcp_entry["source"] == ".claude-userlevel/.mcp.json"
    assert mcp_entry["install_as"] == "user_mcp_registrations"
    assert mcp_entry["template"] is True

    # M4: soul flipped on. Source stays at config/SOUL.md (canonical git-tracked).
    # Dest SOUL.md lands at ~/.claude/SOUL.md via plain copy (no template, no merge).
    assert by_id["soul"]["enabled"] is True
    soul_entry = by_id["soul"]["files"][0]
    assert soul_entry["source"] == "config/SOUL.md"
    assert soul_entry["dest"] == "SOUL.md"
    assert soul_entry.get("template", False) is False
    assert soul_entry.get("merge", False) is False


def test_userlevel_settings_points_soul_at_user_level(fake_repo: Path) -> None:
    """M4 (#339) acceptance: rendered SessionStart command reads SOUL from
    {{CLAUDE_USER_HOME}}/SOUL.md (so it works in any CWD, not tied to
    JARVIS_HOME — user-level install becomes the source of SOUL at runtime)."""
    repo_root = Path(__file__).resolve().parents[1]
    settings_src = repo_root / ".claude-userlevel" / "settings.json"
    claude_home = Path("/opt/fakehome/.claude")
    rendered = installer.template_content(settings_src, repo_root, claude_home).decode("utf-8")
    data = json.loads(rendered)
    # Every SessionStart cat must reference the user-level SOUL, not
    # <JARVIS_HOME>/config/SOUL.md.
    for entry in data["hooks"]["SessionStart"]:
        for hook in entry["hooks"]:
            cmd = hook["command"]
            if "SOUL.md" in cmd:
                assert f"{claude_home.as_posix()}/SOUL.md" in cmd, (
                    f"SessionStart SOUL path not rewritten to user level: {cmd}"
                )
                assert "config/SOUL.md" not in cmd, (
                    f"leftover relative config/SOUL.md reference: {cmd}"
                )


def test_userlevel_source_files_exist() -> None:
    """Source-of-truth files for M3 must ship in-tree."""
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / ".claude-userlevel" / "settings.json").exists()
    assert (repo_root / ".claude-userlevel" / ".mcp.json").exists()


# ---------- #344: tightening before M3 ----------


def test_path_rewrite_preserves_urls() -> None:
    """Regex must NOT rewrite `scripts/` / `config/` tokens that are part of
    a URL or a compound identifier. Was `\\b(scripts|config)/` — too greedy.
    """
    data = {
        "docs": "https://example.com/scripts/foo",
        "nested": {"ref": "https://cdn.example.com/config/v2/x.yaml"},
        "compound": "my-scripts/foo",
        "list": ["https://gh.io/scripts/a", "scripts/keep"],
    }
    out = installer._transform_json_paths(data, "/abs/repo")
    assert out["docs"] == "https://example.com/scripts/foo"
    assert out["nested"]["ref"] == "https://cdn.example.com/config/v2/x.yaml"
    assert out["compound"] == "my-scripts/foo"
    # The one real path still gets rewritten.
    assert out["list"][0] == "https://gh.io/scripts/a"
    assert out["list"][1] == "/abs/repo/scripts/keep"


def test_path_rewrite_handles_embedded_command_paths() -> None:
    """Command strings with multiple relative paths (whitespace-separated)
    must have all path tokens rewritten."""
    cmd = "python scripts/a.py && cat config/SOUL.md && python scripts/b.py"
    out = installer._transform_json_paths(cmd, "/abs/repo")
    assert "/abs/repo/scripts/a.py" in out
    assert "/abs/repo/config/SOUL.md" in out
    assert "/abs/repo/scripts/b.py" in out
    assert " scripts/" not in out
    assert " config/" not in out


def test_fresh_install_rollback_on_apply_failure(
    manifest: Path, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh-state apply failure must leave target_root removed, not half-written.

    Without the fix, a failure mid-apply leaves a stub directory that
    detect_state reads as outdated next time, masking the original error.
    """
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    assert plan.state == "fresh"
    assert plan.backup_path is None

    # Make apply create the target dir (simulating partial write) then fail.
    real_copy_file = installer._copy_file
    call_count = {"n": 0}

    def flaky_copy_file(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            real_copy_file(*args, **kwargs)
            return
        raise RuntimeError("simulated apply failure")

    monkeypatch.setattr(installer, "_copy_file", flaky_copy_file)

    with pytest.raises(RuntimeError, match="simulated apply failure"):
        installer.apply_plan(plan, m, run_env=None)

    # Sanity: target_root exists and has partial content.
    assert plan.target_root.exists()
    assert any(plan.target_root.iterdir())

    # Now invoke the rollback helper directly (what main() does in the except).
    installer._rollback_failed_apply(plan)
    assert not plan.target_root.exists(), "fresh install failure must remove target_root entirely"


def test_health_check_uses_shlex_for_argv_parsing(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd.split() broke on paths with spaces; shlex handles quoted args."""
    seen: list[list[str]] = []

    class FakeProc:
        returncode = 0
        pid = 99999

        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        seen.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)

    m = {
        "health_check": {
            "enabled": True,
            "commands": [
                'python "/tmp/path with spaces/script.py" --flag value',
            ],
        }
    }
    status, _logs = installer.run_health_check(m, fake_repo)
    assert status == "ok"
    assert len(seen) == 1
    argv = seen[0]
    # Whether posix or windows flavor of shlex, the quoted path must stay
    # as ONE argv element (not split on spaces).
    assert any("path with spaces" in tok for tok in argv), (
        f"quoted path split by whitespace: argv={argv}"
    )
    assert argv[0] == _sys.executable  # python token resolved to running interpreter


# ---------- #338 (M3): settings.json + .mcp.json deep-merge ----------


def test_deep_merge_preserves_user_hook_events(tmp_path: Path) -> None:
    """User events jarvis doesn't own (e.g. Stop) must survive merge."""
    existing = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [{"type": "command", "command": "user-custom-session.sh"}],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "user-cleanup.sh"}]}],
        },
        "theme": "dark",  # top-level user key
    }
    source = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python /abs/jarvis/scripts/session-context.py",
                        }
                    ],
                }
            ],
            "PreCompact": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python /abs/jarvis/scripts/pre-compact-backup.py",
                        }
                    ]
                }
            ],
        }
    }
    merged = installer._deep_merge_jarvis_json(existing, source)

    # Jarvis replaces SessionStart wholesale — user's custom SessionStart entry is gone.
    ss = merged["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "session-context.py" in ss
    assert "user-custom-session.sh" not in ss

    # Jarvis adds PreCompact (it didn't exist before).
    assert "PreCompact" in merged["hooks"]
    assert "pre-compact-backup.py" in merged["hooks"]["PreCompact"][0]["hooks"][0]["command"]

    # Stop (user-owned event jarvis doesn't touch) is preserved.
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"] == "user-cleanup.sh"

    # Top-level non-hooks user key preserved.
    assert merged["theme"] == "dark"


def test_deep_merge_preserves_user_mcp_servers(tmp_path: Path) -> None:
    """User-added mcpServers entries must survive; jarvis-owned ones replaced."""
    existing = {
        "mcpServers": {
            "memory": {"command": "old-memory", "args": ["obsolete"]},
            "user-custom": {"command": "npx", "args": ["user-server"]},
        }
    }
    source = {
        "mcpServers": {
            "memory": {"command": "python", "args": ["/abs/jarvis/scripts/run-memory-server.py"]},
            "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp@latest"]},
        }
    }
    merged = installer._deep_merge_jarvis_json(existing, source)

    assert merged["mcpServers"]["memory"]["command"] == "python"
    assert "run-memory-server.py" in merged["mcpServers"]["memory"]["args"][0]
    assert merged["mcpServers"]["context7"]["args"][0] == "-y"
    # User-added server preserved.
    assert merged["mcpServers"]["user-custom"]["command"] == "npx"
    assert merged["mcpServers"]["user-custom"]["args"] == ["user-server"]


def test_merge_json_file_fresh_write_when_dest_missing(tmp_path: Path) -> None:
    """No existing target → write source as-is (still templated)."""
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"hooks": {"SessionStart": [{"command": "x"}]}}), encoding="utf-8")
    dest = tmp_path / "out" / "settings.json"
    installer._merge_json_file(src, dest, template=False, repo_root=tmp_path, claude_home=tmp_path)
    assert dest.exists()
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["hooks"]["SessionStart"][0]["command"] == "x"


def test_merge_json_file_merges_onto_existing(tmp_path: Path) -> None:
    """Existing dest → deep-merge (user keys preserved)."""
    dest = tmp_path / "settings.json"
    dest.write_text(
        json.dumps({"hooks": {"Stop": [{"c": "user"}]}, "theme": "dark"}), encoding="utf-8"
    )
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"hooks": {"SessionStart": [{"c": "jarvis"}]}}), encoding="utf-8")

    installer._merge_json_file(src, dest, template=False, repo_root=tmp_path, claude_home=tmp_path)
    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert merged["hooks"]["Stop"][0]["c"] == "user"  # survived
    assert merged["hooks"]["SessionStart"][0]["c"] == "jarvis"  # added
    assert merged["theme"] == "dark"  # survived


def test_merge_json_file_corrupt_existing_falls_back_to_source(tmp_path: Path) -> None:
    """Unparseable existing dest → treat as absent and write source fresh."""
    dest = tmp_path / "settings.json"
    dest.write_text("{not valid json", encoding="utf-8")
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"hooks": {"SessionStart": [{"c": "new"}]}}), encoding="utf-8")

    installer._merge_json_file(src, dest, template=False, repo_root=tmp_path, claude_home=tmp_path)
    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert merged["hooks"]["SessionStart"][0]["c"] == "new"


def test_merge_json_preserves_user_mcp_on_reapply(manifest: Path, fake_repo: Path) -> None:
    """Full flow: apply once, user adds a custom mcpServer, re-apply,
    jarvis-owned server updates while custom server survives.
    Idempotency for jarvis keys: re-apply doesn't duplicate."""
    # Manifest mcp_config uses copy_file by default in fixture; switch to merge.
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for g in data["groups"]:
        if g["id"] == "mcp_config":
            g["files"][0]["merge"] = True
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    m = installer.load_manifest(manifest)
    plan1 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan1, m, run_env=None)

    # User manually adds a custom server.
    mcp_path = plan1.target_root / ".mcp.json"
    existing = json.loads(mcp_path.read_text(encoding="utf-8"))
    existing["mcpServers"]["user-obsidian"] = {"command": "npx", "args": ["user-mcp"]}
    mcp_path.write_text(json.dumps(existing), encoding="utf-8")

    # Bump SHA so build_plan considers us outdated.
    (plan1.target_root / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")
    plan2 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan2, m, run_env=None)

    final = json.loads(mcp_path.read_text(encoding="utf-8"))
    # User-added server preserved across re-apply.
    assert final["mcpServers"]["user-obsidian"]["command"] == "npx"
    # Jarvis-owned server present.
    assert "memory" in final["mcpServers"]
    assert "run-memory-server.py" in final["mcpServers"]["memory"]["args"][0]
    # No duplicates — idempotency check.
    assert len(final["mcpServers"]) == 2


def test_merge_action_kind_in_plan(manifest: Path, fake_repo: Path) -> None:
    """Entries flagged `merge: true` produce merge_json actions, not copy_file."""
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for g in data["groups"]:
        if g["id"] == "hooks_settings":
            g["files"][0]["merge"] = True
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)
    kinds = {a.group: a.kind for a in plan.actions if a.group}
    assert kinds.get("hooks_settings") == "merge_json"
    assert kinds.get("mcp_config") == "copy_file"  # not flagged, stays copy


def test_real_userlevel_templates_rewrite_scripts_paths(fake_repo: Path) -> None:
    """The real .claude-userlevel/ templates must get scripts/ -> abs rewritten.

    Post-M4 (#339): settings.json no longer has a `config/SOUL.md`
    reference — SOUL is read from `{{CLAUDE_USER_HOME}}/SOUL.md`.
    """
    repo_root = Path(__file__).resolve().parents[1]
    settings_src = repo_root / ".claude-userlevel" / "settings.json"
    mcp_src = repo_root / ".claude-userlevel" / ".mcp.json"

    settings_rendered = installer.template_content(
        settings_src, repo_root, Path("/opt/fake/.claude")
    ).decode("utf-8")
    mcp_rendered = installer.template_content(mcp_src, repo_root, Path("/opt/fake/.claude")).decode(
        "utf-8"
    )

    repo_posix = repo_root.as_posix()
    # Every `scripts/` reference in the rendered output is absolute-rooted.
    assert "python scripts/" not in settings_rendered
    assert f"{repo_posix}/scripts/" in settings_rendered
    # mcp: args use relative paths that should rewrite too.
    assert f"{repo_posix}/scripts/run-memory-server.py" in mcp_rendered


def test_userlevel_skills_dir_exists_and_has_whitelisted_skills() -> None:
    """Source-of-truth directory must exist with every whitelisted skill.

    Convention: entries beginning with ``_`` are shared reference material
    (e.g. ``_shared/tdd/`` consumed by /implement and /delegate in TDD-mode,
    #593), not skills. They live under the skills tree so install-time
    orphan-prune keeps them, but they have no ``SKILL.md`` — the directory
    must exist and be non-empty.
    """
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / ".claude-userlevel" / "skills"
    assert src.is_dir(), f"{src} must exist — M2 source of truth"

    m = installer.load_manifest(repo_root / "install-manifest.yaml")
    include = next(
        d["include"] for g in m["groups"] if g["id"] == "skills" for d in g["directories"]
    )
    for name in include:
        entry_dir = src / name
        if name.startswith("_"):
            assert entry_dir.is_dir(), f"whitelisted shared resource missing: {entry_dir}"
            assert any(entry_dir.iterdir()), f"shared resource is empty: {entry_dir}"
            continue
        skill_md = entry_dir / "SKILL.md"
        assert skill_md.exists(), f"whitelisted skill missing: {skill_md}"


def test_every_source_skill_is_whitelisted() -> None:
    """Inverse of the whitelist check: a skill present in the source tree but
    absent from the manifest `include` list would be silently quarantined to
    `.skills-orphaned/` on install and never reach `~/.claude/skills/`.

    This is the #1048 failure: `/status` (#1018) shipped under the source tree
    but was forgotten in the whitelist, so it never installed on any device and
    nothing failed CI. Every directory carrying a `SKILL.md` MUST be whitelisted
    UNLESS it is an explicitly-documented intentional exclusion below.
    """
    # Skills that live in the repo but are deliberately NOT installed at user
    # level — the installer's orphan-prune quarantines them by design. Each entry
    # needs a reason so a future accidental omission can't hide here.
    INTENTIONALLY_NOT_INSTALLED = {
        # Personal project-scoped skill (Petr's D&D Obsidian vault, device-specific
        # absolute paths). Stored in-repo but not a universal user-level skill.
        "dnd-prep",
    }

    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / ".claude-userlevel" / "skills"

    m = installer.load_manifest(repo_root / "install-manifest.yaml")
    include = set(
        next(
            d["include"] for g in m["groups"] if g["id"] == "skills" for d in g["directories"]
        )
    )
    source_skills = {
        child.name for child in src.iterdir() if child.is_dir() and (child / "SKILL.md").exists()
    }
    missing = source_skills - include - INTENTIONALLY_NOT_INSTALLED
    assert not missing, (
        f"skills present in source but missing from install-manifest.yaml include "
        f"whitelist (would be quarantined on install): {sorted(missing)}. "
        f"If intentional, add to INTENTIONALLY_NOT_INSTALLED with a reason."
    )


# ── #350: backup tolerates files that vanish mid-copy ────────────────


def test_backup_tolerates_vanished_file(
    manifest: Path, fake_repo: Path, monkeypatch, capsys
) -> None:
    """Claude Code rotates files in ~/.claude/debug/ while the installer runs.
    shutil.copytree would abend the whole install with shutil.Error; we patch
    _copy_tolerant onto shutil.copy2 so the backup completes and the install
    proceeds, logging the skipped entry to stderr."""
    m = installer.load_manifest(manifest)
    plan1 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan1, m, run_env=None)

    # Create an extra debug entry that will "vanish" during the next backup.
    debug_dir = plan1.target_root / "debug"
    debug_dir.mkdir(exist_ok=True)
    vanishing = debug_dir / "latest"
    vanishing.write_text("ephemeral\n", encoding="utf-8")

    real_copy2 = installer.shutil.copy2

    def flaky_copy2(src, dst, *, follow_symlinks=True):
        if str(src).endswith("latest"):
            raise FileNotFoundError(2, "simulated mid-copy vanish", str(src))
        return real_copy2(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(installer.shutil, "copy2", flaky_copy2)

    # Trigger outdated re-apply → exercises the backup path.
    (plan1.target_root / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")
    plan2 = installer.build_plan(m, fake_repo)
    assert plan2.state == "outdated"

    # Must NOT raise. Backup completes; skipped entry is logged to stderr.
    installer.apply_plan(plan2, m, run_env=None)

    assert plan2.backup_path.exists()
    assert (plan2.backup_path / "SOUL.md").exists()
    assert not (plan2.backup_path / "debug" / "latest").exists()  # the vanishing one
    stderr = capsys.readouterr().err
    assert re.search(r"backup:\s+skipped unreadable entry.*latest", stderr)


def test_backup_tolerates_locked_file(manifest: Path, fake_repo: Path, monkeypatch, capsys) -> None:
    """Windows log rotation can hold a PermissionError on the file being appended to.
    Same tolerance applies (#350 review nit)."""
    m = installer.load_manifest(manifest)
    plan1 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan1, m, run_env=None)

    (plan1.target_root / "debug").mkdir(exist_ok=True)
    (plan1.target_root / "debug" / "locked.log").write_text("x\n", encoding="utf-8")

    real_copy2 = installer.shutil.copy2

    def locked_copy2(src, dst, *, follow_symlinks=True):
        if str(src).endswith("locked.log"):
            raise PermissionError(13, "file is locked by another process", str(src))
        return real_copy2(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(installer.shutil, "copy2", locked_copy2)

    (plan1.target_root / ".jarvis-version").write_text("old-sha\n", encoding="utf-8")
    plan2 = installer.build_plan(m, fake_repo)
    installer.apply_plan(plan2, m, run_env=None)

    assert plan2.backup_path.exists()
    assert not (plan2.backup_path / "debug" / "locked.log").exists()
    err = capsys.readouterr().err
    assert re.search(r"backup:\s+skipped unreadable entry.*locked\.log", err)


# ---------- legacy parent-dir .mcp.json quarantine ----------


def _legacy_mcp_payload(rel_path: str = "jarvis/scripts/run-memory-server.py") -> str:
    return json.dumps(
        {"mcpServers": {"memory": {"command": "python", "args": [rel_path]}}},
        indent=2,
    )


def test_find_legacy_parent_mcp_detects_relative_jarvis_refs(tmp_path: Path) -> None:
    repo = tmp_path / "Github" / "jarvis"
    repo.mkdir(parents=True)
    legacy = tmp_path / "Github" / ".mcp.json"
    legacy.write_text(_legacy_mcp_payload(), encoding="utf-8")

    found = installer.find_legacy_parent_mcp(repo)
    assert legacy.resolve() in [p.resolve() for p in found]


def test_find_legacy_parent_mcp_ignores_absolute_paths(tmp_path: Path) -> None:
    repo = tmp_path / "Github" / "jarvis"
    repo.mkdir(parents=True)
    correct = tmp_path / "Github" / ".mcp.json"
    correct.write_text(
        _legacy_mcp_payload(rel_path=str(repo / "scripts" / "run-memory-server.py")),
        encoding="utf-8",
    )

    assert installer.find_legacy_parent_mcp(repo) == []


def test_find_legacy_parent_mcp_ignores_unrelated_mcp_configs(tmp_path: Path) -> None:
    repo = tmp_path / "Github" / "jarvis"
    repo.mkdir(parents=True)
    unrelated = tmp_path / "Github" / ".mcp.json"
    unrelated.write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["foo-mcp"]}}}),
        encoding="utf-8",
    )

    assert installer.find_legacy_parent_mcp(repo) == []


def test_apply_plan_quarantines_legacy_parent_mcp(manifest: Path, fake_repo: Path) -> None:
    legacy = fake_repo.parent / ".mcp.json"
    legacy.write_text(_legacy_mcp_payload(), encoding="utf-8")

    m = installer.load_manifest(manifest)
    plan = installer.build_plan(m, fake_repo)

    quarantine = [a for a in plan.actions if a.kind == "quarantine_file"]
    assert any(Path(a.source).resolve() == legacy.resolve() for a in quarantine)

    installer.apply_plan(plan, m, run_env=None)

    assert not legacy.exists()
    assert (legacy.parent / ".mcp.json.bak.pre-jarvis-migration").exists()


def test_quarantine_dest_avoids_clobbering_existing_bak(tmp_path: Path) -> None:
    legacy = tmp_path / ".mcp.json"
    legacy.write_text("{}", encoding="utf-8")
    existing_bak = tmp_path / ".mcp.json.bak.pre-jarvis-migration"
    existing_bak.write_text("{}", encoding="utf-8")

    dest = installer._quarantine_dest(legacy)
    assert dest != existing_bak
    assert dest.name.startswith(".mcp.json.bak.pre-jarvis-migration-")


# ---------- user-scope MCP registration ----------


def _user_mcp_manifest(fake_repo: Path, target_root: Path) -> Path:
    """Manifest variant that registers MCPs via `claude mcp add` instead of copying."""
    data = {
        "version": 1,
        "target_root": str(target_root),
        "version_marker": ".jarvis-version",
        "groups": [
            {
                "id": "mcp_config",
                "enabled": True,
                "files": [
                    {
                        "source": ".mcp.json",
                        "install_as": "user_mcp_registrations",
                        "template": True,
                    }
                ],
            },
        ],
        "env_vars": [],
        "health_check": {"enabled": False},
        "backup": {"prefix": ".claude.backup-", "retain": 3},
    }
    path = fake_repo / "install-manifest-user-mcp.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_plan_user_mcp_registrations_emits_action_per_server(
    fake_repo: Path, tmp_path: Path
) -> None:
    target = tmp_path / "claude_home"
    manifest_path = _user_mcp_manifest(fake_repo, target)
    m = installer.load_manifest(manifest_path)
    plan = installer.build_plan(m, fake_repo)

    regs = [a for a in plan.actions if a.kind == "register_mcp_user"]
    assert len(regs) == 1
    payload = json.loads(regs[0].note)
    assert payload["name"] == "memory"
    assert payload["spec"]["command"] == "python"
    # Path templating applied — relative `scripts/...` rewritten to absolute.
    assert payload["spec"]["args"][0].startswith(fake_repo.as_posix() + "/scripts/")


def test_plan_user_mcp_registrations_quarantines_stale_target_file(
    fake_repo: Path, tmp_path: Path
) -> None:
    target = tmp_path / "claude_home"
    target.mkdir()
    stale = target / ".mcp.json"
    stale.write_text("{}", encoding="utf-8")

    manifest_path = _user_mcp_manifest(fake_repo, target)
    m = installer.load_manifest(manifest_path)
    plan = installer.build_plan(m, fake_repo)

    quarantine = [a for a in plan.actions if a.kind == "quarantine_file"]
    assert any(Path(a.source).resolve() == stale.resolve() for a in quarantine)


def test_apply_register_mcp_user_calls_injected_runner(fake_repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "claude_home"
    manifest_path = _user_mcp_manifest(fake_repo, target)
    m = installer.load_manifest(manifest_path)
    plan = installer.build_plan(m, fake_repo)

    calls: list[tuple[str, dict]] = []

    def fake_register(name: str, spec: dict) -> None:
        calls.append((name, spec))

    installer.apply_plan(plan, m, run_env=None, register_mcp=fake_register)

    assert calls and calls[0][0] == "memory"
    assert calls[0][1]["command"] == "python"


def test_register_mcp_user_stdio_command_shape(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._register_mcp_user(
        "memory",
        {"command": "python", "args": ["/abs/run.py"], "env": {"K": "V"}},
    )

    # First call: idempotent remove. Second: add — name BEFORE -e to avoid
    # the variadic -e swallowing the positional (#432).
    assert captured[0][:5] == ["claude", "mcp", "remove", "-s", "user"]
    add = captured[1]
    assert add[:5] == ["claude", "mcp", "add", "-s", "user"]
    # Layout: ... -s user <name> -e K=V -- <command> <args...>
    name_idx = add.index("memory")
    assert "-e" in add and "K=V" in add
    e_idx = add.index("-e")
    assert name_idx < e_idx, "name must come BEFORE -e (variadic eats positional)"
    assert "--" in add
    dd = add.index("--")
    assert e_idx < dd, "-e must come BEFORE -- separator"
    assert add[dd + 1 :] == ["python", "/abs/run.py"]


def test_register_mcp_user_stdio_command_shape_no_env(monkeypatch) -> None:
    """Stdio without env vars — name still goes before `--` boundary."""
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._register_mcp_user("simple", {"command": "uvx", "args": ["foo-mcp"]})

    add = captured[1]
    dd = add.index("--")
    # No -e flag should appear when env is absent.
    assert "-e" not in add
    # Layout: ... -s user simple -- uvx foo-mcp
    assert add[dd - 1] == "simple"
    assert add[dd + 1 :] == ["uvx", "foo-mcp"]


def test_register_mcp_user_http_command_shape(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._register_mcp_user(
        "remote",
        {
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer xxx"},
        },
    )

    add = captured[1]
    assert "--transport" in add and "http" in add
    assert "-H" in add and "Authorization: Bearer xxx" in add
    # Layout: ... --transport http <name> <url> -H "..." (#432)
    # Headers AFTER positionals so variadic -H doesn't eat <name>.
    name_idx = add.index("remote")
    url_idx = add.index("https://example.com/mcp")
    h_idx = add.index("-H")
    assert name_idx < url_idx < h_idx, "positionals must come BEFORE -H (variadic)"


def test_register_mcp_user_http_no_headers(monkeypatch) -> None:
    """HTTP server without auth headers — clean positional layout."""
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._register_mcp_user(
        "open-mcp",
        {"type": "http", "url": "https://example.com/mcp"},
    )

    add = captured[1]
    assert "-H" not in add
    assert add[-2:] == ["open-mcp", "https://example.com/mcp"]


def test_register_mcp_user_raises_on_add_failure(monkeypatch) -> None:
    seq = iter([0, 1])  # remove succeeds, add fails

    def fake_run(cmd, **kwargs):
        class R:
            returncode = next(seq)
            stdout = ""
            stderr = "boom"

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    with pytest.raises(RuntimeError, match="claude mcp add failed"):
        installer._register_mcp_user("x", {"command": "y", "args": []})


def test_register_mcp_user_passes_timeout(monkeypatch) -> None:
    """Both the remove and add calls must carry a timeout so a child that
    inherits the capture pipe and never exits can't hang the installer."""
    timeouts: list[float | None] = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs.get("timeout"))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._register_mcp_user("memory", {"command": "python", "args": ["/abs/run.py"]})

    assert timeouts == [installer._MCP_SUBPROCESS_TIMEOUT, installer._MCP_SUBPROCESS_TIMEOUT]


def test_register_mcp_user_raises_on_add_timeout(monkeypatch) -> None:
    """A hung `claude mcp add` surfaces as RuntimeError, not a silent hang."""

    def fake_run(cmd, **kwargs):
        # remove (no `add` token) succeeds; the add call times out.
        if "add" in cmd:
            raise installer.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    with pytest.raises(RuntimeError, match="claude mcp add timed out"):
        installer._register_mcp_user("x", {"command": "y", "args": []})


def test_register_mcp_user_tolerates_remove_timeout(monkeypatch) -> None:
    """A hung idempotent-remove must not block the add that follows it."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "remove" in cmd:
            raise installer.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    # Should NOT raise — the add still runs after the remove times out.
    installer._register_mcp_user("simple", {"command": "uvx", "args": ["foo-mcp"]})
    assert any("add" in c for c in calls), "add must still run after remove timeout"


def test_set_env_tolerates_setx_timeout(monkeypatch, capsys) -> None:
    """A hung `setx` is reported and skipped, not propagated as a crash."""

    def fake_run(cmd, **kwargs):
        raise installer.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    # Must not raise.
    installer._set_env("FOO", "bar", "windows")
    assert "timed out" in capsys.readouterr().err


def test_resolve_claude_cli_uses_pathext_when_available(monkeypatch, tmp_path) -> None:
    """Regression: bare ``claude`` (no extension) on Windows breaks
    ``CreateProcessW`` when a sibling ``claude.CMD`` also exists; resolver must
    return the PATHEXT-aware match so subprocess can launch it directly.
    """
    fake = tmp_path / "claude.CMD"
    fake.write_text("@echo off\n")
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(fake) if name == "claude" else None
    )
    assert installer._resolve_claude_cli() == str(fake)


def test_resolve_claude_cli_falls_back_to_bare_name(monkeypatch) -> None:
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    assert installer._resolve_claude_cli() == "claude"


# ---------- #4: list-leaf union merge ----------


def test_deep_merge_unions_list_leaf_preserving_user_entries() -> None:
    """A user's multi-element `fallbackModel` array must survive a source scalar
    (regression #4: source-wins collapsed the array to one string)."""
    existing = {"fallbackModel": ["claude-opus-4-8", "claude-sonnet-4-6"]}
    source = {"fallbackModel": "claude-haiku-4-5"}
    merged = installer._deep_merge_jarvis_json(existing, source)
    assert merged["fallbackModel"] == [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ]


def test_deep_merge_unions_nested_permission_lists_with_dedup() -> None:
    """`permissions.allow`/`deny` are user-owned arrays — union, don't replace,
    and don't duplicate entries present on both sides (idempotency)."""
    existing = {"permissions": {"allow": ["Bash(ls)", "Read(*)"], "deny": ["Bash(rm)"]}}
    source = {"permissions": {"allow": ["Read(*)", "Bash(git status)"]}}
    merged = installer._deep_merge_jarvis_json(existing, source)
    assert merged["permissions"]["allow"] == ["Bash(ls)", "Read(*)", "Bash(git status)"]
    # User-only key under the same dict parent is untouched.
    assert merged["permissions"]["deny"] == ["Bash(rm)"]


def test_deep_merge_scalar_leaf_still_source_wins() -> None:
    """Non-list leaves keep source-wins semantics (no behavior change)."""
    merged = installer._deep_merge_jarvis_json({"theme": "dark"}, {"theme": "light"})
    assert merged["theme"] == "light"


def test_deep_merge_list_leaf_when_existing_absent() -> None:
    """Source list with no existing counterpart is taken as-is."""
    merged = installer._deep_merge_jarvis_json({}, {"fallbackModel": ["a", "b"]})
    assert merged["fallbackModel"] == ["a", "b"]


# ---------- #3: prune orphan user-scope MCP servers ----------


def _write_mcp_source_with_gate(repo: Path) -> Path:
    src = repo / "user.mcp.json"
    src.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {"command": "python", "args": ["mem-server"]},
                    "uml": {
                        "command": "python",
                        "args": ["uml-server"],
                        "x-jarvis-requires-env": "UML_GATE_HOME",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return src


def _write_live_user_config(path: Path, servers: dict) -> None:
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_plan_prunes_orphan_user_scope_server(fake_repo: Path, monkeypatch) -> None:
    """A live user-scope server absent from source is planned for prune;
    source servers — including device-gated ones skipped this run — are not."""
    monkeypatch.delenv("UML_GATE_HOME", raising=False)  # uml gated off here
    src = _write_mcp_source_with_gate(fake_repo)
    live = fake_repo / "live.claude.json"
    _write_live_user_config(
        live,
        {
            "memory": {"command": "python", "args": ["mem-server"]},
            "uml": {"command": "python", "args": ["uml-server"]},
            "bambu": {"command": "npx", "args": ["bambu-mcp"]},
        },
    )
    actions = installer._plan_mcp_user_registrations(src, fake_repo, fake_repo / "t", live)
    pruned = [a.dest for a in actions if a.kind == "prune_mcp_user"]
    assert pruned == ["bambu"]  # orphan only
    # The orphan's live spec is stashed in the note for recoverability.
    bambu_action = next(a for a in actions if a.kind == "prune_mcp_user")
    assert json.loads(bambu_action.note)["args"] == ["bambu-mcp"]


def test_plan_no_prune_without_live_config(fake_repo: Path, monkeypatch) -> None:
    """Default (no live config path) plans zero prune actions — keeps the
    per-spec unit path hermetic."""
    monkeypatch.setenv("UML_GATE_HOME", "/opt/uml")
    src = _write_mcp_source_with_gate(fake_repo)
    actions = installer._plan_mcp_user_registrations(src, fake_repo, fake_repo / "t")
    assert not [a for a in actions if a.kind == "prune_mcp_user"]


def test_read_user_mcp_servers_tolerates_missing_and_corrupt(tmp_path: Path) -> None:
    assert installer._read_user_mcp_servers(tmp_path / "nope.json") == {}
    corrupt = tmp_path / "c.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert installer._read_user_mcp_servers(corrupt) == {}


def test_prune_mcp_user_runs_remove_with_timeout(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        assert kwargs.get("timeout") == installer._MCP_SUBPROCESS_TIMEOUT

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._prune_mcp_user("bambu")
    assert calls == [["claude", "mcp", "remove", "-s", "user", "bambu"]]


def test_prune_mcp_user_tolerates_failure(monkeypatch, capsys) -> None:
    """A failed prune warns and returns — cleanup must not abort the install."""

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "no such server"

        return R()

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._prune_mcp_user("bambu")  # must not raise
    assert "failed" in capsys.readouterr().err


def test_prune_mcp_user_tolerates_timeout(monkeypatch, capsys) -> None:
    def fake_run(cmd, **kwargs):
        raise installer.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "_resolve_claude_cli", lambda: "claude")
    installer._prune_mcp_user("bambu")  # must not raise
    assert "timed out" in capsys.readouterr().err


def test_apply_plan_dispatches_prune_mcp_user(tmp_path: Path) -> None:
    """A prune_mcp_user action invokes the injected prune callable with the
    server name."""
    pruned: list[str] = []
    plan = installer.Plan(
        state="outdated",
        actions=[
            installer.Action(
                kind="prune_mcp_user",
                source=str(tmp_path / "live.json"),
                dest="bambu",
                group="mcp_config",
                note="{}",
            )
        ],
        backup_path=None,
        current_sha="sha",
        previous_sha="old",
        target_root=tmp_path / "t",
        repo_root=tmp_path,
    )
    installer.apply_plan(plan, {}, run_env=None, register_mcp=None, prune_mcp=pruned.append)
    assert pruned == ["bambu"]


def test_format_plan_renders_prune_mcp_user(tmp_path: Path) -> None:
    plan = installer.Plan(
        state="outdated",
        actions=[
            installer.Action(
                kind="prune_mcp_user",
                source="live.json",
                dest="bambu",
                group="mcp_config",
                note="{}",
            )
        ],
        backup_path=None,
        current_sha="sha",
        previous_sha="old",
        target_root=tmp_path / "t",
        repo_root=tmp_path,
    )
    out = installer.format_plan(plan)
    assert "mcp_prune" in out
    assert "claude mcp remove -s user bambu" in out


def test_unknown_install_as_raises(fake_repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "claude_home"
    bad = {
        "version": 1,
        "target_root": str(target),
        "version_marker": ".jarvis-version",
        "groups": [
            {
                "id": "mcp_config",
                "enabled": True,
                "files": [{"source": ".mcp.json", "install_as": "bogus", "template": True}],
            }
        ],
    }
    path = fake_repo / "bad-manifest.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    m = installer.load_manifest(path)
    with pytest.raises(ValueError, match="unknown install_as"):
        installer.build_plan(m, fake_repo)


# ── #706: BOM/CRLF .env encoding scan ──────────────────────────────


class TestEnvEncodingScan:
    """Tests for .env BOM/CRLF detection and fix."""

    def test_detect_bom(self, tmp_path: Path) -> None:
        """BOM-prefixed .env is flagged."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfTOKEN=abc123\n")
        assert installer._detect_env_issues(env_file) == "BOM"

    def test_detect_crlf(self, tmp_path: Path) -> None:
        """CRLF line endings in .env are flagged."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"TOKEN=abc123\r\nKEY=val\r\n")
        assert installer._detect_env_issues(env_file) == "CRLF"

    def test_detect_bom_and_crlf(self, tmp_path: Path) -> None:
        """Both BOM and CRLF in same file are flagged."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfTOKEN=abc123\r\n")
        issues = installer._detect_env_issues(env_file)
        assert "BOM" in issues
        assert "CRLF" in issues
        assert "+" in issues

    def test_clean_file_unflagged(self, tmp_path: Path) -> None:
        """UTF-8-no-BOM + LF file returns empty string."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"TOKEN=abc123\nKEY=val\n")
        assert installer._detect_env_issues(env_file) == ""

    def test_scan_finds_bom_and_crlf(self, tmp_path: Path) -> None:
        """_scan_env_encoding flags both BOM and CRLF .env files."""
        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        # BOM file under .claude/
        bom_env = claude_home / "plugins" / "cache"
        bom_env.mkdir(parents=True)
        (bom_env / ".env").write_bytes(b"\xef\xbb\xbfTOKEN=abc\n")
        # CRLF file under .claude/**/
        crlf_env = claude_home / "debug"
        crlf_env.mkdir()
        (crlf_env / "test.env").write_bytes(b"KEY=val\r\n")

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Clean repo-root .env (should not appear in findings).
        (repo_root / ".env").write_bytes(b"CLEAN=ok\n")

        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert len(findings) == 2
        paths = {str(p) for p, _, _ in findings}
        assert str(bom_env / ".env") in paths
        assert str(crlf_env / "test.env") in paths
        # All findings are fixable (is_user_env=True)
        assert all(u for _, _, u in findings)

    def test_repo_root_env_is_warn_only(self, tmp_path: Path) -> None:
        """Repo-root .env with issues is flagged as is_user_env=False."""
        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".env").write_bytes(b"\xef\xbb\xbfTOKEN=abc\n")

        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert len(findings) == 1
        assert not findings[0][2]  # is_user_env=False

    def test_fix_strips_bom(self, tmp_path: Path) -> None:
        """_fix_env_encoding strips UTF-8 BOM."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfTOKEN=abc\n")
        installer._fix_env_encoding(env_file, "BOM")
        assert env_file.read_bytes() == b"TOKEN=abc\n"

    def test_fix_converts_crlf(self, tmp_path: Path) -> None:
        """_fix_env_encoding converts CRLF to LF."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"TOKEN=abc\r\nKEY=val\r\n")
        installer._fix_env_encoding(env_file, "CRLF")
        assert env_file.read_bytes() == b"TOKEN=abc\nKEY=val\n"

    def test_fix_bom_and_crlf(self, tmp_path: Path) -> None:
        """_fix_env_encoding handles both BOM and CRLF."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfTOKEN=abc\r\n")
        installer._fix_env_encoding(env_file, "BOM+CRLF")
        assert env_file.read_bytes() == b"TOKEN=abc\n"

    def test_scan_skips_directories(self, tmp_path: Path) -> None:
        """rglob("*.env") may match dir names; _scan_env_encoding skips them."""
        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        # Create a directory named .env (unusual but possible).
        (claude_home / "some.env").mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert findings == []  # no crash, no false positive

    # ── Review-finding regressions (PR #725 round 1) ───────────────

    def test_scan_missing_claude_home_returns_no_findings(self, tmp_path: Path) -> None:
        """B3: claude_home absent → return []. Python 3.12 ``Path.rglob`` raises
        ``FileNotFoundError`` on a missing base since gh-73435; the pre-apply
        scan on a fresh install must not crash."""
        claude_home = tmp_path / ".claude-does-not-exist-yet"
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Must not raise — fresh-install path.
        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert findings == []

    def test_scan_missing_claude_home_still_checks_repo_env(self, tmp_path: Path) -> None:
        """B3 follow-up: missing claude_home must not suppress repo-root .env warning."""
        claude_home = tmp_path / ".claude-does-not-exist-yet"
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".env").write_bytes(b"\xef\xbb\xbfTOKEN=abc\n")
        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert len(findings) == 1
        path, issues, is_user = findings[0]
        assert path == repo_root / ".env"
        assert "BOM" in issues
        assert is_user is False  # repo-root .env stays warn-only

    @pytest.mark.skipif(
        not hasattr(__import__("os"), "symlink"),
        reason="symlink unsupported on this platform / unprivileged",
    )
    def test_scan_skips_symlink_escape(self, tmp_path: Path) -> None:
        """M2: a symlink under claude_home that resolves outside it is skipped.
        Prevents arbitrary-file read+write via planted symlink."""
        import os as _os

        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secrets.env"
        target.write_bytes(b"\xef\xbb\xbfPROD_SECRET=value\n")
        link = claude_home / "leaked.env"
        try:
            _os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation denied (Windows non-admin)")
        findings = installer._scan_env_encoding(claude_home, repo_root)
        assert findings == [], "symlink escape must be skipped, not surfaced as a fixable finding"
        # Original target untouched (proves _fix_env_encoding never ran on it).
        assert target.read_bytes() == b"\xef\xbb\xbfPROD_SECRET=value\n"

    def test_fix_atomic_writes_via_tempfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B2: a SIGKILL between truncate and write must not destroy the .env.
        Simulated by raising in os.fsync after the tempfile is written but
        before os.replace — original file content must survive untouched."""
        import os as _os

        env_file = tmp_path / ".env"
        original = b"\xef\xbb\xbfTOKEN=critical-prod-secret\n"
        env_file.write_bytes(original)

        real_fsync = _os.fsync

        def boom(fd: int) -> None:
            real_fsync(fd)
            raise OSError("simulated disk full mid-write")

        monkeypatch.setattr(_os, "fsync", boom)
        with pytest.raises(OSError, match="simulated disk full"):
            installer._fix_env_encoding(env_file, "BOM")
        assert env_file.read_bytes() == original, (
            "atomic write contract broken — original .env destroyed on interruption"
        )
        # Tempfile cleanup: only the original remains.
        siblings = [p.name for p in tmp_path.iterdir()]
        assert siblings == [".env"], f"tempfile leak: {siblings}"

    def test_fix_noop_when_already_clean(self, tmp_path: Path) -> None:
        """B2 follow-up: clean file → no write at all (preserves mtime/perms)."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"TOKEN=abc\n")
        # Mark mtime far in past to detect any rewrite.
        import os as _os

        _os.utime(env_file, (1_000_000_000, 1_000_000_000))
        installer._fix_env_encoding(env_file, "BOM")  # claims BOM but content is clean
        st = env_file.stat()
        assert st.st_mtime == 1_000_000_000, "clean-file fast path was bypassed"

    @staticmethod
    def _wire_main(monkeypatch, manifest_path, fake_repo) -> None:
        """Point installer.__file__ at fake_repo so main() derives the right
        repo_root via ``Path(__file__).resolve().parents[2]``."""
        fake_installer = fake_repo / "scripts" / "install" / "installer.py"
        fake_installer.parent.mkdir(parents=True, exist_ok=True)
        fake_installer.touch()
        monkeypatch.setattr(installer, "__file__", str(fake_installer))

    def test_main_fix_env_encoding_without_apply_warns(
        self,
        manifest: Path,
        fake_repo: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """m3 minor: --fix-env-encoding without --apply must warn, not silently no-op."""
        self._wire_main(monkeypatch, manifest, fake_repo)
        rc = installer.main(["--manifest", str(manifest), "--fix-env-encoding"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "no effect without --apply" in err

    def test_main_fix_env_encoding_with_state_current(
        self,
        manifest: Path,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """B1: when state=='current', --fix-env-encoding --apply must STILL run
        the fix loop. The state==current short-circuit was unreachable for the
        most common re-run case (broken .env on an already-installed machine)."""
        self._wire_main(monkeypatch, manifest, fake_repo)
        # First apply → state becomes current.
        rc = installer.main(["--manifest", str(manifest), "--apply", "--skip-health-check"])
        assert rc == 0

        m = installer.load_manifest(manifest)
        target = installer._expand(m["target_root"])
        # Now corrupt a .env file under target_root.
        broken = target / "broken.env"
        broken.write_bytes(b"\xef\xbb\xbfTOKEN=abc\r\nKEY=val\r\n")

        # Re-run with state=current + --fix-env-encoding — must repair it.
        rc = installer.main(
            ["--manifest", str(manifest), "--apply", "--fix-env-encoding", "--skip-health-check"]
        )
        assert rc == 0
        assert broken.read_bytes() == b"TOKEN=abc\nKEY=val\n"

    def test_main_fresh_install_scans_post_apply(
        self,
        manifest: Path,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """M3: on fresh install, the pre-apply scan sees an empty target. The
        fix loop must re-scan after apply_plan or it always says 'no fixable
        files found' even when newly-installed .env files have issues."""
        # Plant a BOM-laden .env IN the source tree so apply_plan copies it
        # into the fresh target. We piggy-back on the SOUL.md group by renaming
        # the dest, but the simpler path is to add a tiny standalone group.
        bad_env_src = fake_repo / "config" / "broken.env"
        bad_env_src.write_bytes(b"\xef\xbb\xbfTOKEN=abc\r\n")

        m = installer.load_manifest(manifest)
        m["groups"].append(
            {
                "id": "test_env",
                "enabled": True,
                "files": [
                    {"source": "config/broken.env", "dest": "broken.env", "template": False},
                ],
            }
        )
        manifest.write_text(yaml.safe_dump(m), encoding="utf-8")

        self._wire_main(monkeypatch, manifest, fake_repo)
        rc = installer.main(
            ["--manifest", str(manifest), "--apply", "--fix-env-encoding", "--skip-health-check"]
        )
        assert rc == 0
        target = installer._expand(m["target_root"])
        assert (target / "broken.env").read_bytes() == b"TOKEN=abc\n", (
            "post-apply re-scan must repair newly-installed .env files; "
            "pre-apply scan can't see them"
        )

    def test_main_fix_loop_continues_on_oserror(
        self,
        manifest: Path,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """M1: an OSError on one .env must not exit with an unhandled traceback.
        Other files continue to be processed; the apply itself reports success."""
        self._wire_main(monkeypatch, manifest, fake_repo)
        # First apply → state becomes current.
        rc = installer.main(["--manifest", str(manifest), "--apply", "--skip-health-check"])
        assert rc == 0

        m = installer.load_manifest(manifest)
        target = installer._expand(m["target_root"])
        bad = target / "bad.env"
        good = target / "good.env"
        bad.write_bytes(b"\xef\xbb\xbfTOKEN=x\n")
        good.write_bytes(b"\xef\xbb\xbfTOKEN=y\n")

        real_fix = installer._fix_env_encoding

        def selective_fix(path: Path, issues: str) -> None:
            if path.name == "bad.env":
                raise OSError("simulated permission denied")
            return real_fix(path, issues)

        monkeypatch.setattr(installer, "_fix_env_encoding", selective_fix)
        rc = installer.main(
            ["--manifest", str(manifest), "--apply", "--fix-env-encoding", "--skip-health-check"]
        )
        assert rc == 0, "OSError on one .env must not fail the whole install"
        err = capsys.readouterr().err
        assert re.search(r"could not fix.*bad\.env", err)
        # good.env got fixed despite bad.env failing.
        assert good.read_bytes() == b"TOKEN=y\n"

    def test_platforms_filter_skips_non_matching(self, tmp_path: Path) -> None:
        """m2 minor: env_vars entry with platforms=['windows'] only on windows.
        Asserts the filter is applied, regardless of which platform the test
        runs on (one of the two must be excluded)."""
        manifest = {
            "version": 1,
            "target_root": str(tmp_path / ".claude"),
            "env_vars": [
                {"name": "WIN_ONLY", "value": "1", "platforms": ["windows"]},
                {"name": "POSIX_ONLY", "value": "1", "platforms": ["posix"]},
                {"name": "BOTH", "value": "1"},  # platforms omitted → applies everywhere
            ],
            "groups": [],
        }
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-m",
                "init",
            ],
            cwd=repo,
            check=True,
        )
        plan = installer.build_plan(manifest, repo)
        set_env_names = {a.dest for a in plan.actions if a.kind == "set_env"}
        if installer._platform() == "windows":
            assert "WIN_ONLY" in set_env_names
            assert "POSIX_ONLY" not in set_env_names
        else:
            assert "POSIX_ONLY" in set_env_names
            assert "WIN_ONLY" not in set_env_names
        assert "BOTH" in set_env_names  # platforms omitted → always applies


# ---------- #856: E2E dry-run + real subprocess health check + structured oracles ----------


def test_health_check_real_subprocess_succeeds(fake_repo: Path) -> None:
    """run_health_check with real subprocess call succeeds for a simple command."""
    m = {"health_check": {"enabled": True, "commands": [_sys.executable + " -c exit(0)"]}}
    status, logs = installer.run_health_check(m, fake_repo)
    assert status == "ok"
    assert any("OK" in line for line in logs)


def test_health_check_substitutes_python_token_with_sys_executable(
    fake_repo: Path,
) -> None:
    """Manifest uses 'python' or 'python3' as a portable token; run_health_check
    must resolve it to sys.executable at spawn time so the command works on
    Windows (where python3 is absent) and Linux (where python may be absent)."""
    m = {
        "health_check": {
            "enabled": True,
            "commands": ["python3 -c exit(0)", "python -c exit(0)"],
        }
    }
    status, logs = installer.run_health_check(m, fake_repo)
    assert status == "ok", f"python/python3 token substitution failed: {logs}"


def test_main_health_check_timeout_does_not_rollback(
    manifest: Path,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A health-check TIMEOUT is inconclusive, not a failed apply: main() must
    leave the completed apply in place (no _rollback_failed_apply), tell the
    operator, and exit 4 — distinct from rc 3 (health fail → rollback). In the
    2026-06-12 incident the apply had succeeded; killing the wedged installer
    at that point must not cost the user their install.
    """
    fake_installer = fake_repo / "scripts" / "install" / "installer.py"
    fake_installer.parent.mkdir(parents=True, exist_ok=True)
    fake_installer.touch()
    monkeypatch.setattr(installer, "__file__", str(fake_installer))

    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    m = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    m["health_check"] = {
        "enabled": True,
        "timeout": 2,
        "commands": [f"{_sys.executable} {sleeper}"],
    }
    manifest.write_text(yaml.safe_dump(m), encoding="utf-8")

    # --skip-env: this test exercises the timeout/rollback path, not env setup.
    # Without it _set_env appends `export JARVIS_HOME=...` to the real
    # ~/.bashrc/~/.zshrc on a dev machine running the suite locally.
    rc = installer.main(["--manifest", str(manifest), "--apply", "--skip-env"])
    assert rc == 4

    captured = capsys.readouterr()
    assert "TIMEOUT" in captured.out
    assert "NOT rolled back" in captured.err

    # Fresh-install rollback would have rmtree'd target_root — it must survive,
    # version marker intact.
    target = installer._expand(m["target_root"])
    assert target.exists(), "timeout must NOT roll back a completed apply"
    assert (target / ".jarvis-version").exists()


def test_main_dry_run_plans_does_not_create_files(
    manifest: Path,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: main() without --apply builds + prints plan but creates nothing on disk.

    The installer default mode is dry-run. This test verifies the full main()
    pipeline — from argparse through build_plan to format_plan — without
    executing any writes, and asserts the target directory is left untouched.
    """
    fake_installer = fake_repo / "scripts" / "install" / "installer.py"
    fake_installer.parent.mkdir(parents=True, exist_ok=True)
    fake_installer.touch()
    monkeypatch.setattr(installer, "__file__", str(fake_installer))

    m = installer.load_manifest(manifest)
    target = installer._expand(m["target_root"])
    assert not target.exists(), "precondition: target does not exist yet"

    rc = installer.main(["--manifest", str(manifest)])
    assert rc == 0, "dry-run must return 0"

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "state:" in out
    assert "actions:" in out

    # Plan was printed but NOT applied — target must still be absent.
    assert not target.exists(), "dry-run must NOT create target files"
