# Sandcastle agent image — slice 2 (epic #534, issue #540)
#
# Runtime: Claude Code CLI + git + gh + python3 + memory MCP bridge.
# The agent inside this container talks to a local Ollama instance running
# on the host (Docker Desktop's host.docker.internal:11434), via Ollama's
# native Anthropic-compatible endpoint. Memory MCP runs in-container over
# stdio and reaches Supabase using a Supabase anon key (RLS-gated, slice 3).
#
# Build (from repo root — context must include mcp-memory/ and
# .claude-userlevel/skills/):
#   docker build -t sandcastle:jarvis -f .sandcastle/Dockerfile .
#
# Used by: .sandcastle/main.mts (npm run sandcastle)
#
# Decisions:
#   894ac658 — Claude Code + local Ollama, sterile container
#   436f9549 — sandcastle opens PRs only, never merges
#   228a2d9b — full memory MCP in container, anon key + provenance discipline

FROM node:22-bookworm

# System deps. python3 + venv for the memory MCP server. jq for gh shell snippets.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      curl \
      ca-certificates \
      jq \
      gnupg \
      python3 \
      python3-venv \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — needed for `gh issue edit`, `gh pr create`. Official apt repo.
RUN install -dm 0755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Memory MCP — vendor the entire mcp-memory/ directory because server.py imports
# from sibling modules (client, embeddings, recall, handlers/*). Install Python
# deps into an isolated venv at /opt/mcp-memory/.venv to avoid PEP 668 conflicts
# with system python on Debian bookworm.
COPY mcp-memory/ /opt/mcp-memory/
RUN python3 -m venv /opt/mcp-memory/.venv \
    && /opt/mcp-memory/.venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/mcp-memory/.venv/bin/pip install --no-cache-dir -r /opt/mcp-memory/requirements.txt \
    && rm -rf /root/.cache

# Container-scoped MCP config — onSandboxReady (main.mts) copies this over the
# worktree's .mcp.json at run start. Sandcastle uses copy-on-write worktrees
# (see node_modules/@ai-hero/sandcastle/dist/CopyToWorktree.js) so the host
# repo's .mcp.json is never mutated. Override is necessary because the host
# .mcp.json registers many host-only servers (telegram, blender, etc.) that
# would fail noisily inside the sterile container.
COPY .sandcastle/container-mcp.json /opt/sandcastle/container-mcp.json

# Match sandcastle's reference image: rename node user → agent, keep UID 1000
# so bind-mounts owned by the host user resolve cleanly.
RUN usermod -d /home/agent -m -l agent node \
    && groupmod -n agent node
USER agent
WORKDIR /home/agent

# Claude Code CLI (official installer). Lands in ~/.local/bin/claude.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/agent/.local/bin:$PATH"

# Canonical skill set — vendor .claude-userlevel/skills/ so the agent has the
# same skill surface as an inline /implement session. User-scope path picked up
# automatically by Claude Code at runtime.
COPY --chown=agent:agent .claude-userlevel/skills/ /home/agent/.claude/skills/

# Sandcastle bind-mounts the worktree at SANDBOX_REPO_DIR and exec's into it.
# Container just needs to stay alive; sandcastle drives commands via `docker exec`.
ENTRYPOINT ["sleep", "infinity"]
