"""Tests for the quote checker (#86). No network: every test passes its own fetch."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_quotes import (  # noqa: E402
    check,
    extract_quotes,
    html_to_text,
    main,
    normalize,
    quote_in_source,
)

PAD = " filler" * 60  # pushes a fake page past the near-empty threshold


def _fetcher(pages: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in pages:
            raise OSError(f"unreachable: {url}")
        return pages[url] + PAD

    return fetch


def _doc(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "doc.md"
    path.write_text(f"---\napplies_when: x\n---\n\n{body}\n", encoding="utf-8")
    return path


def _verdicts(tmp_path: Path, body: str, pages: dict[str, str]) -> list[tuple[str, str]]:
    return [(q.text, v) for q, v, _ in check(_doc(tmp_path, body), fetch=_fetcher(pages))]


# --- extraction --------------------------------------------------------------


def test_extracts_quote_with_its_paragraph_links():
    quotes = extract_quotes('The [docs](https://a.example) say "exit code two blocks".\n')
    assert [(q.text, q.urls) for q in quotes] == [("exit code two blocks", ("https://a.example",))]


def test_quote_spanning_lines_is_joined_and_reported_at_its_first_line():
    text = 'Intro line.\nIt says "exit code\ntwo blocks" per [docs](https://a.example).\n'
    [quote] = extract_quotes(text)
    assert quote.text == "exit code two blocks"
    assert quote.line == 2


def test_skips_code_fences_inline_code_and_short_quotes():
    text = (
        '```json\n{"say": "this is in a fence"}\n```\n\n'
        'Set `"edit": "deny all writes"` and call it "ok" [x](https://a.example).\n'
    )
    assert extract_quotes(text) == []


def test_skips_frontmatter():
    assert extract_quotes('---\nx: "a b c d"\n---\n\nbody\n') == []


def test_paragraph_without_links_falls_back_to_previous_paragraph():
    text = 'See [docs](https://a.example).\n\nThe same page says "exit code two blocks".\n'
    [quote] = extract_quotes(text)
    assert quote.urls == ("https://a.example",)


def test_bare_url_counts_as_a_link():
    [quote] = extract_quotes('It says "exit code two blocks" (https://a.example/page).\n')
    assert quote.urls == ("https://a.example/page",)


def test_list_item_is_its_own_paragraph():
    text = '- one [a](https://a.example)\n- two says "exit code two blocks"\n'
    [quote] = extract_quotes(text)
    assert quote.urls == ("https://a.example",)  # previous item, not the same one
    assert quote.line == 2


# --- matching ------------------------------------------------------------------


def test_normalize_ignores_case_markdown_curly_quotes_dashes_and_pipes():
    assert normalize("**Never** — “edit” | `x`") == normalize("never - edit x")


def test_html_spacing_before_punctuation_does_not_break_a_match():
    page = html_to_text("<li>Customizable template (default: <code>MADR</code>)</li>")
    assert quote_in_source("Customizable template (default: MADR)", page)


def test_ellipsis_checks_each_piece():
    source = "The hook runs first. Much later text. It then blocks the call."
    assert quote_in_source("The hook runs first … it then blocks the call", source)
    assert not quote_in_source("The hook runs first … it then allows the call", source)


def test_paraphrase_does_not_match():
    assert not quote_in_source("exit code two blocks", "Exit code 2 blocks the call.")


# --- verdicts --------------------------------------------------------------------


def test_found(tmp_path):
    body = 'The [docs](https://a.example) say "exit code two blocks".'
    assert _verdicts(tmp_path, body, {"https://a.example": "exit code two blocks it"}) == [
        ("exit code two blocks", "found")
    ]


def test_not_found_when_the_linked_page_lacks_the_text(tmp_path):
    body = 'The [docs](https://a.example) say "exit code two blocks".'
    assert _verdicts(tmp_path, body, {"https://a.example": "exit code 2 blocks"}) == [
        ("exit code two blocks", "NOT FOUND")
    ]


def test_found_elsewhere_flags_a_quote_credited_to_the_wrong_page(tmp_path):
    body = (
        'Tool A [docs](https://a.example) say "exit code two blocks".\n\n'
        "Tool B has [its own docs](https://b.example)."
    )
    pages = {"https://a.example": "nothing relevant", "https://b.example": "exit code two blocks"}
    assert _verdicts(tmp_path, body, pages) == [("exit code two blocks", "found elsewhere")]


def test_no_source(tmp_path):
    assert _verdicts(tmp_path, 'We call this "the safe default path".', {}) == [
        ("the safe default path", "no source")
    ]


def test_unfetchable_when_no_linked_page_can_be_fetched(tmp_path):
    body = 'See [a](https://a.example) and [b](https://b.example): "exit code two blocks".'
    assert _verdicts(tmp_path, body, {}) == [("exit code two blocks", "unfetchable")]


def test_not_found_names_the_sources_it_could_not_fetch(tmp_path, capsys):
    body = 'See [a](https://a.example) and [b](https://b.example): "exit code two blocks".'
    doc = _doc(tmp_path, body)
    [(_, verdict, unfetched)] = check(doc, fetch=_fetcher({"https://a.example": "nothing"}))
    assert (verdict, unfetched) == ("NOT FOUND", ("https://b.example",))
    main([str(doc)], fetch=_fetcher({"https://a.example": "nothing"}))
    assert "could not fetch: https://b.example" in capsys.readouterr().out


def test_near_empty_page_is_unfetchable_not_a_mismatch(tmp_path):
    doc = _doc(tmp_path, 'The [docs](https://a.example) say "exit code two blocks".')
    [(_, verdict, _)] = check(doc, fetch=lambda url: "Checking your browser")
    assert verdict == "unfetchable"


def test_relative_link_reads_the_local_file(tmp_path):
    (tmp_path / "other.md").write_text("It says: exit code two blocks.\n", encoding="utf-8")
    body = 'The [other doc](other.md#part) says "exit code two blocks".'
    assert _verdicts(tmp_path, body, {}) == [("exit code two blocks", "found")]


def test_main_exits_1_on_not_found_and_0_otherwise(tmp_path, capsys):
    doc = _doc(tmp_path, 'The [docs](https://a.example) say "exit code two blocks".')
    assert main([str(doc)], fetch=_fetcher({"https://a.example": "other"})) == 1
    assert 'NOT FOUND: "exit code two blocks"' in capsys.readouterr().out
    assert main([str(doc)], fetch=_fetcher({"https://a.example": "exit code two blocks"})) == 0
    assert main([]) == 2


# --- review findings on #88 ------------------------------------------------------


def test_fallback_is_only_the_paragraph_right_before(tmp_path):
    body = 'See [docs](https://a.example).\n\nOther words.\n\nWe say "some own phrase here".'
    assert _verdicts(tmp_path, body, {"https://a.example": "x"}) == [
        ("some own phrase here", "no source")
    ]


def test_a_heading_breaks_the_fallback():
    [quote] = extract_quotes('See [docs](https://a.example).\n\n## Next\n\nIt says "a b c".\n')
    assert quote.urls == ()


def test_binary_local_link_is_unfetchable_not_a_crash(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG\x80\x81\x82")
    body = 'See ![diagram](img.png) and [docs](https://a.example): "wrong words entirely here".'
    assert _verdicts(tmp_path, body, {"https://a.example": "other"}) == [
        ("wrong words entirely here", "NOT FOUND")
    ]


def test_quote_across_blockquote_lines(tmp_path):
    body = 'The [docs](https://a.example) say:\n> "exit code\n> two blocks the call"'
    assert _verdicts(tmp_path, body, {"https://a.example": "exit code two blocks the call"}) == [
        ("exit code two blocks the call", "found")
    ]


def test_url_with_parentheses_link_title_anchor_and_comment():
    text = (
        "<!-- a note \"not a quote at all\" -->\n"
        '[wiki](https://w.example/Foo_(bar) "Docs Page Title Here") and [above](#x)'
        ' say "exit code two blocks".\n'
    )
    [quote] = extract_quotes(text)
    assert (quote.text, quote.urls, quote.line) == (
        "exit code two blocks", ("https://w.example/Foo_(bar)",), 2
    )


def test_link_text_stays_and_a_backtick_fence_does_not_close_a_tilde_fence():
    text = (
        "~~~\n```python\n~~~\n\n"
        'The [page "x" link](https://a.example) says "exit code two blocks".\n'
    )
    assert [q.text for q in extract_quotes(text)] == ["exit code two blocks"]


def test_a_stray_comment_opener_does_not_hide_later_paragraphs():
    text = (
        "Type `<!--` to start.\n\n"
        'The [docs](https://a.example) say "exit code two blocks".\n\n`-->`\n'
    )
    assert [q.text for q in extract_quotes(text)] == ["exit code two blocks"]


def test_line_number_ignores_words_inside_a_comment():
    text = 'See [d](https://a.example) <!-- exit here -->\nand then\n"exit code two blocks".\n'
    [quote] = extract_quotes(text)
    assert quote.line == 3


# --- CI check (#98) -------------------------------------------------------------


def test_ci_not_found_exits_nonzero_and_summary_counts_it(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    doc = _doc(tmp_path, 'The [docs](https://a.example) say "exit code two blocks".')
    assert main([str(doc)], fetch=_fetcher({"https://a.example": "other"})) == 1
    assert "1 NOT FOUND" in capsys.readouterr().out


def test_ci_unfetchable_only_exits_zero_and_prints_the_count(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    body = (
        'See [a](https://a.example) and [b](https://b.example): "exit code two blocks".\n\n'
        'Also [c](https://c.example) says "a third quote here".'
    )
    assert main([str(_doc(tmp_path, body))], fetch=_fetcher({})) == 0
    out = capsys.readouterr().out
    assert "unfetchable sources: 3" in out
    assert "0 NOT FOUND" in out
    assert "unfetchable sources: 3" in summary.read_text(encoding="utf-8")


def test_ci_unfetchable_source_is_counted_once_across_quotes_and_docs(tmp_path, capsys):
    one = tmp_path / "one.md"
    one.write_text('[a](https://a.example) says "exit code two blocks".\n', encoding="utf-8")
    two = tmp_path / "two.md"
    two.write_text('[a](https://a.example) says "another quote right here".\n', encoding="utf-8")
    assert main([str(one), str(two)], fetch=_fetcher({})) == 0
    assert "unfetchable sources: 1" in capsys.readouterr().out


# --- JSON report for the weekly run (#105) ----------------------------------------


def test_json_report_lists_each_quote_not_found_with_its_unfetched_sources(tmp_path):
    body = (
        'The [docs](https://a.example) and [b](https://b.example) say "exit code two blocks".\n\n'
        'The [c](https://c.example) page says "this one is found".'
    )
    doc = _doc(tmp_path, body)
    report = tmp_path / "out.json"
    pages = {"https://a.example": "other", "https://c.example": "this one is found"}
    assert main(["--json", str(report), str(doc)], fetch=_fetcher(pages)) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert (data["docs"], data["quotes"], data["not_found"]) == (1, 2, 1)
    assert data["unfetchable_sources"] == ["https://b.example"]
    [finding] = data["findings"]
    assert finding == {
        "doc": str(doc), "line": 5, "verdict": "NOT FOUND", "quote": "exit code two blocks",
        "tried": ["https://a.example", "https://b.example"], "unfetched": ["https://b.example"],
    }


def test_json_flag_without_a_path_or_docs_prints_usage():
    assert main(["--json"]) == 2
    assert main(["--json", "out.json"]) == 2
