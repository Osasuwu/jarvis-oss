"""Cross-platform bootstrap for mcp-memory server.
Finds the venv python automatically and execs the server.
Only needs stdlib — runs under any Python 3, then switches to venv.
"""
import os
import sys
import subprocess

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
server = os.path.join(root, "mcp-memory", "server.py")

candidates = [
    os.path.join(root, ".venv", "Scripts", "python.exe"),  # Windows
    os.path.join(root, ".venv", "bin", "python"),           # macOS/Linux
]

for python in candidates:
    if os.path.isfile(python):
        sys.exit(subprocess.call([python, server]))

print("No venv found at", os.path.join(root, ".venv"), file=sys.stderr)
print("Run: scripts/setup-device.sh", file=sys.stderr)
sys.exit(1)
