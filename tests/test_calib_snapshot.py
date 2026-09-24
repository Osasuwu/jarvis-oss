"""Calibration snapshot branches (#138): built from the full tree of a corpus commit, checked
byte for byte outside the overlay list."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("calib_snapshot", ROOT / "scripts" / "calib_snapshot.py")
cs = importlib.util.module_from_spec(_spec)
sys.modules["calib_snapshot"] = cs
_spec.loader.exec_module(cs)

SKILL = ".agents/skills/review-doc/SKILL.md"
DRIFT = "drift-key: " + "a" * 64 + " (model: claude-opus-5)"


def _git(repo: Path, *args: str, data: bytes | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          input=data).stdout.decode().strip()


def _write(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`old` is a round N commit; `main` is later, with the machinery and the answer key."""
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@example.invalid")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    old = _commit(repo, {
        "docs/a.md": "claim: the file is missing\n",
        "examples/x.md": "old example\n",
        SKILL: "old skill\n",
        cs.STUB: "old calibration, seeded-error answers\n",
    }, "round N")
    main = _commit(repo, {
        "docs/a.md": "claim fixed\n",
        "examples/x.md": None,
        "examples/y.md": "the trace the claim lacked\n",
        "the-file": "now present\n",
        ".github/workflows/doc-review.yml": "on: workflow_dispatch\n",
        "scripts/doc_review.py": "print('review')\n",
        SKILL: "new skill\n",
        ".agents/skills/review-doc/calibration/RULES.md": "rules\n",
        ".agents/skills/review-doc/calibration/draw_click_audit.py": "draw\n",
        cs.STUB: f"# Calibration\n\nResults quote the entry: the file is missing.\n\n{DRIFT}\n",
        ".agents/skills/review-doc/calibration/corpus.md": "answer key: the file is missing\n",
    }, "fix the claim, add the machinery")
    monkeypatch.chdir(repo)
    return repo, old, main


def _tamper(repo: Path, base: str, files: dict[str, str | None], parent: str) -> str:
    """A commit with `base`'s tree edited by `files`, and the given parent."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo / ".git" / "tamper-index")}
    run = lambda *a, data=None: subprocess.run(["git", *a], cwd=repo, env=env, check=True,
                                               capture_output=True, input=data).stdout.decode().strip()
    run("read-tree", base)
    for rel, text in files.items():
        if text is None:
            run("update-index", "--force-remove", "--", rel)
        else:
            blob = run("hash-object", "-w", "--stdin", data=text.encode())
            run("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")
    tree = run("write-tree")
    return run("commit-tree", tree, "-p", parent, data=b"tampered\n")


def test_build_is_the_old_tree_with_only_the_overlay(repo):
    repo, old, main = repo
    status_before = _git(repo, "status", "--porcelain")
    branch, commit = cs.build(old, "main")

    assert branch == f"calib2/{main[:7]}/{old[:7]}"
    assert _git(repo, "rev-parse", branch) == commit
    assert _git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:] == [old]
    show = lambda rel: _git(repo, "show", f"{commit}:{rel}")
    assert show("docs/a.md") == "claim: the file is missing"
    assert show("examples/x.md") == "old example"
    assert show(SKILL) == "new skill"
    for rel in cs.OVERLAY:
        assert show(rel) == _git(repo, "show", f"main:{rel}")
    files = set(_git(repo, "ls-tree", "-r", "--name-only", commit).splitlines())
    assert "examples/y.md" not in files and "the-file" not in files
    assert not files & set(cs.REMOVED)
    stub = show(cs.STUB)
    assert DRIFT in stub and "the file is missing" not in stub
    assert cs.check(branch, old, "main") == []
    assert _git(repo, "status", "--porcelain") == status_before
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_build_is_idempotent_and_never_moves_a_branch(repo):
    repo, old, main = repo
    assert cs.build(old, "main") == cs.build(old, "main")
    branch = cs.branch_name(old, main)
    _git(repo, "branch", "-f", branch, main)
    with pytest.raises(SystemExit, match="different tree"):
        cs.build(old, "main")
    assert _git(repo, "rev-parse", branch) == main


@pytest.mark.parametrize("files, parent, expected", [
    ({"docs/a.md": "claim fixed\n"}, "old", "docs/a.md: differs from"),
    ({"examples/y.md": "trace\n"}, "old", "examples/y.md: added since"),
    ({"examples/x.md": None}, "old", "examples/x.md: removed since"),
    ({SKILL: "old skill\n"}, "old", f"{SKILL}: not the file at main"),
    ({cs.STUB: "old calibration, seeded-error answers\n"}, "old", f"{cs.STUB}: not the stub"),
    ({cs.REMOVED[0]: "answer key\n"}, "old", f"{cs.REMOVED[0]}: present"),
    ({}, "main", "its parent is not"),
])
def test_check_names_every_break_of_the_rule(repo, files, parent, expected):
    repo, old, main = repo
    branch, commit = cs.build(old, "main")
    bad = _tamper(repo, commit, files, {"old": old, "main": main}[parent])
    problems = cs.check(bad, old, "main")
    assert len(problems) == 1 and expected in problems[0], problems


def test_check_cli_exits_1_on_a_main_based_snapshot(repo, capsys):
    # The calibration-1 build: `main` with the doc set swapped in.
    repo, old, main = repo
    branch, commit = cs.build(old, "main")
    overlay_build = _tamper(repo, main, {"docs/a.md": "claim: the file is missing\n",
                                         cs.REMOVED[0]: None}, main)
    assert cs.main(["check", overlay_build, old]) == 1
    out = capsys.readouterr().out
    assert "examples/y.md: added since" in out and "the-file: added since" in out
    assert cs.main(["check", branch, old]) == 0


@pytest.mark.parametrize("text", ["no key here\n", f"{DRIFT}\n{DRIFT}\n"])
def test_stub_needs_exactly_one_drift_key_line(text):
    with pytest.raises(SystemExit, match="drift-key lines"):
        cs.stub_text(text, "0" * 40)


def test_overlay_list_covers_the_real_skill_and_workflow():
    tracked = subprocess.run(["git", "ls-files", "--", ".agents/skills/review-doc"], cwd=ROOT,
                             check=True, capture_output=True, text=True).stdout.split()
    assert set(tracked) == (set(cs.OVERLAY) - {".github/workflows/doc-review.yml",
                                               "scripts/doc_review.py"}) | {cs.STUB, *cs.REMOVED}
    workflow = (ROOT / ".github" / "workflows" / "doc-review.yml").read_text(encoding="utf-8")
    assert "python scripts/doc_review.py classify" in workflow
    assert "scripts/doc_review.py" in cs.OVERLAY
    spec = importlib.util.spec_from_file_location("doc_review_paths", ROOT / "scripts" / "doc_review.py")
    dr = importlib.util.module_from_spec(spec)
    sys.modules["doc_review_paths"] = dr
    spec.loader.exec_module(dr)
    assert dr.WORKFLOW_PATH in cs.OVERLAY and dr.SKILL_PATH in cs.OVERLAY
    assert dr.CALIBRATION_PATH == cs.STUB


def test_real_stub_carries_the_one_drift_key_line_of_main():
    calibration = (ROOT / cs.STUB).read_text(encoding="utf-8")
    stub = cs.stub_text(calibration, "0" * 40)
    drift = [line for line in calibration.splitlines() if line.startswith("drift-key: ")]
    assert stub.endswith(drift[0] + "\n")
    assert [line for line in stub.splitlines() if line.startswith("drift-key:")] == drift
