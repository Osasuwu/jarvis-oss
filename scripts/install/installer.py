"""Jarvis install/sync — applies install-manifest.yaml into ~/.claude/.

Epic #335 M1 (#336). Handles three device states:
  fresh     — target_root missing or has no .jarvis-version → full install
  outdated  — .jarvis-version present but differs from current repo SHA → re-apply
  current   — .jarvis-version matches current SHA → no-op

Default mode is dry-run. Destructive writes require explicit --apply.

NOTE: .mcp.json is round-tripped through json.loads/json.dumps during installation
and therefore JSONC comments and key ordering are not preserved. Future authors who
add comments to .mcp.json should expect them to be stripped on install.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml


DEFAULT_MANIFEST = "install-manifest.yaml"

# Bound external-command calls so a child that inherits the capture pipe and
# never exits can't hang the installer forever (latent grandchild-pipe hang —
# `capture_output=True` keeps reading until every writer closes the fd). The
# health check already uses a 30s bound; `claude mcp add` may spin up Node and
# hit the network, so it gets more headroom than the instant local `setx`.
_MCP_SUBPROCESS_TIMEOUT = 120
_ENV_SUBPROCESS_TIMEOUT = 30


# ---------- data model ----------


@dataclasses.dataclass
class Action:
    """One planned filesystem action."""

    kind: str  # "copy_file" | "copy_dir" | "merge_json" | "quarantine_file" | "prune_orphan" | "register_mcp_user" | "prune_mcp_user" | "write_version" | "set_env"
    source: str | None
    dest: str
    template: bool = False
    group: str = ""
    note: str = ""


@dataclasses.dataclass
class Plan:
    state: str  # "fresh" | "outdated" | "current"
    actions: list[Action]
    backup_path: Path | None
    current_sha: str
    previous_sha: str | None
    target_root: Path
    repo_root: Path


# ---------- helpers ----------


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def current_git_sha(repo_root: Path) -> str:
    return _run_git(repo_root, "rev-parse", "HEAD")


def _is_git_worktree_checkout(repo_root: Path) -> bool:
    """True when `repo_root` is a linked worktree, not the main checkout.

    A worktree's `.git` is a FILE holding a `gitdir: ...` pointer into the
    main checkout's `.git/worktrees/<name>`; the main checkout's `.git` is a
    directory. #1199: the installer resolves `repo_root` from its own file
    location, which for a worktree is the worktree tree — global-scope MCP
    registration (`claude mcp add -s user`) must run from the main checkout.
    """
    return (repo_root / ".git").is_file()


def read_version(target_root: Path) -> str | None:
    marker = target_root / ".jarvis-version"
    if not marker.exists():
        return None
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} did not parse to a mapping")
    if data.get("version") != 1:
        raise ValueError(f"unsupported manifest version {data.get('version')!r}; expected 1")
    return data


def detect_state(target_root: Path, current_sha: str) -> tuple[str, str | None]:
    if not target_root.exists():
        return "fresh", None
    prev = read_version(target_root)
    if prev is None:
        # Target exists but no version marker — treat as outdated so we
        # re-apply, but preserve via backup.
        return "outdated", None
    if prev == current_sha:
        return "current", prev
    return "outdated", prev


# ---------- template substitution ----------


# Match `scripts/` or `config/` only at a token boundary — start of string or
# preceded by whitespace. Protects URLs (`https://.../scripts/x`) and compound
# names (`my-scripts/x`) from being rewritten, while still catching embedded
# commands like `python scripts/foo.py && cat config/SOUL.md`.
_POSIX_PATH_PATTERN = re.compile(r"(?<!\S)(scripts|config)/")


# A pre-migration `.mcp.json` sitting in any parent dir of JARVIS_HOME (e.g.
# `<repos-root>\.mcp.json`) shadows the correctly-templated user-level file:
# Claude Code walks up from CWD and binds the first `.mcp.json` it finds.
# Pre-migration files reference `jarvis/scripts/...` as a *relative* path,
# which only resolves when CWD == the legacy file's parent. From any other
# project (redrobot, etc.) the server fails to launch. Detect by JSON content,
# not just filename, so we don't quarantine unrelated parent-dir MCP configs.
_LEGACY_RELATIVE_JARVIS_PATTERN = re.compile(r"^jarvis[\\/]")

_BOM_PREFIX = b"\xef\xbb\xbf"
_CRLF_BYTES = b"\r\n"
_ENV_WARN_MSG = "WARN: {} has {}; MCP servers using naive regex may silently drop env vars."


def _detect_env_issues(path: Path) -> str:
    """Check a single .env file for BOM and/or CRLF. Returns '' if clean."""
    raw = path.read_bytes()
    parts = []
    if raw[:3] == _BOM_PREFIX:
        parts.append("BOM")
    if _CRLF_BYTES in raw:
        parts.append("CRLF")
    return "+".join(parts) if parts else ""


def _scan_env_encoding(claude_home: Path, repo_root: Path) -> list[tuple[Path, str, bool]]:
    """Scan .env files for BOM/CRLF issues under claude_home and repo root.

    Returns list of (path, issues_summary, is_user_env).
    is_user_env=True for files under claude_home (fixable).
    is_user_env=False for repo-root .env (warn-only — gitignored, may be intentional).

    Behaviour notes:
    - On fresh install ``claude_home`` may not exist yet; ``Path.rglob`` raises
      ``FileNotFoundError`` on a missing base since Python 3.12 (gh-73435), so
      we short-circuit before touching the iterator.
    - ``Path.rglob`` follows symlinks. To prevent a malicious symlink under
      ``claude_home`` from making the fixer rewrite an arbitrary credentials
      file, each candidate's resolved path must stay within ``claude_home``.
    """
    findings: list[tuple[Path, str, bool]] = []
    if claude_home.is_dir():
        claude_home_resolved = claude_home.resolve()
        for env_file in claude_home.rglob("*.env"):
            if not env_file.is_file():
                continue
            try:
                resolved = env_file.resolve()
                resolved.relative_to(claude_home_resolved)
            except (OSError, ValueError):
                # Symlink escapes claude_home (ValueError) or target is gone
                # mid-scan (OSError). Either way, skip — don't read or fix it.
                continue
            issues = _detect_env_issues(env_file)
            if issues:
                findings.append((env_file, issues, True))

    repo_env = repo_root / ".env"
    if repo_env.is_file():
        issues = _detect_env_issues(repo_env)
        if issues:
            findings.append((repo_env, issues, False))

    return findings


def _fix_env_encoding(path: Path, issues: str) -> None:
    """Rewrite a single .env file to UTF-8-no-BOM + LF line endings.

    Atomic: writes to a sibling tempfile and ``os.replace`` it into place, so
    a SIGKILL / Ctrl-C / disk-full between truncate and write can never leave
    an empty ``.env`` (credentials unrecoverable). Original file mode is
    preserved across the swap.
    """
    raw = path.read_bytes()
    new_raw = raw
    if "BOM" in issues and new_raw[:3] == _BOM_PREFIX:
        new_raw = new_raw[3:]
    if "CRLF" in issues:
        new_raw = new_raw.replace(_CRLF_BYTES, b"\n")
    if new_raw == raw:
        return  # nothing to do — preserve mtime/perms exactly
    try:
        orig_mode = path.stat().st_mode
    except OSError:
        orig_mode = None
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_raw)
            fh.flush()
            os.fsync(fh.fileno())
        if orig_mode is not None:
            try:
                os.chmod(tmp_path, orig_mode)
            except OSError:
                pass  # best-effort; Windows ACLs make this advisory anyway
        os.replace(tmp_path, path)
    except BaseException:
        # Cleanup tempfile on any failure (incl. KeyboardInterrupt) so we
        # don't leave detritus alongside the original .env.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _transform_json_paths(node: Any, repo_root_posix: str) -> Any:
    """Rewrite relative `scripts/...` and `config/...` references to
    absolute paths inside the jarvis repo. JSON-aware walk preserves
    structure while only touching string leaves."""
    if isinstance(node, str):
        return _POSIX_PATH_PATTERN.sub(lambda m: f"{repo_root_posix}/{m.group(1)}/", node)
    if isinstance(node, list):
        return [_transform_json_paths(x, repo_root_posix) for x in node]
    if isinstance(node, dict):
        return {k: _transform_json_paths(v, repo_root_posix) for k, v in node.items()}
    return node


def _references_relative_jarvis(node: Any) -> bool:
    """True if any string leaf is a relative path beginning with `jarvis/` or `jarvis\\`."""
    if isinstance(node, str):
        return bool(_LEGACY_RELATIVE_JARVIS_PATTERN.match(node))
    if isinstance(node, list):
        return any(_references_relative_jarvis(x) for x in node)
    if isinstance(node, dict):
        return any(_references_relative_jarvis(v) for v in node.values())
    return False


def find_legacy_parent_mcp(repo_root: Path, max_depth: int = 4) -> list[Path]:
    """Return parent-dir `.mcp.json` files referencing jarvis with relative paths.

    Walks up to `max_depth` parents from `repo_root` (typically JARVIS_HOME).
    A file is flagged only when its JSON content contains a string starting
    with `jarvis/` or `jarvis\\` — i.e. a path that resolves correctly when
    CWD is the legacy file's parent dir but breaks elsewhere. Absolute paths
    (already-templated by a prior install) are left alone.
    """
    found: list[Path] = []
    parent = repo_root.parent
    for _ in range(max_depth):
        if parent == parent.parent:  # filesystem root
            break
        candidate = parent / ".mcp.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            else:
                if _references_relative_jarvis(data):
                    found.append(candidate)
        parent = parent.parent
    return found


def _backup_dest(path: Path, label: str) -> Path:
    """Compute non-clobbering `.bak.<label>` destination for `path`."""
    base = path.with_name(path.name + f".bak.{label}")
    if not base.exists():
        return base
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.bak.{label}-{stamp}")


def _quarantine_dest(path: Path) -> Path:
    """Legacy-MCP quarantine path. See `_backup_dest` for the generic form."""
    return _backup_dest(path, "pre-jarvis-migration")


def _user_mcp_config_path() -> Path:
    """Path to the live user-scope MCP config that `claude mcp add -s user`
    writes to (`~/.claude.json` → top-level `mcpServers` block)."""
    return Path.home() / ".claude.json"


def _read_user_mcp_servers(config_path: Path) -> dict[str, Any]:
    """Return the `mcpServers` block from a live user-scope config, or {}.

    Tolerant of a missing or unparseable file — both mean "no servers known"
    rather than an error: the installer must still run on a fresh machine
    where `~/.claude.json` doesn't exist yet.
    """
    if not config_path.is_file():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _plan_mcp_user_registrations(
    source: Path,
    repo_root: Path,
    target_root: Path,
    user_mcp_config: Path | None = None,
) -> list[Action]:
    """Generate a `register_mcp_user` action per server in `source` (.mcp.json).

    Claude Code does NOT read `~/.claude/.mcp.json` as user-scope MCP config —
    only project-scope (CWD walk) and the `mcpServers` block inside
    `~/.claude.json` (managed by `claude mcp add -s user`). Earlier installer
    revisions dropped the file under `target_root` where Claude Code never
    looked. This helper reads that file and plans `claude mcp add -s user`
    invocations that actually register servers in user scope.

    Path templating (`scripts/...` → `<repo_root>/scripts/...`) and
    `{{JARVIS_HOME}}` substitution are applied before serialising each spec
    into the action note, so apply-time runs see absolute paths.

    Also schedules a quarantine of any pre-existing `target_root/.mcp.json`
    left over from the dead file-drop strategy.

    When `user_mcp_config` is given (the live `~/.claude.json`), also plans
    `prune_mcp_user` actions for user-scope servers present there but absent
    from `source` (#3 — drift: servers dropped from source were left
    registered forever, e.g. a `bambu` server that outlived its manifest
    entry). Source is authoritative for user-scope MCP. Device-gated servers
    (skipped here because their env is unset) are NOT pruned — they remain in
    `source`, so they're excluded from the orphan set. Prune is opt-in via the
    param so the per-spec unit tests stay hermetic; the default `None` plans
    no prune.
    """
    rendered = template_content(source, repo_root, target_root).decode("utf-8")
    data = json.loads(rendered)
    actions: list[Action] = []
    source_names = set(data.get("mcpServers") or {})
    for name, spec in (data.get("mcpServers") or {}).items():
        # Device-capability gate (#uml): a server may declare an env var it
        # cannot run without (e.g. uml needs UML_MCP_HOME pointing at the local
        # uml-mcp + Kroki backend, present only on the routine host). On devices
        # where that var is unset, skip registration instead of installing a
        # server that would fail to launch. `pop` also strips the marker so it
        # never reaches `claude mcp add`. Set the var on another device and the
        # next install picks the server up automatically — no source change.
        required_env = spec.pop("x-jarvis-requires-env", None)
        if required_env and not os.environ.get(required_env):
            print(
                f"  skip mcp {name!r}: requires env {required_env} (unset on this device)",
                file=sys.stderr,
            )
            continue
        payload = json.dumps({"name": name, "spec": spec}, ensure_ascii=False)
        actions.append(
            Action(
                kind="register_mcp_user",
                source=str(source),
                dest=name,
                template=False,
                group="mcp_config",
                note=payload,
            )
        )
    stale = target_root / ".mcp.json"
    if stale.is_file():
        actions.append(
            Action(
                kind="quarantine_file",
                source=str(stale),
                dest=str(_quarantine_dest(stale)),
                group="mcp_config",
                note="superseded by user-scope MCP registrations",
            )
        )
    if user_mcp_config is not None:
        live = _read_user_mcp_servers(user_mcp_config)
        for orphan in sorted(set(live) - source_names):
            # Stash the orphan's full live spec in the note so a mistaken prune
            # is recoverable from the dry-run log / plan output.
            note = json.dumps(live[orphan], ensure_ascii=False)
            actions.append(
                Action(
                    kind="prune_mcp_user",
                    source=str(user_mcp_config),
                    dest=orphan,
                    group="mcp_config",
                    note=note,
                )
            )
    return actions


def _venv_python_candidates(repo_root: Path) -> list[Path]:
    """Mirrors scripts/run-memory-server.py's venv-python lookup order."""
    return [
        repo_root / ".venv" / "Scripts" / "python.exe",  # Windows
        repo_root / ".venv" / "bin" / "python",  # macOS/Linux
    ]


def _mcp_action_requires_venv(spec: dict[str, Any], repo_root: Path) -> bool:
    """True when `spec` invokes a repo-venv python — e.g. `python
    scripts/x.py`, templated to an absolute `repo_root`-rooted path by
    `_plan_mcp_user_registrations`. A python command pointed at a path
    outside `repo_root` (e.g. `${UML_MCP_HOME}/server.py`) doesn't depend on
    this repo's own `.venv`.

    `template_content` renders args with forward slashes regardless of OS
    (#1199 — a raw string `.startswith(str(repo_root))` breaks on Windows,
    where `repo_root` renders with backslashes), so args are compared as
    `Path` objects rather than strings.
    """
    if spec.get("command") not in ("python", "python3"):
        return False
    repo_root = repo_root.resolve()
    for a in spec.get("args") or []:
        try:
            if Path(a).resolve().is_relative_to(repo_root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _check_mcp_venv_dependencies(actions: list[Action], repo_root: Path) -> None:
    """Raise loudly if a planned MCP registration depends on `.venv` and no
    venv-python candidate exists at `repo_root` (#1199) — otherwise the
    registration succeeds but the server fails to launch on first use.
    """
    for action in actions:
        if action.kind != "register_mcp_user":
            continue
        payload = json.loads(action.note)
        spec = payload["spec"]
        if not _mcp_action_requires_venv(spec, repo_root):
            continue
        if not any(p.is_file() for p in _venv_python_candidates(repo_root)):
            raise RuntimeError(
                f"MCP server {payload['name']!r} requires a repo .venv, but none "
                f"was found at {repo_root / '.venv'}; run scripts/setup-device.py "
                "(or setup-device.sh) before installing"
            )


def _resolve_claude_cli() -> str:
    """Return an executable path for the Claude Code CLI.

    On Windows the npm wrapper installs both ``claude`` (POSIX shell script,
    no extension) and ``claude.CMD`` to ``%APPDATA%\\npm``. ``CreateProcessW``
    only consults PATHEXT when the bare name fails to resolve to a file —
    if a sibling ``claude`` (no extension) exists, it wins, and Windows
    refuses to launch it as a process (FileNotFoundError / WinError 2).
    ``shutil.which`` honours PATHEXT, so it picks the ``.CMD`` directly.
    Fall back to bare ``claude`` for environments where it isn't on PATH
    yet but will be (e.g. fresh installs); the subprocess error message
    will be clearer than a silent miss.
    """
    return shutil.which("claude") or "claude"


def _register_mcp_user(name: str, spec: dict[str, Any]) -> None:
    """Run `claude mcp add -s user` for one server, removing any prior entry first.

    Idempotent: a stale entry is removed (errors swallowed — it may not exist)
    before the add. Subprocess args are passed as a list so values containing
    spaces or shell metacharacters survive intact.

    Argument order matters (#432). The Claude Code CLI declares variadic
    options:
        -e, --env <env...>
        -H, --header <header...>
    A variadic flag eats every following token until the next flag (or `--`),
    including positional arguments. Putting `-H`/`-e` BEFORE the positional
    `<name>` causes the parser to consume `<name>` as a header/env value and
    fail with `error: missing required argument 'name'`.

    Fix: place positionals first, then the variadic flags. For stdio, the
    `--` separator marks the end of options, so `-e` between `<name>` and
    `--` is safe.
    """
    claude = _resolve_claude_cli()
    try:
        subprocess.run(
            [claude, "mcp", "remove", "-s", "user", name],
            check=False,
            capture_output=True,
            timeout=_MCP_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Best-effort cleanup — a hung remove must not block the add below.
        print(
            f"  warn: `claude mcp remove {name}` timed out "
            f"after {_MCP_SUBPROCESS_TIMEOUT}s; continuing",
            file=sys.stderr,
        )
    cmd: list[str] = [claude, "mcp", "add", "-s", "user"]
    transport = spec.get("type")
    if transport in {"http", "sse"}:
        # Order: --transport <t> <name> <url> -H ... -H ...
        # Headers AFTER positionals so the -H variadic doesn't swallow them.
        cmd += ["--transport", transport, name, spec["url"]]
        for hk, hv in (spec.get("headers") or {}).items():
            cmd += ["-H", f"{hk}: {hv}"]
    else:
        # Order: <name> -e ... -- <command> <args...>
        # Env flags AFTER name so the -e variadic doesn't swallow it; the
        # `--` separator then marks the boundary before the inner command.
        cmd += [name]
        for ek, ev in (spec.get("env") or {}).items():
            cmd += ["-e", f"{ek}={ev}"]
        cmd += ["--", spec["command"], *spec.get("args", [])]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_MCP_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"claude mcp add timed out after {_MCP_SUBPROCESS_TIMEOUT}s for {name!r}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"claude mcp add failed for {name!r}: {result.stderr.strip() or result.stdout.strip()}"
        )


def _prune_mcp_user(name: str) -> None:
    """Remove an orphan user-scope server via `claude mcp remove -s user`.

    Cleanup, not a load-bearing install step: a failure (or timeout) warns to
    stderr and returns rather than raising, so a stuck `claude mcp remove`
    never rolls back an otherwise-good install. The server simply stays
    registered until the next run retries the prune.
    """
    claude = _resolve_claude_cli()
    try:
        result = subprocess.run(
            [claude, "mcp", "remove", "-s", "user", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=_MCP_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(
            f"  warn: prune of orphan mcp {name!r} timed out "
            f"after {_MCP_SUBPROCESS_TIMEOUT}s; leaving it registered",
            file=sys.stderr,
        )
        return
    if result.returncode != 0:
        print(
            f"  warn: prune of orphan mcp {name!r} failed: "
            f"{result.stderr.strip() or result.stdout.strip()}",
            file=sys.stderr,
        )
        return
    print(f"  pruned orphan user-scope mcp {name!r}", file=sys.stderr)


def _substitute_placeholders(text: str, repo_root: Path, claude_home: Path) -> str:
    return text.replace("{{JARVIS_HOME}}", repo_root.as_posix()).replace(
        "{{CLAUDE_USER_HOME}}", claude_home.as_posix()
    )


def _template_bytes(raw: bytes, ext: str, repo_root: Path, claude_home: Path) -> bytes:
    """Templating core shared by `template_content` and git-history reads.

    For .json content: parse, rewrite relative `scripts/`/`config/` paths to
    absolute, pretty-print. For other content: plain placeholder replace.
    Non-text / non-json content falls back to a raw copy (no transformation).
    """
    if ext == ".json":
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw
        transformed = _transform_json_paths(data, repo_root.as_posix())
        rendered = json.dumps(transformed, indent=2, ensure_ascii=False) + "\n"
        return _substitute_placeholders(rendered, repo_root, claude_home).encode("utf-8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return _substitute_placeholders(text, repo_root, claude_home).encode("utf-8")


def template_content(source: Path, repo_root: Path, claude_home: Path) -> bytes:
    """Read source, apply templating, return bytes to write at dest."""
    return _template_bytes(source.read_bytes(), source.suffix.lower(), repo_root, claude_home)


def _git_show_at(repo_root: Path, sha: str, rel_path: str) -> bytes | None:
    """Return file bytes at `sha:rel_path` in `repo_root`'s git history.

    None on any failure — no git repo, unknown sha, or the path didn't exist
    at that commit. Callers must treat None as "no base to diff against" and
    fall back to plain union (no pruning).
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{sha}:{rel_path}"],
            cwd=repo_root,
            capture_output=True,
            timeout=_ENV_SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# ---------- planning ----------


def build_plan(
    manifest: dict[str, Any],
    repo_root: Path,
    target_root_override: str | None = None,
) -> Plan:
    target_root = _expand(target_root_override or manifest.get("target_root", "~/.claude"))
    current_sha = current_git_sha(repo_root)
    state, previous_sha = detect_state(target_root, current_sha)

    actions: list[Action] = []

    if state == "current":
        return Plan(
            state=state,
            actions=actions,
            backup_path=None,
            current_sha=current_sha,
            previous_sha=previous_sha,
            target_root=target_root,
            repo_root=repo_root,
        )

    for group in manifest.get("groups") or []:
        if not group.get("enabled"):
            continue
        gid = group.get("id", "?")
        for entry in group.get("files") or []:
            src = repo_root / entry["source"]
            install_as = entry.get("install_as")
            if install_as == "user_mcp_registrations":
                actions.extend(
                    _plan_mcp_user_registrations(
                        src, repo_root, target_root, _user_mcp_config_path()
                    )
                )
                continue
            if install_as is not None:
                raise ValueError(f"manifest group {gid!r}: unknown install_as {install_as!r}")
            dest = target_root / entry["dest"]
            # `merge: true` → deep-merge JSON instead of plain overwrite.
            # Preserves user keys not owned by jarvis (M3 #338).
            kind = "merge_json" if entry.get("merge") else "copy_file"
            actions.append(
                Action(
                    kind=kind,
                    source=str(src),
                    dest=str(dest),
                    template=bool(entry.get("template")),
                    group=gid,
                )
            )
        for entry in group.get("directories") or []:
            src = repo_root / entry["source"]
            dest = target_root / entry["dest"]
            include = entry.get("include")
            if not include and entry.get("dest") == "rules":
                # The rules carrier is where deleting a Tier-B rule file must
                # actually remove it from ~/.claude/rules/ on next apply. A
                # glob-based (no include:) entry skips orphan-detection (see
                # test_directory_without_include_skips_orphan_check — that's
                # deliberate for e.g. the skills group), which for `rules`
                # means a deleted file silently comes back (#1274 AC4).
                # ceiling: only `dest == "rules"` is guarded — a future carrier
                # group needing the same delete-detection guarantee needs its
                # own `== "<name>"` branch here. Upgrade path: a manifest-level
                # `require_include: true` flag, read the same way `include`/
                # `template` already are, so the guard is declarative instead
                # of an enumerated string list.
                raise ValueError(
                    f"manifest group {gid!r}: directories entry {entry.get('source')!r} "
                    "dest=rules has no `include:` whitelist — the rules carrier "
                    "must declare one explicitly so deletions are detectable"
                )
            actions.append(
                Action(
                    kind="copy_dir",
                    source=str(src),
                    dest=str(dest),
                    template=bool(entry.get("template")),
                    group=gid,
                    note=f"include={include}" if include else "",
                )
            )
            # Orphan cleanup (#576 #927): if the entry pins an `include` whitelist
            # and the destination already exists, anything under dest not in
            # the whitelist is a leftover from a previous install whose
            # source/manifest no longer lists it. Move each leftover to a
            # `.skills-orphaned/` sibling OUTSIDE the skills dir so the skill
            # loader never picks it up (naming it .bak.orphan inside skills/
            # was the original bug — Claude Code loads any subdir regardless
            # of suffix).
            # Skip names containing `.bak.` — leftovers from the old naming
            # scheme; the suffix chain guard still prevents re-quarantine.
            if include and dest.exists() and dest.is_dir():
                orphan_dir = dest.parent / ".skills-orphaned"
                allowed = set(include)
                for child in sorted(dest.iterdir()):
                    if child.name in allowed:
                        continue
                    if ".bak." in child.name:
                        continue
                    orphan_dest = orphan_dir / child.name
                    if orphan_dest.exists():
                        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                        orphan_dest = orphan_dir / f"{child.name}-{stamp}"
                    actions.append(
                        Action(
                            kind="prune_orphan",
                            source=str(child),
                            dest=str(orphan_dest),
                            group=gid,
                            note=f"absent from {entry['dest']} include whitelist",
                        )
                    )

    for legacy in find_legacy_parent_mcp(repo_root):
        actions.append(
            Action(
                kind="quarantine_file",
                source=str(legacy),
                dest=str(_quarantine_dest(legacy)),
                group="legacy_mcp",
                note="parent-dir .mcp.json shadows ~/.claude/.mcp.json",
            )
        )

    actions.append(
        Action(
            kind="write_version",
            source=None,
            dest=str(target_root / manifest.get("version_marker", ".jarvis-version")),
            note=current_sha,
        )
    )

    current_platform = _platform()
    for env in manifest.get("env_vars") or []:
        # `platforms` is optional in the schema — omitted entries apply
        # everywhere. When present, it must include the running platform
        # or the action is skipped (silent platform-scoped opt-out).
        platforms = env.get("platforms")
        if platforms is not None and current_platform not in platforms:
            continue
        value = env.get("value", "").format(repo_root=str(repo_root))
        actions.append(
            Action(
                kind="set_env",
                source=None,
                dest=env["name"],
                note=value,
            )
        )

    # Backup only when target_root already exists AND we have destructive actions.
    has_writes = any(a.kind in {"copy_file", "copy_dir", "merge_json"} for a in actions)
    backup_path: Path | None = None
    if target_root.exists() and has_writes:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = (manifest.get("backup") or {}).get("prefix", ".claude.backup-")
        backup_path = target_root.parent / f"{prefix}{stamp}"

    return Plan(
        state=state,
        actions=actions,
        backup_path=backup_path,
        current_sha=current_sha,
        previous_sha=previous_sha,
        target_root=target_root,
        repo_root=repo_root,
    )


# ---------- execution ----------


def _copy_dir(
    src: Path,
    dest: Path,
    include: Iterable[str] | None,
    template: bool,
    repo_root: Path,
    claude_home: Path,
) -> None:
    # A manifest `directories:` entry may name a source that doesn't exist
    # yet (e.g. a `rules` group declared ahead of the first rule file) —
    # treat that as "nothing to install for this entry" rather than crashing
    # install.ps1 -Apply for every user (#1274).
    if not src.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    allowed = set(include) if include else None
    for child in src.iterdir():
        if allowed is not None and child.name not in allowed:
            continue
        if child.is_dir():
            _copy_dir(child, dest / child.name, None, template, repo_root, claude_home)
        else:
            _copy_file(child, dest / child.name, template, repo_root, claude_home)


def _copy_file(
    src: Path,
    dest: Path,
    template: bool,
    repo_root: Path,
    claude_home: Path,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if template:
        dest.write_bytes(template_content(src, repo_root, claude_home))
    else:
        shutil.copy2(src, dest)


# Keys inside `settings.json.hooks` and `.mcp.json.mcpServers` are treated
# as "wholesale-replace on conflict": when the source declares a hook event
# or MCP server, it overwrites the target's entry for that key and leaves
# every other key alone. Per-child-dict replace is the only strategy that
# stays idempotent (re-apply never duplicates) while still letting users
# keep custom entries under events/servers jarvis doesn't own.
_JARVIS_OWNED_REPLACE_PARENTS = ("hooks", "mcpServers")


def _deep_merge_jarvis_json(existing: Any, source: Any, base: Any = None) -> Any:
    """Merge `source` onto `existing` using jarvis-aware semantics.

    - For dict parents named in `_JARVIS_OWNED_REPLACE_PARENTS`
      (top-level `hooks`, `mcpServers`): each child key in `source`
      wholesale replaces the same key in `existing`; children in
      `existing` not mentioned by `source` are preserved.
    - For other dicts: recurse.
    - For list-valued leaves (either side a list): stable-dedup union,
      existing entries first. Claude Code treats `settings.json` arrays like
      `permissions.allow`/`permissions.deny` and `fallbackModel` (up to three
      model ids) as user-owned and does NOT merge them across scopes, so a
      wholesale source-wins replace silently drops every entry the user added
      (#4 — a user's multi-element `fallbackModel` array collapsed to the
      source's single scalar). A scalar on either side is coerced to a
      1-element list so a scalar/array mismatch unions cleanly.
    - For other non-dicts at the leaf: `source` wins.

    `base` (optional) is the source's content at the previously-installed
    commit — the missing third state that lets list-leaf merges distinguish
    "jarvis removed this entry upstream" (prune from `existing`) from "the
    user added this entry locally, it was never in any source version"
    (always preserved, since it's never in `base`). Pass `None` (default) to
    reproduce the plain union-only behavior — used when there's no previous
    install, no git history, or the file wasn't tracked at that commit.

    The same distinction applies one level up, at whole dict keys: a key
    present in `base` but dropped from `source` (e.g. a deprecated top-level
    setting like `skillOverrides`) is pruned from `existing` too, provided
    `existing` still matches what `base` had there — i.e. the local mirror
    was never customized away from the installed default. A key the user
    edited locally so it differs from `base` is left alone; a key that was
    never in `base` at all (genuinely user-added) is untouched regardless.

    Not a general-purpose deep-merge — tuned for the two files M3 ships.
    """
    if not isinstance(existing, dict) or not isinstance(source, dict):
        return source
    base_dict = base if isinstance(base, dict) else {}
    out = dict(existing)
    if base is not None:
        for key in base_dict:
            if key not in source and key in out and out[key] == base_dict[key]:
                del out[key]
    for key, src_val in source.items():
        if (
            key in _JARVIS_OWNED_REPLACE_PARENTS
            and isinstance(src_val, dict)
            and isinstance(out.get(key), dict)
        ):
            merged_child = dict(out[key])
            for child_key, child_val in src_val.items():
                merged_child[child_key] = child_val
            out[key] = merged_child
        elif isinstance(src_val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_jarvis_json(out[key], src_val, base_dict.get(key))
        elif isinstance(src_val, list) or isinstance(out.get(key), list):
            out[key] = _union_list_leaf(out.get(key), src_val, base_dict.get(key))
        else:
            out[key] = src_val
    return out


def _union_list_leaf(existing: Any, source: Any, base: Any = None) -> list[Any]:
    """Stable-dedup union of two list-valued leaves, existing entries first.

    Either argument may be a scalar (coerced to a 1-element list) or absent
    (``None`` → empty list). Preserves order and drops duplicates by value,
    so re-applying the installer is idempotent. See `_deep_merge_jarvis_json`
    for why list leaves union rather than source-wins.

    When `base` is given, entries present in `base` but absent from `source`
    are treated as deliberately removed upstream and pruned from `existing`
    before the union — this is what lets a source-side deletion actually
    reach the mirror instead of surviving forever via the union. Entries
    never seen in `base` (genuinely user-added) are untouched by pruning.
    """
    existing_items = (
        existing if isinstance(existing, list) else ([] if existing is None else [existing])
    )
    source_items = source if isinstance(source, list) else ([] if source is None else [source])
    if base is not None:
        base_items = base if isinstance(base, list) else [base]
        removed = [item for item in base_items if item not in source_items]
        existing_items = [item for item in existing_items if item not in removed]
    merged: list[Any] = list(existing_items)
    for item in source_items:
        if item not in merged:
            merged.append(item)
    return merged


def _merge_json_file(
    src: Path,
    dest: Path,
    template: bool,
    repo_root: Path,
    claude_home: Path,
    previous_sha: str | None = None,
) -> None:
    """Write `src` to `dest`, deep-merging with any existing dest JSON.

    If dest exists and parses as JSON, merge (user keys jarvis doesn't own
    are preserved). If dest is absent or unparseable, fall through to a
    plain write — identical to `_copy_file` in that case.

    `previous_sha`, when given, is used to fetch `src`'s content as of the
    previously-installed commit (`git show <sha>:<rel_path>`) as the merge's
    "base" state — see `_deep_merge_jarvis_json`. Any failure to resolve it
    (no git history, path not tracked at that commit, `src` outside
    `repo_root`) degrades silently to the old union-only behavior.
    """
    if template:
        new_bytes = template_content(src, repo_root, claude_home)
    else:
        new_bytes = src.read_bytes()
    try:
        new_data = json.loads(new_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Not JSON — fall back to plain write. Shouldn't happen for
        # manifest entries flagged `merge: true`, but safe by default.
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new_bytes)
        return

    base_data: Any = None
    if previous_sha and dest.exists():
        try:
            rel_src = src.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel_src = None
        if rel_src:
            base_bytes = _git_show_at(repo_root, previous_sha, rel_src)
            if base_bytes is not None:
                if template:
                    base_bytes = _template_bytes(
                        base_bytes, src.suffix.lower(), repo_root, claude_home
                    )
                try:
                    base_data = json.loads(base_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    base_data = None

    merged: Any = new_data
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
            merged = _deep_merge_jarvis_json(existing, new_data, base_data)
        except (OSError, json.JSONDecodeError):
            # Unparseable existing → treat as absent (backup already captured it).
            merged = new_data

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _set_env(name: str, value: str, platform: str) -> None:
    if platform == "windows":
        try:
            result = subprocess.run(
                ["setx", name, value],
                check=False,
                capture_output=True,
                timeout=_ENV_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(
                f"setx {name} timed out after {_ENV_SUBPROCESS_TIMEOUT}s",
                file=sys.stderr,
            )
            return
        if result.returncode != 0:
            stderr_msg = result.stderr.decode(errors="replace").strip()
            print(f"setx {name} failed (rc={result.returncode}): {stderr_msg}", file=sys.stderr)
    else:
        rc_files = [Path.home() / ".bashrc", Path.home() / ".zshrc"]
        line = f'export {name}="{value}"\n'
        for rc in rc_files:
            if not rc.exists():
                continue
            existing = rc.read_text(encoding="utf-8")
            if f"export {name}=" in existing:
                continue
            rc.write_text(existing + "\n# added by jarvis installer\n" + line, encoding="utf-8")


def _platform() -> str:
    return "windows" if os.name == "nt" else "posix"


def _copy_tolerant(src: str, dst: str, *, follow_symlinks: bool = True) -> str | None:
    """shutil.copy2 that tolerates entries which vanish or lock mid-copy.

    Claude Code actively rotates files under ``~/.claude/debug/`` while the
    installer runs. ``shutil.copytree`` defaults to aggregate-then-raise on
    such races, aborting the whole backup (#350). These artefacts aren't
    user data — skip with a stderr note and continue instead of failing
    the install.

    Tolerated errors:
    - ``FileNotFoundError`` — entry disappeared between scandir and copy
    - ``PermissionError`` — entry is held open with an exclusive lock
      (common on Windows while a log file is being rotated / appended to)
    """
    try:
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
    except (FileNotFoundError, PermissionError) as e:
        print(f"backup: skipped unreadable entry {src} ({e})", file=sys.stderr)
        return None


_BACKUP_MANIFEST_NAME = ".jarvis-backup-manifest.json"
_DESTRUCTIVE_KINDS = {"copy_file", "copy_dir", "merge_json"}


def _backup_target_root(target_root: Path, backup_path: Path, actions: list[Action]) -> None:
    """Back up only the paths ``actions`` will overwrite, not the whole target_root tree.

    ``target_root`` can hold hundreds of MB of unrelated runtime state
    (session transcripts under ``projects/``, telemetry, debug logs) that the
    installer never writes to and that may be actively growing/locked while a
    Claude Code session is running on the device. Copying the whole tree made
    backups slow enough to be interrupted mid-copy (see memory
    ``install_apply_not_during_active_claude_session``). Scoping the backup to
    actual write targets keeps it fast and avoids racing live writers.

    A manifest of the touched relative paths ships alongside the backup so
    ``rollback`` can undo exactly this set — including dest paths that didn't
    exist yet pre-apply (nothing to restore, but still removed on rollback).

    ``symlinks=True`` preserves symlinks as symlinks rather than dereferencing;
    combined with ``ignore_dangling_symlinks=True`` this future-proofs against
    broken junctions inside the target tree (Claude Code can create them).
    """
    touched: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if action.kind not in _DESTRUCTIVE_KINDS:
            continue
        try:
            rel_str = Path(action.dest).relative_to(target_root).as_posix()
        except ValueError:
            continue
        if rel_str in seen:
            continue
        seen.add(rel_str)
        touched.append(rel_str)

    backup_path.mkdir(parents=True, exist_ok=True)
    for rel_str in touched:
        src = target_root / rel_str
        if not src.exists():
            continue
        dst = backup_path / rel_str
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                copy_function=_copy_tolerant,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _copy_tolerant(str(src), str(dst))

    (backup_path / _BACKUP_MANIFEST_NAME).write_text(
        json.dumps(touched, indent=2), encoding="utf-8"
    )


def apply_plan(
    plan: Plan,
    manifest: dict[str, Any],
    run_env: Callable[[str, str, str], None] | None = _set_env,
    register_mcp: Callable[[str, dict[str, Any]], None] | None = _register_mcp_user,
    prune_mcp: Callable[[str], None] | None = _prune_mcp_user,
) -> None:
    if plan.state == "current":
        return
    if plan.backup_path is not None:
        _backup_target_root(plan.target_root, plan.backup_path, plan.actions)

    plan.target_root.mkdir(parents=True, exist_ok=True)

    for action in plan.actions:
        if action.kind == "copy_file":
            _copy_file(
                Path(action.source),
                Path(action.dest),
                action.template,
                plan.repo_root,
                plan.target_root,
            )
        elif action.kind == "merge_json":
            _merge_json_file(
                Path(action.source),
                Path(action.dest),
                action.template,
                plan.repo_root,
                plan.target_root,
                plan.previous_sha,
            )
        elif action.kind == "copy_dir":
            # Re-derive include from manifest — cheaper than threading it through.
            include = _include_for(manifest, action.group, action.source, plan.repo_root)
            _copy_dir(
                Path(action.source),
                Path(action.dest),
                include,
                action.template,
                plan.repo_root,
                plan.target_root,
            )
        elif action.kind == "quarantine_file":
            src = Path(action.source)
            dst = Path(action.dest)
            if src.exists():
                src.rename(dst)
                print(f"quarantined legacy {src} -> {dst}", file=sys.stderr)
        elif action.kind == "prune_orphan":
            src = Path(action.source)
            dst = Path(action.dest)
            # dst was computed at plan time; recompute if a collision appeared
            # since (rare race during long installs).
            if dst.exists():
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                dst = dst.parent / f"{dst.name}-{stamp}"
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                print(f"quarantined orphan {src} -> {dst}", file=sys.stderr)
        elif action.kind == "register_mcp_user":
            if register_mcp is not None:
                payload = json.loads(action.note)
                register_mcp(payload["name"], payload["spec"])
        elif action.kind == "prune_mcp_user":
            if prune_mcp is not None:
                prune_mcp(action.dest)
        elif action.kind == "write_version":
            Path(action.dest).write_text(action.note + "\n", encoding="utf-8")
        elif action.kind == "set_env":
            if run_env is not None:
                run_env(action.dest, action.note, _platform())


def _include_for(
    manifest: dict[str, Any],
    group_id: str,
    source: str,
    repo_root: Path,
) -> list[str] | None:
    """Return include filter for a directory group if source matches.

    Compares as absolute paths (action.source is absolute via build_plan;
    entry['source'] is repo-relative in the manifest, resolved against
    repo_root here). Earlier revisions compared a relative manifest path
    against an absolute action path and silently never matched, disabling
    every directory-group whitelist in production (caught by
    `test_apply_plan_creates_files_and_version_marker` after #413).
    """
    source_abs = Path(source).resolve()
    for group in manifest.get("groups") or []:
        if group.get("id") != group_id:
            continue
        for entry in group.get("directories") or []:
            entry_abs = (repo_root / entry["source"]).resolve()
            if entry_abs == source_abs:
                return entry.get("include")
    return None


# ---------- rollback / health ----------


def prune_backups(target_root: Path, prefix: str, retain: int) -> list[Path]:
    parent = target_root.parent
    if not parent.exists():
        return []
    backups = sorted(
        (p for p in parent.iterdir() if p.is_dir() and p.name.startswith(prefix)),
        key=lambda p: p.name,
    )
    dropped: list[Path] = []
    while len(backups) > retain:
        victim = backups.pop(0)
        shutil.rmtree(victim, ignore_errors=True)
        dropped.append(victim)
    return dropped


def rollback(target_root: Path, backup_path: Path) -> None:
    """Restore ``target_root`` from ``backup_path``.

    Scoped backups (see ``_backup_target_root``) carry a manifest of exactly
    which relative paths were touched — rollback removes and restores only
    those, leaving unrelated target_root state (session transcripts, caches)
    untouched. Backups without a manifest (pre-scoping legacy format, or a
    hand-built directory as in tests/manual ``--rollback <path>`` use) fall
    back to a full wholesale replace.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"backup {backup_path} not found")

    manifest_file = backup_path / _BACKUP_MANIFEST_NAME
    if not manifest_file.exists():
        if target_root.exists():
            shutil.rmtree(target_root)
        shutil.copytree(backup_path, target_root)
        return

    touched: list[str] = json.loads(manifest_file.read_text(encoding="utf-8"))
    for rel_str in touched:
        dst = target_root / rel_str
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        src = backup_path / rel_str
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def _rollback_failed_apply(plan: Plan) -> None:
    """Restore target_root to pre-apply state after a failed apply.

    - outdated/current path → restore from backup (backup_path is set).
    - fresh path → backup_path is None (nothing to restore from), so rmtree
      the half-written target so the next run starts clean. Without this,
      a failed fresh install leaves a stub that `detect_state` reads as
      outdated, masking the real failure.
    """
    if plan.backup_path and plan.backup_path.exists():
        print(f"rolling back from {plan.backup_path}", file=sys.stderr)
        rollback(plan.target_root, plan.backup_path)
        return
    if plan.state == "fresh" and plan.target_root.exists():
        print(f"fresh install failed — removing {plan.target_root}", file=sys.stderr)
        shutil.rmtree(plan.target_root, ignore_errors=True)


HEALTH_CHECK_TIMEOUT_DEFAULT = 30


# SIGKILL is POSIX-only — absent on Windows. _kill_tree references it solely
# inside an ``os.name != "nt"`` branch, so it is never evaluated on Windows
# today. Bind it through getattr at module load so a future refactor that hoists
# the signal to a default arg / constant can't raise AttributeError at import
# time on Windows (the platform this installer exists to support). Falls back to
# SIGTERM, which always exists, if SIGKILL is ever unavailable.
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _kill_window_is_failure(returncode: int | None, os_name: str) -> bool:
    """True when a timed-out process self-exited non-zero in the kill window.

    A process can self-exit non-zero in the race between the timeout firing and
    _kill_tree landing (e.g. a slow venv import that crashes at t=timeout+ε).
    That is a genuine FAIL, not an inconclusive timeout — classifying it
    "timeout" leaves a broken apply in place with no rollback.

    On POSIX a positive returncode means the process exited on its own; a
    negative returncode is our SIGKILL (the real timeout path). On Windows
    there is no signal convention — TerminateProcess yields exit 1,
    indistinguishable from a real failure — so we stay conservative there and
    keep treating it as a timeout.
    """
    return os_name != "nt" and returncode is not None and returncode > 0


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate proc AND its descendants; must never raise.

    proc.kill() reaps only the direct child. Health commands spawn
    grandchildren (session-context.py re-execs itself into the venv python);
    a surviving grandchild keeps running — and keeps any inherited handles
    open — long after the installer gave up on the command.

    Precondition (POSIX): proc must have been spawned with
    ``start_new_session=True`` so it is its own process-group leader — the
    ``os.killpg(proc.pid, ...)`` path assumes PGID == PID. The sole call site
    in run_health_check satisfies this; document it here to prevent misuse if
    _kill_tree is ever reused for a proc spawned without a new session.
    """
    if os.name == "nt":
        try:
            # /T walks the tree by parent PID — the direct child is still
            # alive here (we only reach this on TimeoutExpired), so the
            # chain is discoverable.
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                # taskkill failed (access denied, already exited, etc.) —
                # fall through to proc.kill() as a last resort.
                try:
                    proc.kill()
                except OSError:
                    pass
        except (OSError, subprocess.TimeoutExpired):
            # Fallback must honour the "never raise" contract: killing an
            # already-exited process can surface OSError on Windows.
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            # start_new_session=True at spawn made proc a group leader.
            os.killpg(proc.pid, _SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_health_check(manifest: dict[str, Any], repo_root: Path) -> tuple[str, list[str]]:
    """Run manifest health-check commands. Returns (status, logs).

    status: "ok" — every command exited 0; "fail" — a command exited non-zero
    or could not be spawned; "timeout" — a command outlived its time limit and
    its process tree was killed. Callers must treat "timeout" as inconclusive,
    NOT as evidence the apply is broken — see main().
    """
    hc = manifest.get("health_check") or {}
    if not hc.get("enabled"):
        return "ok", []
    timeout = int(hc.get("timeout", HEALTH_CHECK_TIMEOUT_DEFAULT))
    logs: list[str] = []
    for cmd in hc.get("commands") or []:
        # Use shlex so paths with spaces survive — `cmd.split()` breaks them.
        # posix=False on Windows keeps backslashes intact. A malformed entry
        # (unterminated quote) raises ValueError — catch it as a clean "fail"
        # rather than letting an unformatted traceback escape run_health_check.
        try:
            argv = shlex.split(cmd, posix=(os.name != "nt"))
        except ValueError as exc:
            logs.append(f"FAIL {cmd}: malformed command: {exc}")
            return "fail", logs
        # Resolve portable python/python3 tokens to the running interpreter.
        # On Windows python3 is absent; on some Linux distros python is absent.
        if argv and argv[0] in ("python", "python3"):
            argv[0] = sys.executable
        # Output goes to temp FILES, never pipes. With the prior capture_output=True
        # approach, a health command that spawns its own children (session-context.py
        # re-execs into the venv python) left a grandchild holding inherited pipe
        # write-handles; once the timeout killed the direct child, the pipe never
        # reached EOF and the parent blocked forever. File reads cannot block on EOF,
        # so even a grandchild the tree-kill misses can't wedge the installer.
        # stdin=DEVNULL keeps children from waiting on console input.
        popen_kwargs: dict[str, Any] = {}
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True  # killable as a group
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                err_path = Path(td) / "err"
                timed_out = False
                with open(err_path, "wb") as err_f:
                    proc = subprocess.Popen(
                        argv,
                        cwd=repo_root,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=err_f,
                        **popen_kwargs,
                    )
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        _kill_tree(proc)
                        try:
                            proc.wait(timeout=10)  # reap zombie; bounded to avoid re-hang
                        except subprocess.TimeoutExpired:
                            pass  # unkillable (D-state) — proceed, zombie reaped at exit
                        # A timed-out process that self-exited non-zero in the
                        # kill window is a real failure, not an inconclusive
                        # timeout — see _kill_window_is_failure for the platform
                        # reasoning. Don't leave a broken apply unrolled-back.
                        if _kill_window_is_failure(proc.returncode, os.name):
                            logs.append(
                                f"FAIL {cmd} exit={proc.returncode} — exited "
                                f"non-zero during kill window (timed out then "
                                f"self-failed)"
                            )
                            return "fail", logs
                        timed_out = True
                # Decode like the old capture path: locale-independent UTF-8,
                # errors="replace" survives rogue bytes on cp1251 consoles (#352).
                # PermissionError: surviving grandchild may hold the write handle on Windows.
                try:
                    stderr = err_path.read_bytes().decode("utf-8", errors="replace")
                except PermissionError:
                    stderr = "<stderr unavailable — file locked by surviving child>"
                if timed_out:
                    logs.append(
                        f"TIMEOUT {cmd}: no exit after {timeout}s — "
                        f"process tree killed; stderr={stderr[:200]}"
                    )
                    return "timeout", logs
        except OSError as exc:
            logs.append(f"FAIL {cmd}: {exc}")
            return "fail", logs
        if proc.returncode != 0:
            logs.append(f"FAIL {cmd} exit={proc.returncode} stderr={stderr[:200]}")
            return "fail", logs
        logs.append(f"OK   {cmd}")
    return "ok", logs


# ---------- printing ----------


def format_plan(plan: Plan) -> str:
    lines = [
        f"state:        {plan.state}",
        f"repo_root:    {plan.repo_root}",
        f"target_root:  {plan.target_root}",
        f"current_sha:  {plan.current_sha}",
        f"previous_sha: {plan.previous_sha or '(none)'}",
        f"backup:       {plan.backup_path or '(not needed)'}",
        f"actions:      {len(plan.actions)}",
    ]
    if plan.actions:
        lines.append("")
        for a in plan.actions:
            if a.kind == "copy_file":
                lines.append(
                    f"  copy_file  [{a.group:>14}] {a.source} -> {a.dest}"
                    + ("  (template)" if a.template else "")
                )
            elif a.kind == "merge_json":
                lines.append(
                    f"  merge_json [{a.group:>14}] {a.source} -> {a.dest}"
                    + ("  (template)" if a.template else "")
                )
            elif a.kind == "copy_dir":
                extra = f"  {a.note}" if a.note else ""
                lines.append(f"  copy_dir   [{a.group:>14}] {a.source} -> {a.dest}{extra}")
            elif a.kind == "quarantine_file":
                lines.append(
                    f"  quarantine [{a.group:>14}] {a.source} -> {a.dest}"
                    + (f"  ({a.note})" if a.note else "")
                )
            elif a.kind == "prune_orphan":
                lines.append(
                    f"  prune_orph [{a.group:>14}] {a.source} -> {a.dest}"
                    + (f"  ({a.note})" if a.note else "")
                )
            elif a.kind == "register_mcp_user":
                lines.append(f"  mcp_user   [{a.group:>14}] claude mcp add -s user {a.dest}")
            elif a.kind == "prune_mcp_user":
                lines.append(
                    f"  mcp_prune  [{a.group:>14}] claude mcp remove -s user {a.dest}"
                    "  (orphan: not in source)"
                )
            elif a.kind == "write_version":
                lines.append(f"  write_ver  -> {a.dest}  sha={a.note[:12]}")
            elif a.kind == "set_env":
                lines.append(f"  set_env    {a.dest}={a.note}")
    return "\n".join(lines)


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    """Install/sync Jarvis agent machinery into ~/.claude/.

    Exit codes:
      0  success
      2  apply error (rolled back)
      3  health check non-zero exit (rolled back)
      4  health check timeout — inconclusive, apply left in place
      5  refused --apply from a git worktree checkout (no write performed)
    """
    # Line-buffer stdout/stderr so per-action progress shows in real time even
    # when output is captured through a pipe (install.ps1 tees it). Otherwise
    # Python block-buffers a piped stdout and the whole run appears only at
    # exit — which reads as a hang on slow steps like `claude mcp add`.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass  # replaced/non-TextIOWrapper stream (e.g. pytest capture)

    parser = argparse.ArgumentParser(
        prog="jarvis-installer",
        description="Install/sync Jarvis agent machinery into ~/.claude/.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"Path to manifest (default: {DEFAULT_MANIFEST} next to repo root)",
    )
    parser.add_argument("--target", default=None, help="Override target_root")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the install. Default is dry-run (plan only).",
    )
    parser.add_argument(
        "--skip-env",
        action="store_true",
        help="Skip env-var writes even on --apply (useful in CI/tests).",
    )
    parser.add_argument(
        "--rollback",
        metavar="BACKUP_PATH",
        default=None,
        help="Restore target_root from BACKUP_PATH and exit.",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip post-install health check (not recommended).",
    )
    parser.add_argument(
        "--fix-env-encoding",
        action="store_true",
        help="Rewrite .env files with BOM/CRLF to UTF-8-no-BOM + LF (only with --apply).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest) if args.manifest else repo_root / DEFAULT_MANIFEST
    manifest = load_manifest(manifest_path)

    if args.rollback:
        target_root = _expand(args.target or manifest.get("target_root", "~/.claude"))
        rollback(target_root, Path(args.rollback))
        print(f"rolled back {target_root} from {args.rollback}")
        return 0

    plan = build_plan(manifest, repo_root, args.target)
    print(format_plan(plan))

    # Scan .env files for BOM/CRLF (always runs — warns on issues).
    env_findings = _scan_env_encoding(plan.target_root, repo_root)
    for env_path, issues, is_user in env_findings:
        note = " (warn-only — repo-root .env, not fixable)" if not is_user else ""
        print(_ENV_WARN_MSG.format(env_path, issues) + note, file=sys.stderr)

    if not args.apply:
        if args.fix_env_encoding:
            print(
                "WARN: --fix-env-encoding has no effect without --apply",
                file=sys.stderr,
            )
        print("\n(dry-run — re-run with --apply to execute)")
        return 0

    if _is_git_worktree_checkout(repo_root):
        print(
            f"\nERROR: refusing --apply from a git worktree checkout ({repo_root}).\n"
            "Global-scope MCP registration (`claude mcp add -s user`) must run from "
            "the main checkout, not a linked worktree — re-run the installer from "
            "the main checkout directory.",
            file=sys.stderr,
        )
        return 5

    # `state == "current"` short-circuit must NOT skip --fix-env-encoding:
    # the common re-run case is a user with an installed-but-encoding-broken
    # ~/.claude who runs `install.ps1 -Apply -FixEncoding` to repair it.
    if plan.state != "current":
        env_runner = None if args.skip_env else _set_env
        try:
            _check_mcp_venv_dependencies(plan.actions, repo_root)
            apply_plan(plan, manifest, run_env=env_runner)
        except Exception as exc:  # noqa: BLE001
            print(f"\napply failed: {exc}", file=sys.stderr)
            _rollback_failed_apply(plan)
            return 2
    else:
        print("\nno-op — target already at current SHA")

    # Re-scan after apply_plan: on fresh install the pre-apply scan ran
    # against a non-existent target_root and returned nothing. The .env
    # files were created by apply_plan, so only a post-apply scan sees
    # them. Re-emit warnings for anything NEW (not already warned about
    # pre-apply) so users on fresh installs aren't blindsided by silent
    # encoding issues.
    pre_paths = {str(p) for p, _, _ in env_findings}
    post_findings = _scan_env_encoding(plan.target_root, repo_root)
    new_findings = [(p, i, u) for p, i, u in post_findings if str(p) not in pre_paths]
    for env_path, issues, is_user in new_findings:
        note = " (warn-only — repo-root .env, not fixable)" if not is_user else ""
        print(_ENV_WARN_MSG.format(env_path, issues) + note, file=sys.stderr)

    if args.fix_env_encoding:
        user_envs = [(p, i) for p, i, u in post_findings if u]
        if user_envs:
            print(
                f"fixing {len(user_envs)} .env file(s) (BOM/CRLF → UTF-8-no-BOM + LF)",
                file=sys.stderr,
            )
            failed = 0
            for env_path, issues in user_envs:
                try:
                    _fix_env_encoding(env_path, issues)
                except OSError as exc:
                    failed += 1
                    print(
                        f"WARN: could not fix {env_path}: {exc}",
                        file=sys.stderr,
                    )
            if failed:
                print(
                    f"WARN: {failed}/{len(user_envs)} .env fix(es) failed (see above); other apply work succeeded",
                    file=sys.stderr,
                )
        else:
            print("no fixable .env files found", file=sys.stderr)

    if not args.skip_health_check:
        status, logs = run_health_check(manifest, repo_root)
        for line in logs:
            print(line)
        if status == "timeout":
            # Inconclusive ≠ broken. A hung health command (script stuck on
            # network, grandchild that outlived its parent) says nothing
            # about whether the apply itself succeeded — rolling back here
            # would discard a completed, likely-good install. Leave it in
            # place and tell the operator to verify by hand.
            print(
                "\nhealth check timed out — apply left in place (NOT rolled back); "
                "verify manually or re-run the installer (install.ps1 / install.sh)",
                file=sys.stderr,
            )
            return 4
        if status == "fail":
            print("\nhealth check failed", file=sys.stderr)
            _rollback_failed_apply(plan)
            return 3

    backup_cfg = manifest.get("backup") or {}
    dropped = prune_backups(
        plan.target_root,
        backup_cfg.get("prefix", ".claude.backup-"),
        int(backup_cfg.get("retain", 5)),
    )
    for d in dropped:
        print(f"pruned old backup: {d}")

    print("\napply complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
