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
push), and removed lines (deleting a leak must stay possible). Not covered either:
``git push --no-verify``; a device without the hook or the list; text sent by ``curl``, the
browser or another tool; typos, split literals, encoded forms.

Install (once per device that pushes to a public repository):

1. List: keep it outside every repository, one literal per line, and export it as
   ``PERSONAL_LITERALS``. It is the same list as the CI secret the scrub reads, so update both.
   PowerShell:
   ``[Environment]::SetEnvironmentVariable("PERSONAL_LITERALS", (Get-Content -Raw "$HOME/.config/personal-literals.txt"), "User")``.
   Linux/macOS: ``export PERSONAL_LITERALS="$(cat "$HOME/.config/personal-literals.txt")"``.
2. Hook: put this line in ``.git/hooks/pre-push`` (``chmod +x`` on Linux/macOS; ``python`` in
   place of ``python3`` where ``python3`` does not start). Worktrees share the file::

       exec python3 "$(git rev-parse --show-toplevel)/scripts/pre_push_leak_gate.py" "$@"

Run by hand: ``python3 scripts/pre_push_leak_gate.py --range <rev-range>`` scans exactly the
commits ``git rev-list <rev-range>`` lists, e.g. ``--range @{u}..HEAD`` for what the next push
of this branch would send. Unlike the hook it does not leave out commits the remote already
has. The push lines are read from stdin only when git calls the hook, which always passes
``<remote> <url>``; with no arguments, or with a terminal on stdin, the gate reads nothing and
exits 2.

What it prints (one line on stderr) and how it exits:

- ``clean - checked N literal(s) across M new commit(s) in R pushed ref(s).``, exit 0. With
  M = 0 the line adds that the remote already has every pushed commit, so only ref and tag
  names were checked.
- ``nothing checked - git sent no ref updates, so there is nothing to push.``, exit 0. Git
  runs the hook with no push lines on an up-to-date push; nothing is sent and nothing was
  read, which is not the same as clean. ``nothing checked - this push only deletes ...``
  likewise.
- ``blocked``, ``could not ...`` or ``Nothing was checked``, exit 1: the push does not happen.
  Exit 2: the gate was called wrongly and read nothing.
- ``clean - ... commit(s) in range <rev-range>.``, exit 0, from ``--range``; a range with no
  commits says ``nothing checked`` and exits 1.

Check (after installing, and after any change to the list or the hook; ``canary-7f3c9a`` is
made up, use your own):

1. On a branch the remote already has in full, ``git push`` must print
   ``pre-push leak gate: nothing checked - git sent no ref updates, so there is nothing to
   push.``; no line at all means the hook is not installed.
2. Add ``canary-7f3c9a`` to the list and export it again. A push with new commits prints
   ``pre-push leak gate: clean - checked N literal(s) across M new commit(s) in R pushed
   ref(s).``
3. On a throwaway branch commit a file containing ``CANARY_7F3C9A``. Before pushing,
   ``--range @{u}..HEAD`` (or ``--range main..HEAD`` on a branch with no upstream) must
   exit 1 with ``added content in <file>``; the push must then be blocked the same way.
4. Remove the canary from the list and export it again.
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


def load_literals(consequence: str):
    """The literal list and its matcher; with no usable list, say nothing was checked."""
    literals = literals_from_env()
    matcher = compile_literal_matcher(literals)
    if matcher is None:
        print(
            f"{PREFIX}: no personal literals configured. Set {LITERALS_ENV_VAR} (one literal "
            f"per line) from the list kept outside the repo. Nothing was checked{consequence}.",
            file=sys.stderr,
        )
    return literals, matcher


def report_findings(scan: Scan, where: str) -> None:
    print(
        f"{PREFIX}: blocked. Personal literal(s) found in {where} (values withheld):",
        file=sys.stderr,
    )
    for finding in scan.findings:
        print(f"  {finding}", file=sys.stderr)


def run(argv: list[str], stdin: str) -> int:
    """Hook mode: ``argv`` is git's ``<remote> <url>``, ``stdin`` its push lines."""
    literals, matcher = load_literals(", so the push is blocked")
    if matcher is None:
        return 1

    remote_arg = argv[1] if len(argv) > 1 else ""
    remote = remote_arg if remote_arg in git("remote").split() else None
    scan = Scan(matcher)
    base_of_root = empty_tree()
    pushed = deleted = unreadable = 0
    for raw in stdin.splitlines():
        parts = raw.split()
        if not parts:
            continue
        if len(parts) != 4:
            unreadable += 1
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if is_zero(local_sha):
            deleted += 1
            continue  # deleting a remote ref sends no content
        pushed += 1
        scan.scan_ref_name(remote_ref)
        scan.scan_ref_name(local_ref)
        target = scan.scan_tag_objects(local_sha)
        if git("cat-file", "-t", target).strip() != "commit":
            continue
        for commit in commits_to_scan(target, remote_sha, remote):
            scan.scan_commit(commit, base_of_root)

    if scan.findings:
        report_findings(scan, "what this push sends")
        print(
            "Rewrite those commits to remove them, then push again. A literal already on the "
            "remote does not block: removing it is always allowed.",
            file=sys.stderr,
        )
    if unreadable:
        # A line git sent but this gate cannot parse is a ref it did not check.
        print(
            f"{PREFIX}: could not read {unreadable} push line(s) (expected <local ref> "
            "<local sha> <remote ref> <remote sha>). Those refs were not checked, so the push "
            "is blocked.",
            file=sys.stderr,
        )
    if scan.findings or unreadable:
        return 1
    if not pushed:
        # Git runs the hook with no lines on an up-to-date push: nothing is sent, so the push
        # may go on, but the line must not read as a scan that found nothing.
        if deleted:
            what = f"this push only deletes {deleted} remote ref(s), which sends no content"
        else:
            what = "git sent no ref updates, so there is nothing to push"
        print(f"{PREFIX}: nothing checked - {what}.", file=sys.stderr)
        return 0
    line = (
        f"{PREFIX}: clean - checked {len(literals)} literal(s) across {len(scan.commits)} new "
        f"commit(s) in {pushed} pushed ref(s)"
    )
    if not scan.commits:
        line += "; the remote already has every pushed commit, so only ref and tag names were"
        line += " checked"
    print(f"{line}.", file=sys.stderr)
    return 0


def run_range(rev_range: str) -> int:
    """Manual mode: scan exactly the commits ``git rev-list <rev-range>`` lists."""
    literals, matcher = load_literals("")
    if matcher is None:
        return 1
    scan = Scan(matcher)
    shown = scan.shown(rev_range)
    commits = git("rev-list", "--end-of-options", rev_range).split()
    if not commits:
        print(f"{PREFIX}: nothing checked - range {shown} holds no commits.", file=sys.stderr)
        return 1
    base_of_root = empty_tree()
    for commit in commits:
        scan.scan_commit(commit, base_of_root)
    if scan.findings:
        report_findings(scan, f"range {shown}")
        return 1
    print(
        f"{PREFIX}: clean - checked {len(literals)} literal(s) across {len(scan.commits)} "
        f"commit(s) in range {shown}.",
        file=sys.stderr,
    )
    return 0


NOT_RUN_BY_GIT = (
    f"{PREFIX}: not run by git, which passes <remote> <url>, so stdin was not read. Nothing was "
    "checked. By hand: --range <rev-range>, e.g. --range @{u}..HEAD."
)
RANGE_NEEDS_A_VALUE = (
    f"{PREFIX}: --range takes exactly one <rev-range>, e.g. --range @{{u}}..HEAD. Nothing was "
    "checked."
)
STDIN_IS_TERMINAL = (
    f"{PREFIX}: stdin is a terminal, not git's push lines, so it was not read. Nothing was "
    "checked. By hand: --range <rev-range>, e.g. --range @{u}..HEAD."
)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    args = argv[1:]
    manual = bool(args) and args[0] == "--range"
    try:
        if manual:
            if len(args) != 2:
                print(RANGE_NEEDS_A_VALUE, file=sys.stderr)
                return 2
            return run_range(args[1])
        if len(args) != 2:
            print(NOT_RUN_BY_GIT, file=sys.stderr)
            return 2
        if sys.stdin is None or sys.stdin.isatty():
            print(STDIN_IS_TERMINAL, file=sys.stderr)
            return 2
        return run(argv, sys.stdin.read())
    except (GitError, OSError, subprocess.SubprocessError) as exc:
        # Fail closed: a gate that errors out has checked nothing.
        consequence = "" if manual else ", so the push is blocked"
        print(
            f"{PREFIX}: could not complete the scan ({exc}). Nothing was checked{consequence}.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
