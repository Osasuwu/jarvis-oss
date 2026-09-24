"""Calibration snapshot branches (#138): the full tree of a corpus commit, with only the review
machinery overlaid.

A calibration run reviews a doc as it was at its round N commit `<sha>`. The reviewer is the
current doc-review workflow and review-doc skill, so those files come from an overlay ref (`main`,
or a candidate's branch); every other file is the file at `<sha>`, byte for byte, or absent if it
did not exist there.

    python scripts/calib_snapshot.py build <sha> [--overlay-ref main]   # prints branch and commit
    python scripts/calib_snapshot.py check <branch> <sha> [--overlay-ref main]

`build` writes a commit whose only parent is `<sha>` and points the local branch
`calib2/<overlay short sha>/<sha short>` at it, without touching the working tree or the index. It
never moves an existing branch to a different tree. Pushing is done by hand.

`check` exits 1 and names every path that breaks the rule: a file outside the overlay list that
differs from `<sha>`, was added, or was removed; an overlay file that is not the file at the
overlay ref; a `CALIBRATION.md` that is not the stub `build` writes; an answer-key file that is
present; a commit whose parent is not `<sha>`.

The overlay list is `OVERLAY` below. `CALIBRATION.md` is not copied: the file on `main` quotes
corpus entries, so the snapshot gets a stub that carries only its `drift-key:` line, which the
verdict step reads. `calibration/corpus.md` is the answer key and is absent from every snapshot.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = ".agents/skills/review-doc"
# Copied from the overlay ref, byte for byte: every file a dispatch run of doc-review executes or
# the review-doc skill loads, except the two below. tests/test_calib_snapshot.py pins this list to
# the files under the skill directory and to the script the workflow runs.
OVERLAY = (
    ".github/workflows/doc-review.yml",
    "scripts/doc_review.py",
    f"{SKILL_DIR}/SKILL.md",
    f"{SKILL_DIR}/calibration/RULES.md",
    f"{SKILL_DIR}/calibration/draw_click_audit.py",
)
STUB = f"{SKILL_DIR}/CALIBRATION.md"
REMOVED = (f"{SKILL_DIR}/calibration/corpus.md",)
BRANCH_PREFIX = "calib2"

_DRIFT_LINE_RE = re.compile(r"^drift-key: .*$", re.M)


def _git(*args: str, env: dict | None = None, data: bytes | None = None) -> bytes:
    run = subprocess.run(["git", *args], capture_output=True, env=env, input=data)
    if run.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {run.stderr.decode(errors='replace').strip()}")
    return run.stdout


def _text(*args: str) -> str:
    return _git(*args).decode().strip()


def _commit(rev: str) -> str:
    return _text("rev-parse", "--verify", "-q", f"{rev}^{{commit}}")


def _entry(rev: str, path: str) -> tuple[str, str] | None:
    """(mode, blob id) of `path` at `rev`, or None when absent."""
    out = _git("ls-tree", "-z", rev, "--", path).decode()
    for rec in filter(None, out.split("\0")):
        meta, name = rec.split("\t", 1)
        mode, kind, obj = meta.split()
        if name == path and kind == "blob":
            return mode, obj
    return None


def stub_text(calibration: str, overlay_sha: str) -> str:
    """The snapshot's CALIBRATION.md: the one drift-key line of the overlay ref's file."""
    lines = _DRIFT_LINE_RE.findall(calibration)
    if len(lines) != 1:
        raise SystemExit(f"{STUB} at {overlay_sha[:7]} has {len(lines)} drift-key lines, not 1")
    return (
        "# Review-doc calibration\n\n"
        "Snapshot copy, written by `scripts/calib_snapshot.py`: only the drift-key line of this "
        f"file at `{overlay_sha[:7]}`, which the verdict step reads.\n\n"
        f"{lines[0]}\n"
    )


def _stub_bytes(overlay_sha: str) -> bytes:
    calibration = _git("show", f"{overlay_sha}:{STUB}").decode("utf-8")
    return stub_text(calibration, overlay_sha).encode("utf-8")


def branch_name(sha: str, overlay_sha: str) -> str:
    return f"{BRANCH_PREFIX}/{overlay_sha[:7]}/{sha[:7]}"


def build(sha: str, overlay_ref: str) -> tuple[str, str]:
    """Returns (branch, commit)."""
    sha, overlay_sha = _commit(sha), _commit(overlay_ref)
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git("read-tree", sha, env=env)
        for path in OVERLAY:
            entry = _entry(overlay_sha, path)
            if entry is None:
                raise SystemExit(f"{path} missing at {overlay_ref}")
            _git("update-index", "--add", "--cacheinfo", f"{entry[0]},{entry[1]},{path}", env=env)
        blob = _git("hash-object", "-w", "--stdin", data=_stub_bytes(overlay_sha)).decode().strip()
        _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{STUB}", env=env)
        for path in REMOVED:
            _git("update-index", "--force-remove", "--", path, env=env)
        tree = _git("write-tree", env=env).decode().strip()
    message = (f"calibration snapshot: tree of {sha[:7]}, review machinery from "
               f"{overlay_sha[:7]} (#138)\n")
    branch = branch_name(sha, overlay_sha)
    existing = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                              capture_output=True, text=True).stdout.strip()
    if existing:
        if _text("rev-parse", f"{existing}^{{tree}}") != tree:
            raise SystemExit(f"{branch} exists with a different tree; not moved")
        return branch, existing
    commit = _git("commit-tree", tree, "-p", sha, data=message.encode()).decode().strip()
    _git("branch", branch, commit)
    return branch, commit


def check(branch: str, sha: str, overlay_ref: str) -> list[str]:
    """Every violation of the snapshot rule, one line each; empty when the branch is sound."""
    sha, overlay_sha, head = _commit(sha), _commit(overlay_ref), _commit(branch)
    problems: list[str] = []
    parents = _text("rev-list", "--parents", "-n", "1", head).split()[1:]
    if parents != [sha]:
        problems.append(f"{branch}: its parent is not {sha[:7]}")
    special = set(OVERLAY) | {STUB} | set(REMOVED)
    fields = _git("diff-tree", "-r", "-z", "--no-renames", sha, head).decode().split("\0")
    what = {"A": "added since", "D": "removed since", "M": "differs from", "T": "differs from"}
    for meta, path in zip(fields[0::2], fields[1::2]):
        if meta and path not in special:
            status = meta.split()[-1]
            problems.append(f"{path}: {what.get(status, status)} {sha[:7]}, outside the overlay")
    for path in OVERLAY:
        want, got = _entry(overlay_sha, path), _entry(head, path)
        if want is None:
            problems.append(f"{path}: missing at the overlay ref {overlay_ref}")
        elif got != want:
            problems.append(f"{path}: not the file at {overlay_ref} ({overlay_sha[:7]})")
    got_stub = _entry(head, STUB)
    if got_stub is None or _git("cat-file", "blob", got_stub[1]) != _stub_bytes(overlay_sha):
        problems.append(f"{STUB}: not the stub for {overlay_sha[:7]}")
    for path in REMOVED:
        if _entry(head, path) is not None:
            problems.append(f"{path}: present; the answer key is absent from every snapshot")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("sha")
    b.add_argument("--overlay-ref", default="main")
    c = sub.add_parser("check")
    c.add_argument("branch")
    c.add_argument("sha")
    c.add_argument("--overlay-ref", default="main")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        branch, commit = build(args.sha, args.overlay_ref)
        print(f"{branch} {commit}")
        return 0
    problems = check(args.branch, args.sha, args.overlay_ref)
    for line in problems:
        print(line)
    if not problems:
        print(f"{args.branch}: every file outside the overlay is the file at {args.sha}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
