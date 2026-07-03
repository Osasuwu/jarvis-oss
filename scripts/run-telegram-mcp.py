"""Cross-platform bootstrap for tg-messenger MCP server.
Runs scripts/telegram-mcp-server.py using the project venv.
Same pattern as run-memory-server.py.
"""
import os
import sys
import subprocess

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
server = os.path.join(root, "scripts", "telegram-mcp-server.py")

candidates = [
    os.path.join(root, ".venv", "Scripts", "python.exe"),  # Windows
    os.path.join(root, ".venv", "bin", "python"),           # macOS/Linux
]

for python in candidates:
    if os.path.isfile(python):
        sys.exit(subprocess.call([python, server]))

print("ERROR: no venv found at .venv/", file=sys.stderr)
sys.exit(1)
