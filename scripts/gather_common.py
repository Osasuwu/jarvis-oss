"""Shared I/O types and defaults for status_gather.py and weekly_release_gather.py (#1662).

Extracted out of status_gather.py (#1801) once status_gather.py itself was
retired — weekly_release_gather.py still depends on these symbols.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

# ============================================================================
# Provenance
# ============================================================================


@dataclass
class Provenance:
    """Provenance stamp for one gathered source.

    Fields:
        ran: True if the gather attempted this source.
        ok: True if the source returned data without error.
        input_rows: Number of result rows (-1 for non-row sources like git).
        age: Seconds since data was gathered (None if !ran).
    """

    ran: bool = False
    ok: bool = False
    input_rows: int = -1
    age: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "ok": self.ok,
            "input_rows": self.input_rows,
            "age": self.age,
        }


# ============================================================================
# Injectable I/O callback types
# ============================================================================

# run_gh(repo, args) -> dict with {stdout, stderr, returncode}
RunGhFn = Callable[[str, list[str]], dict]

# query_supabase(url, key, table, params) -> list[dict] | None
QuerySupabaseFn = Callable[[str, str, str, dict], list[dict] | None]

# now_fn() -> float (epoch seconds)
NowFn = Callable[[], float]


# ============================================================================
# Default I/O implementations
# ============================================================================


def _default_run_gh(repo: str, args: list[str]) -> dict:
    # `gh api` addresses the repo through the URL path (repos/<owner>/<name>/...)
    # and rejects a trailing `--repo` flag ("unknown flag: --repo") — appending
    # it unconditionally made every milestone gather fail. Every other gh
    # subcommand needs `--repo` to target the right repo.
    cmd = ["gh", *args] if args and args[0] == "api" else ["gh", *args, "--repo", repo]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"stdout": "", "stderr": str(exc), "returncode": -1}


def resolve_jarvis_home(jarvis_home: str = "") -> str:
    """Resolve the jarvis repo root: explicit arg > $JARVIS_HOME > `git
    rev-parse --show-toplevel` > CWD.

    `git rev-parse` keys off the *current* repo, so a call from another repo
    (e.g. redrobot) would resolve to the wrong toplevel and degrade the
    gather. `$JARVIS_HOME` pins the jarvis root regardless of CWD. Shared by
    status_gather.gather() and weekly_release_gather.gather() (#1662 review —
    the two gathers had drifted into a verbatim-duplicated inline block).
    """
    if jarvis_home:
        return jarvis_home

    jarvis_home = os.environ.get("JARVIS_HOME", "").strip()
    if jarvis_home:
        return jarvis_home

    try:
        git_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if git_result.returncode == 0:
            jarvis_home = git_result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    return jarvis_home or os.getcwd()
