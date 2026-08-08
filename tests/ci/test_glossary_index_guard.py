"""Guard for the CONTEXT.md glossary index drift checker (#1266).

Two jobs, mirroring the `check-memory-deriver-schema.py` +
`test_memory_review_schema.py` pairing:

- **Logic** — exercise `check()` against synthetic glossaries so each failure
  mode it claims to detect is pinned: entry missing from the index, index
  entry with no section body, a term defined twice, a term indexed twice,
  and a missing `## Glossary` section.
- **Live** — run the checker against the real `CONTEXT.md`. This is what
  wires the script into CI: pytest runs on every PR, so an edit that drifts
  the glossary out of sync with its index goes red here rather than rotting
  until someone remembers the script exists.

The duplicate cases are the ones with history behind them: 51fce34 ("dedupe
ADR block") removed a duplicated glossary block by hand, and a set-based
checker reports that state as clean in both directions.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-glossary-index.py"
CONTEXT_PATH = REPO_ROOT / "CONTEXT.md"


def _load_checker():
    """Import the hyphenated script as a module (not importable by name)."""
    spec = importlib.util.spec_from_file_location("check_glossary_index", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    assert SCRIPT_PATH.exists(), f"drift checker missing at {SCRIPT_PATH}"
    return _load_checker()


IN_SYNC = """\
## Glossary

### Index

- **alpha**
- **beta**

### Core

- **alpha** — first letter
- **beta** — second letter

## Next section
"""


def _glossary(index_terms: list[str], body_terms: list[str]) -> str:
    index = "\n".join(f"- **{t}**" for t in index_terms)
    body = "\n".join(f"- **{t}** — description" for t in body_terms)
    return f"## Glossary\n\n### Index\n\n{index}\n\n### Core\n\n{body}\n\n## Next section\n"


class TestCheckLogic:
    def test_in_sync_glossary_passes(self, checker):
        assert checker.check(IN_SYNC) == 0

    def test_entry_missing_from_index_fails(self, checker):
        text = _glossary(index_terms=["alpha"], body_terms=["alpha", "beta"])
        assert checker.check(text) == 1

    def test_indexed_term_with_no_section_body_fails(self, checker):
        text = _glossary(index_terms=["alpha", "beta"], body_terms=["alpha"])
        assert checker.check(text) == 1

    def test_term_defined_twice_in_sections_fails(self, checker):
        """The 51fce34 drift class: a duplicated block is set-equal to a clean one."""
        text = _glossary(index_terms=["alpha", "beta"], body_terms=["alpha", "beta", "beta"])
        assert checker.check(text) == 1, (
            "duplicate section entry reported as in-sync — set-based accumulation "
            "collapsed the second copy"
        )

    def test_term_indexed_twice_fails(self, checker):
        text = _glossary(index_terms=["alpha", "beta", "beta"], body_terms=["alpha", "beta"])
        assert checker.check(text) == 1

    def test_missing_glossary_section_fails(self, checker):
        assert checker.check("# Doc\n\nNo glossary here.\n") == 1

    def test_empty_index_fails(self, checker):
        text = "## Glossary\n\n### Index\n\n### Core\n\n- **alpha** — first\n\n## Next\n"
        assert checker.check(text) == 1

    def test_duplicate_detection_reports_only_repeats(self, checker):
        assert checker._duplicates(["a", "b", "a", "c", "a"]) == ["a"]
        assert checker._duplicates(["a", "b", "c"]) == []


class TestLiveContextFile:
    def test_real_context_glossary_is_in_sync(self, checker):
        """CONTEXT.md itself must pass — this is the script's CI wiring."""
        assert CONTEXT_PATH.exists(), f"CONTEXT.md missing at {CONTEXT_PATH}"
        rc = checker.check(CONTEXT_PATH.read_text(encoding="utf-8"))
        assert rc == 0, (
            "CONTEXT.md glossary index has drifted out of sync with its section "
            f"entries — run `python {SCRIPT_PATH.relative_to(REPO_ROOT)}` for the diff"
        )
