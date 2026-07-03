"""
setup-device.py -- Interactive setup for Jarvis on a new device.
Run: python scripts/setup-device.py
Idempotent -- safe to re-run. Skips steps that are already done.
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = platform.system() == "Windows"
CLAUDE_BIN = "claude"  # resolved to absolute path in check_prerequisites()

# ── Formatting helpers ──────────────────────────────────────────────────────

# Use ANSI colors only if the terminal supports them
_SUPPORTS_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
GREEN = "\033[92m" if _SUPPORTS_COLOR else ""
YELLOW = "\033[93m" if _SUPPORTS_COLOR else ""
RED = "\033[91m" if _SUPPORTS_COLOR else ""
BOLD = "\033[1m" if _SUPPORTS_COLOR else ""
DIM = "\033[2m" if _SUPPORTS_COLOR else ""
RESET = "\033[0m" if _SUPPORTS_COLOR else ""


def ok(msg):
    print(f"  {GREEN}[OK]{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}[!!]{RESET} {msg}")


def fail(msg):
    print(f"  {RED}[FAIL]{RESET} {msg}")


def header(n, title):
    print(f"\n{BOLD}[{n}] {title}{RESET}")


def ask(prompt, default=""):
    """Prompt user for input with optional default."""
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default
    else:
        return input(f"  {prompt}: ").strip()


def run(cmd, **kwargs):
    """Run command, return (success, stdout)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
        return r.returncode == 0, r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


# ── Steps ───────────────────────────────────────────────────────────────────

def check_prerequisites():
    """Check required and optional CLI tools."""
    header(1, "Prerequisites")
    errors = 0

    # Python version
    v = sys.version_info
    if v >= (3, 11):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        fail(f"Python {v.major}.{v.minor} -- need 3.11+")
        errors += 1

    # Node.js (for MCP servers via npx)
    node_ok, node_ver = run(["node", "--version"])
    if node_ok:
        ok(f"Node.js {node_ver}")
    else:
        warn("Node.js not found -- needed for MCP servers (npx)")
        print(f"    {DIM}Install: https://nodejs.org{RESET}")

    # GitHub CLI
    gh_ok, _ = run(["gh", "auth", "status"])
    if gh_ok:
        ok("GitHub CLI (authenticated)")
    else:
        gh_exists = shutil.which("gh") is not None
        if gh_exists:
            warn("GitHub CLI found but not authenticated -- run: gh auth login")
        else:
            warn("GitHub CLI not found -- optional, for GitHub MCP server")
            print(f"    {DIM}Install: https://cli.github.com{RESET}")

    # Claude Code CLI
    # On Windows the executable is `claude.cmd` (npm shim); subprocess.run
    # with a list does NOT expand PATHEXT, so we resolve the absolute path
    # once via shutil.which (which DOES) and reuse it everywhere below.
    global CLAUDE_BIN
    CLAUDE_BIN = shutil.which("claude")
    if CLAUDE_BIN:
        ok("Claude Code CLI")
    else:
        fail("Claude Code CLI not found -- this is required")
        print(f"    {DIM}Install: https://claude.ai/code{RESET}")
        errors += 1

    return errors


def setup_venv():
    """Create Python venv and install dependencies."""
    header(2, "Python environment")
    venv_dir = ROOT / ".venv"

    if IS_WINDOWS:
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_pip = venv_dir / "Scripts" / "pip.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
        venv_pip = venv_dir / "bin" / "pip"

    if not venv_dir.exists():
        print("  Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        ok("Created .venv/")
    else:
        ok(".venv/ exists")

    # Install deps
    reqs = ROOT / "mcp-memory" / "requirements.txt"
    if reqs.exists():
        print("  Installing memory server dependencies...")
        result = subprocess.run(
            [str(venv_pip), "install", "-q", "-r", str(reqs)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            ok("Dependencies installed (supabase, mcp, voyageai, httpx)")
        else:
            # pip may fail but deps might already be installed -- verify
            check_ok, _ = run([str(venv_python), "-c", "import supabase, mcp, httpx"])
            if check_ok:
                ok("Dependencies already installed")
            else:
                err = (result.stderr or result.stdout or "unknown error").strip()
                fail(f"pip install failed: {err[:200]}")
                return 1
    return 0


def setup_env():
    """Create .env and interactively fill required secrets."""
    header(3, "Environment variables (.env)")
    env_file = ROOT / ".env"
    env_example = ROOT / ".env.example"

    # Read existing env vars if file exists
    env_vars = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
        ok(".env exists")
    else:
        if env_example.exists():
            shutil.copy2(env_example, env_file)
            ok("Created .env from .env.example")
            # Re-read
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
        else:
            env_file.write_text(
                "SUPABASE_URL=\nSUPABASE_KEY=\nGITHUB_TOKEN=\nFIRECRAWL_API_KEY=\n",
                encoding="utf-8",
            )
            ok("Created minimal .env")

    # Check required vars and prompt if missing
    changed = False
    supabase_url = env_vars.get("SUPABASE_URL", "")
    supabase_key = env_vars.get("SUPABASE_KEY", "")

    is_url_set = supabase_url and "your-project" not in supabase_url
    is_key_set = supabase_key and "your-" not in supabase_key

    if is_url_set and is_key_set:
        ok(f"SUPABASE_URL = {supabase_url[:40]}...")
        ok("SUPABASE_KEY = ****")
    else:
        print()
        print(f"  {BOLD}Supabase is required for cross-device memory.{RESET}")
        print(f"  {DIM}Get credentials: Supabase dashboard → Settings → API{RESET}")
        print(f"  {DIM}Free tier is sufficient. Create a project at supabase.com{RESET}")
        print()

        if not is_url_set:
            supabase_url = ask("SUPABASE_URL (https://xxx.supabase.co)")
            if supabase_url:
                env_vars["SUPABASE_URL"] = supabase_url
                changed = True

        if not is_key_set:
            supabase_key = ask("SUPABASE_KEY (anon public key)")
            if supabase_key:
                env_vars["SUPABASE_KEY"] = supabase_key
                changed = True

    # Optional vars -- just report status
    for var, label in [
        ("VOYAGE_API_KEY", "Voyage AI (semantic search -- optional, keyword fallback works)"),
        ("GITHUB_TOKEN", "GitHub token (for MCP GitHub server)"),
        ("FIRECRAWL_API_KEY", "Firecrawl (web research -- optional)"),
    ]:
        val = env_vars.get(var, "")
        if val:
            ok(f"{var} set")
        else:
            print(f"  {DIM}-{RESET} {label}: not set")

    # Write back if changed
    if changed:
        _write_env(env_file, env_vars)
        ok("Updated .env with your values")

    return env_vars


def _write_env(env_file, env_vars):
    """Update .env file preserving comments and structure, updating values."""
    lines = env_file.read_text(encoding="utf-8").splitlines()
    updated_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in env_vars:
                new_lines.append(f"{key}={env_vars[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any new keys not in original file
    for k, v in env_vars.items():
        if k not in updated_keys and v:
            new_lines.append(f"{k}={v}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def test_supabase(env_vars):
    """Test Supabase connection and check schema."""
    header(4, "Supabase connection")

    url = env_vars.get("SUPABASE_URL", "")
    key = env_vars.get("SUPABASE_KEY", "")

    if not url or not key or "your-" in url:
        warn("Skipping -- SUPABASE_URL/KEY not configured")
        return 1

    # Test connection via Python
    venv_python = _get_venv_python()
    test_script = f"""
import os
os.environ['SUPABASE_URL'] = '{url}'
os.environ['SUPABASE_KEY'] = '{key}'
from supabase import create_client
c = create_client('{url}', '{key}')
# Try reading memories table
try:
    r = c.table('memories').select('id').limit(1).execute()
    print('OK:memories')
except Exception as e:
    s = str(e)
    if '42P01' in s or 'does not exist' in s.lower():
        print('NO_TABLE')
    else:
        print(f'ERROR:{{s[:150]}}')
# Try reading goals table
try:
    r = c.table('goals').select('id').limit(1).execute()
    print('OK:goals')
except Exception as e:
    s = str(e)
    if '42P01' in s or 'does not exist' in s.lower():
        print('NO_TABLE:goals')
"""

    success, output = run([str(venv_python), "-c", test_script])
    if not success:
        fail("Could not connect to Supabase")
        print(f"    {DIM}Check SUPABASE_URL and SUPABASE_KEY in .env{RESET}")
        return 1

    has_schema = True
    for line in output.splitlines():
        if line == "OK:memories":
            ok("Connected -- memories table exists")
        elif line == "OK:goals":
            ok("Goals table exists")
        elif line == "NO_TABLE":
            warn("Connected, but memories table missing -- run the schema")
            has_schema = False
        elif line.startswith("NO_TABLE:goals"):
            warn("Goals table missing -- run the schema")
            has_schema = False
        elif line.startswith("ERROR:"):
            fail(f"Connection error: {line[6:]}")
            return 1

    if not has_schema:
        print()
        print(f"  {BOLD}Run mcp-memory/schema.sql in Supabase SQL Editor:{RESET}")
        print(f"  {DIM}Supabase dashboard → SQL Editor → paste file → Run{RESET}")
        print(f"  {DIM}File: {ROOT / 'mcp-memory' / 'schema.sql'}{RESET}")

    return 0


def setup_device_config():
    """Generate config/device.json with auto-detected values."""
    header(5, "Device config")
    config_file = ROOT / "config" / "device.json"

    if config_file.exists():
        with open(config_file, encoding="utf-8") as f:
            device = json.load(f)
        ok(f"Device: {device.get('name', '?')} ({device.get('os', '?')})")
        return

    hostname = socket.gethostname()
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    os_name = platform.system()
    if os_name == "Windows":
        os_str = f"Windows {platform.win32_ver()[0] or ''} {platform.version()}".strip()
    elif os_name == "Darwin":
        os_str = f"macOS {platform.mac_ver()[0]}"
    else:
        os_str = f"{os_name} {platform.version()}"

    device = {
        "name": hostname,
        "hostname": hostname,
        "os": os_str,
        "username": username,
        "repos_path": str(ROOT.parent),
        "home": str(Path.home()),
    }

    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(device, f, indent=2, ensure_ascii=False)

    ok(f"Generated config/device.json -- device: {hostname}")
    print(f"  {DIM}This file is gitignored (device-specific){RESET}")


SIBLING_REPOS = [
    {
        "dir": "claude-plugins-official",
        # If you fork the code-review plugin to adapt it, set
        # JARVIS_PLUGINS_FORK=your-username/claude-plugins-official. Otherwise
        # the upstream Anthropic repo is cloned directly.
        "fork": os.environ.get(
            "JARVIS_PLUGINS_FORK", "anthropics/claude-plugins-official"
        ),
        "upstream": "anthropics/claude-plugins-official",
        "purpose": "C16 code-review plugin (fork from Anthropic to adapt, or use upstream)",
    },
]


def setup_sibling_repos():
    """Clone sibling repos that live alongside jarvis (e.g. forked plugins)."""
    header(6, "Sibling repos")

    if not shutil.which("git"):
        warn("git not found -- skipping sibling repo setup")
        return

    repos_path = ROOT.parent

    for repo in SIBLING_REPOS:
        target = repos_path / repo["dir"]
        if target.exists():
            ok(f"{repo['dir']}/ exists at {target}")
            continue

        url = f"https://github.com/{repo['fork']}.git"
        print(f"  Cloning {repo['fork']} -> {target} ...")
        result = subprocess.run(
            ["git", "clone", url, str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            warn(f"Clone failed for {repo['dir']}: {err[:200]}")
            continue

        ok(f"Cloned {repo['dir']} -- {repo['purpose']}")

        upstream_url = f"https://github.com/{repo['upstream']}.git"
        upstream_result = subprocess.run(
            ["git", "-C", str(target), "remote", "add", "upstream", upstream_url],
            capture_output=True, text=True,
        )
        if upstream_result.returncode == 0:
            ok(f"  upstream remote -> {repo['upstream']}")
        else:
            warn(f"  could not add upstream remote: {upstream_result.stderr.strip()[:120]}")


# NB: marketplace name is `jarvis-fork-plugins` for historical reasons —
# only `code-review` is actually a fork (your-username/claude-plugins-official).
# The other 5 are upstream Anthropic plugins consumed via git-subdir directly.
# Kept the name to avoid forcing an opt-in re-add on existing devices.
CLAUDE_PLUGINS = [
    {
        "plugin": "code-review",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "C16 PR code-review (forked + adapted)",
    },
    {
        "plugin": "pr-review-toolkit",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "Specialized PR review agents (comments / tests / errors / types / quality / simplification)",
    },
    {
        "plugin": "session-report",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "HTML report of session usage — tokens / cache / subagents / skills / costly prompts",
    },
    {
        "plugin": "hookify",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "Author custom hooks via markdown rules (conversation pattern detection)",
    },
    {
        "plugin": "claude-md-management",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "Audit + improve CLAUDE.md files; capture session learnings",
    },
    {
        "plugin": "mcp-server-dev",
        "marketplace": "jarvis-fork-plugins",
        "marketplace_path": ".claude/marketplace",
        "purpose": "Skills for designing/building MCP servers (deployment, tool patterns, auth, interactive apps)",
    },
]


def install_claude_plugins():
    """Install Claude Code plugins from in-repo local marketplaces."""
    header(7, "Claude Code plugins")

    if not shutil.which("claude"):
        warn("claude CLI not found -- skipping plugin install")
        return

    # Group entries by (marketplace_name, marketplace_path) so we register +
    # refresh each marketplace once, regardless of how many plugins it serves.
    marketplaces = {}  # name -> (path, [entries])
    for entry in CLAUDE_PLUGINS:
        mp_name = entry["marketplace"]
        mp_path = ROOT / entry["marketplace_path"]
        marketplaces.setdefault(mp_name, (mp_path, []))[1].append(entry)

    for mp_name, (mp_path, entries) in marketplaces.items():
        if not mp_path.exists():
            warn(f"marketplace path missing: {mp_path}")
            continue

        # add: idempotent — first run registers, subsequent runs are no-op
        # ("already on disk"). Absolute path required so claude doesn't
        # mis-treat it as a Git source.
        add_result = subprocess.run(
            [CLAUDE_BIN, "plugins", "marketplace", "add", str(mp_path.resolve())],
            capture_output=True, text=True,
        )
        if add_result.returncode != 0:
            warn(f"marketplace add failed for {mp_name}: "
                 f"{add_result.stderr.strip()[:200]}")
            continue

        # update: re-reads marketplace.json from disk so newly-added plugin
        # entries become visible to install. Without this step, plugins added
        # to an already-registered marketplace would be invisible until the
        # user manually ran `claude plugins marketplace update`.
        update_result = subprocess.run(
            [CLAUDE_BIN, "plugins", "marketplace", "update", mp_name],
            capture_output=True, text=True,
        )
        if update_result.returncode != 0:
            warn(f"marketplace update failed for {mp_name}: "
                 f"{update_result.stderr.strip()[:200]}")
            # don't `continue` — install may still work if marketplace
            # already had the entry from a prior run

        for entry in entries:
            install_result = subprocess.run(
                [
                    CLAUDE_BIN, "plugins", "install",
                    f"{entry['plugin']}@{mp_name}",
                    "--scope", "user",
                ],
                capture_output=True, text=True,
            )
            if install_result.returncode == 0:
                ok(f"{entry['plugin']}@{mp_name} -- {entry['purpose']}")
            else:
                err = (install_result.stderr or install_result.stdout or "").strip()
                warn(f"install failed for {entry['plugin']}: {err[:200]}")


def verify_skills():
    """Check that skills and key config files are present."""
    header(8, "Project integrity")

    for name, path in [
        ("CLAUDE.md", ROOT / "CLAUDE.md"),
        ("config/SOUL.md", ROOT / "config" / "SOUL.md"),
        (".mcp.json", ROOT / ".mcp.json"),
        ("mcp-memory/server.py", ROOT / "mcp-memory" / "server.py"),
    ]:
        if path.exists():
            ok(name)
        else:
            fail(f"{name} missing")

    skills_dir = ROOT / ".claude" / "skills"
    if skills_dir.exists():
        skills = [d.name for d in skills_dir.iterdir()
                  if d.is_dir() and (d / "SKILL.md").exists()]
        ok(f"{len(skills)} skills: {', '.join(sorted(skills))}")
    else:
        warn("No skills found in .claude/skills/")


def print_summary(errors):
    """Print final summary and next steps."""
    print(f"\n{'=' * 50}")
    if errors == 0:
        print(f"{GREEN}{BOLD}Setup complete!{RESET}")
    else:
        print(f"{YELLOW}{BOLD}Setup complete with warnings.{RESET}")

    print(f"""
{BOLD}Next steps:{RESET}
  1. Open this project in Claude Code:
     cd {ROOT}
     claude

  2. Test that Jarvis works:
     /status

  3. Memory server starts automatically via .mcp.json.
     If you see memory errors, check .env values.

{DIM}Docs: README.md | Setup details: SETUP.md{RESET}
{DIM}Report issues: https://github.com/your-username/your-repo/issues{RESET}
""")


def _get_venv_python():
    venv_dir = ROOT / ".venv"
    if IS_WINDOWS:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{'=' * 50}")
    print(f"  Jarvis - Device Setup")
    print(f"{'=' * 50}{RESET}")
    print(f"  {DIM}Project: {ROOT}{RESET}")
    print(f"  {DIM}OS: {platform.system()} {platform.version()}{RESET}")

    errors = 0
    errors += check_prerequisites()
    errors += setup_venv()
    env_vars = setup_env()
    errors += test_supabase(env_vars)
    setup_device_config()
    setup_sibling_repos()
    install_claude_plugins()
    verify_skills()
    print_summary(errors)

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
