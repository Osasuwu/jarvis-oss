# Ollama Setup for Federation & Delegation Agents

Local LLM runtime for LangGraph persistent agents. Ollama handles cheap, always-on decisions (routing, classification) without burning Claude tokens.

## Environment

- **Home PC (dev)**: i5 9400F, a 6 GB VRAM GPU, 32GB RAM — current development host
- **The routine host (prod, future)**: a 16 GB VRAM GPU — reserved for production agents; not yet network-accessible (see #143)

## Install

Ollama 0.18.2 installed on home PC. Runs as a service on `localhost:11434`.

```bash
ollama --version   # → ollama version is 0.18.2
curl http://localhost:11434/api/tags   # → lists installed models
```

## Chosen model: `qwen3:4b`

| Model | Size | VRAM fit on a 6 GB VRAM GPU | Notes |
|-------|------|--------------------------|-------|
| `qwen3:4b` | 2.5 GB | Yes, plenty of headroom | **Selected for Sprint 1** |
| `qwen3:8b` | 5.2 GB | Tight (5.2 of 6GB) | Reserve for the routine host |
| `qwen3:8b-4k` | 5.2 GB | Same | Shorter context variant |

Reasoning: `qwen3:4b` handles event classification reliably at ~1.8–2.4s warm latency. The larger `qwen3:8b` offers little upside for simple routing/classification and pushes VRAM close to the limit on the dev box. When the routine host comes online, switch the agent config to `qwen3:8b` without code changes.

## Verified acceptance criteria (#171)

| Criterion | Result |
|-----------|--------|
| Ollama running as service | Yes, `localhost:11434` |
| Model responds via HTTP API | Yes, `/api/generate` |
| Structured JSON output | Yes, with `format` schema enforcement |
| Response time < 2s (warm, simple prompt) | ~1.8s simple JSON, ~2.4s schema-enforced |
| Cold start | ~63s VRAM load (qwen3:4b, 2.5GB) — first-request only |

## Critical gotcha: `think: false`

Qwen3 is a reasoning model. By default, Ollama puts the reasoning trace in the `thinking` field and leaves `response` **empty**. With `format=json` the JSON output also ends up in `thinking` — an agent that reads `response` gets an empty string and silently fails.

**Always pass `think: false`** at the top level of the request body:

```bash
curl -s -X POST http://localhost:11434/api/generate -d '{
  "model": "qwen3:4b",
  "prompt": "Classify this GitHub event...",
  "stream": false,
  "think": false,
  "format": {
    "type": "object",
    "properties": {
      "class": {"type": "string", "enum": ["noise", "info", "action"]},
      "reason": {"type": "string"}
    },
    "required": ["class", "reason"]
  },
  "options": {"temperature": 0, "num_predict": 80}
}'
```

For `langchain-ollama` Python client, pass `think=False` via model kwargs. Wire this into the shared client config in `agents/` so it is not rediscovered per-agent.

## HTTP API cheatsheet

```bash
# List models
curl http://localhost:11434/api/tags

# Generate (one-shot)
curl -X POST http://localhost:11434/api/generate -d '{"model":"qwen3:4b","prompt":"...","think":false,"stream":false}'

# Chat (multi-turn)
curl -X POST http://localhost:11434/api/chat -d '{"model":"qwen3:4b","messages":[...],"think":false,"stream":false}'
```

## Migration notes

- Connection is `http://localhost:11434` by default. When the routine host comes online, change via env var (e.g. `OLLAMA_HOST`) — no code changes.
- `think: false` is model-family specific to Qwen3 reasoning variants. Non-reasoning models (e.g. `llama3.2`) ignore it; safe to keep unconditionally in shared client config.
