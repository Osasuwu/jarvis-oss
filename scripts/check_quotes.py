"""Quote check — a writer's step before review-doc (#86).

Lists every passage in quotation marks in a markdown doc and checks that it appears, verbatim, in
a source linked from the same paragraph. When a paragraph has no link, the links of the paragraph
right before it are tried, which covers "The same page says …". If those miss, every other link in
the doc is tried,
which catches a quote credited to the wrong page. Matching ignores case, whitespace, markdown
emphasis, table pipes, and curly-versus-straight quotes and dashes. A quote with "…" is checked
piece by piece.

    python scripts/check_quotes.py docs/some-doc.md [more.md ...]

Each quote gets one verdict:

- ``found`` — every piece is in one of the candidate sources.
- ``found elsewhere`` — only a link from another part of the doc has it. Check the attribution.
- ``NOT FOUND`` — no source linked from the doc contains it. Fix the quote, or the link. If a
  candidate could not be fetched, it is listed; check that one by hand first.
- ``no source`` — no link in this paragraph or the one right before it.
- ``unfetchable`` — no candidate source could be fetched, or each came back near-empty (a
  script-rendered page, a bot check, a rate limit). Check these by hand.

Exits 1 if any quote is ``NOT FOUND``. This checks wording only, not whether the doc's claim
around the quote matches the source; that stays review-doc's job.
"""

from __future__ import annotations

import html
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

MIN_QUOTE_WORDS = 3
MIN_PIECE_WORDS = 2

_QUOTE_RE = re.compile(r'"([^"]+)"|“([^”]+)”')
# [text](url) or [text](url "title"); the url may hold one level of parentheses.
_LINK_RE = re.compile(r"\[([^\]]*)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)>\]`\"]+")
MIN_SOURCE_CHARS = 300
_ELLIPSIS_RE = re.compile(r"\s*(?:…|\.\.\.|\[…\]|\[\.\.\.\])\s*")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_BLOCKQUOTE_RE = re.compile(r"^\s*(?:>\s?)+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass(frozen=True)
class Quote:
    line: int
    text: str
    urls: tuple[str, ...]


def _strip_frontmatter(lines: list[str]) -> int:
    """Return the index of the first body line."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Split the body into (first line number, joined text); code fences are dropped.

    A blank line, a heading, a table row or a new list item starts a new paragraph.
    """
    lines = [_BLOCKQUOTE_RE.sub("", line) for line in _COMMENT_RE.sub(_blank, text).splitlines()]
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    fence = ""  # the marker that opened the current fence; only the same one closes it

    def flush() -> None:
        if buf:
            out.append((start, " ".join(s.strip() for s in buf)))
            buf.clear()

    for i in range(_strip_frontmatter(lines), len(lines)):
        line = lines[i]
        opener = _FENCE_RE.match(line)
        if opener and (not fence or opener.group(1) == fence):
            flush()
            fence = "" if fence else opener.group(1)
            continue
        if fence:
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith(("#", "|")) or _LIST_ITEM_RE.match(line):
            flush()
            start = i + 1
            buf.append(line)
            if stripped.startswith(("#", "|")):
                flush()
            continue
        if not buf:
            start = i + 1
        buf.append(line)
    flush()
    return out


def _blank(match: re.Match) -> str:
    """An HTML comment becomes blank lines, so line numbers stay right."""
    return "\n" * match.group(0).count("\n")


def _drop_code_quotes(paragraph: str) -> str:
    """Drop quote marks inside inline code, then the backticks: `"edit": "deny"` is no quote."""
    parts = paragraph.split("`")
    for i in range(1, len(parts), 2):
        parts[i] = parts[i].replace('"', "").replace("“", "").replace("”", "")
    return "".join(parts)


def _quote_line(lines: list[str], start: int, body: str) -> int:
    """The line a quote starts on: the paragraph's line holding its first word."""
    first = body.split()[0]
    for i in range(start - 1, len(lines)):
        if first in lines[i].replace("`", ""):
            return i + 1
        if not lines[i].strip():
            break
    return start


def _urls(paragraph: str) -> tuple[str, ...]:
    """Markdown link targets, then bare URLs, in order."""
    urls = tuple(url for _, url in _LINK_RE.findall(paragraph) if not url.startswith("#"))
    bare = _BARE_URL_RE.findall(_LINK_RE.sub("", paragraph))
    return urls + tuple(u.rstrip(".,;:") for u in bare)


def doc_urls(text: str) -> tuple[str, ...]:
    """Every link in the doc body, once each."""
    return tuple(dict.fromkeys(u for _, para in _paragraphs(text) for u in _urls(para)))


def extract_quotes(text: str) -> list[Quote]:
    quotes: list[Quote] = []
    lines = text.splitlines()
    prev_urls: tuple[str, ...] = ()
    for line, para in _paragraphs(text):
        urls = _urls(para)
        candidates = urls or prev_urls
        prose = _LINK_RE.sub(r"[\1]", _drop_code_quotes(para))  # a link title is no quote
        for match in _QUOTE_RE.finditer(prose):
            body = match.group(1) or match.group(2)
            if len(body.split()) >= MIN_QUOTE_WORDS:
                quotes.append(Quote(_quote_line(lines, line, body), body, candidates))
        prev_urls = urls
    return quotes


def normalize(text: str) -> str:
    text = html.unescape(text)
    text = _LINK_RE.sub(r"\1", text)
    text = text.translate(
        {
            0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
            0x2013: "-", 0x2014: "-", 0x00A0: " ",
        }
    )
    text = re.sub(r"[*_`\\|]", "", text)
    text = text.replace('"', "")
    text = re.sub(r"\s+", " ", text)
    # HTML-to-text joins inline elements with spaces: "<code>x</code>)" becomes "x )".
    text = re.sub(r" (?=[).,;:!?])", "", text)
    text = re.sub(r"(?<=[(]) ", "", text)
    return text.strip().lower()


def quote_pieces(quote: str) -> list[str]:
    pieces = [normalize(p) for p in _ELLIPSIS_RE.split(quote)]
    return [p.strip(" .,;:") for p in pieces if len(p.split()) >= MIN_PIECE_WORDS]


def quote_in_source(quote: str, source: str) -> bool:
    pieces = quote_pieces(quote)
    haystack = normalize(source)
    return bool(pieces) and all(p in haystack for p in pieces)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(page: str) -> str:
    parser = _TextExtractor()
    parser.feed(page)
    return " ".join(parser.parts)


def fetch_url(url: str) -> str:
    """Rewrite to the fetchable form, then return the page as text."""
    url = url.split("#", 1)[0]
    blob = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/(.+)", url)
    if blob:
        url = f"https://raw.githubusercontent.com/{blob.group(1)}/{blob.group(2)}"
    request = urllib.request.Request(url, headers={"User-Agent": "jarvis-oss check_quotes"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
            break
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))
    return html_to_text(body) if "html" in content_type else body


def check(doc: Path, fetch=fetch_url) -> list[tuple[Quote, str, tuple[str, ...]]]:
    """Each quote with its verdict and the candidate sources that could not be fetched."""
    cache: dict[str, str | None] = {}

    def source(url: str) -> str | None:
        if url not in cache:
            if url.startswith(("http://", "https://")):
                try:
                    text = fetch(url)
                except Exception:
                    text = None
                cache[url] = text if text and len(normalize(text)) >= MIN_SOURCE_CHARS else None
            else:
                local = (doc.parent / url.split("#", 1)[0]).resolve()
                try:
                    cache[url] = local.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):  # missing, a directory, an image
                    cache[url] = None
        return cache[url]

    text = doc.read_text(encoding="utf-8")
    quotes = extract_quotes(text)
    all_urls = doc_urls(text)
    results: list[tuple[Quote, str, tuple[str, ...]]] = []
    for quote in quotes:
        near = [t for t in (source(u) for u in quote.urls) if t is not None]
        unfetched = tuple(u for u in quote.urls if source(u) is None)
        if any(quote_in_source(quote.text, t) for t in near):
            results.append((quote, "found", ()))
            continue
        others = [u for u in all_urls if u not in quote.urls]
        far = [t for t in (source(u) for u in others) if t is not None]
        if any(quote_in_source(quote.text, t) for t in far):
            verdict = "found elsewhere"
        elif not quote.urls:
            verdict = "no source"
        elif not near:
            verdict = "unfetchable"
        else:
            verdict = "NOT FOUND"
        results.append((quote, verdict, unfetched))
    return results


def main(argv: list[str], fetch=fetch_url) -> int:
    if not argv:
        print(__doc__)
        return 2
    failed = False
    for name in argv:
        for quote, verdict, unfetched in check(Path(name), fetch=fetch):
            if verdict != "found":
                print(f"{name}:{quote.line}: {verdict}: \"{quote.text}\"")
                if verdict == "NOT FOUND":
                    print(f"    tried: {', '.join(quote.urls)}")
                    failed = True
                if unfetched:
                    print(f"    could not fetch: {', '.join(unfetched)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
