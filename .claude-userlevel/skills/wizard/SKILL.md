---
name: wizard
description: Generate a bash wizard that walks a human through steps only they can do: provisioning infra, capturing credentials/CI secrets, an unfamiliar dashboard, or a one-off migration/cutover. Skip it for steps the agent can perform itself.
---

# Wizard

A **wizard** is a bash script that walks a human, step by step, through a manual procedure that's tedious to do by hand and tedious to re-explain to an AI every time. It opens each URL, says exactly what to click and copy, captures the values, writes them where they belong (`.env`, GitHub secrets), confirms at every stage, and shows how many stages are left. It might configure a third-party service, run a one-off migration, or move the project from one state to another.

Adapted from upstream `mattpocock/skills` `engineering/wizard` — see [AIHERO_CREDIT.md](../AIHERO_CREDIT.md) for the adaptation summary. Evaluated and adopted via #1405 (milestone #59): no existing jarvis skill covers this gap — `setup-tasks` is scoped to internal scheduled-task/cron bootstrapping, not one-off human-facing dashboard or credential walkthroughs.

Two concrete jarvis use cases this fills: the `proxy-network-restore` goal (walking a new VPS host's dashboard step by step to stand up VLESS+Reality on 3X-UI) and Supabase/VoyageAI credential setup on a fresh device.

The delightful UX is already solved by [template.sh](template.sh) — stage-by-stage progress, confirmation gates, cross-platform URL opening (including WSL), hidden secret entry, idempotent `.env` upserts, `gh secret`/`gh variable` writes, and a closing summary. **Your job is only to scope the procedure and author its stages.** The library above the `STAGES` marker is identical in every wizard; that consistency is the point — never hand-edit it.

A wizard is ephemeral by default — built for one run, saved to a scratch or `scripts/` path, deleted when the job's done. Commit it only when the user wants a repeatable setup path that should live in the repo.

## Relationship to other skills

`/setup-tasks` bootstraps *jarvis's own* scheduled-task infrastructure on a device jarvis already controls — no human-only step in the loop. `/wizard` is the opposite case: a procedure the agent structurally cannot perform itself (clicking through a dashboard UI, copying a secret only visible after a manual reveal step). No skill-to-skill calls — `/wizard` doesn't invoke or get invoked by other skills (ADR-0001).

## Process

### 1. Scope the procedure

Work out every manual step the human must take and every value that gets captured along the way. Read the repo first — don't ask cold:

- For setup: `README`, `docker-compose*`, framework config, and `.github/workflows/*` (every `secrets.*` / `vars.*` reference is a value the wizard must produce). The `.env*` family is **not** readable — including `.env.example`: the permission deny is a glob over the whole family and deny rules cannot carry allowlist exceptions, so the secret-free template shares the real files' fate. When the var list exists only in `.env.example`, ask the user to paste it; it holds no values, so pasting is safe.
- For a migration or transition: the current state, the target state, and the irreversible actions between them.

Then show the user the ordered list of stages and the values each produces, and confirm — they may add, drop, or reorder.

**Done when:** every stage is named in order, and for each captured value you know (a) where the human gets it, (b) where it's written (`.env`, a GitHub secret, both, or nowhere — some stages are pure actions), and (c) whether it's secret (hidden entry) or public.

### 2. Map each stage's journey

For each stage, write the precise path a human follows: which URL to open, what to do there, where a value is shown, which variable it fills — e.g. "Dashboard → Developers → API keys → Reveal test key → copy". Where you don't actually know the current UI or the exact command, say so and ask the user or check the docs — never invent steps that may not exist.

**Done when:** every stage traces to concrete instructions a stranger could follow.

### 3. Author the wizard

Copy `template.sh` to the target path (default: the session scratchpad directory — the `Scratchpad Directory` path given in every session's Environment block — or `scripts/` if it's meant to be repeatable). Replace the example stage with one `stage` per step, in dependency order. Use the library helpers — `stage`, `say`/`step`, `open_url`, `ask`/`ask_secret`, `write_env`, `set_secret`/`set_var`, `pause`/`confirm` — and set `TOTAL_STAGES` to the number of stages you wrote.

Hold the bar the template sets: open the URL before asking for its value, use `ask_secret` for anything secret, `write_env` every persisted value, `set_secret` only the values CI actually needs, and `confirm` before any irreversible action. Each `stage` clears the screen so only the current step is visible — keep a stage to one focused task so nothing the human needs scrolls away. Don't touch the library above the marker.

### 4. Verify and hand off

- `bash -n <script>`; run `shellcheck` if available.
- `chmod +x <script>`.
- Don't run it end-to-end yourself — it opens browsers and blocks on human input. Trace it statically instead: every value from step 1 is captured and lands where step 1 said, and every `set_secret` name exactly matches a `secrets.*` reference in CI.
- Tell the user how to run it. If it's a repeatable setup path, commit it and link it from the README so the next person runs the script instead of asking an AI.

## Dry-run walkthrough

Before relying on this skill, trace it statically against one real jarvis use case (the `proxy-network-restore` VPS dashboard walkthrough, or Supabase/VoyageAI credential setup) confirming the generated script's stage plan is sound — without actually running it end-to-end, since that opens browsers and blocks on human input.
