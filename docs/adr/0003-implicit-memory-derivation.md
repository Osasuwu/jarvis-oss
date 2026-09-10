# Implicit memory derivation: always-gate review, owner-level scope

**Status:** accepted (2026-05-08, epic [#549](https://github.com/Osasuwu/jarvis/issues/549))

Honcho-style passive derivation of `user` and `feedback` memory entries from session transcripts runs as Jarvis-native infra (Stop hook accumulator + SessionEnd Deriver on Workshop+Ollama with DeepSeek fallback + scheduled volume-triggered Dreamer), not as a skill. Two interlocking shape decisions go here because they cement the subsystem's safety boundary and its scope-reach, and a reader of the hook config alone would otherwise misread both.

## Decision

1. **Always-gate review (no auto-promote tier).** Every Deriver/Dreamer write lands `requires_review=true` regardless of confidence. Recall hides these by default; opt-in via `include_unreviewed=true`. Owner accept via `memory_review_decide` is the only path to live memory in v1.
2. **Owner-level scope, not jarvis-project scope.** SessionEnd hook lives in user-level `~/.claude/settings.json` (mirrored from `.claude-userlevel/`) and fires on every owner session regardless of which project the session was opened in. Candidate `project` field is content-determined by the Deriver: `user`-type → global (`project=null`); `feedback` → session's project or global per content. Dreamer is a single owner-level pass across all projects.

## Why

**Always-gate** matches the threat model: `feedback` memories enter `always_load` and shape every future session — a wrong one is a silent reasoning bias surfacing only via `/reflect` outcome attribution months later. The "explicit-correction signal" that would gate auto-promote is the exact reasoning the Deriver hallucinates, so conditioning on it is circular. Review burden is a UX problem to solve in `/learn`, not a permissions problem to solve in derivation.

**Owner-level scope** follows from the role: Jarvis is the personal-agent layer over the owner's sessions, not a project-scoped tool. Both redrobot and jarvis are the owner's projects; the Deriver observes one human across all of their work. Project-scoping the Deriver would either fragment owner-universal facts (communication style, work rhythm) into one project's namespace, or duplicate compute by running per-project Dreamers over the same accepted-memory corpus.

The two decisions reinforce each other: owner-level scope means the Deriver can write across project boundaries, which raises the blast radius of a wrong candidate — the always-gate is the safety boundary that makes the broader scope tolerable. Reversing one without the other is unsafe.

## Considered alternatives

- **Tiered auto-promote at confidence ≥0.9 with explicit-correction signal.** Rejected: the signal detection is itself the unreliable inference; circular gating.
- **Jarvis-project-only Deriver, redrobot opts in later.** Rejected: artificially limits derivation despite Jarvis being the personal-agent layer; forces awkward "redrobot opt-out" special-case in shared `mcp-memory` recall pipeline.
- **Per-project recall default for `include_unreviewed`.** Rejected: leaks project asymmetry into the shared recall pipeline config; the asymmetry doesn't actually exist once Deriver is owner-level.
- **Honcho SaaS integration.** Rejected upstream of this ADR (decision `d9592c05` in epic body) — keep all derivation local on Workshop+Ollama for cost and privacy.

## Consequences

- Future tiered auto-promote is allowed but must be **data-driven**: ≥4 weeks of `memory_review_decide` audit data showing which candidate features predict accept-without-edit, then a separate ADR.
- `mcp-memory/server.py` and `schema.sql` migration is shared with redrobot — column-collision precheck via `information_schema` and a cross-project recall smoke test are blockers on the migration PR.
- The `/learn` skill is a thin wrapper over `memory_review_*` RPCs; no `accept_all` primitive (would normalise rubber-stamping and undercut the always-gate).
- Throughput is bounded so single-by-single review remains feasible: ≤5 Deriver candidates per session-end, ≤20 per Dreamer run, weekly-scale review session in `/learn`.
- Reversal cost: changing always-gate later means re-classifying accumulated unreviewed candidates under new rules; changing owner-level scope means reshuffling provenance namespaces and hook installation paths across all 3 devices.

## Linked decisions

- `ccce2be6-12c9-4236-a267-f56786f8d647` — Q1 Deriver scope
- `31ebba19-adb6-4ad0-ac33-ceac5bc5cea2` — Q4 Always-gate v1
- `d162cca4-25ba-4342-b6e2-c1c92bd2ba78` — Q3 Merge proposal shape
- `fa9bd40d-9d30-4c13-ad1d-98669d3b1e2a` — Q2 Dreamer cadence
- `6aa3c882-d955-4661-ab24-0ac2e190593c` — Q7 Quality safeguards
- `eb62980e-0e95-442f-ba3c-3bb885d368d5` — Q8 Two-layer privacy scrubber
- `81216bc3-5c32-4001-8ea3-0761bf4f42e2` — Q5 Thin-wrapper `/learn`
- `b37fb780-96a6-459c-8c2a-b5632bc7592f` — Q6 Owner-level scope
