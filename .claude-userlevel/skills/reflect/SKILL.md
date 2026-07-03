---
name: reflect
description: "Cross-session behavioral audit: extract communication and work patterns from local Claude Code sessions. Two phases: Phase A (per-device) extracts patterns to ~/.cache; user manually transfers files between devices. Phase B (cross-device) merges them and produces a comprehensive behavioral report. Triggers: weekly audit, on-demand 'что я делаю не так', 'analyze comms', 'паттерны общения', 'merge comms'."
---

# Reflect

Cross-session behavioral audit across Claude Code sessions. Analyzes communication patterns (correctives, affirmatives, interaction style) to surface recurring behaviors and generate feedback rules.

Two-phase skill. Phase A on each device produces a per-device patterns file in `~/.cache`. User manually transfers those files (Drive web UI, USB, Obsidian sync — anything outside the model context) into one local folder on the device that will run Phase B. Phase B reads them locally and produces the cross-device behavioral report.

> **Why no auto-upload**: model-side base64-encoding of personal-pattern files combined with reading them back into context trips Anthropic's AUP classifier. Manual file transfer by the user avoids that path entirely. Skill scripts read/write only local paths and make zero outbound network calls.

## What it does NOT do

- Does not auto-write to memory. User reviews the report and decides which rules to save.
- Does not analyze task content — only interaction patterns (correctives, affirmatives, style).
- Does not auto-upload anywhere. No GDrive MCP calls. No base64 round-trips of artifacts.
- Output artifacts NEVER go into the jarvis repo — only `~/.cache`.

## Conventions

- **Per-device staging**: `~/.cache/jarvis-comms-analysis/{DATE}_{DEVICE}/` — holds `comms_extract.jsonl`, `{DEVICE}_patterns.json`
- **Cross-device merge dir**: `~/.cache/jarvis-comms-analysis/merge_{DATE}/` — user manually copies each device's `{DEVICE}_patterns.json` here before running Phase B
- **Sessions source**: `~/.claude/projects/*/*.jsonl` (Path.home() resolves correctly on all OSes)
- **Scripts location**: `$JARVIS_HOME/scripts/analyze-comms/` (set by installer; same dir on every device)

---

## Phase A — Per-device extraction

Trigger: "analyze comms" / "проанализируй сессии" / "паттерны общения" / "что я делаю не так"

### Step A1 — Prepare staging dir

```bash
DEVICE=$(hostname)
DATE=$(date +%Y-%m-%d)
STAGE="$HOME/.cache/jarvis-comms-analysis/${DATE}_${DEVICE}"
mkdir -p "$STAGE"
```

If directory already exists from a same-day run, append `_2`, `_3`, etc.

### Step A2 — Run extraction + stats

```bash
SCRIPTS="$JARVIS_HOME/scripts/analyze-comms"
python "$SCRIPTS/extract_comms.py"  "$STAGE/comms_extract.jsonl"
python "$SCRIPTS/analyze_comms.py"  "$STAGE/comms_extract.jsonl"
```

Print `analyze_comms.py` output inline — aggregate stats only, no quotes, safe to show.

If `interactive sessions: 0` → stop. Nothing to analyze.

### Step A3 — Compress to patterns file

```bash
python "$SCRIPTS/compress_patterns.py" \
  "$STAGE/comms_extract.jsonl" \
  "$STAGE/${DEVICE}_patterns.json"
```

Print output inline (stats only, no quotes).

### Step A4 — Report to user

Show:
- Aggregate stats (already printed in A2)
- Absolute path to `$STAGE/${DEVICE}_patterns.json`
- "Run Phase A on other devices, then manually collect each device's `{DEVICE}_patterns.json` into one folder on the Phase B host (`~/.cache/jarvis-comms-analysis/merge_{DATE}/` is the default), and trigger `merge comms`."

Do NOT base64-encode or read the patterns file content back into the model context. Report the path only.

Do NOT run qualitative analysis at this stage — that's Phase B's job.

---

## Phase B — Cross-device analysis

Trigger: "merge comms" / "cross-device analysis" / "объедини анализ" / "смержи паттерны"

Prerequisite: user has manually collected `{DEVICE}_patterns.json` files from ≥2 devices into one local folder.

### Step B1 — Confirm merge dir contents

Default merge dir:
```bash
STAGE_MERGE="$HOME/.cache/jarvis-comms-analysis/merge_$(date +%Y-%m-%d)"
mkdir -p "$STAGE_MERGE"
ls "$STAGE_MERGE"/*_patterns.json 2>/dev/null
```

If empty or fewer than 2 files → ask the user where the patterns.json files are. Either prompt them to move the files into `$STAGE_MERGE`, or accept an alternate path argument. Do NOT auto-fetch from GDrive or any remote source.

### Step B2 — Run cross-device merge

```bash
SCRIPTS="$JARVIS_HOME/scripts/analyze-comms"
python "$SCRIPTS/analyze_cross_device.py" \
  "$STAGE_MERGE/"*_patterns.json \
  "$STAGE_MERGE/merged_patterns.json"
```

Print output inline — confidence ranking, no quotes.

### Step B3 — Qualitative analysis (agent)

Read `merged_patterns.json` (<50KB for 3 devices). Spawn a `general-purpose` Agent with this brief — include the full JSON content inline:

> Behavioral pattern data from N Claude Code sessions across M devices (JSON below).
> Structure: corrective_patterns (moments where user pushed back on Jarvis, categorized,
> with confidence_score + frequency_pct + examples), affirmatives (what worked), style stats.
>
> Write a comprehensive analysis in Russian. Requirements:
> - ALL corrective patterns, sorted by confidence_score desc — no Top-N cutoff
> - Per pattern: name, confidence level (high/medium/low), freq%, n_sessions, n_devices,
>   2-3 anchor quotes from examples, why it matters behaviorally
> - Affirmatives: what Jarvis does that works
> - Style: phrasing, language split, bimodal length distribution
> - Metacommunication: how disagreement is expressed, autonomy escalation cycle
> - 2-3 non-obvious findings
> - Candidate feedback rules for ALL encodable patterns:
>     **Rule**: [the rule]
>     **Why**: [evidence + confidence level]
>     **How to apply**: [trigger + action]
> - No word limit. Include low-confidence patterns with appropriate label.
>
> Write the report directly to `<STAGE_MERGE>/report_{DATE}.md` with the Write tool. Do NOT echo full quotes back to the main agent — return only path + section list + word count.
>
> DATA: ```json {merged_patterns.json content} ```

If subagent refuses (safety policy) → do the analysis inline by reading merged_patterns.json directly.

### Step B4 — Show report + offer memory writes

Read `$STAGE_MERGE/report_{DATE}.md` directly. Show inline:
- All corrective patterns: name + confidence + 1-line why
- Full text of ALL candidate feedback rules
- Path to full report file

Offer "Save N rules to memory?" — only after cross-device run (≥2 devices). Flag single-device-only patterns separately.

Do NOT auto-write to memory. User decides. Do NOT auto-upload the report anywhere.

---

## Safety notes

- patterns files contain anchor quotes — treat as sensitive personal data
- NEVER commit output artifacts to the jarvis repo
- NEVER base64-encode artifacts and read them back into the model context — that pattern (large opaque blob + personal-pattern content) trips the AUP classifier
- Skill makes NO outbound network calls. All GDrive auto-upload paths have been intentionally removed. User transfers files manually between devices.
- If staging dir already exists from a previous run on the same day, append `_N` suffix rather than overwriting

## Limitations

- Corrective regex is narrow — subtle pushback generates false negatives
- Category inference is heuristic — "other" bucket may be large; the Phase B agent fills the gap
- Sessions with <3 user msgs are skipped
- Same pattern corrected on 2 devices counts twice — intentional (cross-device repetition = stronger signal)
- Manual file transfer between Phase A devices and the Phase B host adds friction, but no auto-upload path exists that doesn't trip AUP
