"""Meta-test for the .sandcastle/prompt.md bang-backtick guard (#611).

Sandcastle 0.5.7 (`@ai-hero/sandcastle`) preprocesses prompts with the regex
``/!`([^`]+)`/g`` — every ``!`...``` sequence is executed as a shell command.
In ``.sandcastle/prompt.md`` we hit this on line 23: the phrase
``The `!` shell blocks ...`` writes backtick-bang-backtick mid-line; the regex
greedily grabs everything to the next backtick (line 28) and feeds it to
``sh -c``, which exits with a syntax error. See #611 root cause section.

The fix shipped in this PR has two parts:

1. ``.sandcastle/prompt.md`` line 23 rewritten so ``!`` is never adjacent to
   another backtick — ``The "!" shell blocks ...``.
2. A local pre-commit hook (``sandcastle-prompt-md-no-mid-line-bang-backtick``)
   that fails any commit re-introducing a non-leading ``!`` directly followed
   by a backtick in that file.

This file is the meta-test. Per CLAUDE.md "Path-filtered CI guards require a
meta-test" (#326), and per the schema-drift-guard pattern in
``tests/ci/test_schema_drift_guard.py``, every guard ships with a co-located
test that asserts both (a) the config wires the canonical target and regex,
and (b) the rule blocks/allows the right inputs. Lives in ``tests/ci/`` so
``.github/workflows/ci-meta.yml`` picks it up automatically.

Convention deviation note: the grill-output AC in #611 named
``tests/sandcastle/test_prompt_md_guard.py``. Moved to ``tests/ci/`` to
match the existing path-filtered-guard convention and so ci-meta runs it
without a workflow edit (workflows are protected — see ``docs/security``).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
PROMPT_MD = REPO_ROOT / ".sandcastle" / "prompt.md"

HOOK_ID = "sandcastle-prompt-md-no-mid-line-bang-backtick"
GUARD_REGEX = r".!`"
FILES_FILTER = r"^\.sandcastle/prompt\.md$"


# -- Config check ------------------------------------------------------------


def _load_hooks() -> list[dict]:
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    return [h for repo in cfg["repos"] for h in repo.get("hooks", [])]


def _guard_hook() -> dict:
    hooks = _load_hooks()
    matches = [h for h in hooks if h.get("id") == HOOK_ID]
    assert matches, (
        f"Missing pre-commit hook id={HOOK_ID!r}. Guards #611 regression — "
        f"see {PRE_COMMIT_CONFIG.relative_to(REPO_ROOT)}."
    )
    assert len(matches) == 1, f"Duplicate hook id={HOOK_ID!r}"
    return matches[0]


class TestPreCommitHookConfig:
    """Lock down the most load-bearing invariant: the hook wires the right
    file, the right regex, and runs at pre-commit stage. If any of these
    drift, the guard silently stops catching #611-class regressions."""

    def test_pre_commit_config_exists(self):
        assert PRE_COMMIT_CONFIG.exists()

    def test_hook_registered(self):
        _guard_hook()

    def test_hook_targets_prompt_md(self):
        hook = _guard_hook()
        assert hook.get("files") == FILES_FILTER, (
            f"Hook must target {FILES_FILTER!r}; got {hook.get('files')!r}"
        )

    def test_hook_regex_matches_documented_rule(self):
        hook = _guard_hook()
        assert hook.get("entry") == GUARD_REGEX, (
            f"Hook regex drifted from documented rule {GUARD_REGEX!r}; "
            f"got {hook.get('entry')!r}"
        )

    def test_hook_uses_pygrep_language(self):
        hook = _guard_hook()
        assert hook.get("language") == "pygrep"

    def test_hook_runs_at_pre_commit_stage(self):
        hook = _guard_hook()
        stages = hook.get("stages", [])
        assert "pre-commit" in stages, f"Hook must run pre-commit; got stages={stages}"


# -- Logic check -------------------------------------------------------------


def _is_bad(content: str) -> bool:
    """Pure-Python reimplementation of the pygrep rule.

    pygrep with a non-negated entry fails the file iff ``re.search(entry, content)``
    matches. Keep this in sync with ``GUARD_REGEX``.
    """
    return bool(re.search(GUARD_REGEX, content))


class TestGuardLogic:
    def test_legitimate_leading_bang_backtick_passes(self):
        """The three real shell blocks in prompt.md all start at column 0."""
        content = (
            "# Context\n\n"
            "!`gh issue list --label sandcastle`\n\n"
            "!`git log --oneline -10`\n"
        )
        assert not _is_bad(content)

    def test_mid_line_bang_backtick_fails(self):
        """The exact #611 trigger: backtick-bang-backtick inside prose."""
        content = "The `!` shell blocks run before the agent's first turn.\n"
        assert _is_bad(content)

    def test_bang_not_followed_by_backtick_passes(self):
        """``!cmd`` (no backtick directly after ``!``) does not trigger sandcastle's regex."""
        content = "Use `!cmd` style.\n"  # `!cmd` — bang then 'c', not bang then backtick
        assert not _is_bad(content)

    def test_empty_passes(self):
        assert not _is_bad("")

    def test_current_prompt_md_passes(self):
        """The shipping ``.sandcastle/prompt.md`` must pass the guard.

        This is the end-to-end assertion: after the fix lands, the canonical
        prompt must be clean.
        """
        assert PROMPT_MD.exists()
        assert not _is_bad(PROMPT_MD.read_text(encoding="utf-8"))


# -- Semantic parity with sandcastle's own regex -----------------------------


class TestSandcastleSemanticsParity:
    """Independent of our guard: every position in ``prompt.md`` where
    sandcastle's own ``/!`([^`]+)`/g`` matches must be at column 0 (i.e. an
    intended shell block). If a future edit produces a sandcastle-matched
    block mid-line, this test fires even if the guard regex drifts.
    """

    SANDCASTLE_REGEX = re.compile(r"!`([^`]+)`")

    def test_all_sandcastle_matches_are_at_line_start(self):
        content = PROMPT_MD.read_text(encoding="utf-8")
        offenders = []
        for m in self.SANDCASTLE_REGEX.finditer(content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            if m.start() != line_start:
                snippet = m.group(0)[:60].replace("\n", "\\n")
                offenders.append(
                    f"offset={m.start()} (line-start={line_start}): {snippet!r}"
                )
        assert not offenders, (
            "Sandcastle would parse mid-line !`...` blocks as shell commands "
            "(see #611). Offenders:\n  " + "\n  ".join(offenders)
        )
