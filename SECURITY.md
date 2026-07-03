# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Email:** [Create a GitHub Security Advisory](../../security/advisories/new)

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment:** within 48 hours
- **Assessment:** within 7 days
- **Fix:** depends on severity, typically within 30 days

## Scope

This policy covers:
- `mcp-memory/server.py` (MCP server handling Supabase connections)
- `.github/workflows/` (CI/CD pipelines)
- Any credential or secret exposure
- Dependency vulnerabilities

## Out of Scope

- This is a personal project, not a production service
- Theoretical attacks requiring physical access to the developer's machines
- Social engineering attacks

## Security Measures

- **Gitleaks** pre-commit hook and CI workflow for secret scanning
- **Dependabot** for dependency updates (when enabled)
- **Claude code-review** on PRs (via `.github/workflows/code-review.yml`)
- Secrets stored in GitHub Actions secrets, never in code

## Supported Versions

Only the latest version on `main` branch is supported.
