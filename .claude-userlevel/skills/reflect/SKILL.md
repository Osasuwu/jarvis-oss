---
name: reflect
description: "Cross-session behavioral audit: extracts communication/work patterns from Claude Code sessions on all devices (local + remote via SSH, manual transfer as fallback) and merges them into one report. Triggers: weekly audit, 'что я делаю не так', 'analyze comms', 'паттерны общения', 'merge comms'."
model: opus
effort: high
---

# Reflect

Cross-session behavioral audit across Claude Code sessions. Analyzes communication patterns (correctives, affirmatives, interaction style) to surface recurring behaviors and generate feedback rules.

Two-phase skill, single run end-to-end. Phase A extracts a per-device patterns file — locally, then on each other device via SSH (user's own machines on the user's tailnet). Only the small `{DEVICE}_patterns.json` (<20 KB) is pulled back via scp; the raw extract never leaves its device. Phase B merges the collected files locally and produces the cross-device behavioral report.

> **Why SSH and not upload**: model-side base64-encoding of personal-pattern files combined with reading them back into context trips Anthropic's AUP classifier, and third-party upload (GDrive etc.) publishes personal data. scp between the user's own devices is local file transfer — no third party, no blob-in-context. Skill scripts read/write only local paths and make zero outbound network calls themselves.

## What it does NOT do

- Does not auto-write to memory. User reviews the report and decides which rules to save.
- Does not analyze task content — only interaction patterns (correctives, affirmatives, style).
- Does not upload anywhere. No GDrive/cloud calls. No base64 round-trips of artifacts.
- Does not pull raw extracts (`comms_extract.jsonl`, ~MBs of quotes) off a device — only the compressed patterns file.
- Output artifacts NEVER go into the jarvis repo — only `~/.cache`.
- SSH host topology (aliases, IPs, usernames) NEVER goes into repo files, commits, or issues — the repo is public. Read it from local `~/.ssh/config` at runtime.

## Conventions

- **Per-device staging**: `~/.cache/jarvis-comms-analysis/{DATE}_{DEVICE}/` — holds `comms_extract.jsonl`, `{DEVICE}_patterns.json`
- **Cross-device merge dir**: `~/.cache/jarvis-comms-analysis/merge_{DATE}/` — patterns files land here (scp-pulled or manually copied) before Phase B
- **Sessions source**: `~/.claude/projects/*/*.jsonl` (Path.home() resolves correctly on all OSes)
- **Scripts location**: `$JARVIS_HOME/scripts/analyze-comms/` (set by installer; same relative layout on every device)

---

## Phase A — Extraction (local + remote sweep)

Trigger: "analyze comms" / "проанализируй сессии" / "паттерны общения" / "что я делаю не так" / weekly audit

### Step A1 — Local extraction

```bash
DEVICE=$(hostname)
DATE=$(date +%Y-%m-%d)
STAGE="$HOME/.cache/jarvis-comms-analysis/${DATE}_${DEVICE}"
mkdir -p "$STAGE"

SCRIPTS="$JARVIS_HOME/scripts/analyze-comms"
python "$SCRIPTS/extract_comms.py"     "$STAGE/comms_extract.jsonl"
python "$SCRIPTS/analyze_comms.py"     "$STAGE/comms_extract.jsonl"
python "$SCRIPTS/compress_patterns.py" "$STAGE/comms_extract.jsonl" "$STAGE/${DEVICE}_patterns.json"
```

Print script output inline — aggregate stats only, no quotes.
If `interactive sessions: 0` → skip this device, continue with remotes.
If the staging dir already exists from a same-day run, append `_2`, `_3`, etc.
Caveat: sanity-check `$JARVIS_HOME` points at a real repo checkout before using it (a leaked test env var once pointed it at a pytest temp dir).

### Step A2 — Discover remote jarvis devices

Hosts come from the **local** `~/.ssh/config` — private file, stays on the device.

1. `memory_recall(query="reflect ssh hosts jarvis devices")` — if a confirmed host list exists in memory, use it.
2. Otherwise: read `Host` aliases from `~/.ssh/config`, show the list to the user, and ask which are jarvis devices (some aliases belong to other projects' machines — exclude them). Store the confirmed list to memory (`memory_store`, name `reflect_ssh_jarvis_hosts`, `source_provenance: skill:reflect`) so the next run needs no question.
3. Never write aliases/IPs/usernames into any repo file, issue, PR, or the report itself.

### Step A3 — Remote extraction over SSH

For each remote host, run Phase A remotely and pull back only the patterns file.

**Windows remotes** (default SSH shell is cmd.exe — cmd syntax, `%VAR%` expansion, `&&` chaining, `if not exist … mkdir`):

```bash
HOST=<alias>
# remote hostname first — it names the staging dir and the patterns file
RDEV=$(ssh "$HOST" hostname | tr -d '\r')
RSTAGE="%USERPROFILE%\\.cache\\jarvis-comms-analysis\\${DATE}_${RDEV}"

ssh "$HOST" "if not exist \"$RSTAGE\" mkdir \"$RSTAGE\""
ssh "$HOST" "cd /d \"%JARVIS_HOME%\\scripts\\analyze-comms\" && python extract_comms.py \"$RSTAGE\\comms_extract.jsonl\" && python compress_patterns.py \"$RSTAGE\\comms_extract.jsonl\" \"$RSTAGE\\${RDEV}_patterns.json\""
```

**Unix remotes**: same commands as Step A1 with `$HOME`/`$JARVIS_HOME` syntax. Detect by trying `ssh "$HOST" uname` — success ⇒ Unix.

Notes:
- If `%JARVIS_HOME%` is unset on the remote, fall back to the repo path known for that device (ask memory / the user once, store it).
- If the remote repo predates these scripts → `ssh "$HOST" "cd /d %JARVIS_HOME% && git pull"` first (fast-forward only; not clean → skip the device and report).
- Host unreachable / command fails → **skip the device, continue with the rest**, name the missing device in the final report. Never block the whole run on one host.

### Step A4 — Pull patterns files into merge dir

```bash
MERGE="$HOME/.cache/jarvis-comms-analysis/merge_${DATE}"
mkdir -p "$MERGE"
cp "$STAGE/${DEVICE}_patterns.json" "$MERGE/"
scp "$HOST:.cache/jarvis-comms-analysis/${DATE}_${RDEV}/${RDEV}_patterns.json" "$MERGE/"
```

Pull ONLY `*_patterns.json` (<20 KB each). Never scp `comms_extract.jsonl`.

Do NOT base64-encode or read patterns-file content back into the model context. Paths and script stdout only.

**Fallback (no SSH)**: if no hosts are configured or none are reachable, report the local patterns-file path and ask the user to run Phase A on the other devices and copy each `{DEVICE}_patterns.json` into `$MERGE` manually (USB, Obsidian sync — anything outside the model context), then trigger "merge comms".

---

## Phase B — Cross-device analysis

Runs immediately after Phase A when ≥2 patterns files were collected. Also triggerable standalone: "merge comms" / "cross-device analysis" / "объедини анализ" / "смержи паттерны".

### Step B1 — Confirm merge dir contents

```bash
ls "$MERGE"/*_patterns.json
```

Fewer than 2 device files → fall back to asking the user where the files are (see Phase A fallback). Do NOT auto-fetch from any remote service.

### Step B2 — Run cross-device merge

Pass input files **explicitly** — never a glob that could match `merged_patterns.json` from a previous run (the script skips its own output as a guard, but be explicit anyway):

```bash
SCRIPTS="$JARVIS_HOME/scripts/analyze-comms"
python "$SCRIPTS/analyze_cross_device.py" \
  "$MERGE/<dev1>_patterns.json" "$MERGE/<dev2>_patterns.json" \
  "$MERGE/merged_patterns.json"
```

Print output inline — confidence ranking, no quotes.

### Step B3 — Qualitative analysis (agent)

Read `merged_patterns.json` (<50KB for 3 devices). Spawn a `general-purpose` Agent with this brief — include the full JSON content inline:

> Behavioral pattern data from N Claude Code sessions across M devices (JSON below).
> Structure: corrective_patterns (moments where user pushed back on Jarvis, categorized,
> with confidence_score + frequency_pct + examples), affirmatives (what worked), style stats.
>
> Data quality: the regex classifier has false positives. If an example's "correction"
> text is clearly system-generated rather than typed by the user (compaction summaries
> starting "This session is being continued…", skill payloads starting "Base directory
> for this skill:", hook output "Stop hook feedback:"), treat it as an extraction
> artifact: discount it, recompute your confidence in the pattern from genuine examples
> only, and state explicitly how many examples were genuine.
>
> Write a comprehensive analysis in Russian. Requirements:
> - ALL corrective patterns, sorted by confidence_score desc — no Top-N cutoff
> - Per pattern: name, confidence level (high/medium/low) after artifact discounting,
>   freq%, n_sessions, n_devices, 2-3 anchor quotes from genuine examples, why it matters
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
> Write the report directly to `<MERGE>/report_{DATE}.md` with the Write tool. Do NOT echo full quotes back to the main agent — return only path + section list + word count.
>
> DATA: ```json {merged_patterns.json content} ```

If subagent refuses (safety policy) → do the analysis inline by reading merged_patterns.json directly.

### Step B4 — Show report + offer memory writes

Read `$MERGE/report_{DATE}.md` directly. Show inline:
- All corrective patterns: name + (discounted) confidence + 1-line why
- Full text of ALL candidate feedback rules
- Path to full report file
- Devices skipped during the sweep, if any

Offer "Save N rules to memory?" — only after cross-device run (≥2 devices). Flag single-device-only patterns separately.

Do NOT auto-write to memory. User decides. Do NOT auto-upload the report anywhere.

---

## Safety notes

- patterns files contain anchor quotes — treat as sensitive personal data
- NEVER commit output artifacts to the jarvis repo
- NEVER put SSH topology (aliases, IPs, usernames, paths embedding usernames) into repo files, commits, issues, or PRs — the repo is public; `~/.ssh/config` is the runtime source
- NEVER base64-encode artifacts and read them back into the model context — that pattern (large opaque blob + personal-pattern content) trips the AUP classifier
- ssh/scp only between the user's own devices (aliases from the user's own `~/.ssh/config`); no third-party upload path exists in this skill
- If staging dir already exists from a previous run on the same day, append `_N` suffix rather than overwriting

## Limitations

- Corrective regex is narrow — subtle pushback generates false negatives. System-message false positives are filtered at extraction since 2026-07, but patterns files produced by older script versions may still contain them (the B3 brief compensates)
- Category inference is heuristic — "other" bucket may be large; the Phase B agent fills the gap
- Sessions with <3 user msgs are skipped
- Same pattern corrected on 2 devices counts twice — intentional (cross-device repetition = stronger signal)
- Remote sweep requires SSH reachability and a current repo checkout on each device; unreachable devices are skipped and named in the report; manual transfer remains the fallback
