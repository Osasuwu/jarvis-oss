"""Divergence guard for the review-cleanliness gate (#1229).

jarvis and redrobot enforce review cleanliness over the SAME shared code-review
plugin, and until #1229 they did it with *different* logic and no test comparing
them. The asymmetry cost real diagnosis time: identical plugin behaviour
produced opposite CI outcomes, which read as jarvis-specific config drift rather
than the shared plugin bug it actually was.

The resolution (decision `8cfd030b`, mandate `faaf6671`) split the gate in two:

  DETECTION is uniform everywhere — aggregate denials across the WHOLE
  execution log. Selecting a single `result` with `| last` is the #1229
  shadowing bug (redrobot#1371, PR #1362, run 28666054188: the action's own
  summary reported 11 denials while a `last`-selecting guard read 0 and passed
  green). Every repo, every attempt job, same detection.

  BLOCKING POLICY: as of #1242 (decision `bf771ebd`) the canon default is
  BLINDED-ONLY everywhere — denials fail only when the run also posted no
  fresh verdict for the head SHA. This superseded jarvis's strict tripwire
  (mandate `faaf6671`, #1229) after its premise — 0 denials reachable via
  allowlist widening — was falsified in jarvis itself: the observed denial
  classes (sandbox write-boundary redirects on PR #1241, `python3` heredoc /
  `find -exec` exec shapes on PR #1243) are structurally un-allowlistable,
  the same conclusion that earlier earned redrobot its `f96089ee` exception.
  Both repos now share one policy; the per-repo asymmetry is gone.

This guard pins the uniform detection half and pins the blinded-only policy's
FAIL-CLOSED FLOOR (denials + no fresh verdict ⇒ exit 1; is_error ⇒ exit 1) so
the carve-out cannot silently widen to always-pass. It compares three
copies of the step — live `review`, canon `attempt-1`, canon `attempt-2` — since
the canon's retry wrapper needs the gate inside each attempt job (EXEC_FILE is a
runner-local path and cannot cross a job boundary).

Structural note, same as the verdict parity guard: canon and live are NOT
byte-equal (canon is a 3-job retry wrapper with `{{ axis }}` placeholders, live
is a single `review` job), so this compares the load-bearing patterns rather
than the whole file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.repo_baseline import Manifest, Renderer
from scripts.repo_baseline.canon import load_canon_template

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"

STEP_NAME = "Verify review ran cleanly"

# The uniform-DETECTION half. Must appear verbatim in every copy of the step.
DETECTION_INVARIANTS = [
    # is_error over ALL result objects, not just the trailing one
    "is_error=$(jq -r 'any(.[] | select(.type == \"result\"); .is_error == true)'",
    # synthesized display-only count, taken as a second signal (#1210)
    "count_field=$(jq -r '[.. | .permission_denials_count? // empty] | max // 0'",
    # union of every real permission_denials[] array anywhere in the log
    "count_array=$(jq -r '[.. | .permission_denials? // empty] | add // [] | length'",
    # whichever is larger — neither field alone can mask a denial
    "denials=$(( count_field > count_array ? count_field : count_array ))",
    # diagnostic listing enumerates from the same unioned arrays
    "[.. | .permission_denials? // empty] | add // [] | .[]",
]

# The shapes that produced the bug. Must appear in NO copy of the step.
DETECTION_ANTIPATTERNS = [
    # single-result selection — the #1229 shadowing bug
    "| last",
    # count read in isolation off one result, the #1210 always-zero read
    "denials=$(jq -r '(.permission_denials_count // 0)'",
]


def _cleanly_run(steps: list[dict]) -> str:
    step = next(s for s in steps if s.get("name") == STEP_NAME)
    return step["run"]


def _code_only(run: str) -> str:
    """Drop shell comment lines — the antipattern checks target executed code.

    The step deliberately *quotes* the buggy `| last` selector in its comments
    to explain why it is not used; matching those would be a false positive.
    """
    return "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def canon_doc() -> dict:
    template = load_canon_template(".github/workflows/code-review.yml")
    assert template is not None, "canon code-review.yml template must exist"
    manifest = Manifest.from_dict(
        {
            "repo": "Osasuwu/jarvis",
            "profile": "full",
            "required_check_contexts": ["verify-verdict"],
        }
    )
    return yaml.safe_load(Renderer().render(template, manifest))


@pytest.fixture(scope="module")
def live_run() -> str:
    doc = yaml.safe_load(LIVE_WORKFLOW.read_text(encoding="utf-8"))
    return _cleanly_run(doc["jobs"]["review"]["steps"])


@pytest.fixture(scope="module")
def canon_attempt1_run(canon_doc) -> str:
    return _cleanly_run(canon_doc["jobs"]["attempt-1"]["steps"])


@pytest.fixture(scope="module")
def canon_attempt2_run(canon_doc) -> str:
    return _cleanly_run(canon_doc["jobs"]["attempt-2"]["steps"])


@pytest.fixture(scope="module")
def all_runs(live_run, canon_attempt1_run, canon_attempt2_run) -> dict[str, str]:
    return {
        "live review job": live_run,
        "canon attempt-1": canon_attempt1_run,
        "canon attempt-2": canon_attempt2_run,
    }


class TestUniformDetection:
    @pytest.mark.parametrize("pattern", DETECTION_INVARIANTS)
    def test_invariant_present_everywhere(self, all_runs, pattern):
        for where, run in all_runs.items():
            assert pattern in run, (
                f"{where} is missing the detection invariant {pattern!r}. "
                f"Denial DETECTION is uniform by decision 8cfd030b — only the "
                f"blocking policy is allowed to differ per repo."
            )

    @pytest.mark.parametrize("pattern", DETECTION_ANTIPATTERNS)
    def test_antipattern_absent_everywhere(self, all_runs, pattern):
        for where, run in all_runs.items():
            assert pattern not in _code_only(run), (
                f"{where} contains {pattern!r} — the shape that let a trailing "
                f"zeroed result shadow the real aggregate (#1229)."
            )

    def test_canon_attempts_are_byte_identical(self, canon_attempt1_run, canon_attempt2_run):
        # The canon comment on attempt-2 promises this; pin it so a fix applied
        # to one attempt job cannot silently skip the other.
        assert canon_attempt1_run == canon_attempt2_run, (
            "The cleanliness gate must be byte-identical between canon "
            "attempt-1 and attempt-2 — a retry that checks differently is a "
            "hole in the gate."
        )


class TestBlindedOnlyIsCanonDefault:
    """#1242 (decision bf771ebd): blinded-only everywhere, floor pinned."""

    def test_blinded_only_marker_present(self, all_runs):
        for where, run in all_runs.items():
            assert "BLINDED-ONLY" in run, f"{where} lost the blinded-only marker"

    def test_forgiveness_branch_present(self, all_runs):
        # Denials are non-fatal ONLY via the freshness-anchored verdict check:
        # a /code-review comment created at/after the head commit time.
        for where, run in all_runs.items():
            code = _code_only(run)
            assert "created_at >= $head" in code, (
                f"{where} lost the freshness-anchored verdict-presence check — "
                f"without it a denial-bearing run either always fails (the "
                f"#1242 false-fail) or is forgiven on a STALE comment."
            )
            assert 'if [ "${fresh:-0}" -gt 0 ]; then' in code, (
                f"{where} must forgive denials only behind the fresh-verdict "
                f"branch, defaulting to fail when the query yields nothing."
            )

    def test_fail_closed_floor_present(self, all_runs):
        # The ratchet that replaced the strict tripwire: the carve-out must
        # not widen to always-pass. Denials with no fresh verdict exit 1;
        # is_error exits 1.
        for where, run in all_runs.items():
            code = _code_only(run)
            assert "posted no fresh verdict" in run, (
                f"{where} lost the blinded fail path — denials that left no "
                f"fresh verdict mean the reviewer never completed; exit 1."
            )
            assert code.count("exit 1") >= 2, (
                f"{where} must hard-exit 1 on both fail paths (is_error, "
                f"blinded denial) — decision bf771ebd pins this floor."
            )

    def test_no_unconditional_denial_fail(self, all_runs):
        # The strict shape (any denial ⇒ error+exit) must be gone — it is the
        # #1242 false-fail. `denials -gt 0` may appear only as the entry to
        # the blinded-only branch, never followed directly by an ::error.
        for where, run in all_runs.items():
            code = _code_only(run)
            assert "STRICT TRIPWIRE" not in run, (
                f"{where} regressed to the strict tripwire — superseded by "
                f"#1242 (decision bf771ebd)."
            )


class TestCanonPolicyNoteIsDocumented:
    """AC4 (#1229): the per-repo rationale lives in the canon file's comments.

    Read from raw canon source, not the parsed YAML — comments do not survive
    `yaml.safe_load`.
    """

    @pytest.fixture(scope="class")
    def canon_source(self) -> str:
        template = load_canon_template(".github/workflows/code-review.yml")
        assert template is not None
        return template

    @pytest.mark.parametrize(
        "marker",
        [
            "REVIEW-CLEANLINESS GATE",
            "#1229",
            "#1242",
            "strict",
            "blinded-only",
            "f96089ee",
            "bf771ebd",
        ],
    )
    def test_policy_note_documents_the_asymmetry(self, canon_source, marker):
        assert marker in canon_source, (
            f"The canon policy note must document {marker!r} so a future reader "
            f"can tell a declared exception from silent drift (#1229 AC4)."
        )
