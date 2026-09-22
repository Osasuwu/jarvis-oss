"""Pre-push leak gate (#100): block a push that carries a personal literal.

Installed as git's ``pre-push`` hook. It reads the literal list from ``PERSONAL_LITERALS``,
the same source the CI scrub reads (a repository secret there, a per-device environment
variable set from a file outside the repo here), and scans what the push sends and the remote
does not have yet:

- the added lines of every new commit (each commit, so a literal added and then removed
  inside the same push still blocks, since both commits are sent),
- the names of the files those commits touch,
- every new commit message,
- the name and message of a pushed annotated tag, and the pushed ref names.

Any variant of a literal counts (case, separators, path forms; see
``scrub_personal_literals.compile_literal_matcher``). A hit blocks the push and names where it
is, never the value: a file name or ref name that itself matches is withheld too. With no
usable list the push is blocked and the message says nothing was checked.

Not scanned: commit author and committer identity (an operator's own name would block every
push), and removed lines (deleting a leak must stay possible).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrub_personal_literals import (  # noqa: E402
    LITERALS_ENV_VAR,
    compile_literal_matcher,
    literals_from_env,
)

PREFIX = "pre-push leak gate"
WITHHELD = "<name withheld: it matches>"


class GitError(Exception):
    pass


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8", errors="replace")


def is_zero(sha: str) -> bool:
    return set(sha) == {"0"}


def object_exists(spec: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", spec], capture_output=True).returncode == 0


def empty_tree() -> str:
    return (
        subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            input=b"",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def added_lines_by_file(diff: str) -> list[tuple[str, str]]:
    """(path, added line) pairs from a unified diff; header lines are never content."""
    pairs: list[tuple[str, str]] = []
    path = "?"
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_header = True
            path = "?"
            continue
        if in_header:
            if line.startswith("+++ "):
                target = line[4:]
                path = target[2:] if target.startswith("b/") else target
            elif line.startswith("@@"):
                in_header = False
            continue
        if line.startswith("+"):
            pairs.append((path, line[1:]))
    return pairs


class Scan:
    def __init__(self, matcher) -> None:
        self.matcher = matcher
        self.findings: list[str] = []
        self.commits: set[str] = set()

    def hit(self, text: str) -> bool:
        return bool(self.matcher.search(text))

    def shown(self, name: str) -> str:
        return WITHHELD if self.hit(name) else name

    def add(self, where: str, what: str) -> None:
        finding = f"{where}: {what}"
        if finding not in self.findings:
            self.findings.append(finding)

    def scan_ref_name(self, ref: str) -> None:
        if self.hit(ref):
            self.add("pushed ref", "ref name")

    def scan_tag_objects(self, sha: str) -> str:
        """Scan annotated tag objects down to their target; return the target's sha."""
        while git("cat-file", "-t", sha).strip() == "tag":
            body = git("cat-file", "tag", sha)
            header, _, message = body.partition("\n\n")
            target = sha
            for line in header.splitlines():
                key, _, value = line.partition(" ")
                if key == "object":
                    target = value.strip()
                elif key == "tag" and self.hit(value):
                    self.add(f"tag {sha[:7]}", "tag name")
            if self.hit(message):
                self.add(f"tag {sha[:7]}", "tag message")
            if target == sha:
                break
            sha = target
        return sha

    def scan_commit(self, commit: str, base_of_root: str) -> None:
        if commit in self.commits:
            return
        self.commits.add(commit)
        short = commit[:7]
        if self.hit(git("log", "-1", "--format=%B", commit)):
            self.add(f"commit {short}", "commit message")
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        base = parents[0] if parents else base_of_root
        diff_opts = ["--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"]
        for name in git("diff", "--name-only", *diff_opts, base, commit).splitlines():
            if name and self.hit(name):
                self.add(f"commit {short}", "file name")
        diff = git("diff", "-p", "--text", *diff_opts, base, commit)
        for path, line in added_lines_by_file(diff):
            if self.hit(line):
                self.add(f"commit {short}", f"added content in {self.shown(path)}")


def commits_to_scan(tip: str, remote_sha: str, remote: str | None) -> list[str]:
    """New commits only: those reachable from the pushed tip that the remote already has are
    public, so scanning them would block every push after a leak instead of the leak."""
    excludes: list[str] = []
    if not is_zero(remote_sha) and object_exists(f"{remote_sha}^{{commit}}"):
        excludes.append(remote_sha)
    if remote:
        excludes.append(f"--remotes={remote}")
    args = ["rev-list", tip]
    if excludes:
        args += ["--not", *excludes]
    return git(*args).split()


def run(argv: list[str], stdin: str) -> int:
    literals = literals_from_env()
    matcher = compile_literal_matcher(literals)
    if matcher is None:
        print(
            f"{PREFIX}: no personal literals configured. Set {LITERALS_ENV_VAR} (one literal "
            "per line) from the list kept outside the repo. Nothing was checked, so the push "
            "is blocked.",
            file=sys.stderr,
        )
        return 1

    remote_arg = argv[1] if len(argv) > 1 else ""
    remote = remote_arg if remote_arg in git("remote").split() else None
    scan = Scan(matcher)
    base_of_root = empty_tree()
    for raw in stdin.splitlines():
        parts = raw.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if is_zero(local_sha):
            continue  # deleting a remote ref sends no content
        scan.scan_ref_name(remote_ref)
        scan.scan_ref_name(local_ref)
        target = scan.scan_tag_objects(local_sha)
        if git("cat-file", "-t", target).strip() != "commit":
            continue
        for commit in commits_to_scan(target, remote_sha, remote):
            scan.scan_commit(commit, base_of_root)

    if scan.findings:
        print(
            f"{PREFIX}: blocked. Personal literal(s) found in what this push sends "
            "(values withheld):",
            file=sys.stderr,
        )
        for finding in scan.findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "Rewrite those commits to remove them, then push again. A literal already on the "
            "remote does not block: removing it is always allowed.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{PREFIX}: clean - checked {len(literals)} literal(s) across "
        f"{len(scan.commits)} commit(s).",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    try:
        return run(sys.argv, sys.stdin.read())
    except (GitError, OSError, subprocess.SubprocessError) as exc:
        # Fail closed: a gate that errors out has checked nothing.
        print(
            f"{PREFIX}: could not complete the scan ({exc}); the push is blocked.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
