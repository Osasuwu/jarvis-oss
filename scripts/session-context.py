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
if __name__ == "__main__" and _venv_py.exists() and Path(sys.executable).resolve() != _venv_py.resolve():
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
    """Return current project name if cwd basename matches a known project, else None."""
    try:
        name = Path(os.getcwd()).name.lower()
    except Exception:
        return None
    return name if name in KNOWN_PROJECTS else None


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
    try:
        query = (
            client.table("memories")
            .select("content, updated_at")
            .eq("name", name)
            .is_("deleted_at", "null")
        )
        if project:
            query = query.eq("project", project)
        result = (
            query.order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        print(f"[session-context] snapshot query failed: {e}", file=sys.stderr)
        return None
    if not result.data:
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


def _format_recovery_section(snapshot: str) -> str:
    return (
        "## Pre-Compact Recovery\n"
        "Compaction happened earlier in this session — pre-compact snapshot "
        "below. Treat this as authoritative history for anything older than "
        "the summary above.\n\n"
        f"{snapshot}"
    )


# Cap injected CONTEXT.md size so a runaway file can't blow the smart-zone
# budget at session start. Full file is one Read away if Jarvis needs it.
_CONTEXT_MAX_BYTES = 8 * 1024


def _load_project_context(project_root: Path) -> str | None:
    """Return formatted '## Project Context' section if `CONTEXT.md` exists
    at `project_root`. Truncates at `_CONTEXT_MAX_BYTES` with a note.

    `project_root` is the repo of the *current* session (resolved by the
    caller from cwd). This must NOT default to the jarvis repo — sessions
    started in other tracked repos would otherwise inject jarvis's
    CONTEXT.md into their context.

    Returns None when:
      - the file is missing
      - the file is empty / whitespace-only
      - read fails (logged to stderr)
    """
    ctx_path = project_root / "CONTEXT.md"
    if not ctx_path.exists():
        return None
    try:
        raw = ctx_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[session-context] CONTEXT.md read failed: {e}", file=sys.stderr)
        return None
    if not raw.strip():
        return None
    encoded = raw.encode("utf-8")
    truncated = False
    if len(encoded) > _CONTEXT_MAX_BYTES:
        # Strict byte cap: slice the bytes, then decode with errors="ignore"
        # so a multi-byte sequence cut in the middle is dropped cleanly
        # rather than producing a U+FFFD replacement char.
        raw = encoded[:_CONTEXT_MAX_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    suffix = "\n\n_(truncated — full file at `CONTEXT.md`)_" if truncated else ""
    return f"## Project Context\n{raw.rstrip()}{suffix}"


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
    have_creds = bool(
        os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")
    )
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
    session_id = (
        hook_input.get("session_id")
        or hook_input.get("sessionId")
        or ""
    )

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[session-context] SUPABASE_URL/KEY not set", file=sys.stderr)
        # Still try local fallback on compact resume — a disconnected dev
        # shouldn't lose pre-compact context entirely.
        if compact_resume and session_id:
            snap = _load_snapshot_from_local(session_id)
            if snap:
                print("=" * 60)
                print("MEMORY CONTEXT (auto-loaded — do NOT re-fetch with MCP tools)")
                print("=" * 60)
                print(_format_recovery_section(snap))
                print("=" * 60)
        return

    try:
        client = create_client(url, key)
    except Exception as e:
        print(f"[session-context] Supabase connect failed: {e}", file=sys.stderr)
        return

    project = _detect_project()
    sections = []
    touched_ids: list[str] = []

    # 0. Pre-Compact Recovery — prepended before everything else on compact
    #    resume. Supabase first, local fallback second. Freshness guarded to
    #    avoid cross-run pollution when Claude Code reuses session_ids.
    if compact_resume and session_id:
        snapshot = _load_snapshot_from_supabase(client, session_id, project)
        if not snapshot:
            snapshot = _load_snapshot_from_local(session_id)
        if snapshot:
            sections.append(_format_recovery_section(snapshot))

    # 1. User memories — who is the owner (always). Compact one-line format:
    #    full bodies rot the context; names + descriptions are enough to
    #    remind Jarvis what exists. Full content via memory_get on demand.
    section, ids = _query_memories(client, mem_type="user", limit=2, compact=True)
    if section:
        sections.append("## User Profile\n" + section)
        touched_ids.extend(ids)

    # 2. Always-load memories — evergreen rules not tied to any single project.
    #    Everything else (feedback/decisions) is loaded task-aware via
    #    UserPromptSubmit hook (scripts/memory-recall-hook.py).
    #    Compact one-line format for the same reason as user profile.
    #    NOTE: Do NOT touch always_load memories (bump last_accessed_at) to prevent
    #    access-bias feedback loop (#767). Recency should not dominate semantic recall.
    section, ids = _query_always_load(client, compact=True)
    if section:
        sections.append("## Always-Load Rules\n" + section)
        # CHANGE #767: Skip touching always_load memories to de-bias access-boost
        # touched_ids.extend(ids)

    # 3a. Project domain context (CONTEXT.md) — glossary + invariants + arch
    #     shape. Read order at session start: CLAUDE.md (rules) → SOUL.md
    #     (identity) → CONTEXT.md (domain). Loaded only when session is inside
    #     a project dir AND the file exists.
    #
    #     Project root is resolved from cwd (the directory the session was
    #     launched in), not from `_root` — `_root` is the jarvis repo and
    #     would inject jarvis's CONTEXT.md into another repo's session.
    #     `_detect_project()` only returns a project name when cwd basename
    #     matches a known project, so cwd IS the project root in that case.
    #
    #     CONTEXT.md is canonical at repo root per Pocock convention;
    #     multi-context repos use CONTEXT-MAP.md. Truncated at 8KB to avoid
    #     blowing the smart-zone budget — full file is one Read away.
    if project:
        ctx_section = _load_project_context(Path(os.getcwd()))
        if ctx_section:
            sections.append(ctx_section)

    # 3b. Working state — ONLY when session is inside a known project dir.
    #     In a non-project cwd (e.g. scheduled research) working_state is noise.
    if project:
        section, ids = _query_memories(
            client, mem_type="project", limit=1,
            extra_filter=lambda q: q.eq("name", f"working_state_{project}"),
        )
        if section:
            sections.append(f"## Working State ({project})\n" + section)
            touched_ids.extend(ids)

    # 4. Active goals (always)
    goal_section = _query_goals(client)
    if goal_section:
        sections.append(goal_section)

    # 5. Pending review count — one-line reminder when candidates await review
    pending_count = _query_pending_review_count(client, project)
    if pending_count > 0:
        sections.append(
            "**Pending memory candidates:** "
            f"{pending_count} (run `/learn` to review)"
        )

    # 5b. Architecture sweep recommendation -- recently closed milestones with
    #     enough closed slices to warrant a /improve-codebase-architecture run.
    #     Only fires in a known project dir where the sweep is meaningful.
    if project:
        sweep_section = _check_milestone_sweep(repo=_PROJECT_REPO.get(project))
        if sweep_section:
            sections.append(sweep_section)

    # 5c. Mirror drift warning — show when ~/.claude/.jarvis-version is
    #     behind repo HEAD. Fires anywhere the script can read both SHAs
    #     (inside the jarvis repo). Silent when absent, in sync, or when
    #     git is unavailable (not a git dir / no permission).
    drift_section = _check_mirror_drift()
    if drift_section:
        sections.append(drift_section)

    # 6. Memory catalog — lazy awareness (Phase 7.1). One-line inventory of
    #    live memories (name + type + scope + short description) so Jarvis
    #    knows what exists and can pull full content on demand via memory_get
    #    / memory_recall. Replaces the old recency-based feedback/decision
    #    dumps — those rot recall (see tests/memory-eval/context-rot-baseline.json).
    #    NOTE: catalog ids are NOT added to touched_ids. Showing a memory in
    #    the session-start index is not a read; bumping last_accessed_at here
    #    would create a feedback loop (catalog is sorted by last_accessed_at)
    #    and distort temporal scoring for genuine recall/read events.
    section, _catalog_ids = _query_catalog(client, project)
    if section:
        sections.append("## Memory Catalog\n" + section)

    # Bump last_accessed_at for every memory we just loaded. Phase 1 drives the
    # access-frequency boost in temporal scoring off this column, so
    # session-start loads should count as access. The content_updated_at /
    # updated_at trigger is not fired because we go through the touch_memories
    # RPC which updates only last_accessed_at. Dedup preserves order — same
    # memory can surface in multiple sections (e.g. always_load + user).
    _touch_accessed(client, list(dict.fromkeys(touched_ids)))

    # Output
    if sections:
        print("=" * 60)
        print("MEMORY CONTEXT (auto-loaded — do NOT re-fetch with MCP tools)")
        print("=" * 60)
        print("\n\n".join(sections))
        print("=" * 60)
    else:
        print("[session-context] No memory data available.")


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


def _query_always_load(client, *, compact=False):
    """Query memories tagged 'always_load' (evergreen, cross-project rules).

    Returns (formatted_text, ids).
    """
    try:
        result = (
            client.table("memories")
            .select(_MEMORY_COLS)
            .contains("tags", ["always_load"])
            .order("updated_at", desc=True)
            .execute()
        )
        if result.data:
            fmt = _fmt_memory_compact if compact else _fmt_memory
            sep = "\n" if compact else "\n---\n"
            text = sep.join(fmt(m) for m in result.data)
            ids = [m["id"] for m in result.data if m.get("id")]
            return text, ids
    except Exception as e:
        print(f"[session-context] always_load query failed: {e}", file=sys.stderr)
    return None, []


def _query_catalog(client, project):
    """Compact one-line catalog of live memories — lazy awareness (Phase 7.1).

    Returns (formatted_text, ids). Sorted by last_accessed_at desc so recently
    touched entries surface first. Filters to live memories (not expired, not
    superseded, not soft-deleted, valid_to in future or null) scoped to
    current project or global (project IS NULL).

    Excludes entries already rendered in other sections:
      - type=user (User Profile)
      - 'always_load' in tags (Always-Load Rules)
      - name=working_state_<project> (Working State)

    valid_to is filtered client-side with aware-datetime parsing because the
    project scoping already uses one .or_() clause (PostgREST allows only one
    `or=` parameter per query).
    """
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    try:
        query = (
            client.table("memories")
            .select("id, name, type, project, description, tags, last_accessed_at, valid_to")
            .is_("expired_at", "null")
            .is_("superseded_by", "null")
            .is_("deleted_at", "null")
        )
        if project:
            query = query.or_(f"project.eq.{project},project.is.null")
        else:
            query = query.is_("project", "null")
        result = (
            query.order("last_accessed_at", desc=True, nullsfirst=False)
            .limit(50)
            .execute()
        )
    except Exception as e:
        print(f"[session-context] catalog query failed: {e}", file=sys.stderr)
        return None, []

    if not result.data:
        return None, []

    working_state_name = f"working_state_{project}" if project else None
    entries = []
    ids = []
    for m in result.data:
        if m["type"] == "user":
            continue
        tags = m.get("tags") or []
        if "always_load" in tags:
            continue
        if working_state_name and m["name"] == working_state_name:
            continue
        vt = _parse_ts(m.get("valid_to"))
        if vt and vt <= now_utc:
            continue
        entries.append(_fmt_catalog_entry(m, project))
        if m.get("id"):
            ids.append(m["id"])

    if not entries:
        return None, []
    return "\n".join(entries), ids


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


def _fmt_catalog_entry(m, current_project):
    """One-line catalog entry: `- <name> [<type>/<scope>]: <description>`."""
    p = m.get("project")
    if p is None:
        scope = f"{m['type']}/global"
    elif p == current_project:
        scope = m["type"]
    else:
        scope = f"{m['type']}/{p}"
    desc = (m.get("description") or "").strip()
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return f"- {m['name']} [{scope}]: {desc}"


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
            result = client.rpc("memory_review_list", {
                "queue": "candidate",
                "project_filter": project,
                "limit_count": 1000,
            }).execute()
            total += len(result.data or [])
        except Exception as e:
            print(f"[session-context] pending-review count failed ({project}): {e}", file=sys.stderr)

    # Global candidates (project IS NULL) — always query
    try:
        result = client.rpc("memory_review_list", {
            "queue": "candidate",
            "project_filter": "",
            "limit_count": 1000,
        }).execute()
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
    return (
        f"- [{g['priority']}] {g['title']} "
        f"(`{g['slug']}` | {scope} | {pct_str}{deadline}){tail}"
    )


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
    from datetime import datetime, timedelta, timezone

    if repo is None:
        return None

    now = _now if _now is not None else datetime.now(timezone.utc)
    window_days = days if days is not None else _MILESTONE_SWEEP_DAYS
    min_count = min_slices if min_slices is not None else _MILESTONE_SWEEP_MIN_SLICES

    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/milestones?state=closed&per_page=100"],
            capture_output=True, text=True, timeout=15,
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
        qualified.append((
            m.get("number", 0),
            m.get("title", f"Milestone #{m.get('number', '?')}"),
            int(closed_issues),
            age_days,
        ))

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
    return (
        f"## Architecture Sweep{plural}\n"
        f"{chr(10).join(lines)}"
    )


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
    HEAD, or None when in sync / marker absent / git unavailable.

    Both parameters are injectable for testing: version_path defaults to
    ~/.claude/.jarvis-version, repo_root defaults to the discovered repo root.
    """
    vp = version_path if version_path is not None else _JARVIS_VERSION_PATH
    rr = repo_root if repo_root is not None else _root

    if not vp.exists():
        return None

    try:
        installed = vp.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not installed:
        return None

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=rr, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    if installed == head:
        return None

    try:
        count = subprocess.run(
            ["git", "rev-list", "--count", f"{installed}..HEAD"],
            cwd=rr, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        count = "?"

    return (
        "## Mirror Drift\n"
        f"⚠ Installed ({installed[:12]}) is {count} commit(s) behind "
        f"HEAD ({head[:12]}) — run `install.ps1 -Apply` or "
        f"`install.sh --apply` to sync."
    )


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
