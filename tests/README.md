# tests/ — layout & conventions

The suite is organised into **domain subdirectories**. Each `test_*.py` lives in
the subdir for the capability it exercises; shared helpers live in `_support/`.
Node IDs are `subdir/test_file.py::test_name` — the restructure (#868) preserved
every test's identity, only the path prefix changed.

## Taxonomy

| Subdir | Owns | Source area |
|---|---|---|
| `reactive_core/` | Orchestrator, executor, wake_driver, poller, task dispatch/queue, event emission, PID sidecar, escalation, safety, principal | `agents/`, `scripts/` reactive-core |
| `memory/` | Memory server, recall/store, outcomes, calibration, graph, goals, credentials, events FSM | `mcp-memory/` |
| `decisions/` | `record_decision` Tier-2 gate + doubles | `scripts/record-decision-gate.py` |
| `comms/` | Communication-pattern classifier / reflect surface | `scripts/comm_patterns/` |
| `status/` | `/status` digest + deterministic render | `mcp__status`, `scripts/` status |
| `infrastructure/` | Installer units, hooks, secret scanner/scrubber, protected files, risk radar, session-context — the cross-cutting **catch-all** | `scripts/`, `src/` |
| `ci/` | Path-filtered CI-guard meta-tests (#326) — one per guarded workflow | `.github/workflows/` |
| `install/` | Installer end-to-end integration | `install.ps1` |

Two files stay at the **root** by design (no domain home, cross-cutting entry
points): `test_go_gate.py`, `test_menu_renderer.py`. `conftest.py` also stays at
root — it must sit at the collection root to apply to every subdir.

## Tie-break order (when a test could fit two domains)

Place by the **most specific domain it asserts against**, resolving ties in this
precedence (first match wins):

1. `ci/` — if it's a meta-test for a `paths:`-filtered workflow guard, it goes
   here regardless of what the guard watches.
2. `install/` — if it drives the installer end-to-end.
3. `reactive_core/` → `memory/` → `decisions/` → `comms/` → `status/` — the
   named capability domains, in that order.
4. `infrastructure/` — the catch-all. A test lands here only when it matches no
   named domain above.

Rule of thumb: a test that touches memory *through* the orchestrator is a
`reactive_core/` test (it asserts orchestrator behaviour); a test that exercises
the memory server directly is a `memory/` test.

## Import contract (#978/#980)

- **No `__init__.py` at `tests/` root.** With `--import-mode=prepend` this makes
  pytest insert `tests/` onto `sys.path[0]`, so every subdir test resolves
  `from conftest import ...`. Each subdir *does* carry an empty `__init__.py`, so
  a test's package-qualified node ID (`subdir.test_x`) stays unique across
  same-named files (e.g. two `test_installer.py`).
- **Shared helpers live in `tests/_support/`**, on `pythonpath` (see
  `pyproject.toml`). Import them by their module name, not a `test_` prefix:
  - `from supabase_stubs import FakeClient` (was `test_utils`)
  - `from record_decision_doubles import make_client` (was `test_record_decision_helpers`)
  The `_support` dir is not collected (leading underscore) and its modules are
  never named `test_*`, so they can't be mistaken for test files.

### Subdir naming caveat — package shadowing

A subdir name must **not** equal an importable top-level source package. Under
`--import-mode=prepend` + per-subdir `__init__.py`, pytest would bind
`sys.modules['<name>']` to the empty test `__init__.py` and shadow the real
package. This is why `agents/` → `reactive_core/` and `comm_patterns/` →
`comms/`: the source packages `agents` and `scripts/comm_patterns` would
otherwise be shadowed (the former silently resolving to a stale editable-install
copy). When adding a subdir, check `python -c "import importlib.util,sys;
print(importlib.util.find_spec('<name>'))"` returns `None`.
