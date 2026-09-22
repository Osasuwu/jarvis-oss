"""Contract and behaviour checks for .github/workflows/authority-detector.yml.

The behaviour checks run the embedded github-script body under node against a
mocked `github`, `context` and `core`, as in test_machinery_guard.py.
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
    Path(__file__).parent.parent / ".github" / "workflows" / "authority-detector.yml"
).read_text(encoding="utf-8")


def _script() -> str:
    match = re.search(r"script: \|\n((?:(?: {12}.*)?\n)+)", WORKFLOW)
    assert match, "github-script body missing"
    return textwrap.dedent(match.group(1))


def test_triggers_on_protection_changes_and_label_removal():
    head = WORKFLOW.split("permissions:")[0]
    assert "branch_protection_rule:\n    types: [created, edited, deleted]" in head
    assert "pull_request_target:\n    types: [unlabeled]" in head
    assert not re.search(r"^  pull_request:", head, re.M)
    assert "github.event.label.name == 'waiting-human-review'" in WORKFLOW


def test_never_checks_out_or_runs_pr_content():
    assert "actions/checkout" not in WORKFLOW
    assert not re.search(r"^\s+run:", WORKFLOW, re.M)


def test_permissions_are_issues_write_only():
    perms = re.search(r"^permissions:\n((?:  .*\n)+)", WORKFLOW, re.M).group(1)
    assert perms.split() == ["issues:", "write"]


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_script_does_not_interpolate_event_data_into_the_script_source():
    # `${{ github.event.* }}` inside a script is a code-injection vector
    # (a label name is attacker-chosen text).
    assert "${{" not in _script()


NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { created: [], comments: [] };
const listForRepo = async () => {};
const github = {
  paginate: async (fn) => {
    if (fn !== listForRepo) throw new Error("unexpected paginate target");
    return input.open;
  },
  rest: {
    issues: {
      listForRepo,
      create: async ({ title, body }) => {
        out.created.push({ title, body });
        return { data: { number: 900, title } };
      },
      createComment: async ({ issue_number, body }) => {
        out.comments.push({ issue_number, body });
      },
    },
  },
};
const context = {
  repo: { owner: "o", repo: "r" },
  eventName: input.eventName,
  payload: input.payload,
  serverUrl: "https://github.com",
  runId: 42,
};
const core = { info: () => {}, setFailed: (m) => { throw new Error(m); } };
(async () => {
  await (async function (github, context, core) {
    __SCRIPT__
  })(github, context, core);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _run(event_name, payload, open_issues=()):
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to test the detector script")
        pytest.skip("node not installed")
    src = HARNESS.replace("__SCRIPT__", _script())
    proc = subprocess.run(
        [NODE, "-e", src],
        input=json.dumps({"eventName": event_name, "payload": payload, "open": list(open_issues)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(proc.stdout)


UNLABELED = {
    "action": "unlabeled",
    "sender": {"login": "someone"},
    "label": {"name": "waiting-human-review"},
    "pull_request": {"number": 17},
}
PROTECTION = {
    "action": "edited",
    "sender": {"login": "someone"},
    "rule": {"id": 5, "name": "main"},
}
TITLE = "Authority log: hold releases and protection changes"


def test_first_event_creates_the_rolling_issue_and_records_the_entry():
    out = _run("pull_request_target", UNLABELED)
    assert [c["title"] for c in out["created"]] == [TITLE]
    [comment] = out["comments"]
    assert comment["issue_number"] == 900
    body = comment["body"]
    assert "@someone" in body
    assert "pull_request_target.unlabeled" in body
    assert "PR #17" in body and "waiting-human-review" in body
    assert re.search(r"\*\*time:\*\* \d{4}-\d{2}-\d{2}T", body)


def test_later_events_append_to_the_existing_issue():
    existing = [
        {"number": 3, "title": "unrelated"},
        {"number": 31, "title": TITLE, "pull_request": {}},  # a PR with the title
        {"number": 30, "title": TITLE},
    ]
    out = _run("branch_protection_rule", PROTECTION, existing)
    assert out["created"] == []
    [comment] = out["comments"]
    assert comment["issue_number"] == 30
    assert "branch_protection_rule.edited" in comment["body"]
    assert "branch protection rule `main`" in comment["body"]
