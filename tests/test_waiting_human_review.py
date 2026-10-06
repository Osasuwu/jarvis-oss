"""Behaviour checks for .github/workflows/waiting-human-review.yml (#213).

The hold applies to pull requests that change a document: a path the definition in
.github/hold-paths.json names (docs/ without docs/adr/, incidents/, README.md; ADR-0004
decision 4). The structure gate reads the same file (#241). The checks extract the embedded
github-script body and run it under node against a mocked `github`, `context` and `core`,
as tests/test_machinery_guard.py does, so they test the script that actually ships. The
script reads the definition from its working directory, as the checkout step provides it.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from structure_gate import is_hold_path

ROOT = Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "waiting-human-review.yml").read_text(
    encoding="utf-8"
)
NODE = shutil.which("node")
LABEL = "waiting-human-review"


def _script() -> str:
    match = re.search(r"script: \|\n((?:(?: {12}.*)?\n)+)", WORKFLOW)
    assert match, "github-script body missing"
    return textwrap.dedent(match.group(1))


HARNESS = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { added: [], failed: null, info: [], compared: [] };
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
      get: async () => ({
        data: { labels, requested_reviewers: input.reviewers, requested_teams: [] },
      }),
    },
    repos: {
      compareCommitsWithBasehead: async ({ basehead }) => {
        out.compared.push(basehead);
        if (input.pushed === null) throw new Error("Not Found");
        return { data: { files: input.pushed } };
      },
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
  payload: {
    action: input.action,
    pull_request: { number: 1 },
    before: "b".repeat(40),
    after: "a".repeat(40),
  },
};
const core = {
  setFailed: (m) => { out.failed = m; },
  info: (m) => { out.info.push(m); },
  warning: (m) => { out.info.push(m); },
};
process.env.RUN_ATTEMPT = input.attempt;
(async () => {
  await (async function (github, context, core) {
    __SCRIPT__
  })(github, context, core);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _run(action, files, *, pushed=(), labels=(), reviewers=(), attempt="1", cwd=ROOT):
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to test the hold script")
        pytest.skip("node not installed")
    src = HARNESS.replace("__SCRIPT__", _script())
    payload = {
        "action": action,
        "files": [f if isinstance(f, dict) else {"filename": f} for f in files],
        "pushed": None if pushed is None else [{"filename": p} for p in pushed],
        "labels": list(labels),
        "reviewers": [{"login": r} for r in reviewers],
        "attempt": attempt,
    }
    proc = subprocess.run(
        [NODE, "-e", src],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=cwd,
    )
    return json.loads(proc.stdout)


def _definition_in(directory: Path, definition: dict) -> None:
    target = directory / ".github" / "hold-paths.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(definition), encoding="utf-8")


# --- which pull requests are held ------------------------------------------


@pytest.mark.parametrize(
    "path, held",
    [
        ("docs/guide.md", True),
        ("docs/sub/guide.md", True),
        ("docs/diagram.png", True),
        ("docs/adr/0002-plumbing-only-doc-changes-get-a-full-review.md", False),
        ("README.md", True),
        ("incidents/incidents.csv", True),
        ("incidents/notes/a.md", True),
        ("docs/classes/alpha.md", True),
        ("docs/vocabularies.json", True),
        ("docs/adr-notes/a.md", True),
        ("scripts/README.md", False),
        ("scripts/check_quotes.py", False),
        (".agents/skills/write-doc/SKILL.md", False),
        (".github/workflows/waiting-human-review.yml", False),
        ("tests/test_check_quotes.py", False),
    ],
)
def test_a_fresh_pull_request_is_held_only_when_it_changes_a_document(path, held):
    out = _run("opened", [path, "tests/test_check_quotes.py"])
    if held:
        assert out["added"] == [LABEL]
        assert out["failed"].startswith("Human review is owed")
        assert f"  {path}" in out["failed"]
    else:
        assert out["added"] == []
        assert out["failed"] is None


@pytest.mark.parametrize("path", ["README.md", "incidents/incidents.csv"])
def test_a_pull_request_that_changes_only_that_path_is_held(path):
    out = _run("opened", [path])
    assert out["added"] == [LABEL]
    assert f"  {path}" in out["failed"]


# --- one path definition, read by the hold and by the structure gate ---------

CORPUS = [
    "README.md",
    "scripts/README.md",
    "docs/guide.md",
    "docs/classes/alpha.md",
    "docs/adr/0004-x.md",
    "docs/adr/sub/y.md",
    "docs/adr-notes/z.md",
    "incidents/incidents.csv",
    "incidents/sub/n.md",
    "scripts/s.py",
    "tests/test_x.py",
    ".github/hold-paths.json",
    "notes/a.md",
    "private/keep.md",
    "private/open/a.md",
    "Makefile",
]
CHANGED_DEFINITION = {
    "prefixes": ["notes/", "private/"],
    "excluded_prefixes": ["private/open/"],
    "files": ["Makefile", "docs/adr/0004-x.md"],
}


def _held_by_the_script(cwd) -> set[str]:
    out = _run("opened", CORPUS, cwd=cwd)
    if out["failed"] is None:
        return set()
    return {
        line.strip()
        for line in out["failed"].split("Documents changed:\n", 1)[1].splitlines()
    }


def _held_by_the_gate(definition) -> set[str]:
    return {p for p in CORPUS if is_hold_path(p, definition)}


def test_the_script_and_the_gate_hold_the_same_paths_under_the_shipped_definition():
    definition = json.loads((ROOT / ".github" / "hold-paths.json").read_text(encoding="utf-8"))
    held = _held_by_the_script(ROOT)
    assert held == _held_by_the_gate(definition)
    assert {"README.md", "docs/guide.md", "incidents/incidents.csv"} <= held
    assert not held & {"docs/adr/0004-x.md", "scripts/README.md", "scripts/s.py"}


def test_the_script_and_the_gate_follow_a_changed_definition_together(tmp_path):
    _definition_in(tmp_path, CHANGED_DEFINITION)
    held = _held_by_the_script(tmp_path)
    assert held == _held_by_the_gate(CHANGED_DEFINITION)
    assert held == {"notes/a.md", "private/keep.md", "Makefile", "docs/adr/0004-x.md"}


def test_a_definition_the_script_cannot_read_fails_the_check(tmp_path):
    out = _run("opened", ["docs/a.md"], cwd=tmp_path)
    assert out["added"] == []
    assert ".github/hold-paths.json" in out["failed"]


def test_a_definition_that_is_not_valid_fails_the_check(tmp_path):
    _definition_in(tmp_path, {"prefixes": ["docs/"]})
    out = _run("opened", ["docs/a.md"], cwd=tmp_path)
    assert out["added"] == []
    assert ".github/hold-paths.json" in out["failed"]


def test_ready_for_review_applies_the_hold_like_an_open():
    out = _run("ready_for_review", ["docs/a.md"])
    assert out["added"] == [LABEL]
    assert out["failed"]


def test_moving_a_document_out_of_docs_is_held():
    moved = [{"filename": "notes/a.md", "previous_filename": "docs/a.md"}]
    out = _run("opened", moved)
    assert out["added"] == [LABEL]
    assert "  docs/a.md" in out["failed"]


def test_a_requested_reviewer_holds_without_the_label():
    out = _run("opened", ["docs/a.md"], reviewers=["someone"])
    assert out["added"] == []
    assert "review requested" in out["failed"]


# --- after the hold was cleared ---------------------------------------------


def test_a_push_that_changes_a_document_of_the_pr_puts_the_hold_back():
    out = _run("synchronize", ["docs/a.md", "scripts/s.py"], pushed=["docs/a.md"])
    assert out["compared"] == ["b" * 40 + "..." + "a" * 40]
    assert out["added"] == [LABEL]
    assert "  docs/a.md" in out["failed"]


def test_a_push_that_changes_only_support_files_keeps_the_hold_cleared():
    out = _run("synchronize", ["docs/a.md", "scripts/s.py"], pushed=["scripts/s.py"])
    assert out["added"] == []
    assert out["failed"] is None


def test_a_document_merged_in_from_the_base_branch_does_not_hold():
    # Updating the branch from main brings docs/other.md into the push, not into the PR.
    out = _run("synchronize", ["docs/a.md"], pushed=["docs/other.md", "scripts/s.py"])
    assert out["added"] == []
    assert out["failed"] is None


def test_a_push_that_adds_the_first_document_holds():
    out = _run("synchronize", ["scripts/s.py", "docs/new.md"], pushed=["docs/new.md"])
    assert out["added"] == [LABEL]
    assert out["failed"]


def test_a_failed_compare_holds():
    out = _run("synchronize", ["docs/a.md"], pushed=None)
    assert out["added"] == [LABEL]
    assert out["failed"]


def test_a_compare_at_its_300_file_cap_holds():
    out = _run("synchronize", ["docs/a.md"], pushed=[f"scripts/s{i}.py" for i in range(300)])
    assert out["added"] == [LABEL]
    assert out["failed"]


def test_a_push_to_a_pr_without_documents_is_not_held():
    out = _run("synchronize", ["scripts/s.py"], pushed=["scripts/s.py"])
    assert out["compared"] == []
    assert out["added"] == []
    assert out["failed"] is None


@pytest.mark.parametrize("action", ["opened", "synchronize", "ready_for_review"])
def test_a_rerun_of_an_old_run_does_not_undo_the_release(action):
    out = _run(action, ["docs/a.md"], pushed=["docs/a.md"], attempt="2")
    assert out["added"] == []
    assert out["failed"] is None


# --- the label itself ------------------------------------------------------


def test_the_label_keeps_the_check_red_on_any_event():
    out = _run("labeled", ["scripts/s.py"], labels=[LABEL])
    assert out["added"] == []
    assert f"({LABEL} label present)" in out["failed"]


def test_clearing_the_label_turns_the_check_green():
    out = _run("unlabeled", ["docs/a.md"], labels=[])
    assert out["added"] == []
    assert out["failed"] is None
