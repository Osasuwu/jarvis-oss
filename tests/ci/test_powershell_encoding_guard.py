"""Meta-test: PowerShell Set-Content/Out-File calls must specify -Encoding.

PowerShell 5.1's default encoding for Set-Content/Out-File is UTF-16 LE BOM,
which silently breaks downstream tools (regex-based config parsers, hashing
tools, MCP servers). Three documented memory incidents trace to this bug
class (powershell_5_1_utf8_no_bom_breaks_on_em_dash,
powershell_from_bash_variable_escaping, windows_shim_must_be_exe_for_node_spawn).

This test acts as a CI lint guard: any new or modified .ps1/.psm1/.psd1 file
that calls Set-Content or Out-File without an explicit -Encoding parameter
will fail CI.

Existing offenders live in the allowlist (powershell_encoding_allowlist.txt),
keyed by path:line_number. The test reads the allowlist and skips entries it
recognises, so an allowlisted line can only be touched by a conscious "I'm
fixing this" decision (removing the allowlist entry).

This is the write-side prevention half of the encoding hygiene pattern;
sister to #705 (.gitattributes) on the read side.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOWLIST_PATH = REPO_ROOT / "tests" / "ci" / "powershell_encoding_allowlist.txt"

# Match Set-Content or Out-File as a command name (word boundary).
# Avoids matching inside longer identifiers or string literals.
_CMDLET_RE = re.compile(r"\b(Set-Content|Out-File)\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tracked_ps_files() -> list[Path]:
    """Return list of Paths for every tracked PowerShell file in the repo."""
    result = subprocess.run(
        ["git", "ls-files", "--", "*.ps1", "*.psm1", "*.psd1"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return [Path(f) for f in result.stdout.strip().split("\n") if f]


def _statement_has_encoding(lines: list[str], line_idx: int, col: int) -> bool:
    """Check whether -Encoding appears in the same statement as the cmdlet.

    A single PowerShell statement can span multiple lines when the line ends
    with a pipe (``|``) or line-continuation backtick (`` ` ``).  We scan
    forward from the cmdlet position across continuation lines for
    ``-Encoding``.
    """
    rest = lines[line_idx][col:]
    if "-Encoding" in rest:
        return True

    # Walk continuation lines (ending with | or `)
    cur = line_idx
    while cur < len(lines):
        trimmed = lines[cur].rstrip()
        if trimmed.endswith("|") or trimmed.endswith("`"):
            cur += 1
            if cur < len(lines) and "-Encoding" in lines[cur]:
                return True
        else:
            break
    return False


def _load_allowlist() -> set[str]:
    """Load allowlist; return set of ``path:line`` entries.

    File format (one per line)::

        # optional comment
        path/to/file.ps1:42  # comment is optional after entry
        path/to/other.ps1:15

    Comment-only lines and blank lines are ignored.  Each non-comment line
    is split on whitespace; the first token (``path:line``) is kept.

    Returns an empty set if the file does not exist.
    """
    if not ALLOWLIST_PATH.exists():
        return set()

    entries: set[str] = set()
    with open(ALLOWLIST_PATH, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Take first whitespace-delimited token
            token = stripped.split()[0]
            # Validate format: path:number
            if ":" in token and token.rsplit(":", 1)[1].isdigit():
                entries.add(token)
    return entries


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestPowerShellEncoding:
    """Every Set-Content / Out-File call in tracked PS files must pass
    an explicit ``-Encoding`` parameter."""

    def test_all_calls_have_encoding(self):
        ps_files = _tracked_ps_files()
        allowlist = _load_allowlist()
        offenses: list[str] = []

        for psf in ps_files:
            path = REPO_ROOT / psf
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")

            for i, line in enumerate(lines):
                for m in _CMDLET_RE.finditer(line):
                    if not _statement_has_encoding(lines, i, m.start()):
                        key = f"{psf}:{i + 1}"
                        if key not in allowlist:
                            offenses.append(key)

        assert not offenses, (
            f"Found {len(offenses)} Set-Content/Out-File call(s) without -Encoding.\n"
            f"Add them to {ALLOWLIST_PATH.relative_to(REPO_ROOT)} with a reason, "
            f"or add -Encoding to the call.\n"
            + "\n".join(f"  {o}" for o in offenses)
        )

    def test_allowlist_entries_are_valid(self):
        """Sanity: every allowlist entry references a real file and line."""
        ps_files = {str(p) for p in _tracked_ps_files()}
        allowlist = _load_allowlist()

        bad: list[str] = []
        for entry in allowlist:
            path, line_str = entry.rsplit(":", 1)
            if path not in ps_files:
                bad.append(f"{entry} — file not in tracked PS files")
                continue
            # Verify the line actually contains Set-Content or Out-File
            full_path = REPO_ROOT / path
            if not full_path.exists():
                bad.append(f"{entry} — file does not exist on disk")
                continue
            lines = full_path.read_text(encoding="utf-8").split("\n")
            line_num = int(line_str)
            if line_num > len(lines) or not _CMDLET_RE.search(lines[line_num - 1]):
                bad.append(
                    f"{entry} — line {line_num} has no Set-Content/Out-File; "
                    "allowlist entry is stale"
                )

        assert not bad, (
            f"{len(bad)} invalid allowlist entr(ies):\n" + "\n".join(f"  {b}" for b in bad)
        )

    def test_allowlist_entries_have_comments(self):
        """Every allowlist entry must have a trailing # comment explaining why.

        An uncommented entry is a stale or cargo-culted exemption.  Failing
        CI forces the author to add a rationale comment, which in turn makes
        audits and future cleanups possible.
        """
        if not ALLOWLIST_PATH.exists():
            return

        uncommented: list[str] = []
        with open(ALLOWLIST_PATH, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Line is an actual entry — split on first space to separate
                # the path:line token from potential trailing content.
                token = stripped.split()[0]
                rest = stripped[len(token):].strip()
                if not rest.startswith("#"):
                    uncommented.append(token)

        assert not uncommented, (
            f"{len(uncommented)} allowlist entr(ies) without a comment:\n"
            + "\n".join(f"  {e}" for e in uncommented)
            + "\n\nAdd a trailing # comment explaining why each entry is exempt."
        )
