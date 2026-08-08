---
name: status
model: haiku
description: "User-facing repo-state read via status_digest MCP tool, rendered deterministically (0 LLM tokens by default) — health line + ranked 'Куда смотреть' + 'Аномалии' per repo. `--deep` adds LLM narration. Anchored routing: only `статус`/`status`/`статус <repo>` fire it."
---

# Status

The owner-facing **read** side of status-synthesis (milestone #53). Several-times-a-day call, so the default surface is **deterministic Python — no LLM narration, no tokens spent on rendering**. The judgment already happened upstream: `status_digest` (#1017) wraps `gather() → status_engine.analyze()` and hands back a fully-decided `{health, detector_hits, ranking, provenance}` digest. This skill's only job is to surface it.

**Boundary:** this skill reads and renders. It does not investigate, open issues, comment, rework, or act on findings — those belong to the owner and the sandcastle orchestrator (#531). Surfacing "куда смотреть" is the whole contract; what to do about it is the reader's call.

**Anchored routing (CLAUDE.md skill-routing table, #1018 AC6/AC7).** Only the exact triggers `статус`, `status`, or `статус <repo>` route here. A sentence that merely contains the word — "какой статус у PR #123", "status code 500", a quoted error — is a normal in-context request, NOT a command to run a status investigation. Do not self-fire on incidental uses of the word. This closes the original failure mode where a bare "статус" was over-read as "go investigate everything".

## Step 1 — Call the digest tool

```
mcp__status__status_digest(jarvis_home="<JARVIS_HOME or empty to auto-detect>")
```

Pass `jarvis_home` when `JARVIS_HOME` is set (cron / non-repo CWD); leave it empty to let the server auto-detect from CWD via `git rev-parse`. The tool returns the digest as a JSON text block:

```json
{
  "health": {"ok": false, "reason": "..."},
  "detector_hits": [{"detector": "...", "severity": "...", "repo": "...", "issue_number": 42, "title": "...", "description": "..."}],
  "ranking": [{"rank": 1, "detector_hit": {...}, "reason": "..."}],
  "provenance": {"jarvis": {"ran": true, "ok": true, "input_rows": 12, "age": 120.0}, "redrobot": {...}}
}
```

One call covers **both repos** (jarvis + redrobot) — the gather is repo-fanned and the provenance block carries one stamp per source. Don't loop per repo.

If the tool response begins with `Error in status_digest:` — surface that verbatim to the owner and stop. A failed gather must read as suspicious, never as "all clear".

## Step 2 — Render deterministically (default path, 0 LLM tokens)

Do **not** narrate, summarize, or reformat the digest yourself — that would spend tokens and drift from the snapshot test. Pipe the exact JSON from Step 1 through the pure renderer and print its output verbatim:

```bash
# Write the digest JSON from Step 1 to a temp file (via the Write tool or a
# heredoc), then:
python "${JARVIS_HOME:-.}/scripts/status_render.py" < /tmp/status_digest.json
```

`scripts/status_render.py` ([renderer](../../../scripts/status_render.py)) is a pure function over the digest. It emits:

```
<health line>          🟢 only when every source ran ok + is fresh; 🔴 otherwise

Куда смотреть:          ranked top-N from the engine (omitted when nothing ranked)
  1. ...

Аномалии:               detector hits grouped by repo (omitted when none)
  your-username/jarvis:
    • [MAJOR] stale-in-progress #42
```

**Provenance contract (#1018 AC3) — load-bearing.** The renderer re-derives freshness from `provenance` itself and refuses a green health line unless *every* source `ran`, is `ok`, and is within `FRESHNESS_AGE_SECONDS` — even if `health.ok` is `true`. A silently-failed or stale gather surfaces as degraded, not clear. This is defense-in-depth against a malformed digest; do not "fix" a red line by trusting `health.ok`.

Print the renderer's stdout exactly. That is the default deliverable.

## Step 3 — `--deep` (gated; adds LLM narration)

When the owner passes `--deep` (e.g. `статус --deep`, `/status --deep`):

1. Run the renderer with `--deep` for the deterministic **full picture** — every hit's full `description` plus a `Провенанс:` table:

   ```bash
   python "${JARVIS_HOME:-.}/scripts/status_render.py" --deep < /tmp/status_digest.json
   ```

2. **Then** layer LLM narration on top: read the full descriptions + provenance, and write a short cause/FYI paragraph per ranked item — what likely caused it, what to look at first, any cross-repo pattern. This is the only path that spends LLM tokens. Cause/FYI detail lives here exclusively; it must never leak onto the default surface.

Default (no flag) ends at Step 2. `--deep` is a superset — the deterministic blocks are identical, with the provenance table and narration appended.

## Failure modes

- `status_digest` returns an `Error in ...` text → surface verbatim, stop. Never substitute a synthesized "looks fine".
- Renderer exits non-zero (rc 2 = invalid digest JSON on stdin) → the JSON written to the temp file is malformed; re-capture the tool output exactly (do not hand-edit it) and retry. Don't paper over it with a prose summary.
- `JARVIS_HOME` unset and CWD outside the repo → `${JARVIS_HOME:-.}` resolves to `.`; if `scripts/status_render.py` isn't found, the owner is in the wrong directory — say so rather than guessing a path.
- Health line is 🔴 with a `источники деградировали` reason → a gather source failed or is stale; that IS the finding. Surface it; do not retry hoping for green.
