# UML-MCP Setup Guide

Offline UML/sequence/architecture diagram generation via local Kroki + UML-MCP server.

## Prerequisites

- Python 3.10+
- Docker Desktop
- Java 11+ (for PlantUML rendering inside Kroki)

## 1. Clone and install UML-MCP

```bash
cd ~
git clone https://github.com/antoinebou12/uml-mcp.git
cd uml-mcp
pip install -r requirements.txt
```

## 2. Fix upstream bug (read_only field missing)

As of 2026-04-03 the upstream `MCPSettings` class is missing the `read_only` field that `diagram_tools.py` references. Add it manually:

**File:** `mcp_core/core/config.py`, inside `class MCPSettings`, after `kroki_server`:

```python
read_only: bool = Field(
    default_factory=lambda: os.environ.get("MCP_READ_ONLY", "false").lower()
    in ("true", "1", "yes")
)
```

Check if this has been fixed upstream before applying — search for `read_only` in the `MCPSettings` class.

## 3. Start local Kroki (Docker)

```bash
docker run -d --name kroki -p 8000:8000 yuzutech/kroki
```

To restart after reboot:
```bash
docker start kroki
```

Verify:
```bash
curl http://localhost:8000/
# Should return 200
```

## 4. Configure Claude Code MCP

Add to your workspace `.mcp.json` (e.g. `~/GitHub/.mcp.json`):

```json
"uml": {
  "command": "python",
  "args": ["<HOME>/uml-mcp/server.py"],
  "cwd": "<HOME>/uml-mcp",
  "env": {
    "KROKI_SERVER": "http://localhost:8000",
    "USE_LOCAL_KROKI": "true"
  }
}
```

Replace `<HOME>` with your actual home directory path:
- Windows: `C:/Users/<username>/uml-mcp/server.py`
- macOS/Linux: `/home/<username>/uml-mcp/server.py`

## 5. Verify

Restart Claude Code session, then use:

```
generate_uml(diagram_type="plantuml", code="@startuml\nAlice -> Bob: hello\n@enduml", output_format="svg")
```

Should return an SVG file path.

## Available diagram types

All rendered offline via local Kroki:

| Type | Syntax | Use case |
|------|--------|----------|
| plantuml | `@startuml ... @enduml` | Class, sequence, activity, state, component, deployment, object, usecase |
| mermaid | `graph TD; A-->B` | Flowcharts, sequence, gantt, pie, ER |
| d2 | `a -> b: hello` | Modern diagrams with auto-layout |
| graphviz | `digraph { a -> b }` | Graph/network visualization |
| c4plantuml | C4 DSL | Architecture (C4 model) |
| erd | ERD syntax | Entity-relationship diagrams |
| bpmn | BPMN XML | Business process diagrams |

Full list: 25+ types supported by Kroki.

## Diagram storage convention

Diagrams are saved to `docs/diagrams/` inside each project repository:
- `jarvis/docs/diagrams/`
- `redrobot/docs/diagrams/`

Format: SVG (vector, renders in GitHub, small size).

When generating, pass `output_dir` pointing to the repo's `docs/diagrams/` path.

## Troubleshooting

**Server won't start:** Check `python server.py --list-tools` — should show 2 tools.

**Kroki not responding:** `docker ps --filter name=kroki` — restart if stopped.

**Pillow build error on Python 3.14:** Install Pillow separately first: `pip install pillow --pre`, then `pip install -r requirements.txt`.

**`read_only` AttributeError:** Apply the fix from step 2.
