"""Session context loader for SessionStart hook.

Queries Supabase directly (no MCP) and prints formatted memory + goals.
Output is injected into Claude's context automatically by the hook.

Usage (in hook):  python scripts/session-context.py
Self-bootstraps into venv — works from any Python.

Compact resume recovery (Phase 2, #279):
When the SessionStart hook fires with source=compact, this script also
loads the pre-compact snapshot written by `scripts/pre-compact-backup.py`
(Supabase `session_snapshot_<session_id>` or local
`.claude/session-snapshots/<session_id>.md`) and prepends it under
`## Pre-Compact Recovery`. Snapshots older than
PRE_COMPACT_FRESHNESS_MINUTES are ignored — they belong to a prior run
that happened to reuse the same session_id.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# Guard: only re-exec when run as script. When imported (e.g. by tests via
# importlib with a non-"__main__" module name), skip the re-exec so the
# module's top-level sys.exit doesn't kill pytest collection.
if (
    __name__ == "__main__"
    and _venv_py.exists()
    and Path(sys.executable).resolve() != _venv_py.resolve()
):
    # Forward argv (e.g. --smoke) across the re-exec — without this the flag is
    # silently dropped and the venv child runs the full script (#963 review).
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]))

# ---------------------------------------------------------------------------
# Now running under venv — safe to import dependencies
# ---------------------------------------------------------------------------
from dotenv import load_dotenv

for _env in [_root / ".env", _root.parent / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break

from supabase import create_client

# Ensure UTF-8 output on Windows (cp1251 can't handle Cyrillic in some contexts)
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")


def _load_project_repos() -> dict[str, str]:
    """Map bare repo name → ``owner/repo`` from ``config/repos.conf``.

    Keeps session-context owner-agnostic: whatever repos you list in
    repos.conf become the "known projects" that trigger context injection
    and milestone-sweep checks. No hardcoded owner. Falls back to this
    checkout's own directory name if the config is missing or empty, so a
    fresh clone still recognises its own root.
    """
    mapping: dict[str, str] = {}
    conf = _root / "config" / "repos.conf"
    try:
        for line in conf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slug = line.split()[0]  # "owner/repo" (drop trailing project=N)
            if "/" in slug:
                mapping[slug.split("/", 1)[1]] = slug
    except OSError:
        pass
    if not mapping:
        # No configured repos yet — recognise this checkout's own folder.
        mapping[_root.name] = _root.name
    return mapping


_PROJECT_REPO: dict[str, str] = _load_project_repos()

KNOWN_PROJECTS = set(_PROJECT_REPO)

# Pre-compact snapshots older than this are treated as stale. They most
# likely belong to a different run that happened to reuse the same
# session_id — Claude Code does reuse ids across `--resume` invocations.
PRE_COMPACT_FRESHNESS_MINUTES = 30


def _detect_project():
    """Return (project_name, project_root) for the current cwd, or (None, None).

    Scans all path components (not just the basename) so worktree sessions
    (`<repo>/.claude/worktrees/<name>`) and subdirectory cwds resolve to the
    containing repo. Rightmost match wins. `project_root` points at the
    directory whose CONTEXT.md belongs to this session: the worktree root
    when cwd is inside `<repo>/.claude/worktrees/<name>` (the worktree has
    its own checkout, possibly with uncommitted CONTEXT.md edits), else the
    repo directory itself.
    """
    try:
        parts = Path(os.getcwd()).parts
    except Exception:
        return None, None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() in KNOWN_PROJECTS:
            root = Path(*parts[: i + 1])
            rest = parts[i + 1 :]
            if len(rest) >= 3 and rest[0] == ".claude" and rest[1] == "worktrees":
                root = root.joinpath(*rest[:3])
            return parts[i].lower(), root
    return None, None


def _read_hook_input() -> dict:
    """Parse SessionStart hook input from stdin.

    Claude Code pipes a JSON object with session_id, transcript_path, cwd,
    hook_event_name, and a source/matcher field when the hook fires. When
    this script runs standalone (no pipe), stdin is a TTY and we return
    an empty dict so the rest of main() proceeds unchanged.
    """
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_compact_resume(hook: dict) -> bool:
    """True when this SessionStart invocation is Claude Code resuming post-compact.

    Claude Code surfaces the matcher under different keys depending on the
    hook event (`source` for SessionStart, `matcher` for PreCompact). We
    check both and a few synonyms to avoid version-coupling.
    """
    event = (hook.get("hook_event_name") or "").strip()
    if event and event != "SessionStart":
        return False
    # `source` is the documented SessionStart field; `matcher` is what the
    # Jarvis hooks.settings block uses; guard against future renames.
    candidates = (
        hook.get("source"),
        hook.get("matcher"),
        hook.get("trigger"),
    )
    return any(str(c).lower() == "compact" for c in candidates if c)


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_session_id(session_id) -> str | None:
    """Return session_id if it matches the allowlist, else None.

    session_id arrives over stdin from Claude Code. Before using it to
    build a filesystem path or a Supabase `name` we constrain it to an
    allowlist (alphanum + `_-`) so a malformed/hostile value can't escape
    the snapshots directory or collide with unrelated memory rows.
    """
    if not isinstance(session_id, str):
        return None
    sid = session_id.strip()
    if not sid or not _SESSION_ID_RE.match(sid):
        return None
    return sid


def _load_snapshot_from_supabase(client, session_id: str, project: str | None = None):
    """Return snapshot content if `session_snapshot_<session_id>` is fresh, else None.

    Phase 1 upserts snapshots on `(project, name)` — two projects can share
    a `session_id` and keep separate rows. When `project` is known, filter
    on it to avoid picking up a sibling row. `.order(..., desc=True)` keeps
    selection deterministic even when the filter leaves multiple rows.
    """
    from datetime import datetime, timedelta, timezone

    sid = _safe_session_id(session_id)
    if not sid:
        return None
    name = f"session_snapshot_{sid}"
    # Project-filtered lookup first; on miss, retry unfiltered — snapshots
    # written before worktree-aware project detection landed under
    # project=NULL, and `name` embeds the session UUID so a cross-project
    # false positive is practically impossible.
    filters = [project, None] if project else [None]
    result = None
    try:
        for proj in filters:
            query = (
                client.table("memories")
                .select("content, updated_at")
                .eq("name", name)
                .is_("deleted_at", "null")
            )
            if proj:
                query = query.eq("project", proj)
            result = query.order("updated_at", desc=True).limit(1).execute()
            if result.data:
                break
    except Exception as e:
        print(f"[session-context] snapshot query failed: {e}", file=sys.stderr)
        return None
    if result is None or not result.data:
        return None
    row = result.data[0]
    ts = _parse_ts(row.get("updated_at"))
    if ts is not None:
        age = datetime.now(timezone.utc) - ts
        if age > timedelta(minutes=PRE_COMPACT_FRESHNESS_MINUTES):
            return None
    content = row.get("content")
    return content if content else None


def _load_snapshot_from_local(session_id: str):
    """Return snapshot content from `.claude/session-snapshots/<session_id>.md` if fresh."""
    from datetime import datetime, timedelta, timezone

    sid = _safe_session_id(session_id)
    if not sid:
        return None
    path = _root / ".claude" / "session-snapshots" / f"{sid}.md"
    if not path.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        mtime = None
    if mtime is not None:
        age = datetime.now(timezone.utc) - mtime
        if age > timedelta(minutes=PRE_COMPACT_FRESHNESS_MINUTES):
            return None
    try:
        return path.read_text(encoding="utf-8") or None
    except Exception as e:
        print(f"[session-context] local snapshot read failed: {e}", file=sys.stderr)
        return None


def _format_recovery_section(payload: str, session_id: str) -> str:
    sid = _safe_session_id(session_id) or "unknown-session"
    return (
        "## Pre-Compact Recovery\n"
        "Compaction happened earlier — deterministic snapshot pointer below. "
        f"The full row is `session_snapshot_{sid}` via `memory_get`.\n\n"
        f"{payload}"
    )


def _extract_snapshot_header(snapshot: str) -> str | None:
    """Return the verbatim `# Session Snapshot — <sid>` line, if present.

    `/end` resolves the session id from exactly this line, so the pointer
    payload keeps it byte-stable (#1272 AC2).
    """
    for ln in snapshot.splitlines():
        ln = ln.strip()
        if ln.startswith("# Session Snapshot — "):
            return ln
    return None


def _extract_branch(snapshot: str) -> str | None:
    """Return the git branch the snapshot recorded, if present.

    The snapshot writes `- **git branch:** `<branch>` — match the label
    anywhere in the line so the leading list marker doesn't break extraction.
    """
    for ln in snapshot.splitlines():
        ln = ln.strip()
        if "**git branch:**" in ln:
            return ln.split("**git branch:**", 1)[1].strip().strip("`")
    return None


def _read_compaction_count(session_id) -> int | None:
    """Read the per-session compaction generation counter, or None.

    Mirrors the PreCompact hook's counter file
    (`~/.claude/compaction-counts/<sanitized_session_id>.txt`,
    `pre-compact-backup.py` → `_bump_compaction_count`). Best-effort:
    a missing/corrupt file yields None, never an exception.
    """
    sid = _safe_session_id(session_id)
    if not sid:
        return None
    path = Path.home() / ".claude" / "compaction-counts" / f"{sid}.txt"
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _count_session_decisions(client, session_id) -> int | None:
    """Count decision_made episodes stamped with this session id.

    #1269 stamps the harness session id into `payload.session_id` of every
    `record_decision` episode, so the count is a Postgres query keyed by
    session id — no snapshot-text parsing (the parser reads text blocks only).
    Best-effort: query failure yields None, never an exception.
    """
    sid = _safe_session_id(session_id)
    if not sid:
        return None
    try:
        result = (
            client.table("episodes")
            .select("id", count="exact")
            .eq("kind", "decision_made")
            .eq("payload->>session_id", sid)
            .limit(1)
            .execute()
        )
        return result.count if result is not None else None
    except Exception:
        return None


def _compose_recovery_payload(
    snapshot: str,
    session_id: str,
    *,
    generation: int | None = None,
    decision_count: int | None = None,
    cwd: str | None = None,
    transcript_path: str | None = None,
) -> str:
    """Compose the pointer + deterministic minimum snapshot payload (#1272).

    Replaces the verbatim snapshot re-injection: the durable facts move to
    deterministic fields and everything else becomes a pointer to the full
    snapshot row (`session_snapshot_<sid>` via `memory_get`) / transcript.
    The `# Session Snapshot — <sid>` header is kept byte-stable so `/end`
    still resolves the session id.
    """
    sid = _safe_session_id(session_id) or "unknown-session"
    header = _extract_snapshot_header(snapshot) or f"# Session Snapshot — {sid}"
    # Plain labels (no markdown bold): the transcript path is long, and the
    # payload must stay ≤ ~300 chars even with a UUID-length session id.
    lines = [header, ""]
    if generation is not None:
        lines.append(f"- Gen: {generation}")
    if decision_count is not None:
        lines.append(f"- Decisions: {decision_count}")
    if cwd:
        lines.append(f"- cwd: `{cwd}`")
    branch = _extract_branch(snapshot)
    if branch:
        lines.append(f"- branch: `{branch}`")
    if transcript_path:
        lines.append(f"- Transcript: `{transcript_path}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Budget-aware assembly (#1271 C.1 rows 4-5, decisions 7fe96e01/6c32b280/
# 02b48a04). Units are CHARS everywhere (not bytes). 9,500 leaves 500 chars of
# headroom against the 10,000-char harness cap; the self-log makes spill a
# trendable signal instead of a silent truncation.
# ---------------------------------------------------------------------------

ASSEMBLY_BUDGET_CHARS = 9500

# Section drop-priority, HIGHEST = 0 (dropped last) → higher number = dropped
# first. Pre-Compact Recovery outranks everything; the durable layer
# (always-load, working state, owner tasks) outranks the one-line
# reminders, so a startup session still delivers it under budget pressure
# (#1271 AC2). Owner tasks sit above goals (#1392 AC3): an escalation is
# an actionable now-item dispatch() already decided the owner must see,
# not a standing reminder — dropping it under budget pressure would
# defeat the point of writing the row at all, and unlike
# user_profile/goals/reminders below it is NOT skipped on compact resume
# either (#1460): a fresh escalation can land mid-session and the
# just-compacted summary would not carry it, so this section still needs
# to reach the ladder every time. On a compact resume, user_profile/goals/
# reminders are skipped entirely before they ever reach this ladder — they'd
# duplicate what the just-compacted summary + Pre-Compact Recovery pointer
# already carry, so their priority numbers below only govern startup-trigger
# drop order.
# The former CONTEXT.md push (project_context) is gone — Invariants +
# Glossary index now ride @import instead (#1417), which never enters this
# priority ladder.
_PRIORITY_RECOVERY = 0
_PRIORITY_ALWAYS_LOAD = 1
_PRIORITY_USER_PROFILE = 2
_PRIORITY_WORKING_STATE = 3
_PRIORITY_OWNER_TASKS = 4
_PRIORITY_GOALS = 5
_PRIORITY_REMINDER = 6

_BANNER = "MEMORY CONTEXT (auto-loaded — do NOT re-fetch with MCP tools)"
_FOOTER = "=" * 60
_BANNER_BLOCK = f"{_FOOTER}\n{_BANNER}\n{_FOOTER}"

# Self-log location. `.claude/logs/` is gitignored, so the log is
# device-local and never committed.
_SELF_LOG_PATH = _root / ".claude" / "logs" / "session-context-size.jsonl"


def _render(kept: list, dropped_names: list[str]) -> str:
    """Assemble the final output string. The banner block, the reserved
    `dropped:` line and section separators are all accounted here, so
    `len(returned)` is exactly the char count the budget enforces."""
    parts = []
    if dropped_names:
        parts.append("dropped: " + ", ".join(dropped_names))
    parts.extend(rendered for _, _, rendered, _ in kept)
    body = "\n\n".join(parts)
    return f"{_BANNER_BLOCK}\n\n{body}\n\n{_FOOTER}"


def assemble_sections(sections, budget_chars: int = ASSEMBLY_BUDGET_CHARS):
    """Drop lowest-priority sections until the rendered output fits the budget.

    ``sections`` is a list of ``(priority, name, rendered, ids)`` tuples in
    OUTPUT order. priority 0 = highest (dropped last); a higher number = lower
    priority (dropped first). ``ids`` are the memory ids the section was built
    from — only KEPT sections' ids are returned, so a dropped section is never
    fed to ``_touch_accessed`` (#1271 C.1 row 6).

    Returns ``(output, dropped_names, emitted_ids, emitted_chars)`` where
    ``dropped_names`` lists dropped sections in drop order and ``emitted_ids``
    is the deduped, order-preserving union of kept sections' ids.
    """
    kept = list(sections)
    dropped_names: list[str] = []
    while True:
        output = _render(kept, dropped_names)
        if len(output) <= budget_chars or len(kept) <= 1:
            break
        # Lowest priority (max number) first; ties → later output position
        # drops first, so the last-appended reminder is the first to go.
        drop_idx = max(range(len(kept)), key=lambda i: (kept[i][0], i))
        dropped_names.append(kept[drop_idx][1])
        del kept[drop_idx]
    emitted_ids = list(dict.fromkeys(sid for _, _, _, ids in kept for sid in ids))
    return output, dropped_names, emitted_ids, len(output)


def _self_log(
    emitted_chars: int,
    dropped_names: list[str],
    compact_resume: bool,
    session_id: str | None = None,
    project: str | None = None,
) -> None:
    """Append one JSON line per run to the size self-log (#1271 C.1 row 5).

    Log location: ``.claude/logs/session-context-size.jsonl`` under the repo
    root. Spill (emitted_chars > budget) becomes observable and trendable.
    Never raises — a log write failure must not break session start.

    ``session_id`` + ``project`` are the pull-rate join key (#1275): this log
    is the denominator (context assembled), transcript pulls are the numerator
    (context actually used), and without a key there is nothing to join them
    on. The log is written per-WORKTREE, not per-device, so "which run was
    this" cannot be recovered from the file's location either. Both keys are
    always present, null when unknown — a stable row schema lets the reader
    join without probing, and null is honest about an unattributable run in a
    way a missing key is not.

    ``(session_id, project)`` is NOT unique per run. Claude Code reuses
    session ids across ``--resume`` invocations (same reason
    ``PRE_COMPACT_FRESHNESS_MINUTES`` exists), so one resumed session emits
    several rows sharing the pair. ``ts`` is the third component: a reader
    computing pull-rate must key on ``(session_id, project, ts)`` and window
    transcript pulls against the row's ``ts``, or it will conflate distinct
    resumed runs into one inflated denominator.
    """
    from datetime import datetime, timezone

    # session_id arrives over stdin from Claude Code; run it through the same
    # allowlist used everywhere else it acts as a key. A value that fails it
    # cannot join against anything, so it logs as null rather than as garbage.
    safe_session = _safe_session_id(session_id) if session_id else None
    try:
        _SELF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": safe_session,
            "project": project or None,
            "emitted_chars": emitted_chars,
            "budget_chars": ASSEMBLY_BUDGET_CHARS,
            "dropped": dropped_names,
            "compact_resume": compact_resume,
        }
        with open(_SELF_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _run_smoke_check() -> None:
    """Dry health probe for the install health-check — NO Supabase network call.

    The install manifest runs ``session-context.py --smoke`` as a health
    command. Reaching this function already proves the load path is sound: the
    venv re-exec at module top succeeded and every top-level import (dotenv,
    supabase) resolved — a broken venv or missing dependency crashes earlier
    with a non-zero exit the health-check catches. We deliberately skip the
    Supabase query so the health-check does not hinge on network reachability,
    nor on the owner having populated ``.env`` yet. Before #963 the command
    claimed "dry load" but ran the full script, network calls included.

    Always exits 0; credential absence is reported but is config state, not an
    install failure (the installer owns the venv, not the owner's ``.env``).
    """
    have_creds = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))
    creds_note = "creds present" if have_creds else "creds absent (config pending)"
    # Emit to stderr: the installer's health-check runner redirects child stdout
    # to DEVNULL and captures only stderr (#963). A stdout print would be
    # silently discarded, hiding this diagnostic from the install log.
    print(
        f"[session-context] smoke ok — venv + deps loaded, no network; {creds_note}",
        file=sys.stderr,
    )


def main():
    # --smoke: dry health probe, short-circuits before any Supabase network
    # call (#963). Checked before _read_hook_input so it never blocks on stdin.
    if "--smoke" in sys.argv[1:]:
        _run_smoke_check()
        return

    hook_input = _read_hook_input()
    compact_resume = _is_compact_resume(hook_input)
    session_id = hook_input.get("session_id") or hook_input.get("sessionId") or ""

    # Resolved before the no-credentials early return below, not after it:
    # that branch also self-logs, and a row without `project` is unjoinable
    # (#1275). It is a pure cwd inspection — no network, no Supabase.
    project, _project_root = _detect_project()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[session-context] SUPABASE_URL/KEY not set", file=sys.stderr)
        # Still try local fallback on compact resume — a disconnected dev
        # shouldn't lose pre-compact context entirely. Routed through the
        # assembler so the size self-log records the fallback too.
        if compact_resume and session_id:
            snap = _load_snapshot_from_local(session_id)
            if snap:
                payload = _compose_recovery_payload(
                    snap,
                    session_id,
                    generation=_read_compaction_count(session_id),
                    decision_count=None,
                    cwd=hook_input.get("cwd") or "",
                    transcript_path=hook_input.get("transcript_path") or "",
                )
                sections = [
                    (
                        _PRIORITY_RECOVERY,
                        "recovery",
                        _format_recovery_section(payload, session_id),
                        [],
                    )
                ]
                output, dropped_names, emitted_ids, emitted_chars = assemble_sections(sections)
                _self_log(
                    emitted_chars,
                    dropped_names,
                    compact_resume,
                    session_id=session_id,
                    project=project,
                )
                print(output)
        return

    try:
        client = create_client(url, key)
    except Exception as e:
        print(f"[session-context] Supabase connect failed: {e}", file=sys.stderr)
        return

    # Each section is (priority, name, rendered, memory_ids): priority 0 =
    # highest (dropped last), higher number = lower priority. Output order is
    # the append order here; ids feed _touch_accessed for KEPT sections only.
    sections = []

    # 0. Pre-Compact Recovery — prepended before everything else on compact
    #    resume. Supabase first, local fallback second. Freshness guarded to
    #    avoid cross-run pollution when Claude Code reuses session_ids.
    #    #1272: the push is now the pointer + deterministic minimum (≤~300
    #    chars); the full snapshot stays in the row for /end's memory_get pull.
    if compact_resume and session_id:
        snapshot = _load_snapshot_from_supabase(client, session_id, project)
        if not snapshot:
            snapshot = _load_snapshot_from_local(session_id)
        if snapshot:
            payload = _compose_recovery_payload(
                snapshot,
                session_id,
                generation=_read_compaction_count(session_id),
                decision_count=_count_session_decisions(client, session_id),
                cwd=hook_input.get("cwd") or "",
                transcript_path=hook_input.get("transcript_path") or "",
            )
            sections.append(
                (
                    _PRIORITY_RECOVERY,
                    "recovery",
                    _format_recovery_section(payload, session_id),
                    [],
                )
            )

    # 1. User memories — who is the owner. Skipped on compact resume (#1460):
    #    duplicates what the just-compacted transcript summary + Pre-Compact
    #    Recovery pointer already carry; startup still gets the full block.
    #    Compact one-line format: full bodies rot the context; names +
    #    descriptions are enough to remind Jarvis what exists. Full content
    #    via memory_get on demand.
    if not compact_resume:
        section, ids = _query_memories(client, mem_type="user", limit=2, compact=True)
        if section:
            sections.append(
                (
                    _PRIORITY_USER_PROFILE,
                    "user_profile",
                    "## User Profile\n" + section,
                    ids,
                )
            )

    # 2. Always-load memories — evergreen rules not tied to any single project.
    #    Everything else (feedback/decisions) is loaded task-aware via
    #    UserPromptSubmit hook (scripts/memory-recall-hook.py).
    #    Compact one-line format for the same reason as user profile.
    #    NOTE: ids deliberately NOT carried — always_load memories are never
    #    touched (#767) to avoid an access-bias feedback loop.
    section, _ids = _query_always_load(client, compact=True)
    if section:
        sections.append(
            (
                _PRIORITY_ALWAYS_LOAD,
                "always_load",
                "## Always-Load Rules\n" + section,
                [],
            )
        )

    # 3. Working state — ONLY when session is inside a known project dir.
    #     In a non-project cwd (e.g. scheduled research) working_state is noise.
    if project:
        section, ids = _query_memories(
            client,
            mem_type="project",
            limit=1,
            extra_filter=lambda q: q.eq("name", f"working_state_{project}"),
        )
        if section:
            sections.append(
                (
                    _PRIORITY_WORKING_STATE,
                    "working_state",
                    f"## Working State ({project})\n" + section,
                    ids,
                )
            )

    # 3b. Owner-assigned task_queue rows (always, including compact resume) —
    #     escalations dispatch() wrote for the principal (#1392 AC3). Not
    #     gated on `project` since an escalation can concern any repo, and
    #     not skipped on compact resume like goals/reminders below (#1460)
    #     since a fresh escalation may have landed mid-session and the
    #     just-compacted summary would not carry it.
    owner_tasks_section = _query_owner_tasks(client)
    if owner_tasks_section:
        sections.append((_PRIORITY_OWNER_TASKS, "owner_tasks", owner_tasks_section, []))

    # 4. Active goals. Skipped on compact resume (#1460) — same duplication
    #    rationale as section 1.
    if not compact_resume:
        goal_section = _query_goals(client)
        if goal_section:
            sections.append((_PRIORITY_GOALS, "goals", goal_section, []))

    # 5. One-line reminders — lowest priority, first to drop under budget.
    #    Skipped entirely on compact resume (#1460): these are session-start
    #    nudges, redundant the moment a session is already mid-work.

    if not compact_resume:
        # 5a. Pending review count — one-line reminder when candidates await review
        pending_count = _query_pending_review_count(client, project)
        if pending_count > 0:
            sections.append(
                (
                    _PRIORITY_REMINDER,
                    "pending_review",
                    f"**Pending memory candidates:** {pending_count} (run `/learn` to review)",
                    [],
                )
            )

        # 5b. Architecture sweep recommendation -- recently closed milestones with
        #     enough closed slices to warrant a /improve-codebase-architecture run.
        #     Only fires in a known project dir where the sweep is meaningful.
        if project:
            sweep_section = _check_milestone_sweep(repo=_PROJECT_REPO.get(project))
            if sweep_section:
                sections.append((_PRIORITY_REMINDER, "milestone_sweep", sweep_section, []))

        # 5c. Mirror drift warning — show when ~/.claude/.jarvis-version is
        #     behind repo HEAD. Fires anywhere the script can read both SHAs
        #     (inside the jarvis repo). Silent when absent, in sync, or when
        #     git is unavailable (not a git dir / no permission).
        drift_section = _check_mirror_drift()
        if drift_section:
            sections.append((_PRIORITY_REMINDER, "mirror_drift", drift_section, []))

        # 5d. Managed-venv drift check + self-heal (#1312) — hash+import-probe
        #     against the registered "main" env; silent when in sync, otherwise
        #     a visible healed/heal-failed block. Never raises.
        env_drift_section = _check_env_drift()
        if env_drift_section:
            sections.append((_PRIORITY_REMINDER, "env_drift", env_drift_section, []))

        # 5e. MCP bootstrap failure breadcrumbs (#1312) — surfaces
        #     .claude/mcp-failures.jsonl entries newer than 24h, deduped so a
        #     failure never re-reports across sessions.
        mcp_failures_section = _check_mcp_failures()
        if mcp_failures_section:
            sections.append((_PRIORITY_REMINDER, "mcp_failures", mcp_failures_section, []))

    if not sections:
        print("[session-context] No memory data available.")
        return

    # Budget-aware assembly (#1271): drops lowest-priority sections until the
    # output fits ASSEMBLY_BUDGET_CHARS; anything dropped is named on the
    # `dropped:` line. Only KEPT sections' memory ids are touched.
    output, dropped_names, emitted_ids, emitted_chars = assemble_sections(sections)
    _touch_accessed(client, emitted_ids)
    _self_log(
        emitted_chars,
        dropped_names,
        compact_resume,
        session_id=session_id,
        project=project,
    )
    if emitted_chars > ASSEMBLY_BUDGET_CHARS:
        detail = ", ".join(dropped_names) if dropped_names else "no section dropped"
        print(
            f"[session-context] WARNING: emitted {emitted_chars} chars exceeds "
            f"the {ASSEMBLY_BUDGET_CHARS}-char budget ({detail})",
            file=sys.stderr,
        )
    print(output)


# ---------------------------------------------------------------------------
# Memory queries
# ---------------------------------------------------------------------------

_MEMORY_COLS = "id, name, type, project, description, content, tags, updated_at"


def _query_memories(client, *, mem_type, limit, extra_filter=None, compact=False):
    """Query memories table with type filter.

    Returns (formatted_text, ids) — ids are used to bump last_accessed_at.
    When compact=True, renders one line per memory (name + description)
    instead of full content. Use compact for always-loaded reminders, full
    for anything Jarvis actually needs to read verbatim (working state).
    """
    try:
        q = client.table("memories").select(_MEMORY_COLS).eq("type", mem_type)
        if extra_filter:
            q = extra_filter(q)
        result = q.order("updated_at", desc=True).limit(limit).execute()
        if result.data:
            fmt = _fmt_memory_compact if compact else _fmt_memory
            sep = "\n" if compact else "\n---\n"
            text = sep.join(fmt(m) for m in result.data)
            ids = [m["id"] for m in result.data if m.get("id")]
            return text, ids
    except Exception as e:
        print(f"[session-context] {mem_type} query failed: {e}", file=sys.stderr)
    return None, []


# always_load admission cap: DOCTRINE.md > Baseline carrier selection ranks
# always_load as the worst-position, always-pays carrier — reserved for
# dynamic/device-scoped/time-bounded content with no file home. Cap changes
# require a new record_decision citing the prior UUID in memories_used — keep
# this in sync with scripts/eval-recall.py's copy of the same constants
# (decision 3e6594f6-27da-45d9-96d8-516a46716425, #1252 AC3).
ALWAYS_LOAD_MAX_ENTRIES = 4
ALWAYS_LOAD_MAX_BYTES = 6000


def _query_always_load(client, *, compact=False):
    """Query memories tagged 'always_load' (evergreen, cross-project rules).

    Returns (formatted_text, ids). Enforced at read time against
    ALWAYS_LOAD_MAX_ENTRIES / ALWAYS_LOAD_MAX_BYTES (decision
    3e6594f6-27da-45d9-96d8-516a46716425) — over-tagged data degrades
    gracefully (truncated, not blocked) with a loud stderr warning.
    """
    try:
        result = (
            client.table("memories")
            .select(_MEMORY_COLS)
            .contains("tags", ["always_load"])
            .order("updated_at", desc=True)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None, []

        if len(rows) > ALWAYS_LOAD_MAX_ENTRIES:
            print(
                f"[session-context] always_load has {len(rows)} tagged memories, "
                f"cap is {ALWAYS_LOAD_MAX_ENTRIES} — truncating to the most recently "
                "updated. Untag the rest or move them to a file (DOCTRINE.md > "
                "Baseline carrier selection).",
                file=sys.stderr,
            )
            rows = rows[:ALWAYS_LOAD_MAX_ENTRIES]

        fmt = _fmt_memory_compact if compact else _fmt_memory
        sep = "\n" if compact else "\n---\n"
        kept = []
        total_bytes = 0
        for i, m in enumerate(rows):
            rendered = fmt(m)
            size = len(rendered.encode("utf-8"))
            if kept and total_bytes + size > ALWAYS_LOAD_MAX_BYTES:
                print(
                    f"[session-context] always_load byte budget ({ALWAYS_LOAD_MAX_BYTES}B) "
                    f"exceeded after {i}/{len(rows)} entries — truncating remainder.",
                    file=sys.stderr,
                )
                break
            kept.append((rendered, m))
            total_bytes += size

        text = sep.join(rendered for rendered, _ in kept)
        ids = [m["id"] for _, m in kept if m.get("id")]
        return text, ids
    except Exception as e:
        print(f"[session-context] always_load query failed: {e}", file=sys.stderr)
    return None, []


def _parse_ts(val):
    """Parse Supabase timestamp (ISO str or datetime) to aware UTC datetime, or None."""
    from datetime import datetime, timezone

    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _query_pending_review_count(client, project) -> int:
    """Query pending review candidates via memory_review_list RPC.

    Queries both project-specific and global (null-project) pools.
    Returns total count across both. On RPC failure (not yet deployed
    or unreachable), returns 0 — graceful degradation (#556).
    """
    total = 0

    # Project-scoped candidates (only when inside a known project)
    if project:
        try:
            result = client.rpc(
                "memory_review_list",
                {
                    "queue": "candidate",
                    "project_filter": project,
                    "limit_count": 1000,
                },
            ).execute()
            total += len(result.data or [])
        except Exception as e:
            print(
                f"[session-context] pending-review count failed ({project}): {e}", file=sys.stderr
            )

    # Global candidates (project IS NULL) — always query
    try:
        result = client.rpc(
            "memory_review_list",
            {
                "queue": "candidate",
                "project_filter": "",
                "limit_count": 1000,
            },
        ).execute()
        total += len(result.data or [])
    except Exception as e:
        print(f"[session-context] pending-review count failed (global): {e}", file=sys.stderr)

    return total


def _touch_accessed(client, ids):
    """Bump last_accessed_at via the same RPC server.py uses on recall.

    Best-effort / non-fatal: the call is synchronous (one REST round-trip,
    typically 50-200ms), but any exception is logged to stderr and swallowed
    so session start is never blocked by Supabase issues.
    """
    if not ids:
        return
    try:
        client.rpc("touch_memories", {"memory_ids": ids}).execute()
    except Exception as e:
        print(f"[session-context] touch_memories failed: {e}", file=sys.stderr)


def _fmt_memory(m):
    tags = f" [{', '.join(m.get('tags') or [])}]" if m.get("tags") else ""
    return (
        f"### {m['name']} ({m['type']}, {m.get('project') or 'global'}){tags}\n"
        f"*{m.get('description', '')}*\n"
        f"Updated: {m.get('updated_at', '?')}\n\n"
        f"{m['content']}"
    )


def _fmt_memory_compact(m):
    """One-line compact: `- <name>: <description>`. Full via memory_get."""
    desc = (m.get("description") or "").strip()
    if len(desc) > 140:
        desc = desc[:137] + "..."
    return f"- {m['name']}: {desc}"


# ---------------------------------------------------------------------------
# Goal queries
# ---------------------------------------------------------------------------


def _query_goals(client):
    """Query active goals, return formatted string or None."""
    try:
        result = (
            client.table("goals")
            .select("*")
            .eq("status", "active")
            .order("priority")
            .order("deadline", desc=False, nullsfirst=False)
            .execute()
        )
        if not result.data:
            return None
        goals = [_fmt_goal(g) for g in result.data]
        return f"## Active Goals ({len(result.data)})\n" + "\n".join(goals)
    except Exception as e:
        print(f"[session-context] goals query failed: {e}", file=sys.stderr)
        return None


def _fmt_goal(g):
    """One-line compact goal: title, slug, priority, progress, deadline.

    Full body (why/progress items/risks/focuses) is available via goal_get
    on demand — same principle as compact memory rendering. Keeping this
    in always-loaded context means 9+ goals used to burn ~3KB; now ~300B.
    """
    scope = g.get("project") or "cross-project"
    pct = g.get("progress_pct")
    pct_str = f"{pct}%" if isinstance(pct, (int, float)) else "—"
    deadline = f", due {g['deadline']}" if g.get("deadline") else ""
    desc = (g.get("why") or "").strip().splitlines()[0] if g.get("why") else ""
    if len(desc) > 100:
        desc = desc[:97] + "..."
    tail = f" — {desc}" if desc else ""
    return f"- [{g['priority']}] {g['title']} (`{g['slug']}` | {scope} | {pct_str}{deadline}){tail}"


# ---------------------------------------------------------------------------
# Owner-assigned task_queue reader (#1392 AC3)
#
# dispatch() (agents/orchestrator.py) writes an assignee='owner' task_queue
# row for every escalation, regardless of notification mode — but nothing
# ever read those rows back before this. TELEGRAM_NOW pings immediately;
# SESSIONSTART/PARK_MONDAY relied on a reader that didn't exist, so the row
# sat invisible in the DB. This surfaces pending owner rows on every session
# start.
# ---------------------------------------------------------------------------


def _query_owner_tasks(client):
    """Query pending owner-assigned task_queue rows, return formatted string or None."""
    try:
        result = (
            client.table("task_queue")
            .select("*")
            .eq("assignee", "owner")
            .eq("status", "pending")
            .order("priority", desc=True)
            .order("created_at")
            .execute()
        )
        if not result.data:
            return None
        tasks = [_fmt_owner_task(t) for t in result.data]
        return f"## Owner-Assigned Tasks ({len(result.data)})\n" + "\n".join(tasks)
    except Exception as e:
        print(f"[session-context] owner tasks query failed: {e}", file=sys.stderr)
        return None


def _fmt_owner_task(t):
    """One-line compact owner task: goal + why it was escalated."""
    reason = (t.get("escalated_reason") or "").strip()
    tail = f" — {reason}" if reason else ""
    return f"- {t['goal']}{tail}"


# ---------------------------------------------------------------------------
# Milestone-close detection for architecture sweep recommendation (issue #605)
# ---------------------------------------------------------------------------

# How far back (days) to consider a milestone "recently closed".
try:
    _MILESTONE_SWEEP_DAYS = int(os.environ.get("MILESTONE_SWEEP_DAYS", "7"))
except (ValueError, TypeError):
    _MILESTONE_SWEEP_DAYS = 7
# Minimum closed issues to qualify as a "capability-shipping" milestone.
try:
    _MILESTONE_SWEEP_MIN_SLICES = int(os.environ.get("MILESTONE_SWEEP_MIN_SLICES", "3"))
except (ValueError, TypeError):
    _MILESTONE_SWEEP_MIN_SLICES = 3


def _check_milestone_sweep(
    repo: str | None = None,
    days: int | None = None,
    min_slices: int | None = None,
    _now=None,
) -> str | None:
    """Check if any GitHub milestones were recently closed with enough slices
    to warrant an architecture-sweep recommendation.

    Returns a formatted hint string (e.g. "Milestone N closed M days ago —
    run /improve-codebase-architecture") or None when no milestone qualifies.

    Thresholds are configurable via env (MILESTONE_SWEEP_DAYS,
    MILESTONE_SWEEP_MIN_SLICES) or overridden per-call.
    """
    from datetime import datetime, timezone

    if repo is None:
        return None

    now = _now if _now is not None else datetime.now(timezone.utc)
    window_days = days if days is not None else _MILESTONE_SWEEP_DAYS
    min_count = min_slices if min_slices is not None else _MILESTONE_SWEEP_MIN_SLICES

    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/milestones?state=closed&per_page=100"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    try:
        milestones = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(milestones, list):
        return None

    # Collect milestones qualifying for a sweep recommendation.
    qualified: list[tuple[int, str, int, float]] = []
    for m in milestones:
        closed_at_str = m.get("closed_at")
        closed_issues = m.get("closed_issues", 0)
        if not closed_at_str or not isinstance(closed_issues, (int, float)):
            continue
        if closed_issues < min_count:
            continue
        try:
            closed_at = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        age_days = (now - closed_at).total_seconds() / 86400
        if age_days > window_days:
            continue
        qualified.append(
            (
                m.get("number", 0),
                m.get("title", f"Milestone #{m.get('number', '?')}"),
                int(closed_issues),
                age_days,
            )
        )

    if not qualified:
        return None

    # Sort by age (most recently closed first).
    qualified.sort(key=lambda x: x[3])
    lines: list[str] = []
    for num, title, count, age in qualified:
        age_str = f"{age:.0f}d"
        lines.append(
            f"- Milestone #{num} ({title}) closed {age_str} ago with "
            f"{count} closed slice{'s' if count != 1 else ''} — "
            f"consider `/improve-codebase-architecture`"
        )
    plural = "s" if len(qualified) > 1 else ""
    return f"## Architecture Sweep{plural}\n{chr(10).join(lines)}"


# ---------------------------------------------------------------------------
# Mirror drift detection (#753): compare ~/.claude/.jarvis-version vs HEAD
# ---------------------------------------------------------------------------

# Path to the version marker installed by the manifest installer.
_JARVIS_VERSION_PATH = Path.home() / ".claude" / ".jarvis-version"


def _check_mirror_drift(
    version_path: Path | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """Compare installed version marker against repo HEAD.

    Returns a formatted drift warning when the installed SHA differs from
    HEAD, when the marker is missing/empty (mirror never installed or
    install state unknown), or None when in sync / marker unreadable / git
    unavailable.

    A missing or empty marker used to return None (silent) — #1076 §2 asked
    for a loud warning specifically for this case: a device that never ran
    the installer has zero Tier-1 mirror content (DOCTRINE.md, CLAUDE.md,
    skills) and, before this change, nothing said so (decision
    b95ef864-4585-45d6-9baf-8979b29c983b, #1252 AC4). OSError-on-read stays
    silent — a distinct "unreadable/transient" failure class, not
    "never installed".

    Both parameters are injectable for testing: version_path defaults to
    ~/.claude/.jarvis-version, repo_root defaults to the discovered repo root.
    """
    vp = version_path if version_path is not None else _JARVIS_VERSION_PATH
    rr = repo_root if repo_root is not None else _root

    if not vp.exists():
        return (
            "## Mirror Drift\n"
            "⚠ No `~/.claude/.jarvis-version` marker found — the user-level "
            "mirror (CLAUDE.md, DOCTRINE.md, skills) may never have been "
            "installed on this device. Run `install.ps1 -Apply` or "
            "`install.sh --apply` to sync."
        )

    try:
        installed = vp.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not installed:
        return (
            "## Mirror Drift\n"
            "⚠ `~/.claude/.jarvis-version` marker is empty — mirror install "
            "state unknown. Run `install.ps1 -Apply` or `install.sh --apply` "
            "to sync."
        )

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=rr,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    if installed == head:
        return None

    try:
        count = subprocess.run(
            ["git", "rev-list", "--count", f"{installed}..HEAD"],
            cwd=rr,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        count = "?"

    return (
        "## Mirror Drift\n"
        f"⚠ Installed ({installed[:12]}) is {count} commit(s) behind "
        f"HEAD ({head[:12]}) — run `install.ps1 -Apply` or "
        f"`install.sh --apply` to sync."
    )


# ---------------------------------------------------------------------------
# Managed-venv drift detection + self-heal (#1312)
# ---------------------------------------------------------------------------


def _check_env_drift(env_name: str = "main", _env_sync_module=None) -> str | None:
    """Check the long-lived MCP venv via scripts/lib/env_sync.py and self-heal
    on drift (AC#3). Silent when in sync; never raises.
    """
    try:
        if _env_sync_module is not None:
            env_sync = _env_sync_module
        else:
            sys.path.insert(0, str(_root / "scripts" / "lib"))
            import env_sync

        env = env_sync.get_env(env_name)
        if env is None:
            return None

        result = env_sync.check(env)
        if result.in_sync:
            return None

        heal_result = env_sync.heal(env)
        if heal_result.success:
            old = (heal_result.old_hash or "none")[:12]
            new = (heal_result.new_hash or "?")[:12]
            return (
                "## Environment Drift — Healed\n"
                f"⚠ `{env.name}` venv was out of sync ({result.reason}) — "
                f"auto-healed ({old} → {new})."
            )
        return (
            "## Environment Drift — Heal Failed\n"
            f"⚠ `{env.name}` venv is out of sync ({result.reason}) and "
            f"auto-heal failed ({heal_result.reason}). Run manually:\n"
            f"`{env.venv_python} -m pip install -r {env.manifest}`\n"
            f"See log: {heal_result.log_path}"
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# MCP bootstrap failure breadcrumbs (#1312 AC#5)
# ---------------------------------------------------------------------------

_MCP_FAILURES_PATH = _root / ".claude" / "mcp-failures.jsonl"
_MCP_FAILURES_REPORTED_PATH = _root / ".claude" / "mcp-failures.reported.json"
_MCP_FAILURES_FRESHNESS_HOURS = 24


def _check_mcp_failures(
    failures_path: Path | None = None,
    reported_path: Path | None = None,
    _now=None,
) -> str | None:
    """Surface MCP bootstrap failures logged to .claude/mcp-failures.jsonl
    newer than 24h, deduped against previously-reported entries so a failure
    never re-reports across sessions. Never raises.
    """
    from datetime import datetime, timedelta, timezone

    fp = failures_path if failures_path is not None else _MCP_FAILURES_PATH
    rp = reported_path if reported_path is not None else _MCP_FAILURES_REPORTED_PATH

    try:
        now = _now if _now is not None else datetime.now(timezone.utc)
        if not fp.exists():
            return None
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        try:
            reported_raw = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else []
            reported = set(reported_raw) if isinstance(reported_raw, list) else set()
        except (OSError, ValueError):
            reported = set()

        cutoff = now - timedelta(hours=_MCP_FAILURES_FRESHNESS_HOURS)
        new_entries = []
        all_signatures = set()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            signature = f"{entry.get('server', '?')}|{ts_str}"
            all_signatures.add(signature)
            if ts < cutoff or signature in reported:
                continue
            new_entries.append((ts, entry))

        try:
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(sorted(reported | all_signatures)), encoding="utf-8")
        except OSError:
            pass

        if not new_entries:
            return None

        new_entries.sort(key=lambda x: x[0])
        lines_out = [
            f"- `{entry.get('server', '?')}` exited {entry.get('exit_code', '?')} "
            f"at {ts.isoformat()} — see `.claude/logs/mcp-{entry.get('server', '?')}.stderr.log`"
            for ts, entry in new_entries
        ]
        return "## MCP Bootstrap Failures\n" + "\n".join(lines_out)
    except Exception:
        return None


def _parse_json_field(val):
    """Parse a field that might be JSON string or already a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return []
    return []


if __name__ == "__main__":
    main()
