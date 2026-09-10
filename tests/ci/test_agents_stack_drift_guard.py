"""Regression guard: no *live* doc/docstring/comment presents the retired
agents-stack as runnable or current (#1138).

The LangGraph / APScheduler / Postgres-checkpointer "agents-stack" was retired
in #743/#744; the doc, config, and env drift it left behind was swept in #734.
This guard is the *stay-clean* invariant that keeps a future edit from quietly
re-introducing a runnable reference to the dead stack — the exact failure mode
where a stale `python -m agents.dispatcher` in a script outlives the module it
names.

Mechanism — a grep-clean invariant with an inline allowlist:

  * scan every git-tracked file for the retired-token set (word-boundary,
    case-insensitive);
  * every surviving match MUST be covered by ``ALLOWLIST`` below — the single
    source of truth for "this mention is correct by design" (#1138). There is
    no sidecar file: the token set and its allowlist live together here so a
    reviewer sees both in one place.

Word boundaries matter: the bare token ``5433`` (the retired Postgres port)
substring-matches inside SVG coordinate floats and other digit runs. Matching
on ``\b5433\b`` reduces it — and ``docker-compose.agents`` / ``agents.main`` /
``agents.scheduler`` — to zero live matches, which is exactly what #734's sweep
achieved. The dotted module tokens (``agents.dispatcher`` etc.) are the
*runnable* form; the file-path form ``agents/dispatcher.py`` (slash) is a
retirement/lineage reference and is deliberately NOT matched.

Runs via ci-meta.yml (``pytest tests/ci/`` — not path-filtered) and the regular
suite (``testpaths = ["tests"]``), so it fires on every PR.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# The retired agents-stack token set. Casing here is for readability only —
# matching is case-insensitive. Keep this list in lock-step with the retirement
# work (#743/#744/#734); dropping a token silently narrows the guard.
RETIRED_TOKENS: tuple[str, ...] = (
    "langgraph",
    "langchain-ollama",
    "PostgresSaver",
    "event_monitor",
    "docker-compose.agents",
    "5433",
    "SQLAlchemyJobStore",
    "APScheduler",
    "agents.main",
    "agents.scheduler",
    "agents.dispatcher",
)

# Sentinel: every retired token is correct-by-design in this path.
ALL = "*"

# Allowlist of correct-by-design mentions — the single source of truth (#1138).
#
# Keys ending in "/" are directory prefixes; others are exact repo-relative
# paths (posix, matching ``git ls-files`` output). The value is either ``ALL``
# (the whole file is a legitimate home for any retired token) or a set of
# lowercased tokens permitted in that specific file (a genuine drift of a
# *different* token in the same file is still caught).
#
# Anything matching a retired token that is NOT covered here is drift: fix it,
# or — if the mention is genuinely correct-by-design — add it here WITH A REASON.
ALLOWLIST: dict[str, object] = {
    # -- whole-file-legitimate: the mention IS the point of the file ----------
    "supabase/migrations/": ALL,  # applied migrations — frozen historical DDL
    "docs/design/": ALL,  # design-history docs — lineage mentions
    "docs/research/": ALL,  # research drafts — verbatim external/pre-retirement captures
    # -- specific files, restricted to their correct-by-design tokens ---------
    ".claude-userlevel/skills/implement/SKILL.md": {
        "apscheduler"
    },  # historical bug-class example (#304/#298)
    # This guard file itself defines the token set — every token appears here.
    "tests/ci/test_agents_stack_drift_guard.py": ALL,
}

# Compile once. re.escape keeps "." / "-" literal; \b anchors whole-token match.
_TOKEN_RES: dict[str, re.Pattern[str]] = {
    token: re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE) for token in RETIRED_TOKENS
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tracked_files() -> list[str]:
    """Every git-tracked (or staged) repo-relative path, posix-style."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def _allowed(rel_path: str, token: str) -> bool:
    """True iff ``token`` in ``rel_path`` is a correct-by-design mention."""
    token_l = token.lower()
    for key, permitted in ALLOWLIST.items():
        hit = rel_path.startswith(key) if key.endswith("/") else rel_path == key
        if hit and (permitted is ALL or token_l in permitted):  # type: ignore[operator]
            return True
    return False


def _iter_matches():
    """Yield (rel_path, line_no, token) for every retired-token hit in the tree.

    Binary files (containing a NUL byte) are skipped; text is decoded utf-8
    with replacement so an odd byte never crashes the scan.
    """
    for rel in _tracked_files():
        path = REPO_ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:  # binary — not a doc/docstring/comment
            continue
        text = data.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for token, rx in _TOKEN_RES.items():
                if rx.search(line):
                    yield rel, line_no, token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentsStackDriftGuard:
    def test_no_live_runnable_reference_to_retired_stack(self):
        """Every retired-token match must be allowlisted; anything else is drift."""
        offenses = [
            f"{rel}:{line_no}: {token}"
            for rel, line_no, token in _iter_matches()
            if not _allowed(rel, token)
        ]
        assert not offenses, (
            f"Found {len(offenses)} live reference(s) to the retired agents-stack "
            f"(retired in #743/#744, swept in #734).\n"
            "Fix the doc/docstring/comment, or — if the mention is correct by design "
            "— add its path to ALLOWLIST in this file with a reason.\n"
            + "\n".join(f"  {o}" for o in offenses)
        )

    def test_allowlist_entries_are_live(self):
        """No stale exemptions: every ALLOWLIST entry must suppress a real match.

        When a future cleanup removes the last legitimate mention from an
        allowlisted file, its entry becomes dead weight — this test flags it so
        the allowlist stays an honest inventory of correct-by-design mentions.
        """
        raw = {(rel, token.lower()) for rel, _, token in _iter_matches()}
        stale: list[str] = []
        for key, permitted in ALLOWLIST.items():
            covers = any(
                (rel.startswith(key) if key.endswith("/") else rel == key)
                and (permitted is ALL or tok in permitted)  # type: ignore[operator]
                for rel, tok in raw
            )
            if not covers:
                stale.append(key)
        assert not stale, (
            f"{len(stale)} ALLOWLIST entr(ies) no longer match any retired token "
            "and should be removed:\n" + "\n".join(f"  {s}" for s in stale)
        )

    def test_token_set_is_canonical(self):
        """Lock the retired-token set so no token is silently dropped (#1138)."""
        expected = {
            "langgraph",
            "langchain-ollama",
            "PostgresSaver",
            "event_monitor",
            "docker-compose.agents",
            "5433",
            "SQLAlchemyJobStore",
            "APScheduler",
            "agents.main",
            "agents.scheduler",
            "agents.dispatcher",
        }
        assert set(RETIRED_TOKENS) == expected
        assert len(RETIRED_TOKENS) == len(expected), "duplicate token in RETIRED_TOKENS"
