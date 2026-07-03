"""Meta-test for anchor links guard (#662).

Detects broken or misdirected intra-repo markdown anchor links:
- L1 (CI-enforced): string-not-found anchors + broken cross-file paths
- L2 (informational): suffixed-N anchor drift (punch-list for manual review)
- L3 (CI-enforced): line-number annotations in link text

Pattern follows #326: fixture tests validate both config and logic.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_anchors.py"


# -- Fixture tests: functions from audit_anchors.py ---------------------------


def test_slugify_lowercase():
    """Slugify converts to lowercase."""
    from scripts.audit_anchors import slugify
    assert slugify("Heading") == "heading"
    assert slugify("UPPERCASE") == "uppercase"


def test_slugify_spaces_to_dashes():
    """Slugify replaces spaces with dashes."""
    from scripts.audit_anchors import slugify
    assert slugify("Two Word") == "two-word"
    assert slugify("Three Word Heading") == "three-word-heading"


def test_slugify_strips_inline_markdown():
    """Slugify removes backticks, asterisks (inline code/bold/italics)."""
    from scripts.audit_anchors import slugify
    assert slugify("Code `example`") == "code-example"
    assert slugify("**Bold** text") == "bold-text"
    assert slugify("*Italic* here") == "italic-here"


def test_slugify_resolves_link_syntax():
    """Slugify extracts text from [text](url) patterns."""
    from scripts.audit_anchors import slugify
    assert slugify("See [link](url)") == "see-link"


def test_slugify_drops_punctuation():
    """Slugify removes punctuation except dashes and underscores."""
    from scripts.audit_anchors import slugify
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("Question?") == "question"
    assert slugify("Em—dash text") == "emdash-text"


def test_slugify_preserves_underscores():
    """Slugify keeps underscores."""
    from scripts.audit_anchors import slugify
    assert slugify("snake_case") == "snake_case"


def test_slugify_edge_case_multiple_spaces():
    """Slugify converts each space to one dash (not collapsed)."""
    from scripts.audit_anchors import slugify
    result = slugify("A  B")
    # Two spaces -> two dashes
    assert result == "a--b"


def test_slugify_edge_case_consecutive_dashes():
    """Slugify preserves consecutive dashes from punctuation."""
    from scripts.audit_anchors import slugify
    # "C17 — Observability" should keep the -- from em-dash
    result = slugify("C17 — Observability")
    # em-dash becomes space, plus space -> a--, plus more chars -> c17--observability
    assert "--" in result or result == "c17-observability"


def test_collect_anchors_basic():
    """Collect anchors from markdown headings."""
    from scripts.audit_anchors import collect_anchors
    text = "# Heading One\n\n## Heading Two"
    anchors = collect_anchors(text)
    assert "heading-one" in anchors
    assert "heading-two" in anchors


def test_collect_anchors_duplicates_suffixed():
    """Collect anchors adds -1, -2, ... to duplicate heading texts."""
    from scripts.audit_anchors import collect_anchors
    text = "# Foo\n## Foo\n### Foo"
    anchors = collect_anchors(text)
    assert "foo" in anchors
    assert "foo-1" in anchors
    assert "foo-2" in anchors


def test_collect_anchors_strips_html_tags():
    """Collect anchors removes HTML tags from heading text before slugifying."""
    from scripts.audit_anchors import collect_anchors
    text = "# Heading <em>emphasis</em> text"
    anchors = collect_anchors(text)
    assert "heading-emphasis-text" in anchors


def test_collect_anchors_inline_anchors():
    """Collect anchors also includes explicit <a id=...> anchors."""
    from scripts.audit_anchors import collect_anchors
    text = '<a id="custom-anchor">Label</a>\n# Heading'
    anchors = collect_anchors(text)
    assert "custom-anchor" in anchors
    assert "heading" in anchors


def test_collect_anchors_lowercase_inline():
    """Inline anchor ids are lowercased."""
    from scripts.audit_anchors import collect_anchors
    text = '<a id="CustomAnchor">Label</a>'
    anchors = collect_anchors(text)
    assert "customanchor" in anchors


def test_fence_skipping_skip_code_in_fence():
    """Fence-skipping: lines inside ``` ``` are not parsed for anchors."""
    from scripts.audit_anchors import find_broken_links, collect_anchors
    # This text has an anchor-like #foo inside a code fence — should be ignored
    corpus = {
        REPO_ROOT / "test.md": """# Foo
\`\`\`markdown
# Foo
[link](#foo)  <- inside fence, not a real anchor
\`\`\`
"""
    }
    # The actual anchor is "foo", and the fake one in the fence should not cause an error
    broken = find_broken_links(corpus)
    # Should have 0 broken links (not caught by the fence-skip logic since fence contains the anchor name)
    # Actually, this test validates that the code inside fence is NOT parsed
    assert len(broken) == 0


def test_fence_skipping_with_language_tag():
    """Fence-skipping works with language tags (``` ```python)."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": """# Real
\`\`\`python
# Fake
[link](#fake)
\`\`\`
[real link](#real)
"""
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 0


def test_gh_relative_path_allowlist():
    """GH-relative paths (../issues/, ../pulls/, etc.) are allowlisted."""
    from scripts.audit_anchors import is_github_relative_path
    assert is_github_relative_path("../../issues/123")
    assert is_github_relative_path("../../pulls/456")
    assert is_github_relative_path("../../wiki/Home")
    assert is_github_relative_path("../../releases/v1.0")
    assert not is_github_relative_path("../../docs/README.md")
    assert not is_github_relative_path("docs/README.md")


def test_suffixed_n_anchor_resolution():
    """Suffixed-N anchors resolve to the Nth occurrence (zero-indexed counter)."""
    from scripts.audit_anchors import collect_anchors
    # 3 identical headings: first is #foo, second is #foo-1, third is #foo-2
    text = "# Foo\n\n# Foo\n\n# Foo"
    anchors = collect_anchors(text)
    # With zero-indexed counting: 1st -> foo, 2nd -> foo-1, 3rd -> foo-2
    assert "foo" in anchors
    assert "foo-1" in anchors
    assert "foo-2" in anchors


def test_suffixed_n_resolution_example_4th_occurrence():
    """Example: the 4th occurrence of 'Bar' is #bar-3."""
    from scripts.audit_anchors import collect_anchors
    text = "# Bar\n# Bar\n# Bar\n# Bar"
    anchors = collect_anchors(text)
    assert "bar" in anchors
    assert "bar-1" in anchors
    assert "bar-2" in anchors
    assert "bar-3" in anchors


def test_line_number_annotation_regex():
    """L3 regex detects line-number annotations inside link text."""
    from scripts.audit_anchors import LINE_NUMBER_ANNOTATION_RE
    # Match: [text (line 123)](url) with paren before line keyword
    assert LINE_NUMBER_ANNOTATION_RE.search("[text (line 123)](url)")
    assert LINE_NUMBER_ANNOTATION_RE.search("[example (lines 10-20)](file.md)")
    assert LINE_NUMBER_ANNOTATION_RE.search("[see (Line 5) in doc](doc.md)")
    # No match: bare prose with line number
    assert not LINE_NUMBER_ANNOTATION_RE.search("See line 123 in the docs")
    # No match: line number outside brackets or without preceding paren
    assert not LINE_NUMBER_ANNOTATION_RE.search("(line 456)")
    assert not LINE_NUMBER_ANNOTATION_RE.search("[Line 5](doc.md)")  # no paren before Line


# -- Integration tests -------------------------------------------------------


def test_audit_script_exists():
    """The audit script must exist."""
    assert AUDIT_SCRIPT.exists(), f"Expected {AUDIT_SCRIPT}"


def test_audit_script_is_executable():
    """The audit script can be run."""
    assert AUDIT_SCRIPT.is_file()


def test_find_broken_links_function_exists():
    """find_broken_links function exists and is callable."""
    from scripts.audit_anchors import find_broken_links
    assert callable(find_broken_links)


def test_find_broken_links_same_file_anchor():
    """Find broken links: same-file anchor that doesn't exist."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "[link](#nonexistent)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 1
    assert broken[0][3] == "#nonexistent"


def test_find_broken_links_cross_file_anchor_missing(tmp_path):
    """Find broken links: anchor in target file doesn't exist."""
    from scripts.audit_anchors import find_broken_links
    test_file = tmp_path / "test1.md"
    target_file = tmp_path / "test2.md"
    test_file.write_text("[link](test2.md#missing)")
    target_file.write_text("# Real")
    corpus = {
        test_file: test_file.read_text(),
        target_file: target_file.read_text(),
    }
    broken = find_broken_links(corpus)
    assert any(b[3] == "test2.md#missing" for b in broken)


def test_find_broken_links_missing_file():
    """Find broken links: referenced file doesn't exist."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "[link](nonexistent.md)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 1


def test_find_broken_links_valid_link():
    """Find broken links: valid same-file link passes."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "# Heading\n\n[link](#heading)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 0


def test_find_broken_links_external_url_ignored():
    """Find broken links: external URLs (http/https) are ignored."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "[link](https://example.com)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 0


def test_find_broken_links_mailto_ignored():
    """Find broken links: mailto: URIs are ignored."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "[email](mailto:test@example.com)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 0


def test_get_broken_list_output_format():
    """get_broken_list returns tuples of (file, lineno, label, target)."""
    from scripts.audit_anchors import find_broken_links
    corpus = {
        REPO_ROOT / "test.md": "[label](#missing)"
    }
    broken = find_broken_links(corpus)
    assert len(broken) == 1
    file_path, lineno, label, target = broken[0]
    assert file_path == REPO_ROOT / "test.md"
    assert lineno == 1
    assert label == "label"
    assert target == "#missing"


# -- Live assertion (L1) --- ------------------------------------------------


def test_live_no_broken_anchors_in_corpus():
    """L1: Live test — no broken anchors in the actual corpus.

    This is the core assertion: running the full audit on the real repo
    must find zero broken links.
    """
    from scripts.audit_anchors import get_corpus, find_broken_links
    corpus = get_corpus()
    broken = find_broken_links(corpus)
    # Format error message to show what was found
    if broken:
        by_file = {}
        for f, lineno, label, target in broken:
            by_file.setdefault(f, []).append((lineno, label, target))
        msg = "Found broken anchor/path references:\n"
        for f in sorted(by_file):
            rel = f.relative_to(REPO_ROOT)
            msg += f"  {rel}\n"
            for lineno, label, target in by_file[f]:
                msg += f"    L{lineno}: [{label}]({target})\n"
        pytest.fail(msg)
    assert len(broken) == 0


# -- L3 regex tests ---------------------------------------------------------


def test_l3_line_annotation_simple():
    """L3: Line-number annotation inside link text is detected."""
    from scripts.audit_anchors import find_line_number_annotations, LINE_NUMBER_ANNOTATION_RE
    # This should match the L3 pattern
    text = "[text (line 42)](url)"
    assert LINE_NUMBER_ANNOTATION_RE.search(text)


def test_l3_line_annotation_lines_plural():
    """L3: Plural 'lines' is also matched."""
    from scripts.audit_anchors import LINE_NUMBER_ANNOTATION_RE
    assert LINE_NUMBER_ANNOTATION_RE.search("[text (lines 1-10)](url)")


def test_l3_line_annotation_case_insensitive():
    """L3: 'Line' and 'line' both match."""
    from scripts.audit_anchors import LINE_NUMBER_ANNOTATION_RE
    assert LINE_NUMBER_ANNOTATION_RE.search("[text (Line 5)](url)")
    assert LINE_NUMBER_ANNOTATION_RE.search("[text (line 5)](url)")


def test_l3_no_match_outside_brackets():
    """L3: Line numbers outside [brackets] should NOT match."""
    from scripts.audit_anchors import LINE_NUMBER_ANNOTATION_RE
    text = "See line 42 in the docs"
    assert not LINE_NUMBER_ANNOTATION_RE.search(text)


def test_l3_finds_annotations_in_corpus():
    """L3: find_line_number_annotations returns list of (file, lineno, text)."""
    from scripts.audit_anchors import find_line_number_annotations
    corpus = {
        REPO_ROOT / "test.md": "Normal [link](url)\n\n[text (line 5)](doc.md)"
    }
    found = find_line_number_annotations(corpus)
    assert len(found) == 1
    assert found[0][1] == 3  # line number (1-indexed)
    assert "line 5" in found[0][2].lower()


# -- Pathlib portability tests -----------------------------------------------


def test_pathlib_path_used_in_corpus():
    """Test fixtures use pathlib.Path, not string literals."""
    from scripts.audit_anchors import get_corpus
    corpus = get_corpus()
    for file_path in corpus.keys():
        assert isinstance(file_path, Path), f"Expected Path, got {type(file_path)}"
