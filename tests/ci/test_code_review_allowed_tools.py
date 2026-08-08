"""Drift guard for the code-review action's `--allowed-tools` allowlist.

The code-review action runs HEADLESS (`anthropics/claude-code-action@v1`): any
tool the plugin's reviewer agents invoke that is NOT in `--allowed-tools` is
DENIED outright — there is no human to approve the prompt. When the allowlist
is a strict subset of what the plugin actually uses, the denied calls turn into
repeated `permission_denials`: the agents burn turns retrying, then flail at the
final comment-post step — posting `test`/`PLACEHOLDER`/`ping` probe comments,
fragmenting the review across comments, or posting nothing. A missing or
unparseable verdict comment fails the merge gate CLOSED (#993), and the PR ends
up admin-merged. (jarvis#1042; incident `incident_pr963_rework_blowup`.)

Concretely: plugin reviewer agent #9 (structural-growth) runs
`git show <sha>:<file> | wc -l`, and agent #3 reads `git blame` / `git log`.
Those four tools (`git show`, `git blame`, `git log`, `wc`) were absent from the
allowlist for months, producing ~9 denials per run.

This is the #326 silent-subset-drift class: nothing compared the workflow
allowlist against the tools the plugin needs, so the gap was invisible. This
guard pins the load-bearing tools in BOTH the live reference workflow and the
repo-baseline canon (the propagation template pushed to every owned repo,
including redrobot) so the fix can't silently regress.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"
CANON_WORKFLOW = REPO_ROOT / "scripts" / "repo_baseline" / "canon" / "code-review.yml"

# The tools whose absence caused the #1042 permission_denials. These are the
# git/structural tools the plugin's reviewer agents invoke; if any is dropped
# from the allowlist the headless action denies it and the post step degrades.
REQUIRED_TOOLS = (
    # Native file-reading tools. The deployed plugin prose (fork
    # claude-plugins-official) steers reviewer + file-discovery agents to
    # Read/Grep/Glob instead of Bash `cat`/`grep`/`find`. jarvis's allowlist
    # never granted them (redrobot's did) — so the agents were told to use them
    # then DENIED, the core face of the allowlist-drift class
    # (`code_review_allowlist_drift_class`). Dropping any re-opens that drift.
    "Read",
    "Grep",
    "Glob",
    "Bash(git show:*)",
    "Bash(git blame:*)",
    "Bash(git log:*)",
    "Bash(wc:*)",
    # Compound-command guard: headless permission matching splits on ; | && and
    # newlines and checks each sub-command, so an un-allowlisted `echo` prefix
    # (`echo "=== …" ; gh pr view …`) denies the whole compound even though
    # `gh pr view` is allowlisted. This was an observed denial on PR #1226.
    "Bash(echo:*)",
    # #971: the plugin composes the verdict body with the Write tool at
    # /tmp/code-review-comment.md and posts via `gh pr comment --body-file`,
    # so no shell string-interpretation touches review prose (backticks,
    # $(...), $VAR would otherwise be evaluated under bash -c). Dropping this
    # grant denies the Write in the headless runner and the post step degrades
    # back to shell-assembled bodies. Granted UNSCOPED (`Write`, not
    # `Write(//tmp/**)`): the `//tmp/**` glob failed to match the plugin's
    # `/tmp/...` path on the Linux runner, denying the verdict Write.
    "Write",
    # #1218: the code-review plugin's `/code-review` command is dispatched
    # through the `Skill` tool in the headless action (plugin commands are
    # Skill invocations, registered as `code-review:code-review`). Without this
    # grant EVERY review run denies the command itself → ~16 denials, no verdict
    # posted, and the gates pass VACUOUSLY (empty exec log → exit 0), so
    # auto-merge ships PRs unreviewed. This is the plugin-dispatch face of the
    # allowlist-drift class (memory `code_review_allowlist_drift_class`).
    "Skill(code-review:code-review)",
)

# Sanity floor — the pre-existing tools that must never disappear either.
BASELINE_TOOLS = (
    "Bash(gh pr comment:*)",
    "Bash(gh pr diff:*)",
    "Bash(gh pr view:*)",
)

_ALLOWED_TOOLS_RE = re.compile(r'--allowed-tools\s+"([^"]*)"')


def _allowed_tools_blocks(path: Path) -> list[str]:
    """Every `--allowed-tools "..."` string in the file (canon has two jobs)."""
    text = path.read_text(encoding="utf-8")
    blocks = _ALLOWED_TOOLS_RE.findall(text)
    assert blocks, f"no --allowed-tools line found in {path}"
    return blocks


@pytest.mark.parametrize("path", [LIVE_WORKFLOW, CANON_WORKFLOW], ids=["live", "canon"])
def test_required_git_tools_present(path: Path) -> None:
    for block in _allowed_tools_blocks(path):
        for tool in REQUIRED_TOOLS:
            assert tool in block, (
                f"{path.name}: allowlist missing {tool!r} — headless action will "
                f"DENY it, causing permission_denials and degraded post step "
                f"(jarvis#1042). Allowlist was: {block}"
            )


@pytest.mark.parametrize("path", [LIVE_WORKFLOW, CANON_WORKFLOW], ids=["live", "canon"])
def test_baseline_tools_present(path: Path) -> None:
    for block in _allowed_tools_blocks(path):
        for tool in BASELINE_TOOLS:
            assert tool in block, f"{path.name}: allowlist dropped baseline tool {tool!r}"


def test_canon_jobs_share_one_allowlist() -> None:
    """Canon's two retry jobs (attempt-1/attempt-2) must carry identical lists —
    a fix applied to one job but not the other still leaks denials on retry."""
    blocks = _allowed_tools_blocks(CANON_WORKFLOW)
    assert len(blocks) == 2, f"expected 2 allowlist blocks in canon, got {len(blocks)}"
    assert blocks[0] == blocks[1], "canon attempt-1 and attempt-2 allowlists diverged"


def test_live_and_canon_allowlists_match() -> None:
    """Live reference and canon template must agree, or propagated repos get a
    different (stale) allowlist than the one we validated here."""
    live = _allowed_tools_blocks(LIVE_WORKFLOW)[0]
    canon = _allowed_tools_blocks(CANON_WORKFLOW)[0]
    assert live == canon, (
        "live workflow and canon allowlists diverged — re-snapshot the canon "
        "after changing the live allowlist so the fix propagates to owned repos"
    )


# ---------------------------------------------------------------------------
# Plugin command-prose ⇄ allowlist pin (jarvis#1225)
# ---------------------------------------------------------------------------
#
# The plugin command file (`Osasuwu/claude-plugins-official`
# …/plugins/code-review/commands/code-review.md) is the PROSE that instructs the
# headless reviewer agents. When that prose tells an agent to run a Bash command
# that is NOT in the action's `--allowed-tools` allowlist, the headless runner
# DENIES it (no human to approve), the denials pile up, and the review gate
# degrades / fail-closes — exactly the claude-plugins-official#10 incident (a
# step-1(d) `gh api` instruction that was unreachable in CI).
#
# claude-plugins-official#10 fixed it with a one-time grep (its AC6). This pins
# that grep permanently — same "guards need a fixture test" convention as
# jarvis#326: every backtick-quoted shell command in the vendored command prose
# must match an `--allowed-tools` glob, UNLESS the surrounding sentence forbids
# it.
#
# Two sources, one live and one vendored:
#   * Allowlist — parsed LIVE from `.github/workflows/code-review.yml` via
#     `_allowed_tools_blocks` (the mechanism this module already uses; the issue
#     says pin against whatever copy/fetch mechanism the module already uses —
#     no new sync channel). The workflow allowlist is what actually governs
#     headless denial.
#   * Command prose — a VENDORED static snapshot of the plugin command file
#     (`tests/ci/fixtures/plugin_code_review_command.md`). ci-meta has no network
#     and jarvis carries no submodule of the plugins repo, so the prose is
#     hand-synced; the snapshot is re-vendored when the plugin command changes
#     (accepted trade-off, jarvis#1225 — the extraction heuristic and snapshot
#     both need maintenance as the prose evolves). The snapshot's own frontmatter
#     allowlist is additionally asserted ⊆ the live workflow allowlist so a stale
#     snapshot that grants MORE than CI can't pass silently.
#
# Extraction heuristic (AC2 — tolerate non-command backtick spans):
#   1. A backtick span is a COMMAND CANDIDATE iff, after splitting on shell
#      separators (`|` `||` `&&` `;`), a sub-span's first whitespace token is a
#      known CLI executable (`gh`, `git`, `python`, …) AND the sub-span has ≥2
#      tokens. This drops file paths (`/tmp/…`), URLs (`https://…`), JSON field
#      names (`headRefOid`), tool names (`WebFetch`), globs (`*.lock`), and bare
#      executables (`python3`, `cat`) — none are command instructions to pin.
#   2. A candidate is a VIOLATION iff it matches NO allowlist prefix (exact or
#      token-boundary prefix) AND its containing sentence carries no negation /
#      prohibition cue (not / never / forbidden / unreachable / deliberately …).
#      The negation carve-out is what lets the prose safely NAME forbidden
#      commands (`gh api`, `curl`) inside "do NOT run …" instructions without
#      tripping the guard. Sentence segmentation is on `. ; ! ?` / newline /
#      em-dash, with backtick spans MASKED first so dotted args
#      (`-q .commit.committer.date`) don't fragment the surrounding sentence.

PLUGIN_COMMAND_SNAPSHOT = (
    Path(__file__).resolve().parent / "fixtures" / "plugin_code_review_command.md"
)

# Bash(<prefix>:*) grant → command prefix. Write(...) / Skill(...) grants are
# not shell commands and are ignored (the `:` class-exclusion stops the capture
# before the `:*`).
_BASH_ALLOW_RE = re.compile(r"Bash\(([^):]+):\*\)")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]+|[—–]")
_SHELL_SEP_RE = re.compile(r"\|\||&&|[|;]")

# First token of a backtick span that marks it as a shell-command instruction
# (vs. a file path, JSON key, glob, or tool name).
_CLI_EXECUTABLES = frozenset(
    {
        "gh",
        "git",
        "curl",
        "wget",
        "python",
        "python3",
        "bash",
        "sh",
        "node",
        "npm",
        "npx",
        "pip",
        "wc",
        "grep",
        "cat",
        "find",
    }
)

# Negation / prohibition cues. A backtick command whose sentence carries any of
# these is an instruction NOT to run it (or a note that it is unreachable), so it
# must not be flagged as an allowlist violation.
_NEGATION_RE = re.compile(
    r"\bnot\b|\bnever\b|n't\b|\bforbid\w*|\bavoid\b|\bunreachable\b"
    r"|\bcannot\b|\bdeliberately\b|\bdenied\b|\bunavailable\b"
    r"|not allowlisted|not in the allowed|rather than|instead of",
    re.IGNORECASE,
)


def _allowlist_command_prefixes(block: str) -> set[str]:
    """Bash(...) command prefixes from an allowlist string, minus the `:*`."""
    return {m.strip() for m in _BASH_ALLOW_RE.findall(block)}


def _prose_body(markdown: str) -> str:
    """Command prose = the markdown minus its leading YAML frontmatter."""
    return _FRONTMATTER_RE.sub("", markdown, count=1)


def _split_subcommands(span: str) -> list[str]:
    return [s.strip() for s in _SHELL_SEP_RE.split(span) if s.strip()]


def _is_command_candidate(subcmd: str) -> bool:
    toks = subcmd.split()
    return len(toks) >= 2 and toks[0] in _CLI_EXECUTABLES


def _command_matches_allowlist(subcmd: str, prefixes: set[str]) -> bool:
    return any(subcmd == p or subcmd.startswith(p + " ") for p in prefixes)


def _iter_backtick_commands(prose: str):
    """Yield (subcommand, containing_sentence) for every candidate CLI command
    in a backtick span. Spans are masked with delimiter-free placeholders before
    sentence segmentation so dotted args don't fragment the sentence."""
    spans = list(_BACKTICK_RE.finditer(prose))
    contents: list[str] = []
    masked: list[str] = []
    last = 0
    for i, m in enumerate(spans):
        masked.append(prose[last : m.start()])
        masked.append(f"{i}")
        contents.append(m.group(1))
        last = m.end()
    masked.append(prose[last:])
    masked_text = "".join(masked)
    placeholder_re = re.compile(r"(\d+)")
    for sentence in _SENTENCE_SPLIT_RE.split(masked_text):
        for pm in placeholder_re.finditer(sentence):
            for sub in _split_subcommands(contents[int(pm.group(1))]):
                if _is_command_candidate(sub):
                    yield sub, sentence


def _prose_command_violations(markdown: str, prefixes: set[str]) -> list[str]:
    """Backticked CLI commands the prose instructs that are neither allowlisted
    nor sitting inside a prohibition sentence."""
    violations: list[str] = []
    for sub, sentence in _iter_backtick_commands(_prose_body(markdown)):
        if _command_matches_allowlist(sub, prefixes):
            continue
        if _NEGATION_RE.search(sentence):
            continue
        violations.append(sub)
    return violations


# Pre-#10-fix step 1(d): the `gh api` instruction that made the review gate fail
# in headless CI (`gh api repos/<owner>/<repo>/commits/<sha> -q ...`, positively
# instructed, not in a prohibition sentence). Vendored inline as the AC3 negative
# fixture — the guard MUST flag it. Note the "not" in the *previous* sentence
# (`head-aware, not "any prior review from me".`) is deliberately in a different
# sentence-segment, which is why the guard segments on sentences rather than a
# fixed-distance window: a stray negation nearby must not suppress the flag.
_PRE_FIX_STEP_1D = (
    '   - For (d), the check MUST be head-aware, not "any prior review from me". '
    "Resolve the head SHA and its committer time "
    "(`gh pr view <n> --json headRefOid -q .headRefOid`, then "
    "`gh api repos/<owner>/<repo>/commits/<sha> -q .commit.committer.date`) and "
    "compare against the creation time of your latest `### Code review` comment."
)


def _live_prefixes() -> set[str]:
    return _allowlist_command_prefixes(_allowed_tools_blocks(LIVE_WORKFLOW)[0])


def test_plugin_prose_commands_within_allowlist() -> None:
    """Every backticked CLI command the plugin prose instructs is covered by the
    live workflow `--allowed-tools` allowlist (or sits in a prohibition
    sentence). A violation means headless CI would DENY that command."""
    markdown = PLUGIN_COMMAND_SNAPSHOT.read_text(encoding="utf-8")
    violations = _prose_command_violations(markdown, _live_prefixes())
    assert not violations, (
        "plugin command prose instructs Bash commands absent from the "
        f"--allowed-tools allowlist (headless CI will DENY these): {violations}. "
        "Either add the tool to code-review.yml's allowlist or reword the prose. "
        "If the plugin command changed, re-vendor the snapshot. (jarvis#1225)"
    )


def test_prose_guard_flags_pre_fix_gh_api() -> None:
    """AC3: the guard fails on the pre-#10-fix content (the step 1(d) `gh api`
    instruction). Without this negative case the guard could pass vacuously."""
    violations = _prose_command_violations(_PRE_FIX_STEP_1D, _live_prefixes())
    assert any(v.startswith("gh api") for v in violations), (
        f"expected the pre-fix `gh api` instruction to be flagged; got {violations}"
    )


def test_prose_guard_skips_prohibition_mentions() -> None:
    """AC2: commands named ONLY inside prohibition sentences ("NEVER run …",
    "not in the allowed tools") must not be flagged as violations."""
    markdown = PLUGIN_COMMAND_SNAPSHOT.read_text(encoding="utf-8")
    violations = _prose_command_violations(markdown, _live_prefixes())
    for forbidden in ("gh api", "curl", "wget", "gh pr checkout", "git fetch"):
        assert not any(v.startswith(forbidden) for v in violations), (
            f"{forbidden!r} is mentioned only in prohibition context and must "
            f"not be flagged; violations={violations}"
        )


@pytest.mark.parametrize(
    "span,is_cmd",
    [
        ("gh pr view <n> --json headRefOid,commits", True),
        ("git show <sha>:<file> | wc -l", True),
        ("python -m py_compile <file>", True),
        ("gh api repos/o/r/commits/sha -q .commit.date", True),
        ("headRefOid", False),  # JSON field name
        ("WebFetch", False),  # tool name, not a shell exe
        ("/tmp/code-review-comment.md", False),  # file path
        ("https://github.com/o/r/blob/sha/f#L1-L2", False),  # URL
        ("*.lock", False),  # glob
        ("python3", False),  # bare executable, no args
        ("cat", False),  # bare, single token
    ],
)
def test_command_candidate_heuristic(span: str, is_cmd: bool) -> None:
    """AC2: the candidate classifier separates shell commands from paths, JSON
    keys, tool names, globs, and bare executables."""
    candidates = [s for s in _split_subcommands(span) if _is_command_candidate(s)]
    assert bool(candidates) is is_cmd, f"{span!r} classified wrong (candidates={candidates})"


def test_snapshot_frontmatter_allowlist_subset_of_live() -> None:
    """The vendored snapshot's own frontmatter allowlist must not grant more Bash
    tools than the live workflow — else a stale snapshot could 'pass' commands
    that CI would actually deny."""
    markdown = PLUGIN_COMMAND_SNAPSHOT.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.match(markdown)
    assert fm, "snapshot missing YAML frontmatter"
    extra = _allowlist_command_prefixes(fm.group(1)) - _live_prefixes()
    assert not extra, (
        f"snapshot frontmatter grants Bash tools absent from live workflow: "
        f"{extra} — re-vendor the snapshot or update code-review.yml"
    )
