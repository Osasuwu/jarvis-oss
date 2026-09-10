"""Guard for the diff-scoped mutation probe tool (#1287 AC9).

Two jobs, mirroring the `check-glossary-index.py` + `test_glossary_index_guard.py`
pairing convention:

- **Logic** — exercise the pure AST helpers (`parse_diff_hunks`, `find_mutation_sites`,
  `apply_mutation`) against synthetic inputs, so each mutation kind the tool claims to
  detect and apply is pinned independently of any real git repo or subprocess.
- **Live** — one end-to-end smoke: write a real temp module + real pytest test to disk,
  run `probe_file` against it with a real subprocess test command, and confirm a mutation
  that actually breaks the tested behavior is reported as killed (not survived). This is
  the runnable check for the tool's actual job (per the `non-trivial-logic-runnable-check`
  rule) — the Logic tests alone would not catch a broken subprocess/file-restore wiring.

This script is NOT registered as a path-filtered, PR-blocking GitHub Actions workflow
(`docs/reference/ci-guard-meta-tests.md`'s `path-filtered-ci-guards-meta-test` convention
applies to that class specifically) — it is a report-only developer tool per the issue's
own text ("no threshold, no gate"), same non-workflow-wired precedent as
`check-glossary-index.py` itself (confirmed via grep: no `.github/workflows/*.yml`
references it either). pytest running this file on every PR is what wires the *logic*
into CI; there is no separate blocking gate to wire.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "diff-mutation-probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("diff_mutation_probe", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    assert SCRIPT_PATH.exists(), f"mutation probe script missing at {SCRIPT_PATH}"
    return _load_module()


class TestParseDiffHunks:
    def test_single_added_line(self, probe):
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -10,0 +11 @@ def f():
            +    return x == 1
            """)
        assert probe.parse_diff_hunks(diff) == {"foo.py": {11}}

    def test_multi_line_hunk_covers_full_range(self, probe):
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -1,0 +5,3 @@
            +a
            +b
            +c
            """)
        assert probe.parse_diff_hunks(diff) == {"foo.py": {5, 6, 7}}

    def test_pure_deletion_hunk_contributes_no_lines(self, probe):
        """`+n,0` means nothing survives at that spot in the new file — nothing to mutate."""
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -10,2 +9,0 @@
            -removed1
            -removed2
            """)
        assert probe.parse_diff_hunks(diff) == {}

    def test_deleted_file_is_ignored(self, probe):
        diff = textwrap.dedent("""\
            diff --git a/gone.py b/gone.py
            --- a/gone.py
            +++ /dev/null
            @@ -1,2 +0,0 @@
            -x = 1
            -y = 2
            """)
        assert probe.parse_diff_hunks(diff) == {}

    def test_multiple_files(self, probe):
        diff = textwrap.dedent("""\
            diff --git a/a.py b/a.py
            --- a/a.py
            +++ b/a.py
            @@ -1,0 +2 @@
            +x = 1
            diff --git a/b.py b/b.py
            --- a/b.py
            +++ b/b.py
            @@ -1,0 +3 @@
            +y = 2
            """)
        assert probe.parse_diff_hunks(diff) == {"a.py": {2}, "b.py": {3}}


class TestFindMutationSites:
    def test_detects_comparison_operator_on_target_line(self, probe):
        source = "def f(x):\n    return x == 1\n"
        sites = probe.find_mutation_sites(source, {2})
        assert any(s.kind == "cmp" and s.lineno == 2 for s in sites)

    def test_ignores_comparison_off_target_lines(self, probe):
        source = "def f(x):\n    return x == 1\n"
        sites = probe.find_mutation_sites(source, {1})
        assert sites == []

    def test_detects_boolean_operator(self, probe):
        source = "def f(a, b):\n    return a and b\n"
        sites = probe.find_mutation_sites(source, {2})
        assert any(s.kind == "bool" for s in sites)

    def test_detects_boolean_constant(self, probe):
        source = "def f():\n    return True\n"
        sites = probe.find_mutation_sites(source, {2})
        assert any(s.kind == "bool_const" for s in sites)

    def test_detects_arithmetic_operator(self, probe):
        source = "def f(n):\n    return n + 1\n"
        sites = probe.find_mutation_sites(source, {2})
        assert any(s.kind == "arith" for s in sites)


class TestApplyMutation:
    def test_flips_comparison_operator(self, probe):
        source = "def f(x):\n    return x == 1\n"
        site = probe.find_mutation_sites(source, {2})[0]
        mutated = probe.apply_mutation(source, site)
        assert "!=" in mutated
        assert "==" not in mutated

    def test_flips_boolean_operator(self, probe):
        source = "def f(a, b):\n    return a and b\n"
        site = next(s for s in probe.find_mutation_sites(source, {2}) if s.kind == "bool")
        mutated = probe.apply_mutation(source, site)
        assert " or " in mutated

    def test_flips_boolean_constant(self, probe):
        source = "def f():\n    return True\n"
        site = probe.find_mutation_sites(source, {2})[0]
        mutated = probe.apply_mutation(source, site)
        assert "False" in mutated

    def test_unknown_site_raises(self, probe):
        source = "def f(x):\n    return x == 1\n"
        bogus = probe.MutationSite(lineno=99, col_offset=0, kind="cmp", desc="flip Eq")
        with pytest.raises(ValueError):
            probe.apply_mutation(source, bogus)


class TestProbeFileLive:
    """Real subprocess + real file mutation — the actual job, not just the AST helpers."""

    def test_mutation_that_breaks_behavior_is_killed(self, probe, tmp_path):
        module_path = tmp_path / "under_test.py"
        module_path.write_text(
            "def is_even(n):\n    return n % 2 == 0\n",
            encoding="utf-8",
        )
        test_path = tmp_path / "test_under_test.py"
        test_path.write_text(
            "from under_test import is_even\n\n"
            "def test_is_even():\n"
            "    assert is_even(4)\n"
            "    assert not is_even(3)\n",
            encoding="utf-8",
        )
        original = module_path.read_text(encoding="utf-8")

        results = probe.probe_file(
            str(module_path),
            {2},
            f'"{sys.executable}" -m pytest "{test_path}" -q',
        )

        assert module_path.read_text(encoding="utf-8") == original, (
            "probe_file must restore the original file after each mutant"
        )
        assert results, "expected at least one mutation site on the `==` comparison line"
        cmp_results = [r for r in results if "Eq" in r.desc]
        assert cmp_results and all(not r.survived for r in cmp_results), (
            "flipping `== 0` to `!= 0` must break is_even() and redden test_is_even — "
            "if this reports survived, the subprocess wiring or file restore is broken"
        )

    def test_broken_test_cmd_raises_instead_of_reporting_full_kill(self, probe, tmp_path):
        """A misconfigured test_cmd must not be silently graded as 100% mutation coverage.

        Without a baseline run, every mutant inherits the broken command's non-zero
        exit and is reported `killed` — the tool would claim full coverage while
        having validated nothing.
        """
        module_path = tmp_path / "under_test_broken.py"
        module_path.write_text(
            "def is_even(n):\n    return n % 2 == 0\n",
            encoding="utf-8",
        )
        original = module_path.read_text(encoding="utf-8")
        broken_cmd = f'"{sys.executable}" -c "import sys; sys.exit(1)"'

        with pytest.raises(RuntimeError, match="baseline"):
            probe.probe_file(str(module_path), {2}, broken_cmd)

        assert module_path.read_text(encoding="utf-8") == original, (
            "probe_file must restore the original file even when the baseline check fails"
        )
