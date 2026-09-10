# comm_patterns: re-derived label taxonomy, write-time redaction, global scope

**Status:** accepted (2026-05-10, issue [#580](https://github.com/Osasuwu/jarvis/issues/580), parent [#526](https://github.com/Osasuwu/jarvis/issues/526))

`comm_patterns` is a per-instance row table that stores classifier output for cross-device communication-pattern aggregation. Stop-hook extractor (slice 2, [#581](https://github.com/Osasuwu/jarvis/issues/581)) classifies user→assistant turns and writes here. `/learn comms` (slice 3, [#582](https://github.com/Osasuwu/jarvis/issues/582)) reads. This ADR locks four shape decisions for the schema slice (#580) so a reader of the migration alone does not have to reverse-engineer them.

The row-shape and column choices come from already-recorded decisions linked at the bottom; this ADR resolves the four open questions the parent issue body called out.

## Decisions

### 1. `primary_label` enum — re-derived afresh

Six values, with `subtype text` carrying particulars:

| `primary_label` | Definition |
|---|---|
| `correction_wrong_direction` | Agent went the wrong way; user redirects. (e.g. misread intent, hallucinated, wrong owner of an artefact.) |
| `correction_incomplete` | Agent stopped early or missed an integration step. (e.g. checked Copilot review but not Claude review; backend done, frontend untouched.) |
| `affirmation` | Pure approval, no change requested. ("правильно", "perfect".) |
| `affirmation_with_redirect` | Approval plus nuance or scope adjustment. ("правильно, но изменений не 20, а 3".) |
| `preference_directive` | Persistent rule the user wants applied going forward — style, terminology, format, cadence, process. (e.g. "называй меня user, не owner".) |
| `meta_protocol` | A protocol step was skipped, applied wrong, or needs to be applied differently. (e.g. recall missed; `record_decision` not emitted; grill trigger ignored; "память может быть неактуальной — лучше переспроси".) |

`subtype` is free text and carries the specific subspecies — examples: `repeat_mistake`, `terminology`, `cross_device_miss`, `hallucination`, `scope_shrink`, `recall_missed`, `decision_unrecorded`. `repeat_mistake` is treated as a *modifier* on whichever substantive label fits, not a label of its own — the same correction can recur, and conflating "what kind" with "how-many-th time" inflates the enum.

Confidence is a numeric column, not an `ambiguous` enum bucket; if the classifier is unsure it emits its best guess at low confidence and `/learn comms` filters by threshold.

### 2. Redaction policy

Day-1 redaction is **write-time**, owned by the extractor that classifies the message. The scrubber from Pillar-9 Sprint-1 (`scripts/secret_scrub.py` + `record_decision` PreToolUse hook ancestry, ADR 0003 §"two-layer privacy scrubber") is reused — no new scanner is written for `comm_patterns`.

The shape:

- `anchor_quote text not null` — stores the user-message snippet that triggered the label, after the scrubber has run. If the scrubber substituted any tokens (e.g. `[REDACTED:env:OPENAI_API_KEY]`), the snippet retains the substitutions.
- `redacted boolean not null default false` — set true by the extractor when the scrubber substituted any tokens. Lets `/learn comms` filter or surface "this anchor was scrubbed; the unredacted text is gone".

There is **no second column for raw text**. The point of the scrubber is to ensure secrets/PII never enter Supabase (which is shared with redrobot). Storing the raw alongside the redacted text would defeat the boundary.

### 3. Project scope: global (no `project='jarvis'` pinning)

`comm_patterns` does **not** have a `project` column. Communication patterns are about the owner-agent relationship, not the codebase the session happened to open. The same correction style appears across jarvis, redrobot, and any future repo; project-pinning would either fragment the corpus or force every reader to do `OR project='global'` filtering.

If a downstream consumer needs project context for a specific row, `session_id` resolves to a session directory that resolves to a repo via the existing `~/.claude/projects/<encoded-path>/` convention. That mapping is reconstructible from the device's filesystem; it does not need to be denormalised into the row.

This is consistent with ADR 0003 §2 (owner-level scope for the Deriver). One human, one corpus.

### 4. Why α (per-instance row) over β (per-run jsonb)

α: one row per detected pattern instance (chosen).
β: one row per extractor-run, with a JSONB array of detected patterns inside.

α wins on three axes:

- **Aggregation** — counting by `primary_label`, time-bucketing by `captured_at`, and joining to `embedding` for "find similar past corrections" all reduce to ordinary SQL with α. With β, every read pays the cost of expanding the JSONB array, and indexing a label inside JSONB is awkward (`GIN ((doc->'patterns'))` works but is harder to compose with HNSW on embeddings).
- **Idempotency** — the Stop-hook watermark mechanism (`comm_patterns_watermark`, slice 1) is a pair of integers (`device`, `session_id`, `last_message_idx`). With α, re-running the extractor over already-processed indices is a no-op via the unique `(device, session_id, message_idx)` index. With β, re-running emits a new run-row that overlaps the previous run-row's payload, and dedup logic moves into the reader.
- **Day-1 embeddings** — embeddings are per-instance, not per-run. The β shape would force a sidecar `comm_pattern_embeddings` table keyed on `(run_id, idx_in_jsonb)`, which is α with extra steps. Storing embeddings inline in α (`vector(512)` column, same dim as `memories.embedding`) is the obvious shape.

The α cost is row count: ~1 row per detected pattern per session. At observed rates (60–70 interactive sessions/14d, ~3 detected patterns/session = ~10/day) this is two orders of magnitude below `events_canonical` ingest, so storage is not a constraint.

## Why these — not the predecessors

The four open questions in the parent issue body were called out specifically because the *defaults* the previous design would suggest are wrong:

- The old `/reflect` regex taxonomy (`permission_seeking`, `tunnel_vision`, `hallucination_attribution`, `repeat_mistake`, `cross_device_miss` + `other`) predates the post-refactor concerns visible in the fresh corpus and almost never matched cleanly: of 176 NEG-regex hits across 69 interactive sessions in the 14 days to 2026-05-10, manual review of a stratified sample of 40 found ≈12 clean corrections, none of which were a tight fit for those 5 buckets. Carrying it over would have locked the CHECK constraint on a vocabulary the corpus does not speak.
- "Store raw + redacted side by side" sounds safer until you remember the DB is shared with redrobot. The redaction boundary has to be at the write, not at the read.
- Project-pinning sounds neat until you ask which project the pattern "always asks before deleting" belongs to. None of them; the pattern is owner-level.
- The β (per-run jsonb) shape sounds compact until you index it.

## Considered alternatives

- **Carry old /reflect taxonomy unchanged.** Rejected per fresh-corpus evidence above.
- **Add `ambiguous` enum value for low-confidence cases.** Rejected — `confidence numeric(3,2)` already encodes uncertainty without polluting the enum surface; thresholds belong in readers.
- **Make `repeat_mistake` its own enum value.** Rejected — it is a modifier on substantive labels; belongs in `subtype`.
- **Larger 8–10-value enum** (split `affirmation_with_redirect` into `scope_shrink` / `scope_expand`; separate `terminology` from process directives). Rejected — fine grain belongs in `subtype` to keep the enum stable across re-derivations and to keep the CHECK constraint cheap to evolve.
- **Store raw `anchor_quote_raw text` alongside the redacted `anchor_quote`.** Rejected — defeats the redaction boundary; secrets would round-trip through a DB shared with redrobot.
- **Project-pinned `comm_patterns` with `project='jarvis'` default.** Rejected — communication patterns are owner-level; pinning fragments the corpus.
- **β: one row per extractor run with JSONB array of patterns.** Rejected on aggregation, idempotency, and embedding-locality grounds (above).

## Sample size and method (for §1)

- **Sources:** `~/.claude/projects/<jarvis-project>/*.jsonl` and `~/.claude/projects/<other-project>/*.jsonl`, files modified within 14 days (cutoff: 2026-04-26 → 2026-05-10).
- **Filter:** sessions with ≥3 user messages after stripping tool results, `<system-reminder>` blocks, hook injections, and `<command-…>` echoes (same filter as [`extract_comms.py`](../../scripts/analyze-comms/extract_comms.py)).
- **Pool:** 69 interactive sessions, 676 user messages.
- **Regex prefilter** (kept identical to old `/reflect` for continuity with prior-art baseline):
  - NEG: 176 hits.
  - POS: 17 hits.
- **Manual review:** stratified sample of 40 NEG + 15 POS, reviewed by hand 2026-05-10. Of 40 NEG, ≈12 were clean corrections; the rest were false positives from skill-body echoes and `<task-notification>` blocks.
- **Cross-check:** the six labels above were tested against the 12 clean corrections + 15 POS samples; every clean instance fit one label cleanly with no need for an `other` bucket.

The old vocabulary's failure to match cleanly is the empirical justification for re-derivation, not a stylistic preference.

## Cross-project review

`mcp-memory/schema.sql` and Supabase are shared with redrobot. The slice adds two new tables (`comm_patterns`, `comm_patterns_watermark`) — neither collides with existing redrobot reads/writes. No redrobot column is touched. Confirmed via `information_schema` precheck before the paired `apply_migration` ran.

## Linked decisions

- `9e9ab041-276d-4ef2-ab60-b066c1e05a76` — L3 dual-column taxonomy (primary_label enum + subtype text)
- `32825852-776b-4f87-a1d8-fbcdb02f3436` — α per-instance row shape, day-1 embeddings, Stop-hook trigger with per-device watermark
- `3d8d99ef-0158-433a-8642-3f7b46c17cdf` — anchor_quote (scrubbed) + redacted boolean, write-time scrubber, global project scope
- `2723af58-1d12-40de-884d-98a95307ce2c` — split #526 into three child issues
- `a80445d9-58ba-45ac-acad-c549b4922668` — six-value enum re-derived from fresh corpus (this ADR §1)
