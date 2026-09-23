"""Contract and behaviour checks for .github/workflows/quote-cron.yml (#105).

The contract checks read the workflow as text. The doc-list checks run `git ls-files` with the
workflow's own pathspecs. The shell step runs under bash with a stand-in `python`, and the
github-script body runs under node against a mocked `github`, `context` and `core`, as in
test_authority_detector.py, so the tests exercise the code that ships.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "quote-cron.yml").read_text(encoding="utf-8")


def _script() -> str:
    match = re.search(r"script: \|\n((?:(?: {12}.*)?\n)+)", WORKFLOW)
    assert match, "github-script body missing"
    return textwrap.dedent(match.group(1))


def _run_block() -> str:
    match = re.search(r"- name: Check quotes in every doc\n        run: \|\n((?:(?: {10}.*)?\n)+)", WORKFLOW)
    assert match, "check step missing"
    return textwrap.dedent(match.group(1))


def _pathspecs() -> list[str]:
    match = re.search(r"DOC_PATHSPECS: \|\n((?: {8}\S.*\n)+)", WORKFLOW)
    assert match, "DOC_PATHSPECS missing"
    return [line.strip() for line in match.group(1).splitlines()]


def _git(cwd: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _uncovered(repo: Path) -> set[str]:
    """Tracked markdown files outside tests/fixtures/ that the workflow's list does not take."""
    every = set(_git(repo, "ls-files", "--", "*.md", ":(exclude)tests/fixtures/**"))
    listed = set(_git(repo, "ls-files", "--", *_pathspecs()))
    return every - listed


def _adr_files_stay_in_the_quote_checks(repo: Path) -> None:
    """docs/adr/ is outside doc-review and the structure gate (#144) but inside the quote checks:
    the workflow's `docs/*.md` pathspec matches nested paths, so every tracked ADR is listed."""
    adrs = set(_git(repo, "ls-files", "--", "docs/adr/*.md"))
    assert adrs, "no tracked ADR under docs/adr/"
    listed = set(_git(repo, "ls-files", "--", *_pathspecs()))
    assert adrs <= listed, sorted(adrs - listed)


# --- contract -------------------------------------------------------------


def test_triggers_are_a_weekly_schedule_and_workflow_dispatch_only():
    head = WORKFLOW.split("\npermissions:")[0]
    on = head.split("\non:\n")[1]
    assert re.findall(r"^  (\w+):", on, re.M) == ["schedule", "workflow_dispatch"]
    [cron] = re.findall(r'- cron: "([^"]+)"', on)
    minute, hour, dom, month, dow = cron.split()
    assert (dom, month) == ("*", "*") and re.fullmatch(r"[0-6]", dow), cron
    assert minute.isdigit() and hour.isdigit(), cron


def test_permissions_are_contents_read_and_issues_write():
    perms = re.search(r"^permissions:\n((?:  .*\n)+)", WORKFLOW, re.M).group(1)
    assert sorted(perms.split()) == sorted(["contents:", "read", "issues:", "write"])


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert len(uses) == 3
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_runs_are_serialized():
    assert re.search(r"^concurrency:\n  group: quote-cron\n  cancel-in-progress: false", WORKFLOW, re.M)


def test_script_does_not_interpolate_expressions_into_the_script_source():
    assert "${{" not in _script()


# --- the doc list ---------------------------------------------------------


def test_every_tracked_doc_is_in_the_list():
    missing = _uncovered(ROOT)
    assert not missing, (
        f"docs outside quote-cron.yml DOC_PATHSPECS: {sorted(missing)}. "
        "Add their directory to the list."
    )


def test_every_pathspec_matches_a_doc_and_fixtures_are_left_out():
    for spec in _pathspecs():
        assert _git(ROOT, "ls-files", "--", spec), f"pathspec matches nothing: {spec}"
    listed = _git(ROOT, "ls-files", "--", *_pathspecs())
    assert not [p for p in listed if p.startswith("tests/")]


def test_the_list_check_catches_a_new_doc_directory(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel in ("README.md", "docs/a.md", "docs/sub/b.md", "tests/fixtures/bad.md", "guides/new.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    assert _uncovered(tmp_path) == {"guides/new.md"}


# --- the shell step -------------------------------------------------------

def _find_bash() -> str | None:
    """bash on PATH; on Windows, Git's bash (PATH often has WSL's first, which sees other paths)."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for candidate in (Path(git).parent.parent / "bin" / "bash.exe", Path(git).parent / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    return None


BASH = _find_bash()


def _run_step(tmp_path: Path, rc: int):
    if BASH is None:
        if os.environ.get("CI"):
            pytest.fail("bash is required in CI to test the check step")
        pytest.skip("bash not installed")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel in ("README.md", "docs/with space.md", "docs/sub/b.md", "tests/fixtures/x.md"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    args_file = tmp_path / "args.txt"
    # A stand-in for the real script: record the arguments, exit with the given code.
    stub = f'python() {{ printf "%s\\n" "$@" > "{args_file.as_posix()}"; return {rc}; }}\n'
    step = tmp_path / "step.sh"
    # GitHub runs a `run:` block as `bash -e {0}`.
    step.write_text("set -e\n" + stub + _run_block(), encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env["DOC_PATHSPECS"] = "\n".join(_pathspecs()) + "\n"
    env["RUNNER_TEMP"] = "/runner-temp"
    proc = subprocess.run(
        [BASH, step.as_posix()], cwd=repo, env=env, capture_output=True, text=True
    )
    args = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return proc.returncode, args


@pytest.mark.parametrize("rc,expected", [(0, 0), (1, 0), (2, 2)])
def test_step_passes_not_found_on_and_fails_on_a_crash(tmp_path, rc, expected):
    code, args = _run_step(tmp_path, rc)
    assert code == expected
    assert args[:3] == ["scripts/check_quotes.py", "--json", "/runner-temp/quotes.json"]
    assert sorted(args[3:]) == ["README.md", "docs/sub/b.md", "docs/with space.md"]


# --- the rolling issue ----------------------------------------------------

NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { created: [], comments: [], updates: [], summary: "", failed: null };
const listForRepo = async () => {};
const github = {
  paginate: async (fn, args) => {
    if (fn !== listForRepo) throw new Error("unexpected paginate target");
    if (args.state !== "open" || args.creator !== "github-actions[bot]") {
      throw new Error("unexpected issue filter");
    }
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
      update: async (args) => {
        out.updates.push(args);
      },
    },
  },
};
const context = {
  repo: { owner: "o", repo: "r" },
  serverUrl: "https://github.com",
  runId: 42,
};
const summary = {
  addRaw(t) { out.summary += t; return summary; },
  write: async () => summary,
};
const core = { info: () => {}, setFailed: (m) => { out.failed = m; }, summary };
(async () => {
  await (async function (github, context, core, require) {
    __SCRIPT__
  })(github, context, core, require);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

TITLE = "Weekly quote check: NOT FOUND quotes"


def _finding(verdict="NOT FOUND", unfetched=(), doc="docs/a.md", quote="exit code two blocks"):
    return {
        "doc": doc, "line": 7, "verdict": verdict, "quote": quote,
        "tried": ["https://a.example", *unfetched], "unfetched": list(unfetched),
    }


def _report(*findings, unfetchable=()):
    return {
        "docs": 31, "quotes": 270,
        "not_found": sum(f["verdict"] == "NOT FOUND" for f in findings),
        "unfetchable_sources": list(unfetchable), "findings": list(findings),
    }


def _node():
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to test the rolling-issue script")
        pytest.skip("node not installed")
    return NODE


def _run(tmp_path, report, open_issues=(), check=True):
    node = _node()
    path = tmp_path / "quotes.json"
    if report is not None:
        path.write_text(json.dumps(report), encoding="utf-8")
    proc = subprocess.run(
        [node, "-e", HARNESS.replace("__SCRIPT__", _script())],
        input=json.dumps({"open": list(open_issues)}),
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "REPORT": str(path)},
    )
    if not check:
        return proc
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


OPEN = [
    {"number": 3, "title": "unrelated"},
    {"number": 31, "title": TITLE, "pull_request": {}},  # a PR with the title
    {"number": 30, "title": TITLE},
]


def test_not_found_opens_the_issue_and_fails_the_job(tmp_path):
    out = _run(tmp_path, _report(_finding()))
    assert [c["title"] for c in out["created"]] == [TITLE]
    [comment] = out["comments"]
    assert comment["issue_number"] == 900
    assert "`docs/a.md:7`" in comment["body"] and "exit code two blocks" in comment["body"]
    assert "https://github.com/o/r/actions/runs/42" in comment["body"]
    assert out["updates"] == []
    assert out["failed"] and "1 quote(s) NOT FOUND" in out["failed"]
    assert "opened #900" in out["summary"]


def test_not_found_appends_to_the_open_issue(tmp_path):
    out = _run(tmp_path, _report(_finding()), OPEN)
    assert out["created"] == []
    [comment] = out["comments"]
    assert comment["issue_number"] == 30
    assert out["updates"] == []
    assert out["failed"]
    assert "appended to #30" in out["summary"]


def test_clean_run_closes_the_open_issue_with_a_comment(tmp_path):
    out = _run(tmp_path, _report(_finding("found elsewhere")), OPEN)
    assert out["created"] == []
    [comment] = out["comments"]
    assert comment["issue_number"] == 30 and "Clean run" in comment["body"]
    assert out["updates"] == [
        {"owner": "o", "repo": "r", "issue_number": 30, "state": "closed", "state_reason": "completed"}
    ]
    assert out["failed"] is None


def test_clean_run_without_an_open_issue_does_nothing(tmp_path):
    out = _run(tmp_path, _report())
    assert (out["created"], out["comments"], out["updates"], out["failed"]) == ([], [], [], None)


def test_unfetchable_sources_do_not_open_the_issue_and_are_counted(tmp_path):
    report = _report(
        _finding("unfetchable", unfetched=["https://b.example"]),
        unfetchable=["https://b.example", "https://c.example"],
    )
    out = _run(tmp_path, report)
    assert (out["created"], out["comments"], out["failed"]) == ([], [], None)
    assert "2 unfetchable source(s)" in out["summary"]


def test_not_found_with_an_unfetched_candidate_does_not_touch_the_issue(tmp_path):
    report = _report(
        _finding(unfetched=["https://down.example"]), unfetchable=["https://down.example"]
    )
    for open_issues in ((), OPEN):
        out = _run(tmp_path, report, open_issues)
        assert (out["created"], out["comments"], out["updates"]) == ([], [], [])
        assert out["failed"] is None
        assert "1 NOT FOUND with a source that could not be fetched" in out["summary"]
        assert "1 unfetchable source(s)" in out["summary"]
        assert "https://down.example" in out["summary"]  # listed to check by hand


def test_only_confirmed_not_found_goes_in_the_issue(tmp_path):
    report = _report(
        _finding(doc="docs/confirmed.md"),
        _finding(doc="docs/maybe.md", unfetched=["https://down.example"]),
        unfetchable=["https://down.example"],
    )
    out = _run(tmp_path, report)
    [comment] = out["comments"]
    assert "docs/confirmed.md" in comment["body"]
    assert "docs/maybe.md" not in comment["body"]
    assert "docs/maybe.md" in out["summary"]


def test_a_missing_report_fails_without_touching_the_issue(tmp_path):
    proc = _run(tmp_path, None, OPEN, check=False)
    assert proc.returncode != 0
    assert proc.stdout == ""


def test_a_long_list_is_cut_to_fit_a_comment(tmp_path):
    findings = [_finding(doc=f"docs/d{i}.md", quote="word " * 200) for i in range(200)]
    out = _run(tmp_path, _report(*findings))
    [comment] = out["comments"]
    assert len(comment["body"]) < 65536
    assert "more in the run log" in comment["body"]


def test_adr_files_are_inside_the_quote_cron_pathspecs():
    _adr_files_stay_in_the_quote_checks(ROOT)
