# MCP Server Security Audit

Date: 2026-04-15
Scope: All MCP servers in `.mcp.json`

## Audit Summary

| Server | Risk | Read | Write | Network | Secrets access |
|--------|------|------|-------|---------|---------------|
| memory | Medium | Supabase DB | Supabase DB | Supabase API | Needs SUPABASE_KEY, VOYAGE_API_KEY |
| github | Medium | Repos, issues, PRs | Issues, PRs, files, branches | GitHub API | Needs GITHUB_TOKEN |
| firecrawl | Medium | Web pages | None | Arbitrary URLs | Needs FIRECRAWL_API_KEY |
| context7 | Low | Library docs | None | Upstash CDN | None |
| sequential-thinking | None | None | None | None | None |
| reddit | Low | Public Reddit | None | Reddit API | None |
| obsidian | Medium | Vault notes | Vault notes | None (local) | Needs OBSIDIAN_VAULT_PATH |

## Detailed Analysis

### memory (custom — `mcp-memory/server.py`)

**Capabilities:** Full CRUD on Supabase tables (memories, goals, task_outcomes, events, credential_registry).

**Risk: Medium**
- Reads/writes all Jarvis memory, goals, outcomes
- NEW: credential_registry access (metadata only, CHECK constraint prevents values)
- Network: Supabase REST API + Voyage AI embedding API
- No file system access beyond what Python can do

**Mitigations:**
- Supabase RLS could restrict to specific tables (not currently enabled — single-user project)
- credential_registry has DB-level CHECK constraint against storing secrets
- Voyage API is write-only (sends text for embedding, receives vectors)

**Recommendation:** Acceptable for single-user. If multi-user ever needed — add RLS policies.

---

### github (Copilot MCP — `api.githubcopilot.com`)

**Capabilities:** Full GitHub API access scoped to token permissions. Can read/write repos, issues, PRs, branches, files, releases, labels, projects.

**Risk: Medium**
- PUBLIC REPO: anything written to issues/PRs is visible to everyone
- Can create/delete branches, merge PRs
- Can push files (including creating new files with arbitrary content)

**Mitigations:**
- PreToolUse secret scanner hook blocks secret patterns in write operations
- Token scope can be narrowed (currently uses Copilot-provided token)
- Branch protection rules on GitHub prevent force-push to main

**Recommendation:** Secret scanner hook is the primary defense. Consider narrowing token scope if possible (Copilot MCP token scope is managed by GitHub, not configurable per-project).

---

### firecrawl (`firecrawl-mcp`)

**Capabilities:** Web scraping, crawling, search, URL mapping. Can fetch arbitrary URLs.

**Risk: Medium**
- Can send requests to arbitrary URLs (potential data exfiltration via URL params)
- Scrapes content — prompt injection risk from malicious web content
- No write capabilities to external services

**Mitigations:**
- Firecrawl is a read-oriented tool (scrape/search), not a data sender
- URL params could theoretically encode small amounts of data, but this is low bandwidth
- Prompt injection from scraped content is the bigger risk (mitigated by Claude's instruction following)

**Recommendation:** Acceptable. Low exfiltration bandwidth. Main risk is prompt injection from scraped pages — Claude's system prompt resistance is the defense here.

---

### context7 (`@upstash/context7-mcp`)

**Capabilities:** Fetch documentation for libraries/frameworks. Read-only.

**Risk: Low**
- Only accesses Upstash CDN with library documentation
- No write capabilities, no secrets required
- Cannot access arbitrary URLs

**Recommendation:** No changes needed.

---

### sequential-thinking (`@modelcontextprotocol/server-sequential-thinking`)

**Capabilities:** Structured thinking/reasoning aid. No external access.

**Risk: None**
- Pure computation, no network, no file system, no secrets
- Input/output stays within the session

**Recommendation:** No changes needed.

---

### reddit (`reddit-no-auth-mcp-server`)

**Capabilities:** Read public Reddit content. No authentication.

**Risk: Low**
- Read-only access to public Reddit
- No write capabilities
- No authentication needed (uses public API)

**Recommendation:** No changes needed.

---

### obsidian (`@bitbonsai/mcpvault`)

**Capabilities:** Read/write notes in Obsidian vault. Local filesystem access.

**Risk: Medium**
- Can read all personal notes in the vault
- Can write/modify/delete notes
- Local only — no network exfiltration through this server itself

**Mitigations:**
- Vault path is configurable (owner controls which vault is exposed)
- No network access — data stays on device
- Notes are personal but non-secret (owner's security philosophy: personal data leaks are acceptable risk)

**Recommendation:** Acceptable given owner's risk tolerance. If vault contains credentials or secrets, move those to a separate, non-exposed vault.

---

## Reference: Telegram MCP (not in .mcp.json, separate config)

The vendored Telegram MCP (`scripts/telegram-mcp-server.py`) has the best ACL model in the project:
- Two-tier: RW chats (full access) + RO chats (read only)
- Everything else invisible (filtered from list, access denied on read/write)
- ACL check happens BEFORE Telegram API call
- Config in `scripts/.env` (not in main .env)

This pattern should be the reference model for any future integration that supports write access to external services.

## Overall Findings

1. **Primary defense layer works:** PreToolUse secret scanner hook covers the two highest-risk write paths (GitHub + Bash)
2. **No over-privileged servers:** Each server has appropriate scope for its function
3. **Biggest remaining risk:** Prompt injection via firecrawl/web content could theoretically instruct Claude to exfiltrate via Bash. Mitigated by Claude's instruction hierarchy (system prompt > tool output).
4. **No changes needed now:** All servers are at appropriate risk levels for a single-user project with the current guardrails.
