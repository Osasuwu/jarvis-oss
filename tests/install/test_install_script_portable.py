r"""Portability guard test for install scripts.

Verifies that install scripts do not contain:
- Hardcoded usernames (e.g., jdoe, asmith)
- Absolute paths starting with C:\Users\<username>
- Device-specific absolute paths

This is a meta-test in the spirit of CI meta-tests (see CLAUDE.md #326).
It prevents silent failures when scripts are run on other machines.

Note (#743): the scheduler-service *installer* was retired with
agents/scheduler.py when wake_driver replaced the resident scheduler. The
install-specific regression tests (#394 / #410 — NSSM autodiscovery, python
resolution, SeServiceLogonRight, DryRun) went with it. Only the teardown
script (uninstall-scheduler-service.ps1) survives as a one-time cleanup tool
for already-deployed devices, and the repo-wide Join-Path guard still applies
to every .ps1.
"""

import re
from pathlib import Path


def test_uninstall_scheduler_service_portable():
    """Assert the surviving uninstall (teardown) script has no hardcoded paths."""
    script_path = (
        Path(__file__).parent.parent.parent
        / "scripts"
        / "install"
        / "uninstall-scheduler-service.ps1"
    )
    content = script_path.read_text(encoding="utf-8")

    forbidden_paths = re.findall(r"[C|c]:\\[Uu]sers\\[a-zA-Z0-9_-]+", content)
    violations = []
    for line in content.split("\n"):
        if line.strip().startswith("#"):
            continue
        for path in forbidden_paths:
            if path in line and not line.strip().startswith("#"):
                violations.append((line.strip(), path))

    assert not violations, "Script contains hardcoded absolute paths:\n" + "\n".join(
        f"  {path}: {line}" for line, path in violations
    )


def test_no_multiarg_join_path_in_ps_scripts():
    """Windows PowerShell 5.1's Join-Path takes only -Path/-ChildPath; 3+ positional args throw.

    Caught a real bug: install-scheduler-service.ps1 had `Join-Path X "config" "device.json"`
    which crashed with "Не удается найти позиционный параметр" on the routine host install. PS 7+
    accepts the multi-arg form via -AdditionalChildPath, but the default Windows shell is 5.1.

    Multi-segment paths must be chained: Join-Path (Join-Path A "config") "device.json".

    Detection strategy: only flag the broken shape — three or more *simple* arguments
    (variable, double/single-quoted string, or bareword). Parenthesized expressions are
    skipped, which means a chained `Join-Path (Join-Path A B) C` reads as "first arg is `(`"
    and falls through. Trade-off: false negatives on exotic invocations are acceptable;
    false positives would block correct code.
    """
    repo_root = Path(__file__).parent.parent.parent
    ps_scripts = list((repo_root / "scripts").rglob("*.ps1"))
    assert ps_scripts, "Expected at least one .ps1 script under scripts/"

    simple_token = r'(?:\$\w+|"[^"]*"|\'[^\']*\'|\w+)'
    bad_pattern = re.compile(
        r"Join-Path\s+"
        + simple_token
        + r"\s+"
        + simple_token
        + r"\s+"
        + simple_token
        + r"(?:\s|$|\))"
    )

    violations = []
    for script in ps_scripts:
        content = script.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if bad_pattern.search(line):
                violations.append((script.relative_to(repo_root), line_num, stripped))

    assert not violations, (
        "Multi-arg Join-Path X Y Z is not supported in Windows PowerShell 5.1.\n"
        "Chain it: Join-Path (Join-Path X Y) Z\n\n"
        + "\n".join(f"  {path}:{line_num}  {body}" for path, line_num, body in violations)
    )
