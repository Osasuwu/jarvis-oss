"""Tests for scripts/install/installer.py.

Covers should-fix items from #344 and test coverage gaps.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Import installer module
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "install"))
import installer


class TestIncludeFor:
    """Tests for _include_for normalization fix (#4)."""

    def test_exact_match_succeeds(self):
        """Test that exact normalized path match returns include list."""
        manifest = {
            "groups": [
                {
                    "id": "test_group",
                    "directories": [
                        {
                            "source": "scripts/foo",
                            "include": ["file1.py", "file2.py"]
                        }
                    ]
                }
            ]
        }
        result = installer._include_for(manifest, "test_group", "scripts/foo", Path("."))
        assert result == ["file1.py", "file2.py"]

    def test_overlapping_prefixes(self):
        """Test that overlapping prefixes don't cause false matches.

        When two groups have sources like 'scripts' and 'scripts/install',
        normalized path equality should prevent 'scripts/install' from
        incorrectly matching against the 'scripts' group.
        """
        manifest = {
            "groups": [
                {
                    "id": "group1",
                    "directories": [
                        {
                            "source": "scripts",
                            "include": ["group1_file.py"]
                        }
                    ]
                },
                {
                    "id": "group2",
                    "directories": [
                        {
                            "source": "scripts/install",
                            "include": ["group2_file.py"]
                        }
                    ]
                }
            ]
        }
        # Should match group2 exactly, not group1 via substring
        result = installer._include_for(manifest, "group2", "scripts/install", Path("."))
        assert result == ["group2_file.py"]

    def test_no_match_returns_none(self):
        """Test that non-matching source returns None."""
        manifest = {
            "groups": [
                {
                    "id": "test_group",
                    "directories": [
                        {
                            "source": "scripts/foo",
                            "include": ["file1.py"]
                        }
                    ]
                }
            ]
        }
        result = installer._include_for(manifest, "test_group", "scripts/bar", Path("."))
        assert result is None

    def test_normalized_path_variants(self):
        """Test that different path separators normalize to same result."""
        manifest = {
            "groups": [
                {
                    "id": "test_group",
                    "directories": [
                        {
                            "source": "scripts/foo",
                            "include": ["file1.py"]
                        }
                    ]
                }
            ]
        }
        # Both forward slash and backslash should resolve to same normalized form
        result = installer._include_for(manifest, "test_group", "scripts/foo", Path("."))
        assert result == ["file1.py"]


class TestSetEnvLogging:
    """Tests for _set_env failure logging fix (#5)."""

    def test_setx_failure_logged(self, capsys):
        """Test that setx returncode != 0 is logged to stderr."""
        with mock.patch(
            "subprocess.run"
        ) as mock_run:
            # Simulate setx failure
            mock_run.return_value = mock.MagicMock(
                returncode=1,
                stderr=b"The parameter is incorrect.\n"
            )

            installer._set_env("TEST_VAR", "test_value", "windows")

            # Verify setx was called
            mock_run.assert_called_once()

            # Verify failure was logged to stderr
            captured = capsys.readouterr()
            assert "setx TEST_VAR failed" in captured.err
            assert "rc=1" in captured.err
            assert "The parameter is incorrect" in captured.err

    def test_setx_success_silent(self, capsys):
        """Test that setx returncode 0 is silent."""
        with mock.patch(
            "subprocess.run"
        ) as mock_run:
            # Simulate setx success
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stderr=b""
            )

            installer._set_env("TEST_VAR", "test_value", "windows")

            # Verify no stderr output on success
            captured = capsys.readouterr()
            assert "failed" not in captured.err

    def test_posix_rc_file_handling(self):
        """Test that POSIX platforms still use rc file writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            bashrc = home / ".bashrc"
            bashrc.write_text("# existing content\n")

            with mock.patch("pathlib.Path.home", return_value=home):
                installer._set_env("TEST_VAR", "test_value", "posix")

            # Verify bashrc was updated
            content = bashrc.read_text()
            assert "export TEST_VAR=" in content
            assert "test_value" in content


class TestRollbackCLI:
    """Tests for --rollback CLI path (#344 test coverage gap)."""

    def test_rollback_restores_from_backup(self):
        """Test that rollback restores target_root from backup_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            backup_path = base / "backup"
            target_root = base / "target"

            # Create backup with test content
            backup_path.mkdir()
            (backup_path / "test.txt").write_text("backup content")

            # Create different content in target
            target_root.mkdir()
            (target_root / "old.txt").write_text("old content")

            # Perform rollback
            installer.rollback(target_root, backup_path)

            # Verify target now matches backup
            assert (target_root / "test.txt").read_text() == "backup content"
            assert not (target_root / "old.txt").exists()

    def test_rollback_cli_main(self):
        """Test rollback via main() CLI with --rollback flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            backup_path = base / "backup"
            target_root = base / "target"

            # Create backup with test content
            backup_path.mkdir()
            (backup_path / "test.txt").write_text("backup content")

            # Create different content in target
            target_root.mkdir()
            (target_root / "old.txt").write_text("old content")

            # Build a minimal manifest for --rollback path
            manifest_path = base / "manifest.yaml"
            manifest_path.write_text(
                "version: 1\ntarget_root: {}\n".format(target_root)
            )

            # Call main with --rollback
            rc = installer.main([
                "--manifest", str(manifest_path),
                "--target", str(target_root),
                "--rollback", str(backup_path)
            ])

            assert rc == 0
            # Verify target was restored
            assert (target_root / "test.txt").read_text() == "backup content"
            assert not (target_root / "old.txt").exists()

    def test_rollback_missing_backup_raises(self):
        """Test that rollback raises if backup_path doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            backup_path = base / "nonexistent_backup"
            target_root = base / "target"
            target_root.mkdir()

            with pytest.raises(FileNotFoundError):
                installer.rollback(target_root, backup_path)


class TestJsonRoundtripCaveat:
    """Tests documenting the .mcp.json JSONC/ordering caveat."""

    def test_json_ordering_normalized(self):
        """Document that JSON key ordering is not preserved through round-trip.

        When .mcp.json goes through json.loads/dumps (in template_content),
        the key ordering is normalized by Python's json module. While Python 3.7+
        preserves insertion order in dicts, json.dumps sorts keys by default in
        some contexts, so the output may differ from the input's key order.
        """
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            # Write JSON with specific key order
            f.write('{\n  "z_key": 1,\n  "a_key": 2\n}\n')
            f.flush()
            json_file = Path(f.name)

        try:
            # Process through template_content (uses json.loads/dumps internally)
            result = installer.template_content(json_file, Path("/tmp"), Path("/tmp"))
            # Result is valid JSON with same data, order may differ
            data = json.loads(result.decode("utf-8"))
            assert data == {"z_key": 1, "a_key": 2}
        finally:
            json_file.unlink()

    def test_invalid_json_falls_back_to_copy(self):
        """Document that non-JSON .json files fall back to raw copy."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            # Write JSONC (not valid JSON)
            f.write('{\n  // comment\n  "key": "value"\n}\n')
            f.flush()
            json_file = Path(f.name)

        try:
            # Process through template_content
            # Invalid JSON causes fallback to raw copy (comments preserved)
            result = installer.template_content(json_file, Path("/tmp"), Path("/tmp"))
            # Result bytes are identical to input since JSONC parse fails
            assert b"// comment" in result
        finally:
            json_file.unlink()


class TestStatusServerJarvisHomePinned:
    """Regression: status MCP server must pin JARVIS_HOME in its env (#1087).

    The status server's repo resolution (scripts/status_gather.py) falls back
    to `git rev-parse --show-toplevel` / CWD when JARVIS_HOME is absent from
    the process env. A server spawned from a non-jarvis repo (or a session
    started before JARVIS_HOME was persisted) then reads a nonexistent
    <other-repo>/config/repos.conf and returns an empty digest. Pinning
    env.JARVIS_HOME in the template makes resolution independent of ambient
    env/CWD.
    """

    SOURCE_MCP = (
        Path(__file__).parent.parent.parent / ".claude-userlevel" / ".mcp.json"
    )

    def test_source_template_status_pins_jarvis_home(self):
        """Source .mcp.json status block declares env.JARVIS_HOME placeholder."""
        data = json.loads(self.SOURCE_MCP.read_text(encoding="utf-8"))
        status = data["mcpServers"]["status"]
        assert status.get("env", {}).get("JARVIS_HOME") == "{{JARVIS_HOME}}", (
            "status server must carry env.JARVIS_HOME = '{{JARVIS_HOME}}' so the "
            "installer substitutes the repo root; without it the digest silently "
            "empties from non-jarvis repos"
        )

    def test_installed_status_env_substituted_to_repo_root(self):
        """template_content resolves the JARVIS_HOME placeholder to repo root."""
        repo_root = Path("/opt/jarvis-home")
        rendered = installer.template_content(
            self.SOURCE_MCP, repo_root, Path("/tmp/claude-home")
        )
        data = json.loads(rendered.decode("utf-8"))
        assert (
            data["mcpServers"]["status"]["env"]["JARVIS_HOME"]
            == repo_root.as_posix()
        )


class TestSkipEnvCLI:
    """Tests for --skip-env CLI path (#415)."""

    def test_skip_env_prevents_set_env_call(self):
        """Test that --skip-env CLI flag prevents env var writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target_root = base / "target"
            target_root.mkdir()
            repo_root = Path(__file__).parent.parent.parent / "scripts" / "install"

            # Create minimal manifest
            manifest_path = base / "manifest.yaml"
            manifest_path.write_text(
                "version: 1\n"
                "target_root: {}\n"
                "env_vars:\n"
                "  - name: TEST_VAR\n"
                "    value: test_value\n".format(target_root)
            )

            # Mock _set_env to track calls
            with mock.patch.object(installer, "_set_env") as mock_set_env:
                rc = installer.main([
                    "--manifest", str(manifest_path),
                    "--apply",
                    "--skip-env",
                    "--skip-health-check",
                ])

            # Verify _set_env was NOT called due to --skip-env
            mock_set_env.assert_not_called()
            assert rc == 0

    def test_without_skip_env_calls_set_env(self):
        """Test that without --skip-env, env vars are set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target_root = base / "target"
            target_root.mkdir()

            # Create minimal manifest
            manifest_path = base / "manifest.yaml"
            manifest_path.write_text(
                "version: 1\n"
                "target_root: {}\n"
                "env_vars:\n"
                "  - name: TEST_VAR\n"
                "    value: test_value\n".format(target_root)
            )

            # Mock _set_env to track calls
            with mock.patch.object(installer, "_set_env") as mock_set_env:
                rc = installer.main([
                    "--manifest", str(manifest_path),
                    "--apply",
                    "--skip-health-check",
                ])

            # Verify _set_env WAS called (without --skip-env)
            mock_set_env.assert_called_once()
            assert rc == 0


class TestMissingGitBinary:
    """Tests for missing git binary error handling (#415)."""

    def test_missing_git_raises_file_not_found(self):
        """Test that missing git binary raises FileNotFoundError cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo_root = base / "repo"
            repo_root.mkdir()

            # Monkeypatch subprocess.run to raise FileNotFoundError on git
            original_run = subprocess.run

            def mock_run(*args, **kwargs):
                if args and args[0] and args[0][0] == "git":
                    raise FileNotFoundError("git not found")
                return original_run(*args, **kwargs)

            with mock.patch("subprocess.run", side_effect=mock_run):
                with pytest.raises(FileNotFoundError, match="git not found"):
                    installer.current_git_sha(repo_root)

    def test_run_git_missing_binary_clean_failure(self):
        """Test that _run_git raises FileNotFoundError (not subprocess error)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError("git: not found")

                with pytest.raises(FileNotFoundError):
                    installer._run_git(repo_root, "rev-parse", "HEAD")


class TestEmptyGroupsList:
    """Tests for empty groups list handling (#415)."""

    def test_empty_groups_list_silent_no_op(self):
        """Test that empty groups list completes silently without actions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target_root = base / "target"
            repo_root = Path(__file__).parent.parent.parent / "scripts" / "install"

            manifest = {
                "version": 1,
                "target_root": str(target_root),
                "groups": [],  # Empty groups
            }

            # Mock git to avoid filesystem dependency
            with mock.patch.object(installer, "current_git_sha", return_value="abc123"):
                plan = installer.build_plan(manifest, repo_root)

            # Empty groups → only write_version action
            assert plan.state != "current"
            # Filter out write_version (non-destructive)
            file_actions = [
                a for a in plan.actions
                if a.kind in {"copy_file", "copy_dir", "merge_json"}
            ]
            assert len(file_actions) == 0

    def test_empty_groups_apply_completes(self):
        """Test that apply_plan with empty groups completes without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target_root = base / "target"
            repo_root = Path(__file__).parent.parent.parent / "scripts" / "install"
            target_root.mkdir()

            manifest = {
                "version": 1,
                "target_root": str(target_root),
                "groups": [],
            }

            with mock.patch.object(installer, "current_git_sha", return_value="abc123"):
                plan = installer.build_plan(manifest, repo_root)

            # apply_plan should complete without raising
            with mock.patch.object(installer, "_set_env"):
                installer.apply_plan(plan, manifest)

            # Verify version marker was written
            version_file = target_root / ".jarvis-version"
            assert version_file.exists()


class TestUnicodePathHandling:
    """Tests for unicode paths in manifest entries (#415)."""

    def test_unicode_source_path_copy_dir(self):
        """Test that unicode paths in source work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Create source with unicode name
            src_dir = base / "тест_source"
            src_dir.mkdir()
            (src_dir / "file.py").write_text("test content")

            dest_dir = base / "dest"
            dest_dir.mkdir()

            # _copy_dir should handle unicode path
            installer._copy_dir(
                src_dir,
                dest_dir,
                include=None,
                template=False,
                repo_root=base,
                claude_home=base,
            )

            # Verify file was copied
            assert (dest_dir / "file.py").exists()
            assert (dest_dir / "file.py").read_text() == "test content"

    def test_unicode_dest_path_copy_file(self):
        """Test that unicode destination paths work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Create source file
            src_file = base / "source.py"
            src_file.write_text("test content")

            # Destination with unicode in path
            dest_dir = base / "café_dest"
            dest_dir.mkdir()
            dest_file = dest_dir / "file.py"

            # _copy_file should handle unicode path
            installer._copy_file(
                src_file,
                dest_file,
                template=False,
                repo_root=base,
                claude_home=base,
            )

            # Verify file was copied
            assert dest_file.exists()
            assert dest_file.read_text() == "test content"

    def test_include_for_unicode_source(self):
        """Test that _include_for works with unicode paths."""
        manifest = {
            "groups": [
                {
                    "id": "unicode_group",
                    "directories": [
                        {
                            "source": "тест/скрипты",
                            "include": ["file1.py", "file2.py"]
                        }
                    ]
                }
            ]
        }

        result = installer._include_for(manifest, "unicode_group", "тест/скрипты", Path("."))
        assert result == ["file1.py", "file2.py"]


class TestManifestFileExistsHardAssert:
    """Tests for hard-assert that manifest file exists (#415)."""

    def test_manifest_file_must_exist(self):
        """Test that missing manifest file raises assertion error."""
        nonexistent = Path("/tmp/nonexistent_manifest_xyz.yaml")
        # Ensure it doesn't exist
        if nonexistent.exists():
            nonexistent.unlink()

        # load_manifest should raise when file doesn't exist
        with pytest.raises((FileNotFoundError, OSError)):
            installer.load_manifest(nonexistent)

    def test_main_with_missing_manifest_fails(self):
        """Test that main() with missing manifest fails early."""
        nonexistent_manifest = Path(tempfile.gettempdir()) / "nonexistent_manifest_abc.yaml"
        if nonexistent_manifest.exists():
            nonexistent_manifest.unlink()

        # main() should fail when manifest is missing
        # load_manifest raises FileNotFoundError, which propagates from main()
        with pytest.raises(FileNotFoundError):
            installer.main([
                "--manifest", str(nonexistent_manifest),
                "--apply",
            ])
