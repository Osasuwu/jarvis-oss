"""AFK-fit static path-grep helper for /to-tickets and /triage (#642, split per #1708).

Question 1 of the four-question AFK-fit checklist is static: does any
declared-changed file match a protected-path glob for the target repo?
config/protected-paths.json holds two buckets per repo:

- ``hitl``    — identity/security config, a categorical security boundary.
                Any match -> class 3 (afk:3-human), hard refusal.
- ``guarded`` — shared surfaces with off-repo consumers, recoverable via a
                locked plan. Any match -> class 2 (afk:2-plan).

No match in either bucket falls through to Q2-Q4 LLM judgement (documented
in each skill's own SKILL.md, not reimplemented here).
"""

from __future__ import annotations

import fnmatch
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.plan_classifier import label_for  # noqa: E402


def load_protected_paths(path: str | Path) -> dict[str, dict[str, list[str]]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    config: dict[str, dict[str, list[str]]] = {}
    for repo, buckets in data.items():
        if repo.startswith("_"):
            continue
        if not isinstance(buckets, dict):
            raise ValueError(
                f"config/protected-paths.json: {repo!r} uses the legacy flat-list "
                "schema (pre-#1708) — expected a {'hitl': [...], 'guarded': [...]} object"
            )
        config[repo] = {
            "hitl": list(buckets.get("hitl", [])),
            "guarded": list(buckets.get("guarded", [])),
        }
    return config


@dataclass(frozen=True)
class ClassVerdict:
    cls: int | None
    bucket: str | None
    label: str | None
    matched_files: tuple[str, ...]
    reason: str


def classify_static_paths(
    declared_files: list[str],
    repo: str,
    config: dict[str, dict[str, list[str]]],
) -> ClassVerdict:
    if repo not in config:
        return ClassVerdict(
            cls=None,
            bucket=None,
            label=None,
            matched_files=(),
            reason="unknown repo, judge manually",
        )

    for bucket, cls in (("hitl", 3), ("guarded", 2)):
        globs = config.get(repo, {}).get(bucket, [])
        matched = _matched_files(declared_files, globs)
        if matched:
            return ClassVerdict(
                cls=cls,
                bucket=bucket,
                label=label_for(cls),
                matched_files=matched,
                reason=f"matched {bucket} path(s): {', '.join(matched)}",
            )

    return ClassVerdict(
        cls=None,
        bucket=None,
        label=None,
        matched_files=(),
        reason="no protected-path match; fall through to Q2-Q4",
    )


def _matched_files(declared_files: list[str], globs: list[str]) -> tuple[str, ...]:
    if not globs:
        return ()
    matched: list[str] = []
    for declared in declared_files:
        norm = declared.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        for glob in globs:
            if _matches(norm, glob):
                matched.append(declared)
                break
    return tuple(matched)


def _matches(path: str, glob: str) -> bool:
    if glob.endswith("/**"):
        prefix = glob[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if "/**/" in glob:
        head, tail = glob.split("/**/", 1)
        if not path.startswith(head + "/"):
            return False
        return fnmatch.fnmatchcase(path[len(head) + 1 :], "*/" + tail) or fnmatch.fnmatchcase(
            path[len(head) + 1 :], tail
        )
    return fnmatch.fnmatchcase(path, glob)
