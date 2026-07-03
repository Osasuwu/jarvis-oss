"""Device identification for SessionStart hook.

Reads config/device.json if exists, creates with auto-detected values if not.
Prints device info to stdout for hook injection into Claude's context.
"""

import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_config = _root / "config" / "device.json"


def _detect_device():
    """Auto-detect device info."""
    hostname = socket.gethostname()
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    os_name = platform.system()
    os_version = platform.version()

    # Friendly OS string
    if os_name == "Windows":
        os_str = f"Windows {platform.win32_ver()[0] or ''} {os_version}".strip()
    elif os_name == "Darwin":
        os_str = f"macOS {platform.mac_ver()[0]}"
    else:
        os_str = f"{os_name} {os_version}"

    # Detect repos path from this repo's location
    repos_path = str(_root.parent)

    return {
        "name": hostname,  # user can rename to friendly name
        "hostname": hostname,
        "os": os_str,
        "username": username,
        "repos_path": repos_path,
        "home": str(Path.home()),
    }


def main():
    if _config.exists():
        with open(_config, "r", encoding="utf-8") as f:
            device = json.load(f)
    else:
        device = _detect_device()
        _config.parent.mkdir(parents=True, exist_ok=True)
        with open(_config, "w", encoding="utf-8") as f:
            json.dump(device, f, indent=2, ensure_ascii=False)

    # Compact output for hook
    print(f"DEVICE: {device.get('name', '?')} | OS: {device.get('os', '?')} | "
          f"User: {device.get('username', '?')} | Repos: {device.get('repos_path', '?')}")


if __name__ == "__main__":
    main()
