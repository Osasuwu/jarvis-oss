"""
token-refresh.py — Refresh Claude Code OAuth token across all tracked repos.

Usage:
  1. Run `claude setup-token` in a terminal, copy the token
  2. python scripts/token-refresh.py "paste-token-here"
     OR: set CLAUDE_TOKEN env var and run without args

Reads repos from config/repos.conf — no hardcoded repo list.
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS_CONF = os.path.join(ROOT, "config", "repos.conf")


def load_repos():
    """Read repos from config/repos.conf."""
    if not os.path.isfile(REPOS_CONF):
        print(f"Error: {REPOS_CONF} not found")
        sys.exit(1)
    repos = []
    with open(REPOS_CONF) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                repos.append(line)
    return repos


def main():
    token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_TOKEN", "")

    if not token:
        print("[token-refresh] ERROR: No token provided.")
        print()
        print("Steps:")
        print("  1. Open a terminal and run: claude setup-token")
        print("  2. Copy the token shown")
        print('  3. Run: python scripts/token-refresh.py "your-token"')
        sys.exit(1)

    if not token.startswith(("sk-ant-", "oat_")):
        print("[token-refresh] WARNING: Token doesn't look like a Claude token. Proceeding anyway.")

    repos = load_repos()
    print(f"[token-refresh] Updating GitHub secrets for {len(repos)} repos...")

    success = 0
    fail = 0

    for repo in repos:
        result = subprocess.run(
            ["gh", "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", repo],
            input=token, text=True, capture_output=True,
        )
        if result.returncode == 0:
            print(f"[token-refresh] + {repo}")
            success += 1
        else:
            print(f"[token-refresh] x {repo} — check gh auth and repo access")
            fail += 1

    print(f"\n[token-refresh] Done: {success} updated, {fail} failed.")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
