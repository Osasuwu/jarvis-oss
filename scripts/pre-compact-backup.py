"""PreCompact + SessionEnd hook: capture a durable session snapshot.

Parses the session JSONL transcript, composes a structured markdown snapshot,
and upserts it to Supabase (name=`session_snapshot_<session_id>`, type=project)
with source_provenance="hook:pre-compact". Falls back to a local file under
`.claude/session-snapshots/<session_id>.md` when Supabase is unreachable.

Invariants:
- **Never** blocks compaction. Exits 0 on all paths, including failures.
- Snapshot content stays under SIZE_BUDGET bytes (~30KB). Long transcripts
  keep only the last TAIL_KEEP entries with a dropped-head counter.
- TTL retention (#1272): snapshot rows older than SNAPSHOT_TTL_DAYS are
  deleted at write time (best-effort; the Supabase path calls
  `_purge_stale_snapshots` after each successful upsert).
- Every invocation appends one heartbeat line to
  `.claude/session-snapshots/hook.log` — no line at compaction time means the
  harness never ran the hook (e.g. the 2026-06-12 outage: rewriting
  `~/.claude/settings.json` while the desktop app runs disables all hooks
  until app restart).

Registered in user-level `settings.json` under `PreCompact` (matchers `auto`,
`manual`) and `SessionEnd` (all end reasons) — the same snapshot doubles as a
post-session state save that `session-context.py` picks up on resume (#279).

Hook input (stdin, JSON):
  session_id, transcript_path, cwd, hook_event_name,
  trigger ("auto"|"manual") for PreCompact / end_reason ("clear"|"logout"|...)
  for SessionEnd

Related:
- `scripts/session-context.py` — reads snapshots on resume (Phase 2, #279)
- `.claude-userlevel/skills/end/SKILL.md` (installed to `~/.claude/skills/end/`) — consumes snapshot as primary source (Phase 3, #280)
"""

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
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
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]))

# ---------------------------------------------------------------------------
# Under venv — safe to import deps
# ---------------------------------------------------------------------------
from dotenv import load_dotenv

for _env in [_root / ".env", _root.parent / ".env"]:
    if _env.exists():
        # override=True: some shells pre-set empty SUPABASE_*; .env wins.
        load_dotenv(_env, override=True)
        break

# UTF-8 output on Windows — Cyrillic in transcripts must survive the hook log.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SIZE_BUDGET = 30_000  # bytes — content column target
MAX_TRANSCRIPT_LINES = 10_000  # tail-truncate above this
TAIL_KEEP = 8_000  # keep last N lines when truncating
ACTIONS_CAP = 200  # per-section cap on verbose action lines
SNAPSHOT_TTL_DAYS = 30  # snapshot rows older than this are purged at write time


# Derived from config/repos.conf so the set is owner-agnostic; falls back to
# this checkout's own directory name when no repos are configured yet.
def _load_known_projects() -> set[str]:
    names: set[str] = set()
    try:
        for line in (_root / "config" / "repos.conf").read_text(
            encoding="utf-8"
        ).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "/" in line.split()[0]:
                names.add(line.split()[0].split("/", 1)[1])
    except OSError:
        pass
    return names or {_root.name}


KNOWN_PROJECTS = _load_known_projects()


# ---------------------------------------------------------------------------
# Helpers (pure — unit-tested in tests/infrastructure/test_pre_compact_backup.py)
# ---------------------------------------------------------------------------
def _detect_project(cwd: str | None) -> str | None:
    """Return the known project a path belongs to, scanning all components.

    Basename-only matching missed worktree sessions
    (`<repo>/.claude/worktrees/<name>` → basename is the worktree name), so
    snapshots landed under project=NULL and the /end lookup missed them.
    Rightmost match wins so a nested checkout resolves to the inner repo.
    """
    if not cwd:
        return None
    try:
        parts = Path(cwd).parts
    except Exception:
        return None
    for part in reversed(parts):
        if part.lower() in KNOWN_PROJECTS:
            return part.lower()
    return None


def _read_hook_input(stream=None) -> dict:
    """Read hook input JSON from stdin. Tolerate empty / invalid payloads."""
    s = stream if stream is not None else sys.stdin
    try:
        raw = s.read()
        if not raw or not raw.strip():
            return {}
        return json.loads(raw)
    except Exception as e:
        print(f"[pre-compact] bad hook input: {e}", file=sys.stderr)
        return {}


def _parse_transcript(path: Path) -> tuple[list[dict], int, int]:
    """Return (entries, total_seen, dropped_head).

    If the transcript has more than MAX_TRANSCRIPT_LINES lines, keep only the
    last TAIL_KEEP; `dropped_head` reports how many leading entries were cut.
    Malformed lines are silently skipped.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[pre-compact] read transcript failed: {e}", file=sys.stderr)
        return [], 0, 0

    total = len(lines)
    dropped = 0
    if total > MAX_TRANSCRIPT_LINES:
        dropped = total - TAIL_KEEP
        lines = lines[-TAIL_KEEP:]

    entries: list[dict] = []
    for ln in lines:
        try:
            entries.append(json.loads(ln))
        except Exception:
            continue
    return entries, total, dropped


def _extract_user_messages(entries: list[dict]) -> list[tuple[str, str]]:
    """Real user messages only. Skip command-messages, skill invocations,
    scheduled-task bootstrap, tool_result blocks, and sidechain traffic.
    Dedup on the first 200 chars so repeated prompts don't bloat the snapshot.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in entries:
        if d.get("type") != "user" or d.get("isSidechain"):
            continue
        ts = d.get("timestamp", "")
        c = d.get("message", {}).get("content")
        text = ""
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            parts = [
                blk.get("text", "")
                for blk in c
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            text = "\n".join(parts)
        text = text.strip()
        if not text:
            continue
        if (
            text.startswith("<command-message>")
            or text.startswith("<command-name>")
            or text.startswith("<scheduled-task")
            or text.startswith("Base directory for this skill:")
        ):
            continue
        key = text[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append((ts, text))
    return out


def _summarize_tool(name: str, inp: dict) -> str:
    """One-line summary of a tool_use block, keyed by tool name."""
    if name == "Bash":
        return (inp.get("command") or "").replace("\n", " ").strip()[:180]
    if name in ("Edit", "Write"):
        return inp.get("file_path") or ""
    if name == "NotebookEdit":
        return inp.get("notebook_path") or ""
    if name == "Read":
        return inp.get("file_path") or ""
    if name in ("Grep", "Glob"):
        return inp.get("pattern") or ""
    if name == "TodoWrite":
        todos = inp.get("todos", []) or []
        n_in = sum(1 for t in todos if t.get("status") == "in_progress")
        n_done = sum(1 for t in todos if t.get("status") == "completed")
        return f"{len(todos)} todos ({n_done} done, {n_in} in progress)"
    if name == "Skill":
        return inp.get("skill") or ""
    if name == "Agent":
        return inp.get("description") or inp.get("subagent_type") or ""
    if name == "mcp__memory__memory_store":
        return f"name={inp.get('name', '?')} type={inp.get('type', '?')}"
    if name == "mcp__memory__record_decision":
        return (inp.get("decision") or "")[:120]
    if name.startswith("mcp__github__"):
        keys = ("owner", "repo", "issue_number", "pull_number", "title")
        return " ".join(f"{k}={inp[k]}" for k in keys if k in inp)[:180]
    if name.startswith("mcp__ccd_session__"):
        return (inp.get("title") or inp.get("reason") or "")[:120]
    for k in ("description", "title", "command", "query", "prompt"):
        v = inp.get(k)
        if isinstance(v, str):
            return v[:120]
    return ""


def _extract_actions(entries: list[dict]) -> list[tuple[str, str]]:
    """(timestamp, 'ToolName: one-line') tuples from assistant tool_use blocks."""
    out: list[tuple[str, str]] = []
    for d in entries:
        if d.get("type") != "assistant":
            continue
        ts = d.get("timestamp", "")
        for blk in d.get("message", {}).get("content", []) or []:
            if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                continue
            name = blk.get("name", "?")
            inp = blk.get("input", {}) or {}
            summary = _summarize_tool(name, inp)
            out.append((ts, f"{name}: {summary}" if summary else name))
    return out


def _extract_last_todos(entries: list[dict]) -> list[dict]:
    """Most-recent TodoWrite payload — the canonical 'open loops' view."""
    last: list[dict] = []
    for d in entries:
        if d.get("type") != "assistant":
            continue
        for blk in d.get("message", {}).get("content", []) or []:
            if (
                isinstance(blk, dict)
                and blk.get("type") == "tool_use"
                and blk.get("name") == "TodoWrite"
            ):
                last = blk.get("input", {}).get("todos", []) or []
    return last


def _extract_last_assistant_text(entries: list[dict]) -> str:
    for d in reversed(entries):
        if d.get("type") != "assistant":
            continue
        parts = [
            blk.get("text", "")
            for blk in d.get("message", {}).get("content", []) or []
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        text = "\n".join(parts).strip()
        if text:
            return text
    return ""


def _last_git_branch(entries: list[dict]) -> str:
    for d in reversed(entries):
        br = d.get("gitBranch")
        if br:
            return br
    return ""


def _compose_markdown(
    session_id: str,
    trigger: str,
    cwd: str,
    entries: list[dict],
    total_seen: int,
    dropped_head: int,
) -> str:
    """Compose the snapshot body. Enforces SIZE_BUDGET via hard truncation
    with a visible marker — never silently drops content without notice.
    """
    users = _extract_user_messages(entries)
    actions = _extract_actions(entries)
    todos = _extract_last_todos(entries)
    last_text = _extract_last_assistant_text(entries)
    git_branch = _last_git_branch(entries)
    captured = datetime.now(timezone.utc).isoformat()

    lines: list[str] = []
    lines.append(f"# Session Snapshot — {session_id}")
    lines.append("")
    lines.append(f"- **Captured at:** {captured}")
    lines.append(f"- **Trigger:** {trigger}")
    lines.append(f"- **cwd:** `{cwd}`")
    if git_branch:
        lines.append(f"- **git branch:** `{git_branch}`")
    entries_line = (
        f"- **Entries parsed:** {len(entries)} (total seen: {total_seen}"
        + (f", dropped-head: {dropped_head}" if dropped_head else "")
        + ")"
    )
    lines.append(entries_line)
    lines.append("")

    if users:
        lines.append(f"## User messages ({len(users)})")
        for ts, text in users:
            preview = text.replace("\n", " ").strip()
            if len(preview) > 500:
                preview = preview[:500] + " …"
            lines.append(f"- `{ts}` — {preview}")
        lines.append("")

    if actions:
        lines.append(f"## Actions ({len(actions)})")
        if len(actions) > ACTIONS_CAP:
            earlier = actions[: len(actions) - ACTIONS_CAP]
            by_tool = Counter(a[1].split(":", 1)[0] for a in earlier)
            summ = ", ".join(f"{k}×{v}" for k, v in by_tool.most_common())
            lines.append(f"Earlier actions (summarized): {summ}")
            kept = actions[-ACTIONS_CAP:]
        else:
            kept = actions
        for ts, act in kept:
            a = act.replace("\n", " ").strip()
            if len(a) > 200:
                a = a[:200] + " …"
            lines.append(f"- `{ts}` — {a}")
        lines.append("")

    if todos:
        lines.append(f"## Open loops / todos ({len(todos)})")
        status_mark = {"completed": "x", "in_progress": "~", "pending": " "}
        for t in todos:
            mark = status_mark.get(t.get("status", ""), "?")
            content = t.get("content", "")
            lines.append(f"- [{mark}] {content}")
        lines.append("")

    if last_text:
        lines.append("## Last assistant message (text only)")
        snippet = last_text
        if len(snippet) > 2000:
            snippet = snippet[:2000] + "\n…"
        lines.append(snippet)
        lines.append("")

    md = "\n".join(lines)
    if len(md.encode("utf-8")) > SIZE_BUDGET:
        marker = "\n\n<!-- truncated at size budget -->\n"
        budget = SIZE_BUDGET - len(marker.encode("utf-8"))
        md = md.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + marker
    return md


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _purge_stale_snapshots(client, ttl_days: int = SNAPSHOT_TTL_DAYS) -> int | None:
    """Delete session_snapshot_* rows older than ttl_days (#1272 AC3).

    Enforced at write time — called after each successful upsert. Best-effort:
    returns the number of rows deleted, or None on failure; never raises, so
    the never-blocks-compaction invariant holds. RLS may filter the delete to
    zero rows for an anon-key caller; that is logged, not fatal.
    """
    from datetime import datetime, timedelta, timezone

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        # ceiling: LIKE scan over name with no index — fine at ~1.4k rows once
        # per compaction; add idx_memories_name if the table ever grows large.
        result = (
            client.table("memories")
            .delete()
            .like("name", "session_snapshot_%")
            .lt("updated_at", cutoff)
            .execute()
        )
        deleted = len(result.data or [])
        if deleted:
            print(f"[pre-compact] TTL purge: deleted {deleted} stale snapshot rows", file=sys.stderr)
        return deleted
    except Exception as e:
        print(f"[pre-compact] snapshot TTL purge failed: {e}", file=sys.stderr)
        return None


def _persist_supabase(
    session_id: str,
    project: str | None,
    trigger: str,
    content: str,
) -> bool:
    """Upsert the snapshot to memories. Returns True on success."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print(
            "[pre-compact] SUPABASE_URL/SUPABASE_KEY not set — skipping Supabase",
            file=sys.stderr,
        )
        return False
    try:
        from supabase import create_client

        client = create_client(url, key)
        payload = {
            "name": f"session_snapshot_{session_id}",
            "type": "project",
            "project": project,
            "tags": ["session-snapshot", "compression-resilience", trigger or "unknown"],
            "source_provenance": "hook:pre-compact",
            "description": (
                f"Pre-compact session snapshot ({trigger or 'unknown'}) — "
                "recovery source for /end post-compact"
            ),
            "content": content,
        }
        client.table("memories").upsert(payload, on_conflict="project,name").execute()
        _purge_stale_snapshots(client)
        return True
    except Exception as e:
        print(f"[pre-compact] supabase persist failed: {e}", file=sys.stderr)
        return False


def _sanitize_session_id(session_id: str) -> str:
    """Reduce a stdin-sourced session_id to a safe single filename component.

    session_id comes verbatim from untrusted hook JSON, so strip anything that
    isn't an identifier char (path separators, dots, traversal) before it names
    a counter file. Empty → "unknown-session" so we never write a dotfile.
    """
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return safe or "unknown-session"


def _bump_compaction_count(session_id: str) -> int:
    """Increment the per-session compaction generation counter; return new value.

    State lives at ``~/.claude/compaction-counts/<session_id>.txt`` (device-local,
    home-rooted so `statusline.py` can read it regardless of cwd). Best-effort:
    any failure returns the last-known value (or 0) and never raises — the
    never-blocks-compaction invariant holds. Called ONLY on PreCompact events,
    so it counts summaries, not session ends.
    """
    try:
        out_dir = Path.home() / ".claude" / "compaction-counts"
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"{_sanitize_session_id(session_id)}.txt"
        try:
            current = int(f.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            current = 0
        new = current + 1
        f.write_text(str(new), encoding="utf-8")
        return new
    except Exception as e:
        print(f"[pre-compact] compaction-count bump failed: {e}", file=sys.stderr)
        return 0


def _is_within(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` or nested under it.

    Compares resolved path *components* (via ``Path.parents``), not string
    prefixes, so a sibling sharing a name prefix ("session-snapshots-evil" vs
    "session-snapshots") is correctly rejected. Deliberately avoids
    ``Path.is_relative_to`` — that is 3.9+ only and this hook must not assume a
    minimum interpreter past 3.8. Fails safe (returns False / "outside") if the
    paths can't be resolved.
    """
    try:
        path_r = path.resolve()
        root_r = root.resolve()
    except (OSError, ValueError):
        return False
    return path_r == root_r or root_r in path_r.parents


def _persist_local(session_id: str, content: str) -> Path | None:
    try:
        out_dir = _root / ".claude" / "session-snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = (out_dir / f"{session_id}.md").resolve()
        # Guard against path traversal: session_id from untrusted JSON.
        # _is_within compares resolved path components (not string prefixes),
        # so "session-snapshots-evil" can't masquerade as "session-snapshots".
        if not _is_within(out_file, out_dir):
            print("[pre-compact] suspicious session_id in local fallback", file=sys.stderr)
            return None
        # Use binary write to avoid Windows \n→\r\n translation, which would
        # inflate size past SIZE_BUDGET by one byte per newline.
        out_file.write_bytes(content.encode("utf-8"))
        print(f"[pre-compact] local fallback: {out_file}", file=sys.stderr)
        return out_file
    except Exception as e:
        print(f"[pre-compact] local fallback failed: {e}", file=sys.stderr)
        return None


# Heartbeat log is head-trimmed to its last _HOOK_LOG_KEEP_LINES once it grows
# past _HOOK_LOG_MAX_BYTES, so a device with frequent compaction can't let it
# accumulate without bound.
_HOOK_LOG_MAX_BYTES = 1_000_000  # ~1 MB
_HOOK_LOG_KEEP_LINES = 500


def _sanitize_log_field(value: object) -> str:
    """Escape CR/LF/NUL so a hostile field can't forge extra heartbeat lines.

    `session_id` and `trigger` come verbatim from hook stdin (untrusted JSON),
    so the value may not even be a `str` — `str(value)` coerces first. A literal
    newline would otherwise split one heartbeat into several forged lines, the
    exact opposite of the diagnostic contract.
    """
    return str(value).replace("\n", "\\n").replace("\r", "\\r").replace("\x00", "\\x00")


def _trim_hook_log(log_path: Path) -> None:
    """Keep only the last _HOOK_LOG_KEEP_LINES entries when file exceeds _HOOK_LOG_MAX_BYTES.

    No-op until the file exceeds _HOOK_LOG_MAX_BYTES. Best-effort: a failure
    here must never stop the heartbeat, so the single caller wraps it.
    """
    if not log_path.exists() or log_path.stat().st_size <= _HOOK_LOG_MAX_BYTES:
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    trimmed = "".join(lines[-_HOOK_LOG_KEEP_LINES:])
    # Atomic rename: avoids data loss if the process is killed mid-write.
    tmp_path = log_path.with_suffix(".tmp")
    tmp_path.write_text(trimmed, encoding="utf-8")
    os.replace(str(tmp_path), str(log_path))


def _append_hook_log(message: str) -> None:
    """Heartbeat: one line per invocation to `.claude/session-snapshots/hook.log`.

    Distinguishes "hook ran but persistence failed" from "harness never
    executed the hook" — the latter was diagnosable only by the absence of
    Supabase rows during the 2026-06-12 outage. Never raises.
    """
    out_dir = _root / ".claude" / "session-snapshots"
    log_path = out_dir / "hook.log"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except Exception as e:
        # The one function whose job is observability must itself be
        # observable on failure (disk full, bad perms). stderr does not
        # raise, so the "never raises" contract holds.
        print(f"[pre-compact] hook.log write failed: {e}", file=sys.stderr)
        return
    # Trim only after a successful append, so a trim failure can't cost us the
    # heartbeat line we just wrote.
    try:
        _trim_hook_log(log_path)
    except Exception as e:
        # Catch broadly, not just OSError: _trim_hook_log reads the log with
        # read_text(), which raises UnicodeDecodeError (an Exception, not an
        # OSError) on a corrupted/partially-written file. Letting that escape
        # would propagate out of the finally-block heartbeat and force the hook
        # to exit non-zero — breaking the never-blocks-compaction invariant.
        print(f"[pre-compact] hook.log trim failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    session_id = "unknown-session"
    trigger = "unknown"
    # Sentinel for the heartbeat: "init" means we entered main() but reached no
    # explicit outcome. If an exception fires before any stage stamps `outcome`,
    # the finally-block records "error-after:init" — distinguishing a crash at
    # entry from one mid-persist (which carries the last stage's name).
    outcome = "init"
    try:
        hook = _read_hook_input()
        session_id = hook.get("session_id") or hook.get("sessionId") or "unknown-session"
        transcript_path = hook.get("transcript_path") or hook.get("transcriptPath") or ""
        cwd = hook.get("cwd") or os.getcwd()
        event = hook.get("hook_event_name") or hook.get("hookEventName") or ""
        trigger = (
            hook.get("trigger")
            or hook.get("matcher")
            or hook.get("end_reason")
            or "unknown"
        )

        # Compaction-generation counter (the dumb-zone signal under auto-compact).
        # Bump ONLY on PreCompact — this hook is dual-purpose (also SessionEnd),
        # and session ends must not inflate the count. PreCompact carries a
        # trigger of "auto"/"manual"; SessionEnd carries an end_reason instead.
        if event == "PreCompact" or trigger in ("auto", "manual"):
            gen = _bump_compaction_count(session_id)
            print(f"[pre-compact] compaction generation -> {gen}", file=sys.stderr)

        if not transcript_path:
            print("[pre-compact] no transcript_path in hook input", file=sys.stderr)
            outcome = "no-transcript-path"
            return 0

        p = Path(transcript_path)
        # transcript_path is attacker-influenceable hook stdin. Confine reads to
        # the dirs transcripts actually live in (~/.claude and the repo) so a
        # crafted path (e.g. "/etc/passwd") can't be slurped into a snapshot and
        # upserted to Supabase. Fail safe: skip with a logged outcome, never raise.
        allowed_roots = [Path.home() / ".claude", _root]
        if not any(_is_within(p, r) for r in allowed_roots):
            print(f"[pre-compact] transcript_path outside allowed dirs: {p}", file=sys.stderr)
            outcome = "transcript-path-rejected"
            return 0

        if not p.exists():
            print(f"[pre-compact] transcript not found: {p}", file=sys.stderr)
            outcome = "transcript-missing"
            return 0

        entries, total, dropped = _parse_transcript(p)
        content = _compose_markdown(session_id, trigger, cwd, entries, total, dropped)
        project = _detect_project(cwd)

        if _persist_supabase(session_id, project, trigger, content):
            outcome = "supabase"
        elif _persist_local(session_id, content) is not None:
            outcome = "local-fallback"
        else:
            outcome = "persist-failed"
    except Exception as e:
        # Never block compaction — log and move on. Re-stamp the outcome so the
        # heartbeat records that an exception fired (and how far we got) instead
        # of a stale value left over from a partially-completed persist.
        print(f"[pre-compact] unhandled error: {e}", file=sys.stderr)
        outcome = f"error-after:{outcome}"
    finally:
        # Sanitize the stdin-sourced fields before they enter the log line;
        # `outcome` is always one of our own literals — safe.
        _append_hook_log(
            f"session={_sanitize_log_field(session_id)} "
            f"trigger={_sanitize_log_field(trigger)} "
            f"outcome={outcome}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
