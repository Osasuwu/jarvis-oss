#!/usr/bin/env bash
# Jarvis installer — POSIX wrapper.
# Forwards to scripts/install/installer.py. Default is dry-run.
#
# Examples:
#   ./install.sh                   # dry-run plan
#   ./install.sh --apply           # perform install
#   ./install.sh --rollback <path> # restore from backup
#
# Epic #335 / M1 #336.

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    PY=python
fi

exec "$PY" "$repo_root/scripts/install/installer.py" "$@"
