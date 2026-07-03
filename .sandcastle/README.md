# Sandcastle — local AFK smoke loop

This directory is the substrate for [epic #534](https://github.com/Osasuwu/jarvis/issues/534)
— Docker-isolated AFK coding agent. Slice 1 ([#537](https://github.com/Osasuwu/jarvis/issues/537))
shipped the **manual smoke command** on Main PC. Slice 2 ([#540](https://github.com/Osasuwu/jarvis/issues/540))
adds the **memory MCP bridge inside the container** + canonical skill set baked
into the image. Watchdog and scheduler land in slices 4/8.

## What it does

`npm run sandcastle` builds nothing on its own — it expects the image to exist
already. It then:

1. Spins up a `sandcastle:jarvis` container with the Jarvis worktree bind-mounted.
2. Hands the agent prompt (`.sandcastle/prompt.md`) to Claude Code CLI inside
   the container.
3. Claude Code talks to **Ollama on the host** via Ollama's native
   Anthropic-compatible endpoint (no proxy).
4. Agent picks one issue labelled `sandcastle`, works on it, opens a PR, stops.
   It **never merges** — the live orchestrator session reviews and merges.

## Prerequisites

- **Docker Desktop** (Windows / macOS) running. On Linux Docker, you'll need to
  add `--add-host host.docker.internal:host-gateway` to whatever launches the
  container, or set `OLLAMA_BASE_URL` to your host IP.
- **Ollama ≥ v0.14** (Jan 2026 release — first to expose the native
  `/v1/messages` Anthropic-compatible endpoint) running on the host.
  Verify with `ollama --version`.
- **A coding model pulled on Ollama** with tool-use support. Slice 7
  ([#538](https://github.com/Osasuwu/jarvis/issues/538)) benchmarks and picks
  the production default — pin whatever `OLLAMA_MODEL` you want to test in
  `.sandcastle/.env`.
- **Ollama context length ≥ 64K.** Claude Code requires ≥64K context per
  the official integration guide. Ollama's default is 8K — running with the
  default truncates the system prompt and tool definitions silently, and
  smaller models (≤8B) tend to bail to `<promise>COMPLETE</promise>` without
  invoking tools. Restart Ollama with the env var set:
  ```powershell
  $env:OLLAMA_CONTEXT_LENGTH = "65536"
  ollama serve
  ```
  On Workshop hardware with limited VRAM, slice 7 will determine the right
  (model, context, kv-cache-quant) combo. On Main PC with 6 GB VRAM, you may
  need to pick a smaller model or quantised KV cache to fit 64K context.
- **A GitHub PAT** in `.sandcastle/.env` (`GH_TOKEN=…`) scoped to Issues: RW,
  PRs: RW, Contents: RW on this repo.
- **A tracer issue** labelled `sandcastle` exists in the repo and is small
  enough to land in one iteration (e.g. a typo fix). The agent will pick the
  highest-priority such issue.

## Smoke command

From the repo root:

```powershell
# 0. Install Node dev-dependencies (one-time, or after package.json changes).
#    Pulls tsx + @ai-hero/sandcastle.
npm install

# 1. Build the image (one-time, or after Dockerfile changes).
#    Build context is the repo root because the image vendors mcp-memory/
#    and .claude-userlevel/skills/ from outside .sandcastle/.
docker build -t sandcastle:jarvis -f .sandcastle/Dockerfile .

# 2. Copy env example and fill in GH_TOKEN, SUPABASE_URL, SUPABASE_KEY.
cp .sandcastle/.env.example .sandcastle/.env
# edit .sandcastle/.env — set GH_TOKEN, SUPABASE_URL, SUPABASE_KEY (anon!),
# VOYAGE_API_KEY (optional)

# 3. Run the smoke loop. Picks one tracer issue, opens a PR, exits.
#    `npm run sandcastle` auto-loads .sandcastle/.env via Node's
#    --env-file-if-exists flag (Node ≥ 20).
npm run sandcastle
```

Expected outcome: a new branch `feat/<N>-<slug>` is pushed, a PR with
`Closes #<N>` exists, and the container has exited cleanly with no Anthropic
API calls (since `ANTHROPIC_BASE_URL` is pointed at Ollama).

To inspect what the agent actually did, look at the captured session JSONL at
`~/.claude/projects/C-Users-jdoe-GitHub-jarvis/<run-id>.jsonl`. The
`message.model` field on assistant turns confirms which model handled the
request (`qwen3:8b`, `qwen2.5-coder:14b`, etc. — never `claude-…` for
Ollama-routed runs).

## What is intentionally NOT here

| Concern | Lands in |
|---|---|
| PowerShell watchdog (autostart, soft-stop, outcome_record) | slice 4 ([#541](https://github.com/Osasuwu/jarvis/issues/541)) |
| ~~Memory MCP bridge inside container + skills baked in~~ | **slice 2 — landed ([#540](https://github.com/Osasuwu/jarvis/issues/540))** |
| Supabase RLS for `sandcastle:agent` provenance | slice 3 ([#542](https://github.com/Osasuwu/jarvis/issues/542)) |
| Multi-tier model escalation (Ollama → small → DeepSeek → owner) | slice 5 ([#543](https://github.com/Osasuwu/jarvis/issues/543)) |
| Telegram alerting | slice 6 ([#544](https://github.com/Osasuwu/jarvis/issues/544)) |
| Workshop PC schedule + redrobot loop | slices 8/9 ([#545](https://github.com/Osasuwu/jarvis/issues/545), [#546](https://github.com/Osasuwu/jarvis/issues/546)) |

## Decisions (referenced by UUID)

- `894ac658-67da-4f32-a0a2-5b5ebefac8ee` — Runtime: Claude Code + local Ollama, sterile container, no `~/.claude` mount.
- `436f9549-3acf-4ee0-85e5-c7259735d62e` — Sandcastle opens PRs only, never merges.
- `228a2d9b-b57a-4d0f-8771-662482386b8a` — Memory MCP in container with anon key + provenance discipline; skills baked into image (slice 2).

Slice-1 implementation choice (no proxy, native Ollama Anthropic endpoint)
recorded as `decision_made` episode `375449f9-5026-4471-a705-922c5baddf7f`.
