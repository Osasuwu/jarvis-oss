# Jarvis Threat Model

Date: 2026-04-15
Scope: Full system including multi-agent future (Federation & Delegation pillar)

## Data Classification

| Level | Definition | Examples | Storage rules |
|-------|-----------|----------|---------------|
| **Secret** | Raw credentials, API keys, tokens | `SUPABASE_KEY`, `VOYAGE_API_KEY`, SSH keys | `.env` only. Never in memory, git, issues, logs |
| **Sensitive** | Personal data, private notes, financial | Obsidian vault, Telegram messages, credential metadata | Supabase (encrypted at rest), local files. Never in public repos |
| **Internal** | Project decisions, architecture, working state | Memories, goals, outcomes, working state | Supabase, git (private repos OK, public repos OK if no sensitive data) |
| **Public** | Open source code, docs, issues | GitHub repo, PR descriptions, README | Anywhere |

## Attack Surface

### 1. MCP Servers (7 active)

See `docs/security/mcp-audit.md` for per-server analysis. Summary:

| Vector | Risk | Current mitigation |
|--------|------|--------------------|
| Secret exfiltration via GitHub writes | High | PreToolUse secret scanner (12 patterns) |
| Secret exfiltration via bash | High | PreToolUse bash danger scanner (8 patterns) |
| Secret exfiltration via memory_store | Medium | **Sprint 2: #159** |
| Prompt injection via firecrawl/web | Medium | Claude instruction hierarchy |
| Prompt injection via GitHub issues | Medium | None (trusted input assumed) |
| Data corruption via memory writes | Medium | Soft delete with 30-day retention (#160) |
| Obsidian vault data exposure | Low | Local only, no network |

### 2. Subagents (current + Federation & Delegation pillar)

| Vector | Risk | Current mitigation |
|--------|------|--------------------|
| Agent writes secret to memory | Medium | **Sprint 2: #159** |
| Agent modifies protected files | Medium | **Sprint 2: #162** |
| Agent corrupts git state (wrong branch, conflict) | Medium | **Sprint 2: #163** |
| Agent scope creep (edits unrelated files) | Low | CLAUDE.md rules (soft) |
| Agent-to-agent data poisoning | Low | Not yet addressed (Federation & Delegation pillar) |
| Agent infinite loop / resource waste | Low | Token budget awareness in CLAUDE.md |

### 3. External Integrations

| Vector | Risk | Current mitigation |
|--------|------|--------------------|
| Compromised dependency (supply chain) | Medium | Gitleaks CI, Dependabot (partial) |
| GitHub token over-privilege | Low | Copilot-managed token scope |
| Supabase API key exposure | High | Secret scanner, .env exclusion |
| Voyage AI API key exposure | Medium | Secret scanner |

### 4. Local Environment

| Vector | Risk | Current mitigation |
|--------|------|--------------------|
| .env file read by agent | High | SOUL.md rule: never read .env |
| Home directory dotfile access | Medium | SOUL.md boundary rule |
| Cross-device config divergence | Low | Portable .mcp.json (no hardcoded paths) |

## Data Flow

```
User ──→ Claude Code ──→ MCP Servers ──→ External APIs
              │                              │
              ├── Subagents                   ├── Supabase (memory, goals, events)
              │     └── same MCP access       ├── GitHub (issues, PRs, code)
              │                               ├── Voyage AI (embeddings)
              ├── Hooks (PreToolUse)           └── Firecrawl (web scraping)
              │     └── secret-scanner.py
              │
              └── Git (local repo ──→ GitHub remote)
```

Key flows to protect:
1. **Any write path to public GitHub** — secrets must not leak via issues, PRs, commits, comments
2. **Any write to Supabase memory** — secrets must not be stored in memory content
3. **Agent output → agent input** — one agent's output becomes context for another; poisoned output propagates
4. **Web content → Claude context** — scraped pages may contain adversarial instructions

## Risk Matrix

| Threat | Likelihood | Impact | Risk | Status |
|--------|-----------|--------|------|--------|
| Secret leaked in GitHub PR/issue | Low | Critical | High | Mitigated (scanner) |
| Secret leaked in bash command | Low | Critical | High | Mitigated (scanner) |
| Secret stored in Supabase memory | Low | High | Medium | Mitigated (scanner #159) |
| Agent deletes important memory | Medium | Medium | Medium | Mitigated (soft delete #160) |
| Agent modifies protected config | Low | High | Medium | Mitigated (boundaries #162) |
| Agent breaks git state | Medium | Low | Medium | Mitigated (rollback #163) |
| Prompt injection via web scrape | Low | Medium | Low | Accepted (Claude instruction hierarchy) |
| Supply chain attack via dependency | Low | High | Medium | Partial (Dependabot) |
| Prompt injection via GitHub issue | Low | Medium | Low | Accepted (trusted repo) |
| Agent-to-agent poisoning | Low | Medium | Low | Deferred (Federation & Delegation pillar) |

## Mitigations Summary

### Preventive (stop it from happening)
- [x] Secret scanner on GitHub writes (PreToolUse hook)
- [x] Secret scanner on bash commands (PreToolUse hook)
- [x] Secret scanner on memory_store (#159)
- [x] Protected file write blocking (#162)
- [x] SOUL.md behavioral rules (never read .env, never output secrets)
- [x] Gitleaks pre-commit hook + CI workflow
- [x] Credential registry with CHECK constraint (no values stored)

### Detective (know when it happened)
- [x] Gitleaks CI on PRs
- [x] Audit trail for agent actions (#161)
- [ ] Dependabot alerts (partially configured)

### Corrective (undo damage)
- [x] Soft delete for memory with 30-day retention (#160)
- [x] Rollback strategy for agent work (#163)
- [x] Git history (revert commits)

### Accepted Risks
- **Prompt injection via web/issues**: Claude's instruction hierarchy (system > user > tool output) is the defense. No additional mitigation planned unless incidents occur.
- **Single-user assumption**: No RLS, no multi-tenant isolation. Acceptable for solo project.
- **Agent-to-agent poisoning**: Deferred to Federation & Delegation pillar when multi-agent architecture is designed.
