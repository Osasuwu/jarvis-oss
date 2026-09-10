# analyze-comms

Communication-pattern extraction across local Claude Code sessions. Drives a two-phase analysis (Phase A — per-device extraction, Phase B — cross-device merge + qualitative analysis).

History: these scripts were originally bundled inside the old `/reflect` skill at `~/.claude/skills/reflect/`. Migrated here per #530 / ADR-0001 — skill-shape vs. script-shape separation, so the pipeline runs from this directory (`$JARVIS_HOME/scripts/analyze-comms/`) independent of any specific skill.

## Layout

- `extract_comms.py SRC_DIR? OUT_JSONL` — walks `~/.claude/projects/*/*.jsonl` (or `SRC_DIR`) and emits compact JSONL with `{sess, proj, ts, role, len, text}` per real user/assistant turn. Filters out tool I/O, system reminders, hook injections.
- `analyze_comms.py EXTRACT_JSONL` — aggregate stats (no quotes). Safe to print inline.
- `compress_patterns.py EXTRACT_JSONL OUT_PATTERNS_JSON` — per-device pattern compression. Output `<DEVICE>_patterns.json` (~20KB) preserves anchor quotes truncated to 100 chars. Treat as sensitive personal data.
- `detect_rule_violations.py EXTRACT_JSONL OUT_PATTERNS_JSON` — regex/keyword starter detector (#513). Reads `feedback`-type, `always_load`-tagged memories from Supabase (`SUPABASE_URL`/`SUPABASE_KEY` required), derives a keyword set per rule, and flags assistant messages matching a rule above threshold. Merges into `OUT_PATTERNS_JSON` under `"rule_violations"` — must run **after** `compress_patterns.py`, which overwrites the same file. Candidates are for owner labeling (#514), not a precision claim.
- `detect_hallucinations.py EXTRACT_JSONL OUT_PATTERNS_JSON` — correction-phrase detector (#513). Flags a preceding assistant message as a hallucination-attribution candidate when a later user message contains a RU/EN correction phrase within a short lookback window. No Supabase dependency — the phrase list is hardcoded. Merges into `OUT_PATTERNS_JSON` under `"hallucinations"` — same after-`compress_patterns.py` ordering requirement.
- `analyze_cross_device.py PATTERNS_JSON [PATTERNS_JSON ...] OUT_MERGED_JSON` — merges N device patterns files into a single ranked report with confidence weights.
- `build_bundle.py` — deprecated stub, kept to surface a clear error if older docs reference it.
- `collect_labels.py <period> report.md [report.md ...]` — HITL ground-truth collector (#514). Parses the structured `[+]`/`[-]`/`[?]` checkbox lines the owner labels in a Phase B `report.md` copy (format: SKILL.md Step B3), aggregates across N labeled reports (last occurrence wins per candidate), and upserts into Supabase `memories` row `reflect_eval_groundtruth_<period>`. Idempotent — safe to re-run against the same period. Requires `SUPABASE_URL`/`SUPABASE_KEY`.
- `eval_detectors.py <period> <out_dir>` — Phase 2 precision/recall A/B (#514). Reads the ground truth `collect_labels.py` wrote, scores the existing regex detectors (#513) directly from the ground-truth verdicts, runs an LLM classification pass over the same candidate snippets, and applies the issue's decision rule (LLM adopted only if ≥2x regex recall at within-10-points precision) to produce `reflect_eval_summary_<period>.md`. Scaffolding only until ≥3 weeks of labeled reports exist (#514 Phase 1 gate) — the LLM pass takes an injectable `classify_fn`, defaulting to a live (untested-by-CI) `anthropic` call.

## Manual invocation (Phase A — per-device)

```bash
DEVICE=$(hostname)
DATE=$(date +%Y-%m-%d)
STAGE="$HOME/.cache/jarvis-comms-analysis/${DATE}_${DEVICE}"
mkdir -p "$STAGE"

python "$JARVIS_HOME/scripts/analyze-comms/extract_comms.py"          "$STAGE/comms_extract.jsonl"
python "$JARVIS_HOME/scripts/analyze-comms/analyze_comms.py"          "$STAGE/comms_extract.jsonl"
python "$JARVIS_HOME/scripts/analyze-comms/compress_patterns.py"      "$STAGE/comms_extract.jsonl" "$STAGE/${DEVICE}_patterns.json"
python "$JARVIS_HOME/scripts/analyze-comms/detect_rule_violations.py" "$STAGE/comms_extract.jsonl" "$STAGE/${DEVICE}_patterns.json"
python "$JARVIS_HOME/scripts/analyze-comms/detect_hallucinations.py"  "$STAGE/comms_extract.jsonl" "$STAGE/${DEVICE}_patterns.json"
```

`detect_rule_violations.py` requires `SUPABASE_URL`/`SUPABASE_KEY` in the environment (reads `feedback`-type, `always_load`-tagged memories). Both detectors must run **after** `compress_patterns.py` — it overwrites `${DEVICE}_patterns.json`; the detectors merge into it.

Then transfer `${DEVICE}_patterns.json` to the Phase B host (manually — Drive web UI, USB, Obsidian sync). No auto-upload — `*_patterns.json` holds anchor quotes (sensitive personal comms data); an automated upload path would put that on a network channel with no human review, so transfer stays a deliberate manual step.

## Manual invocation (Phase B — cross-device merge)

Collect `*_patterns.json` from each device into one local folder (default `~/.cache/jarvis-comms-analysis/merge_<DATE>/`), then:

```bash
python "$JARVIS_HOME/scripts/analyze-comms/analyze_cross_device.py" \
  "$MERGE_DIR"/*_patterns.json \
  "$MERGE_DIR/merged_patterns.json"
```

Qualitative analysis + memory-write decisions happen agent-side, during Phase B steps B3–B4 of the analysis workflow — not in the script.

## Manual invocation (Phase 2 — eval, #514)

HITL-gated, not part of the automated Phase A/B pipeline. After the owner labels a copy of a Phase B `report.md` (replacing `[ ]` with `[+]`/`[-]`/`[?]` per candidate):

```bash
python "$JARVIS_HOME/scripts/analyze-comms/collect_labels.py" "$PERIOD" "$MERGE/report_labeled.md"
```

Repeat once per labeled weekly report, same `$PERIOD`, before running the eval — the issue's AC requires ≥3 weekly labeled reports before Phase 2 is meaningful:

```bash
python "$JARVIS_HOME/scripts/analyze-comms/eval_detectors.py" "$PERIOD" "$STAGE"
```

Writes `$STAGE/reflect_eval_summary_<PERIOD>.md` with the regex-vs-LLM precision/recall table and the upgrade/stay decision.

## Scheduling Phase A (cron / scheduled task)

Phase A is mechanical and per-device — good candidate for a daily/weekly cron entry. Phase B requires user judgment, stays manual.

Per-device registration is **not** automated yet (separate follow-up; tracked via #530 AC "Cron registered for the recurring portion"). Register manually:

- **Windows** — use `mcp__scheduled-tasks__create_scheduled_task` or Task Scheduler, command: `python "%JARVIS_HOME%\scripts\analyze-comms\extract_comms.py" "%USERPROFILE%\.cache\jarvis-comms-analysis\daily\comms_extract.jsonl"` (extend with `compress_patterns.py` as needed).
- **Linux/macOS** — `crontab -e`, weekly entry pointing at the same scripts.

Scheduling only covers Phase A's mechanical extraction; the qualitative Phase B pass still needs an agent-side trigger (e.g. "analyze comms" / "что я делаю не так") regardless of whether Phase A also runs on a schedule.

## Output discipline

- Output artifacts go to `~/.cache/jarvis-comms-analysis/` only. **Never commit to the repo.**
- `*_patterns.json` contains anchor quotes — sensitive personal data. Don't paste contents into issues/PRs/chat. Pass paths only.
- Don't base64-encode patterns and read them back into model context — that pattern (large opaque blob + personal-pattern content) trips the AUP classifier on the model side.
