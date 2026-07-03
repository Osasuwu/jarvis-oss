# analyze-comms

Communication-pattern extraction across local Claude Code sessions. Used by the `/reflect` skill (Phase A — per-device extraction, Phase B — cross-device merge + qualitative analysis).

History: these scripts were originally bundled inside the `/reflect` skill at `~/.claude/skills/reflect/`. Migrated here per #530 / ADR-0001 — skill-shape vs. script-shape separation. `/reflect` SKILL.md now invokes them from this directory via `$JARVIS_HOME/scripts/analyze-comms/`.

## Layout

- `extract_comms.py SRC_DIR? OUT_JSONL` — walks `~/.claude/projects/*/*.jsonl` (or `SRC_DIR`) and emits compact JSONL with `{sess, proj, ts, role, len, text}` per real user/assistant turn. Filters out tool I/O, system reminders, hook injections.
- `analyze_comms.py EXTRACT_JSONL` — aggregate stats (no quotes). Safe to print inline.
- `compress_patterns.py EXTRACT_JSONL OUT_PATTERNS_JSON` — per-device pattern compression. Output `<DEVICE>_patterns.json` (~20KB) preserves anchor quotes truncated to 100 chars. Treat as sensitive personal data.
- `analyze_cross_device.py PATTERNS_JSON [PATTERNS_JSON ...] OUT_MERGED_JSON` — merges N device patterns files into a single ranked report with confidence weights.
- `build_bundle.py` — deprecated stub, kept to surface a clear error if older docs reference it.

## Manual invocation (Phase A — per-device)

```bash
DEVICE=$(hostname)
DATE=$(date +%Y-%m-%d)
STAGE="$HOME/.cache/jarvis-comms-analysis/${DATE}_${DEVICE}"
mkdir -p "$STAGE"

python "$JARVIS_HOME/scripts/analyze-comms/extract_comms.py"     "$STAGE/comms_extract.jsonl"
python "$JARVIS_HOME/scripts/analyze-comms/analyze_comms.py"     "$STAGE/comms_extract.jsonl"
python "$JARVIS_HOME/scripts/analyze-comms/compress_patterns.py" "$STAGE/comms_extract.jsonl" "$STAGE/${DEVICE}_patterns.json"
```

Then transfer `${DEVICE}_patterns.json` to the Phase B host (manually — Drive web UI, USB, Obsidian sync). No auto-upload; see [SKILL.md](../../.claude-userlevel/skills/reflect/SKILL.md) "Why no auto-upload".

## Manual invocation (Phase B — cross-device merge)

Collect `*_patterns.json` from each device into one local folder (default `~/.cache/jarvis-comms-analysis/merge_<DATE>/`), then:

```bash
python "$JARVIS_HOME/scripts/analyze-comms/analyze_cross_device.py" \
  "$MERGE_DIR"/*_patterns.json \
  "$MERGE_DIR/merged_patterns.json"
```

Qualitative analysis + memory-write decisions happen agent-side via `/reflect` Phase B steps B3–B4 — not in the script.

## Scheduling Phase A (cron / scheduled task)

Phase A is mechanical and per-device — good candidate for a daily/weekly cron entry. Phase B requires user judgment, stays manual.

Per-device registration is **not** automated yet (separate follow-up; tracked via #530 AC "Cron registered for the recurring portion"). Register manually:

- **Windows** — use `mcp__scheduled-tasks__create_scheduled_task` or Task Scheduler, command: `python "%JARVIS_HOME%\scripts\analyze-comms\extract_comms.py" "%USERPROFILE%\.cache\jarvis-comms-analysis\daily\comms_extract.jsonl"` (extend with `compress_patterns.py` as needed).
- **Linux/macOS** — `crontab -e`, weekly entry pointing at the same scripts.

The /reflect skill remains the principal-facing trigger ("analyze comms" / "что я делаю не так") regardless of whether Phase A also runs on a schedule.

## Output discipline

- Output artifacts go to `~/.cache/jarvis-comms-analysis/` only. **Never commit to the repo.**
- `*_patterns.json` contains anchor quotes — sensitive personal data. Don't paste contents into issues/PRs/chat. Pass paths only.
- Don't base64-encode patterns and read them back into model context — that pattern (large opaque blob + personal-pattern content) trips the AUP classifier on the model side.
