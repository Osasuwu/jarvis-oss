"""Contract and behaviour checks for .github/workflows/machinery-guard.yml.

The contract checks read the workflow as text. The behaviour checks extract
the embedded github-script body and run it under node against a mocked
`github`, `context` and `core`, so they test the script that actually ships.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = (
    Path(__file__).parent.parent / ".github" / "workflows" / "machinery-guard.yml"
).read_text(encoding="utf-8")


def _script() -> str:
    match = re.search(r"script: \|\n((?:(?: {12}.*)?\n)+)", WORKFLOW)
    assert match, "github-script body missing"
    return textwrap.dedent(match.group(1))


def _machinery() -> list[str]:
    block = re.search(r"const MACHINERY = \[(.*?)\];", _script(), re.S)
    assert block, "MACHINERY list missing"
    return re.findall(r'"([^"]+)"', block.group(1))


def _is_machinery(path: str) -> bool:
    return any(path.startswith(m) for m in _machinery())


# --- contract -------------------------------------------------------------


def test_triggers_on_pull_request_target_with_all_six_types():
    head = WORKFLOW.split("permissions:")[0]
    assert re.search(r"^on:\n  pull_request_target:\n", head, re.M)
    types = re.findall(r"^      - (\w+)$", head, re.M)
    assert types == [
        "opened", "synchronize", "reopened", "ready_for_review", "labeled", "unlabeled",
    ]
    # Exactly one trigger: a `pull_request` trigger would run the PR's copy.
    assert not re.search(r"^  pull_request:", head, re.M)


def test_never_checks_out_or_runs_pr_content():
    assert "actions/checkout" not in WORKFLOW
    assert not re.search(r"^\s+run:", WORKFLOW, re.M)
    # No ref from the PR head reaches any step.
    assert "head.sha" not in WORKFLOW
    assert "head.ref" not in WORKFLOW


def test_permissions_are_minimal():
    perms = re.search(r"^permissions:\n((?:  .*\n)+)", WORKFLOW, re.M).group(1)
    assert sorted(perms.split()) == sorted(
        ["issues:", "write", "pull-requests:", "write"]
    )
    assert "contents: write" not in WORKFLOW


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/machinery-guard.yml",
        ".github/workflows/waiting-human-review.yml",
        ".agents/skills/review-doc/SKILL.md",
        ".agents/skills/review-doc/calibration/case-01.md",
        ".agents/hooks/secret-scanner.py",
        ".claude/settings.json",
        "tests/structure_gate.py",
        "tests/test_structure_gate.py",
        "tests/test_machinery_guard.py",
        "scripts/check_quotes.py",
        "tests/test_check_quotes.py",
        "scripts/scrub_personal_literals.py",
        "tests/test_scrub_personal_literals.py",
    ],
)
def test_machinery_list_covers(path):
    assert _is_machinery(path)


@pytest.mark.parametrize(
    "path",
    ["docs/publishing-discipline.md", "examples/foo.md", "README.md", "LICENSE"],
)
def test_content_paths_are_not_machinery(path):
    assert not _is_machinery(path)


# --- behaviour --------------------------------------------------------------

NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { added: [], failed: null, info: [] };
const labels = input.labels.map((name) => ({ name }));
const listFiles = async () => {};
const github = {
  paginate: async (fn, params) => {
    if (fn !== listFiles) throw new Error("unexpected paginate target");
    return input.files;
  },
  rest: {
    pulls: {
      listFiles,
      get: async () => ({ data: { labels } }),
    },
    issues: {
      addLabels: async ({ labels: l }) => {
        out.added.push(...l);
        labels.push(...l.map((name) => ({ name })));
      },
    },
  },
};
const context = {
  repo: { owner: "o", repo: "r" },
  payload: { action: input.action, pull_request: { number: 1 } },
};
const core = {
  setFailed: (m) => { out.failed = m; },
  info: (m) => { out.info.push(m); },
};
process.env.RUN_ATTEMPT = input.attempt;
(async () => {
  await (async function (github, context, core) {
    __SCRIPT__
  })(github, context, core);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _run(action, files, labels=(), attempt="1"):
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to test the guard script")
        pytest.skip("node not installed")
    src = HARNESS.replace("__SCRIPT__", _script())
    proc = subprocess.run(
        [NODE, "-e", src],
        input=json.dumps(
            {"action": action, "files": files, "labels": list(labels), "attempt": attempt}
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(proc.stdout)


DOC = [{"filename": "docs/some-guide.md"}]
GATE = [{"filename": "docs/some-guide.md"}, {"filename": "tests/structure_gate.py"}]


def test_no_machinery_passes_with_an_explicit_message():
    out = _run("opened", DOC)
    assert out["failed"] is None
    assert out["added"] == []
    assert any("No reviewer machinery touched" in m for m in out["info"])


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
def test_machinery_push_applies_the_label_and_fails(action):
    out = _run(action, GATE)
    assert out["added"] == ["waiting-human-review"]
    assert "tests/structure_gate.py" in out["failed"]


def test_new_push_after_release_puts_the_hold_back():
    # The human removed the label; a new commit arrives.
    out = _run("synchronize", GATE, labels=[])
    assert out["added"] == ["waiting-human-review"]
    assert out["failed"]


def test_label_present_keeps_the_check_red_without_reapplying():
    out = _run("labeled", GATE, labels=["waiting-human-review"])
    assert out["added"] == []
    assert out["failed"]


def test_removing_the_label_turns_the_check_green_without_a_commit():
    out = _run("unlabeled", GATE, labels=[])
    assert out["added"] == []
    assert out["failed"] is None
    assert any("hold released" in m for m in out["info"])


def test_rerun_of_an_old_push_run_does_not_undo_the_release():
    out = _run("synchronize", GATE, labels=[], attempt="2")
    assert out["added"] == []
    assert out["failed"] is None


def test_renaming_a_gate_out_of_its_directory_is_caught():
    moved = [{"filename": "docs/gate.py", "previous_filename": "tests/structure_gate.py"}]
    out = _run("synchronize", moved)
    assert out["added"] == ["waiting-human-review"]
    assert out["failed"]
