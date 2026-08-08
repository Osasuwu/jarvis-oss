# Deslop standard — comment-only cleanup & regeneration guard

Established: grill episode `f981009e-ac9c-4e17-ac46-56539c5ca846`.

## What "deslop" is

Deslop is comment-only codebase cleanup: remove comments that add no value,
keep everything that does, and prove mechanically that no executable token
changed.

## Keep/remove boundary

Every candidate comment receives exactly one of five dispositions:

| Disposition | Meaning | Action |
|---|---|---|
| `remove` | Comment adds no value; delete it. | Delete |
| `keep_why` | Explains *why*, not *what*. Essential design rationale. | Keep |
| `keep_external` | References external fact: URL, wire format, upstream quirk. | Keep |
| `keep_warning` | Safety comment: fail-open, fail-closed, guardrail, invariant. | Keep |
| `keep_unsure` | Judge cannot confidently determine. | Keep (errs conservative) |

### Rules

1. **Only `remove` deletes.** All four `keep_*` dispositions preserve the
   comment. The system errs on the side of keeping — a removed keeper is worse
   than a kept deletable.
2. **No category is a blind delete.** A `banner_label` or a high-confidence
   `restate` still passes the value check. Approximately 1/3 of high-confidence
   restate and ~90% of meta-process comments turn out to be keepers.
3. **Safety comments** (fail-open, fail-closed, guardrail, any code path that
   swallows or masks an error) → `keep_warning` by default.
4. **External-fact comments** (URL, wire format, upstream quirk, third-party
   API behaviour that is not obvious) → `keep_external` by default.
5. **Traceability references** (Issue/PR/ADR number in a comment) → kept,
   unless the rest of the comment is pure restate with no additional context.
6. **When unsure** → `keep_unsure`. Never delete a borderline keeper.
7. **Docstrings and string literals are out of scope.** The system never
   touches them. Only `#` comments (or equivalent line/block comments in
   non-Python files) are candidates.
8. **Every sweep PR must pass `diff_gate`** — mechanical proof that zero
   executable tokens changed. Sweep PRs that fail the gate are rejected, never
   force-shipped.

## Why a strict boundary

A comment-only sweep is safe only if it is *provably* comment-only. Relying on
reviewer eyeballs for "looks like only comments changed" is the failure mode.
`diff_gate` is the backstop: token-level comparison before and after, proving
that no NAME, OP, STRING, NUMBER, or other executable token differs between the
two versions.

## Components

- **`trace_inventory`** — finds every comment in the target codebase.
- **`comment_classifier`** — assigns one of the five dispositions per comment.
- **`diff_gate`** — mechanically proves a changeset is comment-only.
