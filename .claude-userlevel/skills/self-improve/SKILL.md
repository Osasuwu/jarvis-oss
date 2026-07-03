---
name: self-improve
description: "Autonomous self-improvement: health check, gap analysis, ideation, research, implementation. Absorbs ideate, self-review, repo-health."
version: 2.0.0
---

# Self-Improve

Meta-agent: identify gaps, generate ideas, research, implement. Goal: introduce something genuinely new.

## Usage

- `/self-improve` — full pipeline
- `/self-improve --dry-run` — plan only, no changes

## Pipeline

### Step 1 — Health check

Quick codebase scan (replaces separate self-review + repo-health):
- Run tests: `python -m pytest tests/ -q` (if tests exist)
- Check for obvious issues: broken imports, stale configs, TODO/FIXME density
- Note findings as context — don't fix directly yet

### Step 2 — Load context

In parallel:
```
memory_recall(type="decision", limit=5)
memory_recall(query="working_state", type="project", limit=2)
memory_recall(type="feedback", limit=5)
```

Build a picture: what friction repeats, what's missing, what research sits unacted.

### Step 3 — Ideate

Generate 3-5 ideas for improvement. For each, score:
- **Impact**: H/M/L — how much does this improve Jarvis?
- **Effort**: H/M/L — how long to implement?
- **Risk**: H/M/L — what could break?

Sources of ideas:
- Health check findings (code quality, missing tests)
- Repeated friction patterns (from feedback memories)
- Unacted research findings
- Missing capabilities observed in recent sessions
- Opportunities from new Claude Code / MCP features
- **Known-unknowns** (`known_unknowns` table → repeated recall gaps) — queries Jarvis has been asked but couldn't answer from memory
- **Poor-calibration types** (`memory_calibration_summary` → types with Brier > 0.25) — systemic over/underconfidence is signal worth investigating

#### Metacognition seeds

Before ideating, pull both metacognition signals as candidate seeds. Run in parallel; both may return empty (skill must still work — just skip the section).

**1. Known-unknowns** — retrieval gaps with highest recurrence:
```sql
SELECT query, hit_count, last_seen_at
FROM known_unknowns
WHERE status='open'
ORDER BY hit_count DESC, last_seen_at DESC
LIMIT 5
```
Run via `execute_sql` (works in cloud + local). A query with `hit_count >= 3` is a strong seed: "asked N times, still no answer" → candidate for a research run, a new memory, or a capability gap.

**2. Calibration gaps** — types where confidence diverges from outcome:
```
mcp__memory__memory_calibration_summary(project="jarvis")
```
Types flagged `overconfident` (Brier > 0.25, avg_predicted > avg_actual) often point at a concrete pattern (e.g. "decision memories relying on unverified research") that can be fixed by a feedback rule, a hook, or a schema tweak.

Render both under a **Ideation seeds (metacognition)** subsection in the output. Each seed becomes a candidate idea scored in the table below alongside health-check findings and friction patterns. If both sources return empty, omit the subsection (don't render empty headings).

If no strong ideas: fall back to health check findings for code improvements.

### Step 4 — Select

Top-1 by: High impact + Low/Medium effort + Low/Medium risk.
Prefer ideas connected to real observed problems over theoretical improvements.

### Step 5 — Research

Focused search on the selected idea:
- Specific question, not generic scan
- `firecrawl_search(limit=3)` or `WebSearch`
- If research invalidates the idea → go back to Step 4

### Step 6 — Risk classification

| Risk | Criteria | Action |
|------|----------|--------|
| **Low** | New skill, config tweak, prompt improvement, docs | Auto-implement |
| **Medium** | New hook, MCP config, tool wiring | Show plan, wait for confirmation |
| **High** | Architecture, memory server, SOUL.md, CLAUDE.md | Propose only |

**Never auto-apply:** `.mcp.json`, `mcp-memory/server.py`, `config/SOUL.md`, `CLAUDE.md`, `.env`.

### Step 7 — Implement (skip in --dry-run)

Low risk → implement directly.
Medium risk → show plan, wait for confirmation.
High risk → output proposal only.

Verify: `python -m pytest tests/ -q` if applicable.

### Step 8 — Branch + PR (skip in --dry-run or High risk)

```bash
git checkout -b self-improve/<date>-<slug>
git add <specific files>
git commit -m "self-improve: <what and why>"
git push -u origin self-improve/<date>-<slug>
gh pr create --title "self-improve: <description>" --body "..."
```

## Output

```markdown
## Self-Improve — YYYY-MM-DD

### Health check
- <key findings, or "Clean">

### Ideation seeds (metacognition)
<omit entire subsection if both sources empty>

**Known-unknowns** (top open, by hit_count):
| Query | Hits | Last seen |
|-------|------|-----------|
| ... | N | YYYY-MM-DD |

**Calibration gaps** (Brier > 0.25, n >= 20):
- `<type>` — Brier X.XX, overconfident/underconfident (avg_pred Y vs avg_actual Z)

### Ideas (scored)
| Idea | Impact | Effort | Risk |
|------|--------|--------|------|
| ... | H/M/L | H/M/L | H/M/L |

### Selected
**[Title]** — Impact: H | Effort: L | Risk: L

### Research
<key insight>

### Result
- Implemented / Proposed / Needs approval
- PR: <url> (or --dry-run)
```
