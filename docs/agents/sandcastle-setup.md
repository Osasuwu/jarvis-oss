# Sandcastle on the routine host — setup runbook

Manual setup steps required to bring the sandcastle AFK loop up on a fresh
routine host. Lists the **unautomated** steps; everything in
`scripts/sandcastle/Register-SandcastleTask.ps1` is automated.

Goal of this doc: avoid the Sprint-3 "six unaccounted manual fixes" pattern
(memory `sprint_plan_pillar7_sprint3_2026_04_25`). Every step here either has
a script that performs it, or is listed below with a one-line justification
for why it isn't automatable today.

Decisions referenced:
- `4890aa35` — routine host = production target; Main PC = dev/test bench
- `0c3017c6` — Failure modes: watchdog autostart + soft-stop window
- `f8e27d53` — Multi-tier model escalation (Ollama → Ollama-small → DeepSeek)
- `58670ea5` — routine-host model tier: 14b primary, 7b downgrade (#538)

## One-time setup

### 1. Docker Desktop

Install Docker Desktop. Configure to start with Windows.

> **Why manual:** Docker Desktop installer is a GUI MSI; silent install is
> possible but the WSL2 + Hyper-V toggling has historically been brittle on
> mixed Windows builds. Cheaper to install once by hand than to write+maintain
> a silent-install path. Watchdog `Start-DockerDesktop` autostarts the daemon
> from cold each run.

### 2. Ollama

Install Ollama from `https://ollama.com/download`. Pull the production models:

```powershell
ollama pull qwen2.5-coder:14b
ollama pull qwen2.5-coder:7b   # Tier 1 OOM-downgrade
```

Set `OLLAMA_CONTEXT_LENGTH=65536` as a **machine-wide** environment variable
(`setx /M OLLAMA_CONTEXT_LENGTH 65536`), then restart the Ollama service.
Claude Code requires ≥64K context; Ollama's default 8K silently truncates the
system prompt and tool definitions, causing small models to bail without
invoking tools.

> **Why manual:** the installer is a signed exe with no documented unattended
> flag we have audited. Watchdog autostarts the `ollama serve` process per run
> but can't install it.

### 3. Repos on disk

The jarvis repo lives under your repos root, e.g. `C:\repos\jarvis` (the
device's `config/device.json` sets `repos_path` to that root). The second
repo (redrobot in the examples below) lives alongside it. Both
must be cloned and on `main`/`master` before scheduling.

### 4. `.sandcastle/.env`

Copy `.sandcastle/.env.example` to `.sandcastle/.env` inside each repo and
fill in:

- `GH_TOKEN` — fine-grained PAT scoped to that repo (Issues: RW, PRs: RW,
  Contents: RW). One token per repo.
- `SUPABASE_URL` + `SUPABASE_KEY` (anon key only — service-role key never goes
  into the container; sandcastle slice #542 + #565 enforce RLS on
  `source_provenance LIKE 'sandcastle:%'`).
- `VOYAGE_API_KEY` (optional — only needed if the in-container memory MCP
  re-embeds candidates).
- `DEEPSEEK_API_KEY` (optional — only if Tier 2 escalation is enabled in the
  scheduled task).

`.sandcastle/.env` is gitignored. Never commit.

### 5. Build the image

```powershell
cd C:\repos\jarvis
npm install                                                  # one-time: pulls tsx + sandcastle
docker build -t sandcastle:jarvis -f .sandcastle/Dockerfile .
```

Repeat with `-t sandcastle:redrobot` and `-f` pointing at the redrobot
sandcastle Dockerfile once #546 lands the redrobot-side scaffolding.

> **Why manual:** image builds are intentionally pinned per-host so a stale
> base layer doesn't silently break overnight. Watchdog assumes the image
> exists.

## Register the scheduled task

Once the prerequisites above are in place, the Task Scheduler entries are
**fully automated**:

```powershell
# One-time machine env so the redrobot watchdog finds the worktree at 02:00.
setx /M REDROBOT_REPO_ROOT C:\repos\redrobot
# (open a fresh shell after setx — Machine vars don't refresh in the current one)

# jarvis loop — 22:00 nightly, soft-stop at 02:00
.\scripts\sandcastle\Register-SandcastleTask.ps1 -Repo jarvis

# redrobot loop — 02:00 nightly, soft-stop at 08:00 (non-overlapping)
.\scripts\sandcastle\Register-SandcastleTask.ps1 -Repo redrobot
```

Both repos share the **same** `Run-Sandcastle.ps1` watchdog (it lives in jarvis and is `-Repo`-aware) — no duplication into the redrobot repo. Redrobot only carries its own `.sandcastle/` (Dockerfile, prompt, .env), not the scheduler scripts.

The script is idempotent — running again replaces the existing task. It
refuses to register on non-routine-host devices unless `-Force` is passed.

Verify:

```powershell
Get-ScheduledTask -TaskName 'Sandcastle-Jarvis','Sandcastle-Redrobot' |
    Select-Object TaskName, State, @{Name='NextRun'; Expression={
        ($_ | Get-ScheduledTaskInfo).NextRunTime
    }}
```

Trigger manually for smoke validation:

```powershell
Start-ScheduledTask -TaskName 'Sandcastle-Jarvis'
# Watch the watchdog log:
Get-ChildItem .sandcastle\runtime -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 |
    Get-ChildItem -Filter run.log |
    Get-Content -Wait
```

## After the first overnight run

The morning after the first overnight run, per slice #545 AC:

1. Find the run dir under `.sandcastle/runtime/<timestamp>/` and read
   `run.log` end-to-end for silent failures (timeouts that didn't bubble up,
   git auth errors that got swallowed, etc.).
2. If silent failures found, **file each as a follow-up issue** before
   continuing — do not defer.
3. Confirm the `outcome_record` row exists in Supabase with:
   - `project = 'jarvis'` (or `redrobot`)
   - `pattern_tags` includes both `sandcastle` and `afk`
   - device tag matches `<routine-host-tag>`
4. Review the agent's PR using the `/delegate` §6 audit checklist
   (scope-fit, value-change, interaction effects, symmetric fixes) before
   merging or commenting.

## Disable / remove

```powershell
Unregister-ScheduledTask -TaskName 'Sandcastle-Jarvis' -Confirm:$false
Unregister-ScheduledTask -TaskName 'Sandcastle-Redrobot' -Confirm:$false
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Task fires but `run.log` empty | Working dir mismatch | Verify `WorkingDir` in Task Scheduler matches the repo root. |
| `docker info` timeout in log | Docker Desktop not autostarting cold-boot | Open Docker Desktop manually once, enable "Start Docker Desktop when you sign in to your computer". |
| Agent bails immediately with `<promise>COMPLETE</promise>` | `OLLAMA_CONTEXT_LENGTH` not set machine-wide | `setx /M OLLAMA_CONTEXT_LENGTH 65536`, restart Ollama. |
| OOM during 14b run | Another VRAM consumer present (browser GPU accel, second model loaded) | Watchdog Tier 1 will downgrade to 7b automatically; check `outcome_record.llm.model` to confirm. |
| Agent picks no issue | No issue currently labelled `sandcastle` + open | Queue is empty — expected; no action. |
