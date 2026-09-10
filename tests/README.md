# tests/ — layout & conventions

The suite is organised into **domain subdirectories**. Each `test_*.py` lives in
the subdir for the capability it exercises; shared helpers live in `_support/`.
Node IDs are `subdir/test_file.py::test_name` — the restructure (#868) preserved
every test's identity, only the path prefix changed.

## Taxonomy

| Subdir | Owns | Source area |
|---|---|---|
| `comms/` | Communication-pattern classifier / reflect surface | `scripts/comm_patterns/` |
| `infrastructure/` | Installer units, hooks, secret scanner/scrubber, protected files, risk radar, session-context — the cross-cutting **catch-all** | `scripts/`, `src/` |
| `ci/` | Path-filtered CI-guard meta-tests (#326) — one per guarded workflow | `.github/workflows/` |
| `evals/` | Evaluation-harness tests | `scripts/`, `config/` |
| `plan_review/` | Plan-review gate (planner/critic classification, plan-lock grammar) | `agents/` |
| `skills/` | Skill-contract tests | `.claude/skills/` |
| `weekly_release/` | Weekly-release skill tests | `.claude/skills/weekly-release/` |

Two files stay at the **root** by design (no domain home, cross-cutting entry
points): `test_competence_scoring.py`, `test_repos_conf.py`. `conftest.py` also
stays at root — it must sit at the collection root to apply to every subdir.

## Tie-break order (when a test could fit two domains)

Place by the **most specific domain it asserts against**, resolving ties in this
precedence (first match wins):

1. `ci/` — if it's a meta-test for a `paths:`-filtered workflow guard, it goes
   here regardless of what the guard watches.
2. `comms/` → `plan_review/` → `evals/` → `skills/` → `weekly_release/` — the
   named capability domains, in that order.
3. `infrastructure/` — the catch-all. A test lands here only when it matches no
   named domain above.

## Import contract (#978/#980)

- **No `__init__.py` at `tests/` root.** With `--import-mode=prepend` this makes
  pytest insert `tests/` onto `sys.path[0]`, so every subdir test resolves
  `from conftest import ...`. Each subdir *does* carry an empty `__init__.py`, so
  a test's package-qualified node ID (`subdir.test_x`) stays unique across
  same-named files (e.g. two `test_installer.py`).
- **Shared helpers live in `tests/_support/`**, on `pythonpath` (see
  `pyproject.toml`). Import them by their module name, not a `test_` prefix,
  e.g. `import notify_transport_double` (used by `reactive_core/test_notify.py`
  both as a direct import and via dotted-path resolution, as the string
  `"notify_transport_double:fake_transport"`, exercising the same
  dotted-path-resolution mechanism production code uses to load a transport).
  The `_support` dir is not collected (leading underscore) and its modules are
  never named `test_*`, so they can't be mistaken for test files.

### Subdir naming caveat — package shadowing

A subdir name must **not** equal an importable top-level source package. Under
`--import-mode=prepend` + per-subdir `__init__.py`, pytest would bind
`sys.modules['<name>']` to the empty test `__init__.py` and shadow the real
package. This is why `comm_patterns/` → `comms/`: the source package
`scripts/comm_patterns` would otherwise be shadowed. (A test subdir once named
`reactive_core/`, renamed from `agents/` for the same reason, was itself
retired when the reactive-core agent stack it covered was demolished in
#1802 — `agents/` today is a small, still-live package of plan-review helpers
with no dedicated test subdir of its own.) When adding a subdir, check
`python -c "import importlib.util,sys;
print(importlib.util.find_spec('<name>'))"` returns `None`.
