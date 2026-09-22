"""Click-audit draw (#96): pick the claims a human checks by hand, seeded with the PR head SHA.

The procedure is in RULES.md next to this file. The script reads the doc as it is at the given
commit, lists every claim a click can check, and draws k of them. The draw depends only on the
SHA, the doc path and the claims, so anyone can rerun it and get the same list; the agent that
runs it does not choose.

    python .agents/skills/review-doc/calibration/draw_click_audit.py docs/<doc>.md --sha <head SHA>

Candidate claims, each at its line:

- ``quote``  — a passage in quotation marks (the quote checker's own list), with its sources.
- ``status`` — a ``Status: tried`` or ``Status: sourced`` marker.
- ``linked`` — any other paragraph that links a source: a number, a tool's behaviour, a plan's
  limits, or what a link leads to.

Each claim is ranked by SHA-256 of (SHA, doc path, claim). The k lowest are drawn; the next k are
reserves, used in order when a drawn claim's source cannot be opened. Raising k extends the draw
without reshuffling it. The ranking uses no Python ``random`` or ``hash()``, so it does not change
between Python versions, platforms or interpreter runs.

Exits 0 with the draw, 2 if the SHA or the doc is not usable.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_quotes import extract_quotes, paragraphs  # noqa: E402

DEFAULT_K = 5
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_STATUS_RE = re.compile(r"\bStatus:\s*\**\s*(tried|sourced)\b", re.IGNORECASE)
_SHOWN_CHARS = 240


@dataclass(frozen=True)
class Claim:
    line: int
    kind: str
    text: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class Draw:
    drawn: tuple[Claim, ...]
    reserves: tuple[Claim, ...]


def _status_line(lines: list[str], start: int) -> int:
    """The paragraph's line holding the status marker."""
    for i in range(start - 1, len(lines)):
        if _STATUS_RE.search(lines[i]):
            return i + 1
        if not lines[i].strip():
            break
    return start


def candidates(text: str) -> list[Claim]:
    """Every claim in the doc that a human can check by opening a source, in line order."""
    paras = paragraphs(text)
    starts = [line for line, _, _ in paras]
    quotes = extract_quotes(text)
    quoted_paras = {max(i for i, s in enumerate(starts) if s <= q.line) for q in quotes}
    lines = text.splitlines()

    claims = [Claim(q.line, "quote", q.text, q.urls) for q in quotes]
    for i, (start, para, urls) in enumerate(paras):
        status = _STATUS_RE.search(para)
        if status:
            line = _status_line(lines, start)
            claims.append(Claim(line, "status", lines[line - 1].strip(), urls))
        elif urls and i not in quoted_paras:
            claims.append(Claim(start, "linked", para, urls))
    return sorted(claims, key=lambda c: (c.line, c.kind, c.text))


def _rank(claim: Claim, sha: str, doc: str) -> str:
    key = "\n".join((sha, doc, claim.kind, str(claim.line), claim.text))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def draw(claims: list[Claim], sha: str, doc: str, k: int = DEFAULT_K) -> Draw:
    """The k claims the SHA selects, then up to k reserves. Fewer than k claims: all of them."""
    if not _SHA_RE.match(sha):
        raise ValueError(f"not a full lowercase commit SHA: {sha!r}")
    if k < 1:
        raise ValueError("k must be at least 1")
    doc = Path(doc).as_posix()
    ranked = sorted(set(claims), key=lambda c: _rank(c, sha, doc))
    return Draw(tuple(ranked[:k]), tuple(ranked[k : 2 * k]))


def _show(claim: Claim, doc: str) -> str:
    text = " ".join(claim.text.split())
    if len(text) > _SHOWN_CHARS:
        text = text[: _SHOWN_CHARS - 3] + "..."
    sources = ", ".join(claim.urls) if claim.urls else "none in this paragraph; try the doc's links"
    return f"`{doc}:{claim.line}` — {claim.kind} — {text}\n      Sources: {sources}"


def render(result: Draw, total: int, sha: str, doc: str, k: int) -> str:
    out = [
        "## Click-audit draw",
        "",
        f"Doc: `{doc}` @ {sha}",
        f"Drawn: {len(result.drawn)} of {total} candidate claims (k = {k}).",
        "",
    ]
    out += [f"- [ ] {n}. {_show(c, doc)}" for n, c in enumerate(result.drawn, 1)]
    if result.reserves:
        out += ["", "Reserves, in order, for a drawn claim whose source cannot be opened:", ""]
        out += [f"{n}. {_show(c, doc)}" for n, c in enumerate(result.reserves, 1)]
    return "\n".join(out) + "\n"


def _read_at(repo: str, sha: str, doc: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{sha}:{doc}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FileNotFoundError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout.decode("utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("doc", help="repo-relative path of the doc")
    parser.add_argument("--sha", required=True, help="the PR head SHA, in full")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="claims to draw (default 5)")
    parser.add_argument("--repo", default=".", help="the repository (default: current directory)")
    args = parser.parse_args(argv)

    doc = Path(args.doc).as_posix()
    try:
        claims = candidates(_read_at(args.repo, args.sha, doc))
        result = draw(claims, args.sha, doc, args.k)
    except (ValueError, FileNotFoundError) as exc:
        print(f"draw_click_audit: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render(result, len(claims), args.sha, doc, args.k))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
