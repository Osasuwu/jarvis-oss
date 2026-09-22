"""Tests for the click-audit draw (#96).

The draw has to be reproducible from the PR head SHA alone, so anyone can rerun it and check that
the agent did not pick the claims. These tests pin that down.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".agents" / "skills" / "review-doc" / "calibration" / "draw_click_audit.py"

_spec = importlib.util.spec_from_file_location("draw_click_audit", SCRIPT)
draw_click_audit = importlib.util.module_from_spec(_spec)
sys.modules["draw_click_audit"] = draw_click_audit
_spec.loader.exec_module(draw_click_audit)

candidates = draw_click_audit.candidates
draw = draw_click_audit.draw
main = draw_click_audit.main

SHA_A = "a" * 40
SHA_B = "0123456789abcdef0123456789abcdef01234567"
DOC = "docs/some-doc.md"


def _doc_text(n_quotes: int) -> str:
    paras = [
        f'Quote {i} reads "word{i} alpha beta gamma" as [source {i}](https://example.com/{i}).'
        for i in range(n_quotes)
    ]
    return "---\napplies_when: x\n---\n\n# Title\n\n" + "\n\n".join(paras) + "\n"


# --- candidate population ---------------------------------------------------------------------


def test_candidates_cover_quotes_statuses_and_linked_claims():
    text = (
        "---\napplies_when: x\n---\n\n"
        'The manual says "a quoted source passage" [here](https://example.com/q).\n\n'
        "**Lifecycle.** Edit a file. Status: tried in our private project.\n\n"
        "**Lifecycle.** Harness setting. Status: sourced.\n\n"
        "Depth is four hops, per [the docs](https://example.com/depth).\n\n"
        "A paragraph with no source and no status.\n\n"
        "```\nStatus: tried \"inside a code fence\" https://example.com/fence\n```\n"
    )
    got = [(c.kind, c.line) for c in candidates(text)]
    assert got == [("quote", 5), ("status", 7), ("status", 9), ("linked", 11)]


def test_quote_paragraph_is_not_also_a_linked_claim():
    text = 'Intro.\n\nIt says "one two three four" in [the page](https://example.com/p).\n'
    kinds = [c.kind for c in candidates(text)]
    assert kinds == ["quote"]


def test_linked_claim_keeps_its_links():
    text = "Exit status is 5, see [git config](https://git-scm.com/docs/git-config).\n"
    (claim,) = candidates(text)
    assert claim.kind == "linked"
    assert claim.urls == ("https://git-scm.com/docs/git-config",)


# --- the draw ---------------------------------------------------------------------------------


def test_draw_is_deterministic_for_a_given_sha():
    claims = candidates(_doc_text(20))
    first = draw(claims, SHA_A, DOC)
    second = draw(claims, SHA_A, DOC)
    assert first == second
    assert len(first.drawn) == 5


def test_draw_ignores_the_order_candidates_arrive_in():
    claims = candidates(_doc_text(20))
    assert draw(claims, SHA_A, DOC) == draw(list(reversed(claims)), SHA_A, DOC)


def test_draw_changes_with_the_sha():
    claims = candidates(_doc_text(20))
    assert draw(claims, SHA_A, DOC).drawn != draw(claims, SHA_B, DOC).drawn


def test_draw_changes_with_the_doc_path():
    claims = candidates(_doc_text(20))
    assert draw(claims, SHA_A, DOC).drawn != draw(claims, SHA_A, "docs/other.md").drawn


def test_fewer_than_k_draws_all_of_them():
    claims = candidates(_doc_text(3))
    result = draw(claims, SHA_A, DOC)
    assert sorted(result.drawn, key=lambda c: c.line) == sorted(claims, key=lambda c: c.line)
    assert result.reserves == ()


def test_k_defaults_to_five_and_reserves_follow_the_draw():
    claims = candidates(_doc_text(20))
    result = draw(claims, SHA_A, DOC)
    assert draw_click_audit.DEFAULT_K == 5
    assert len(result.drawn) == 5
    assert len(result.reserves) == 5
    assert not set(result.drawn) & set(result.reserves)
    bigger = draw(claims, SHA_A, DOC, k=6)
    assert bigger.drawn[:5] == result.drawn  # raising k extends the draw, it does not reshuffle it


@pytest.mark.parametrize("sha", ["abc1234", "g" * 40, "", "A" * 39])
def test_draw_rejects_anything_but_a_full_sha(sha):
    with pytest.raises(ValueError):
        draw(candidates(_doc_text(3)), sha, DOC)


# --- the command line, against a real git history ---------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    doc = tmp_path / "docs" / "some-doc.md"
    doc.parent.mkdir()
    doc.write_text(_doc_text(12), encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "doc")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def test_cli_reads_the_doc_at_the_sha_not_the_worktree(repo, capsys):
    root, sha = repo
    assert main([DOC, "--sha", sha, "--repo", str(root)]) == 0
    before = capsys.readouterr().out
    (root / DOC).write_text(_doc_text(1), encoding="utf-8")  # uncommitted edit
    assert main([DOC, "--sha", sha, "--repo", str(root)]) == 0
    assert capsys.readouterr().out == before
    assert sha in before
    assert "Drawn: 5 of 12" in before


def test_cli_output_is_identical_across_processes(repo):
    # A separate interpreter with a different hash seed must draw the same claims.
    root, sha = repo
    outputs = []
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), DOC, "--sha", sha, "--repo", str(root)],
            check=True,
            capture_output=True,
            encoding="utf-8",
            env=env,
        )
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].count("- [ ] ") == 5


def test_cli_fails_on_a_missing_doc(repo, capsys):
    root, sha = repo
    assert main(["docs/nope.md", "--sha", sha, "--repo", str(root)]) == 2
