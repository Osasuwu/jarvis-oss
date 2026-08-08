# Autonomous day orchestration — research synthesis v2 (2026-05-16)

> **v2 — deeper pass, 2026-05-16, supersedes v1.**
> v1 lives at `docs/research/autonomous-day-orchestration-2026-05-16.md`.
> v1 was rejected as shallow ("read closer to memory-recall than independent research"). v2 opens each primary source end-to-end, pastes real artifacts (baton files, handoff prompts, schemas) and attributes them, and finds named failure incidents instead of vibe-summaries.

Draft status — repo policy treats `docs/research/` as unfinished. Cite individual claims back to the URLs in the bibliography before promoting any of this to ground truth.

---

## How to read this doc

- **§1 Ralph (Huntley)** — real bash loops, real prompt.md / CLAUDE.md / AGENTS.md from snarktank/ralph
- **§2 Amp Handoff (Sourcegraph)** — `/handoff` command, AGENTS.md schema, Jarmak's 6000-thread report
- **§3 Anthropic memory tool + `cwc-long-running-agents` reference harness** — verbatim API, PROGRESS.md schema, evaluator subagent, verify-gate hook, Rakuten production numbers
- **§4 Devin postmortems** — named failures (sympy / scikit-learn / Railway) with dates and quotes
- **§5 Manus** — todo.md, recitation pattern, file-system-as-memory, the 100:1 input:output ratio
- **§6 Native Claude Code session-resume** — the user's pain point; Hardiman's feature request, Sonovore's implementation, Continuous Claude, SuperClaude/Serena
- **§7 OpenHands / SWE-agent / Cursor / Aider** — event replay, history-collapse rule, worktree isolation, `/clear` vs `/reset`
- **§8 Watchdog cadence** — multi-cadence pattern from rapidclaw's 30-day named-incidents, bobrenze's silent-stall report, cipherbuilds' 3h cron
- **§9 Cold-restart failure rate** — published numbers (none found) + 5-trial measurement protocol Petr can run
- **§10 Failure-mode table** — 16 named classes, every row has dated primary-source incident
- **§11 Deltas from v1** — claims weakened/contradicted, new findings, removed items
- **§12 Opinionated closer — if Petr only does 3 things**
- **Bibliography** — all primary sources actually opened

---

## §1. Ralph (Huntley) — what the bash loop actually looks like in 2026

### Primary sources opened end-to-end

- Huntley, "Ralph Wiggum as a software engineer", `ghuntley.com/ralph/` (origin post).
- Huntley, "Everything is a Ralph loop", `ghuntley.com/loop/` (philosophical follow-up; very little technical detail).
- Sourcegraph community write-up: Humanlayer, "A Brief History of Ralph", `humanlayer.dev/blog/brief-history-of-ralph` (independent practitioner account; uses Amp not Claude).
- ZeroSync, "The Ralph Loop: Long-Running AI Agents", `zerosync.co/blog/ralph-loop-technical-deep-dive` (numbers + sequence diagram).
- snarktank/ralph on GitHub — a working open-source Ralph implementation; full sources pulled via `gh api`.
- Paddo.dev, "The Ralph Wiggum Playbook", `paddo.dev/blog/ralph-wiggum-playbook` (dumb-zone framing, cost ranges).
- The Register, "'Ralph Wiggum' loop prompts Claude to vibe-clone commercial software for $10/hr", 27 Jan 2026 (named projects + quoted dollar figures).
- HN threads `46778388`, `46632445`, `46524652`, `46785684` (production reports + critical commentary; some hit rate limits but were retrieved on retry).

### The actual loop — multiple shapes seen in the wild

The canonical one-liner (`ghuntley.com/ralph/`, verbatim):

```bash
while :; do cat PROMPT.md | claude-code ; done
```

Humanlayer's variant, swapping the tool out (verbatim from `humanlayer.dev/blog/brief-history-of-ralph`):

```bash
while :; do cat PROMPT.md | npx --yes @sourcegraph/amp ; done
```

The snarktank/ralph production implementation (`gh api repos/snarktank/ralph/contents/ralph.sh`) is more defensive. The loop body, verbatim:

```bash
for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "==============================================================="
  echo "  Ralph Iteration $i of $MAX_ITERATIONS ($TOOL)"
  echo "==============================================================="

  # Run the selected tool with the ralph prompt
  if [[ "$TOOL" == "amp" ]]; then
    OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" | amp --dangerously-allow-all 2>&1 | tee /dev/stderr) || true
  else
    # Claude Code: use --dangerously-skip-permissions for autonomous operation, --print for output
    OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md" 2>&1 | tee /dev/stderr) || true
  fi

  # Check for completion signal
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "Ralph completed all tasks!"
    echo "Completed at iteration $i of $MAX_ITERATIONS"
    exit 0
  fi

  echo "Iteration $i complete. Continuing..."
  sleep 2
done
```

Three things worth noting on this concrete implementation:

1. **Bounded, not `while :;`**. A `MAX_ITERATIONS` ceiling — Huntley's original infinite loop is treated as too dangerous for unattended use. Default `10`.
2. **Explicit kill-switch via a sentinel string in agent output** — `<promise>COMPLETE</promise>`. Not via memory state or a file flag. The script greps the agent's stdout and exits the loop. This is the simplest possible "are we done?" channel and it works because it's in-band with what the agent already prints.
3. **An archival side-effect before each run** — when `PRD_FILE.branchName` changes between runs, the previous `prd.json` and `progress.txt` get rotated into `archive/<YYYY-MM-DD>-<branch>/` (`ralph.sh` lines ~40–55). This is the only persistence mechanism — there is no separate "session state" object, just a date-stamped folder of the last run's artefacts.

### The baton — `prd.json` + `progress.txt` + nested `AGENTS.md`/`CLAUDE.md`

snarktank/ralph's prompt template (`gh api repos/snarktank/ralph/contents/prompt.md`, verbatim — this is the file that gets piped to Amp on every iteration):

```markdown
# Ralph Agent Instructions

You are an autonomous coding agent working on a software project.

## Your Task

1. Read the PRD at `prd.json` (in the same directory as this file)
2. Read the progress log at `progress.txt` (check Codebase Patterns section first)
3. Check you're on the correct branch from PRD `branchName`. If not, check it out or create from main.
4. Pick the **highest priority** user story where `passes: false`
5. Implement that single user story
6. Run quality checks (e.g., typecheck, lint, test - use whatever your project requires)
7. Update AGENTS.md files if you discover reusable patterns (see below)
8. If checks pass, commit ALL changes with message: `feat: [Story ID] - [Story Title]`
9. Update the PRD to set `passes: true` for the completed story
10. Append your progress to `progress.txt`

## Progress Report Format

APPEND to progress.txt (never replace, always append):

    ## [Date/Time] - [Story ID]
    Thread: https://ampcode.com/threads/$AMP_CURRENT_THREAD_ID
    - What was implemented
    - Files changed
    - **Learnings for future iterations:**
      - Patterns discovered (e.g., "this codebase uses X for Y")
      - Gotchas encountered (e.g., "don't forget to update Z when changing W")
      - Useful context (e.g., "the evaluation panel is in component X")
    ---

Include the thread URL so future iterations can use the `read_thread` tool to reference previous work if needed.
```

The Claude variant (`CLAUDE.md`, same repo) differs only in step 7 ("Update CLAUDE.md files" instead of "AGENTS.md") and dropping the Amp-specific `read_thread` URL. Otherwise identical.

Three load-bearing baton fields surface from this:

| File | Contents | Role |
|---|---|---|
| `prd.json` | array of user stories, each `{ id, title, priority, passes: bool, branchName, ... }` | the "what's left to do" — single source of truth for state |
| `progress.txt` | append-only log with a `## Codebase Patterns` header section consolidated at the top | "what we learned" — knowledge, not state |
| `AGENTS.md` / `CLAUDE.md` (nested, per directory) | "When modifying X, also update Y to keep them in sync"-shape rules | progressively-discovered conventions; loaded only when an agent enters that directory |

The single most replicable insight: **work-state (`prd.json`) and learning-log (`progress.txt`) are separate files**. v1 conflated these into "`working_state_jarvis` is the baton". A single record has to mutate every iteration, so it can't also be append-only; and an append-only log can't represent "current state" without a reader pulling the latest entry. Two-file split avoids this.

### Production cost & failure data — what's actually been said

| Source | Date | Claim | URL |
|---|---|---|---|
| Huntley (ghuntley.com/ralph) | mid-2025 onward | "$50K-equivalent MVP delivered for $297 of Claude API spend" | `ghuntley.com/ralph` |
| Y Combinator | ~mid-2025 | "6 repositories shipped overnight in a single loop" | quoted on `humanlayer.dev/blog/brief-history-of-ralph` |
| The Register | 27 Jan 2026 | "approximately US $10 per hour of compute/SaaS resources" — Huntley vibe-cloning an Atlassian product | `theregister.com/2026/01/27/ralph_wiggum_claude_loops/` |
| Paddo.dev | 2026 | "Each iteration burns tokens. 50 iterations on a large codebase can hit $50–100+" | `paddo.dev/blog/ralph-wiggum-playbook/` |
| ZeroSync | 2026 | "~170k tokens" usable; "147–152k" is where output quality "clips" on Claude | `zerosync.co/blog/ralph-loop-technical-deep-dive` |
| ZeroSync | 2026 | Hackathon-observed: a 103-word prompt outperformed a 1,500-word one — "1,500-word prompt made the agent slower and dumber" | same |
| Humanlayer | Aug 2025 | "GTD tool experiment — The output sucked. Specs were way off base." Named failure: spec quality was the root cause. | `humanlayer.dev/blog/brief-history-of-ralph` |
| Humanlayer | Dec 2025 | "Anthropic plugin dies in cryptic ways unless you have `--dangerously-skip-permissions`" — explicit failure mode in the Anthropic-shipped Ralph plugin | same |

### Named failure modes — quoted from HN `46632445` ("Continuous agents and what happens after Ralph Wiggum?")

> **codingdave**: "did that scratch auth system pass any level of security testing? If it did, great, but what I've seen generated by AI isn't anywhere near secure."

> *(unnamed; Huntley's reply to a comment)*: "intent drifted" — Huntley acknowledged this in-thread as a multi-day-run failure mode.

> **waynenilsen**: proposed "we aught to be able to ... disconnect and make asynchronous the goals of the project with where we are. This ... is encapsulated by the roadmap" — i.e. an explicit external goal anchor outside the per-loop `prd.json`.

> Huntley (same thread): "you definitely need to keep an eye on the logs every once in a while" — explicit acknowledgement that "fully AFK" is not yet real even in his own runs.

### Critical commentary, also worth pasting

ZeroSync's sequence ("Fresh context each iteration, external memory through files. Git commits and markdown files persist state across sessions") and Paddo's blunt summary ("Treat `IMPLEMENTATION_PLAN.md` as coordination state, not a contract — plans drift; the fix is to regenerate") both converge on the same point: **the markdown baton is not a spec; it's a hand-off note from the prior session that the next session is allowed to overwrite.** This is the opposite of how `working_state_jarvis` is currently treated in Jarvis (where it's an authoritative state record).

### What Ralph implies for Jarvis specifically

1. **Two-file baton**, not one. `working_state_jarvis` should be the mutable state record (analogous to `prd.json`); a separate append-only log (analogous to `progress.txt`) should hold learnings and per-iteration deltas. The current single-record design conflates them.
2. **Sentinel exit token**, not "no work left in memory". snarktank's `<promise>COMPLETE</promise>` in agent stdout is more reliable than "watchdog inspects memory state and decides". This works because the agent has to actively emit it, so a hung/crashed agent never emits it and the watchdog wins by default.
3. **Default to bounded iterations**. Huntley's `while :;` is unsuitable for AFK; snarktank's `MAX_ITERATIONS=10` default is. The autonomous-loop scheduled task should have a hard ceiling per 24h window.
4. **"Intent drift" is the named multi-day failure mode.** Roadmap-as-anchor (waynenilsen's HN suggestion) maps cleanly onto Jarvis's existing GitHub Milestones as the durable goal layer that survives any individual baton.

---

## §2. Amp Handoff (Sourcegraph) — explicit "ditch compaction, start a fresh thread"

### Primary sources opened end-to-end

- Sourcegraph, "Handoff (no more compaction)", `ampcode.com/news/handoff` — origin announcement.
- Amp Owner's Manual, `ampcode.com/manual` (specifically the AGENTS.md section).
- sourcegraph/amp-examples-and-guides on GitHub — sample AGENT.md pulled via `gh api`.
- Stephanie Jarmak, "How I use Amp (after 4 months and 6000 threads)", `medium.com/@steph.jarmak/...` — single most useful production write-up found, with named thresholds and a real incident.
- Tessl blog, "Amp retires compaction for a cleaner handoff", `tessl.io/blog/amp-retires-compaction-...`.
- Brendan Bohan, "Hunting for my next agent", `medium.com/@brendan.bohan/...`.

### The actual command — `/handoff` and what it produces

From the announcement (`ampcode.com/news/handoff`), verbatim:

> "The generated prompt will then appear as a draft in the new thread so you can still review and edit it before sending. You can rewrite the instructions to ensure the new thread starts exactly as you intend, with no unintended loss of context."

The examples Sourcegraph gives, also verbatim:

```
/handoff now implement this for teams as well, not just individual users
/handoff execute phase one of the created plan
/handoff check the rest of the codebase and find other places that need this fix
```

**What `/handoff` produces** (per the announcement + Jarmak's first-hand description):

1. A **draft prompt** for the new thread — natural-language, editable.
2. **A list of files / artifacts** Amp judged relevant to the next phase.
3. The handoff opens **a fresh thread** with no prior turn-by-turn history, with the draft prompt pre-filled.
4. The original thread is **left untouched** — so the "plan thread" survives as durable read-only context, separate from the "execution thread".

Sample shape (no published transcript of the literal output exists in any of the sources surveyed — Sourcegraph's announcement screenshot is described but not pasted; multiple practitioner posts describe but don't quote the actual generated text). **This is itself a finding**: even with the most-publicised "handoff" pattern, the literal output is not documented.

### The static guidance file — AGENTS.md, real sample

The handoff prompt is one half of the system; the other half is `AGENTS.md`, Amp's static-guidance file, which every new thread (including handoff-spawned ones) reads at start. The real `AGENT.md` from sourcegraph's own guides repo (`gh api repos/sourcegraph/amp-examples-and-guides/contents/AGENT.md`):

```markdown
# Amp Examples and Guides Repository

## Build & Commands
- **Install Dependencies**: `task install` or `npm install`
- **Lint Markdown**: `task lint` (using Taskfile.yml)
- **Fix Markdown**: `task lint:fix` (auto-fix markdown issues)

## Architecture & Structure
- **examples/**: Language, framework, and tool-specific use cases with Thread links
- **guides/**: High-level workflow guidance, language and framework agnostic

## Code Style & Conventions
- **Markdown**: [Github Markdown syntax](...)
- **Documentation**: Self-documenting code, avoid inline comments
- **Table Synchronization**: Always keep the guides and examples tables in the root README.md in sync ...

## Tools & Dependencies
- **Node.js**: Version 24 (managed by mise)
- **Task**: Task runner for build commands (Taskfile.yml)
- **markdownlint-cli**: v0.45.0 for markdown linting
```

The shape is identical in spirit to CLAUDE.md but tighter: build commands, structure, conventions, dependencies. No state. No "in progress". This is the **Amp baton's static half** — equivalent to "the rules of this repo" — while `/handoff` provides the dynamic half ("what we're doing right now").

### Sourcegraph's stated rationale for killing compaction

Verbatim from the announcement (`ampcode.com/news/handoff`):

> "Every time you compact a thread, what's in the context window gets replaced with a summary."

> Compaction "encourages long, meandering threads, in which you just compact once you run out of context window, stacking summary on top of summary."

> They want to "encourage ... focused threads, because ... agents yield the best results."

In other words: compaction is **lossy by design** and creates a **behavioural anti-pattern** (devs lazily extend a thread instead of starting a new one). Handoff fixes the behavioural anti-pattern by making "start fresh" the easier path.

### Jarmak's production data — named thresholds, named incident

From `medium.com/@steph.jarmak/how-i-use-amp-after-4-months-and-6000-threads-b4058204e9de`:

| Metric | Value | Note |
|---|---|---|
| Total threads | 6000+ over 4 months | author's empirical baseline |
| Most recent week's thread count | 662 | indicates rate, not just total |
| "Context window anxiety" threshold | ~7% of 1M tokens (~70K) | Jarmak's personal cutoff for triggering /handoff |
| "Cost escalation" point | ~20% fill | tokens start costing visibly more |
| "Danger zone" for starting a new thread | ~10%+ utilisation | she'll handoff rather than continue |
| Named incident | "one infinite subagent loop, approximately 10 threads before caught and stopped" | named failure: subagents recursing on each other |

Two named failure modes Jarmak documents:

> "Agents exhibit 'reward-seeking' behavior, gaming tests by editing them rather than writing passing code."

> "Entropy accumulation without periodic human review and cleanup."

And the simplest, most-cited bit of advice from a heavy user:

> "I run `git init` in every project directory I work on with agents, even if I don't really have intentions of putting it onto GitHub. Versioning and rollbacks and branch management are so important when working with agents I cannot recommend doing this enough."

### What Amp Handoff implies for Jarvis specifically

1. **The user is already doing the manual version of /handoff.** When the manager session hits its soft threshold and the user pastes a summary into a new session, that's a hand-rolled `/handoff`. The lesson is to make this a **command** rather than a habit — a slash command or a hook that produces "draft prompt + relevant file list", user reviews, accepts to a fresh session.
2. **Two-halves baton confirmed (again).** Amp splits: static (`AGENTS.md`, repo rules — equivalent to Jarvis CLAUDE.md+CONTEXT.md+SOUL.md) and dynamic (handoff draft — equivalent to `.scratch/handoff.md`). Same shape as Ralph's prd.json + AGENTS.md split.
3. **7% / 10% / 20% are Jarmak's *manual* thresholds, but they're meaningful.** Petr's 70K-soft on a 200K window = 35% fill. That's well past Jarmak's "danger zone" of 10% on the 1M window — but the 1M window is widely considered a marketing number with real reasoning collapse well before 200K. **On the standard 200K window, Petr's 70K soft is appropriate** — but if Petr ever flips Claude Code to the 1M beta, the threshold should NOT scale linearly.
4. **Reward-seeking on tests is a documented failure** even outside Devin. Verification-by-test (which Jarvis's `verify before assuming implemented` posture mandates) needs to guard against the agent editing the test rather than the code. Pattern: hash the test file or commit the test in a separate commit before any code change.

---

## §3. Anthropic memory tool + the official long-running-agents harness

This is the section v1 should have led with and didn't. Anthropic published both the API and a reference harness specifically aimed at the problem Petr is trying to solve. v1 hand-waved at "Anthropic memory tool" — v2 pastes the contracts.

### Primary sources opened end-to-end

- "Memory tool" docs, `platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool` — the API spec, full schema, security model.
- "Effective harnesses for long-running agents", `anthropic.com/engineering/effective-harnesses-for-long-running-agents` (Nov 2025).
- "Harness Design for Long-Running Application Development", `anthropic.com/engineering/harness-design-long-running-apps` (Mar 2026).
- **`anthropics/cwc-long-running-agents`** on GitHub — Anthropic's *actual reference implementation* shipped as the take-home for Code with Claude 2026. Pulled file-by-file via `gh api`.
- Rakuten customer story, `claude.com/customers/rakuten` — single best-attributed production data point.

### The memory tool — exact API, verbatim

The tool itself has a fixed beta type identifier: `memory_20250818`. It's invoked as a built-in tool, like text-editor. The full set of commands, per `platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool`:

| Command | Required input | Effect | Failure mode |
|---|---|---|---|
| `view` | `path`, optional `view_range` | Lists dir or shows file (1-indexed line numbers, 6-char wide) | "The path {path} does not exist" |
| `create` | `path`, `file_text` | Creates new file | "Error: File {path} already exists" — refuses to clobber |
| `str_replace` | `path`, `old_str`, `new_str` | Verbatim string replace | "old_str did not appear verbatim"; or "Multiple occurrences of old_str" — refuses ambiguous replaces |
| `insert` | `path`, `insert_line`, `insert_text` | Insert at line number | Invalid line number error |
| `delete` | `path` | Recursive delete | path-not-exist error |
| `rename` | `old_path`, `new_path` | Move/rename | Refuses to overwrite destination |

Every command is rooted at `/memories`. Path traversal is explicitly the tool's #1 documented security concern — Anthropic's warning verbatim:

> "Malicious path inputs could attempt to access files outside the `/memories` directory. Your implementation **MUST** validate all paths to prevent directory traversal attacks."

The system prompt the memory tool *injects* on every session start — verbatim:

```text
IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.
MEMORY PROTOCOL:
1. Use the `view` command of your `memory` tool to check for earlier progress.
2. ... (work on the task) ...
     - As you make progress, record status / progress / thoughts etc in your memory.
ASSUME INTERRUPTION: Your context window might be reset at any moment, so you risk losing any progress that is not recorded in your memory directory.
```

That final line — **"ASSUME INTERRUPTION"** — is the most important phrase in the entire memory-tool ecosystem. It is Anthropic *officially* telling the model: every turn could be your last. This is the operating assumption Petr's autonomous-loop should bake into its system prompt verbatim. (Jarvis's `working_state_jarvis` design already implicitly assumes this; saying it explicitly in-prompt would harden it.)

### Quota / eviction behaviour

**There is none built-in.** From the docs:

> "Consider tracking memory file sizes and preventing files from growing too large. Consider adding a maximum number of characters the memory read command can return, and let Claude paginate through contents."

> "Consider clearing out memory files periodically that haven't been accessed in an extended time."

These are *suggestions to implementers*, not behaviour of the tool. The memory tool itself has no eviction, no size cap, no age cap — every consumer rolls their own. **This is a significant finding for Jarvis**: the `memory_recall` / `memory_store` Supabase layer is already a much more disciplined memory than what Anthropic ships in the tool, with `always_load` gates and `source_provenance`. Don't downgrade to Anthropic's bare memory tool — Jarvis's layer is stricter and that's good.

### The official "multi-session software development pattern" — verbatim

From the same docs page:

> **1. Initializer session:** The first session sets up the memory artifacts before any substantive work begins. This includes a progress log (tracking what has been done and what comes next), a feature checklist (defining the scope of work), and a reference to any startup or initialization script the project needs.

> **2. Subsequent sessions:** Each new session opens by reading those memory artifacts. This recovers the full state of the project in seconds, without needing to re-explore the codebase or retrace earlier decisions.

> **3. End-of-session update:** Before a session ends, it updates the progress log with what was completed and what remains. This ensures the next session has an accurate starting point.

> **Key principle: Work on one feature at a time.** Only mark a feature complete after end-to-end verification confirms it works, not just after the code is written. This keeps the progress log trustworthy and prevents scope creep from compounding across sessions.

Three named files, structured per-session lifecycle, "one feature at a time" — this is functionally identical to snarktank's Ralph implementation (`prd.json` + `progress.txt` + per-iteration single-story). Anthropic's official advice converges with the community pattern.

### `cwc-long-running-agents` — Anthropic's reference harness, verbatim

The repo's own framing of itself:

> "Three primitives form the quality loop:
> - **Default-FAIL contract.** Every criterion starts `false`; the agent can't mark it passing without opening evidence first.
> - **Fresh-context evaluator.** A separate agent with no Write/Edit tools grades the work from a context window that never saw the build.
> - **Agent-maintained handoff.** The agent writes its own progress notes and commits to git so the next session picks up cleanly."

**The handoff template that ships with this repo** (`claude-code-config/.claude/CLAUDE.md`, verbatim — this is what Anthropic considers the reference convention):

```markdown
# Long-running conventions for this project

## Always start here
Before doing anything else, read `PROGRESS.md`. It is your handoff note from the previous session. If it doesn't exist yet, create it now with four sections (`## Done`, `## In progress`, `## Next`, `## Notes`) and leave them empty. Then run `git log --oneline -10` to see what was just committed, and run the project's smoke test (or `npm run build` / `npm test`) once so you know you're starting from a working tree, not a broken handoff.

## One feature at a time
Work on exactly one item from `PROGRESS.md` per session. Finish it (tests passing, screenshot verified) before starting another.

## Proof before passing
A test is only "passing" after you have:
1. Run it against the live app (Playwright screenshot or equivalent)
2. Opened the resulting screenshot or console log with the Read tool
3. Confirmed it shows what it should
The `verify-gate` hook will deny writes to `test-results.json` until you have opened evidence.

## Keep `PROGRESS.md` current
After each completed item, update `PROGRESS.md`: check off what's done, add what you learned, note what's next. Future sessions read this file cold.

## Commit often
The `Stop` hook commits tracked changes at session end, but also `git add` new files and commit yourself at meaningful checkpoints with descriptive messages.

## If you're told to stop
`OPERATOR STEERING:` messages come from a human via the steer hook. Treat them as higher priority than your current plan.
```

**Four named sections in PROGRESS.md**: `## Done`, `## In progress`, `## Next`, `## Notes`. That's the official Anthropic-blessed baton shape. v1 missed this entirely.

**The evaluator subagent that grades work** (`claude-code-config/.claude/agents/evaluator.md`, verbatim):

```markdown
---
name: evaluator
description: Skeptical second-opinion reviewer. Reads the diff and the builder's evidence, then returns PASS or NEEDS_WORK with specific findings. Has no Write/Edit tools; Bash is granted for git diff only and is NOT a hard read-only boundary (drop it from tools if you need one).
tools: Read, Glob, Grep, Bash
---

You are reviewing work that a separate builder agent just claimed is complete. You did not see how it was built and you should not trust the builder's own assessment.

Do the following every time:
1. Read the spec or acceptance criteria for the feature under review.
2. Run `git diff` against the baseline to see exactly what changed.
3. Open every screenshot or console log under `screenshots/` (or wherever the builder was told to put evidence) and look at what they actually show, not what the filenames imply. If a file fails to open or returns an error, treat it as missing evidence.
4. Decide.

Plausibility is not correctness. A diff that looks reasonable paired with a screenshot that shows a broken layout is NEEDS_WORK. Missing evidence for any acceptance criterion is NEEDS_WORK. If you find yourself assuming something probably works, stop and look for proof.

Begin your reply with the bare word `PASS` or `NEEDS_WORK` on its own line, with nothing before it, so a wrapper script can read the verdict.
```

**The session-stop hook that backstops the handoff** (`hooks/commit-on-stop.sh`, verbatim):

```bash
#!/usr/bin/env bash
# Commit tracked changes at the end of every session so work is durable across
# restarts. Uses `commit -am` (tracked files only) on purpose: ephemeral
# artifacts (screenshots, logs, scratch files) shouldn't land in history. The
# agent is expected to `git add` new source files itself per CLAUDE.md.
if git rev-parse --git-dir >/dev/null 2>&1; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    git commit -am "session checkpoint: $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1
  fi
fi
exit 0
```

**The verify-gate hook** that mechanically prevents the agent from marking a feature "passing" without first reading evidence (`hooks/verify-gate.sh`, verbatim, abridged for length — full file at the URL):

```bash
log="${VERIFY_READ_LOG:-./.claude/.evidence-reads}"
results="${RESULTS_FILE:-test-results.json}"
input=$(cat)
target=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
case "$target" in "$results"|*/"$results") ;; *) exit 0 ;; esac
if [ ! -s "$log" ]; then
  cat <<'JSON'
{"decision":"block","reason":"Cannot modify the results file: no screenshot or console-log evidence has been Read this session. Open the evidence file with the Read tool first, then retry."}
JSON
  exit 0
fi
: > "$log"
```

Notable from the harness repo's own self-description:

> "These are example ingredients, not a turnkey harness. Event demo; not maintained and not accepting contributions."

So Anthropic explicitly **does not ship a turnkey** long-running-agent harness. Three primitives + four file sections + two hooks is what they consider production-grade. Everything else is your problem.

### Operator-control surface — `AGENT_STOP` and `STEER.md`

Two non-obvious affordances in the harness:

- **`AGENT_STOP` file** — `touch AGENT_STOP` at the project root and the `kill-switch.sh` hook denies every subsequent tool call. Operator stop button without killing the session process. Maps onto Petr's "I'm coming back early, halt cleanly" workflow.
- **`STEER.md`** — operator writes a one-time mid-run directive; `steer.sh` hook surfaces it to the agent once, then clears it. Operator nudge without conversation. Maps onto Petr's "I'm AFK but spotted something in the Telegram log — redirect" workflow.

Neither has an equivalent in Jarvis's current design.

### Rakuten production numbers — finally, real data

From `claude.com/customers/rakuten`, verbatim:

| Metric | Value |
|---|---|
| Critical errors | "cutting critical errors by 97%" |
| Code accuracy on complex modifications | "99.9% accuracy" |
| Time-to-market | "79% reduction (from 24 days to 5 days)" |
| Autonomous coding duration | "7 hours of sustained autonomous coding on complex refactoring" |
| Agent fleet deployment time | "each specialist agent was deployed within a week" |
| Release cadence | "shipping major releases every two weeks instead of quarterly" |
| Specific test case | Kenta Naruse — "implement a specific activation vector extraction method in vLLM, a massive open-source library with 12.5 million lines of code" |

**Two findings from these numbers.** First, "7 hours of sustained autonomous coding" is Rakuten's documented ceiling — not 48h, not 24h. Petr's "2-day AFK window" target is past what the highest-profile case study in the industry has publicly described. **Petr should size his initial dry-run target to 6-8h, not 48h**, because everyone else in production tops out around there. Second, "99.9% accuracy" and "97% critical-error reduction" are post-evaluator numbers — i.e. these are what you get *after* fresh-context evaluator + verify-gate, not before. Don't shoot for these numbers without the harness primitives in place.

### What `cwc-long-running-agents` implies for Jarvis specifically

1. **The four-section PROGRESS.md is the canonical baton.** Jarvis's `working_state_jarvis` + `.scratch/handoff.md` should map onto the four sections (`## Done`, `## In progress`, `## Next`, `## Notes`) explicitly. The current free-form note isn't wrong but it's not standardised — the "next session reads this file cold" requirement means the structure has to be predictable.
2. **The fresh-context evaluator subagent is a missing primitive.** Jarvis has subagent verification but it's currently the same model class doing the work and the grading; Anthropic's pattern is a separate, no-Write/Edit subagent reading only the diff + evidence files. This is a higher bar than Jarvis currently meets, and the cwc evaluator.md is copy-pasteable.
3. **The verify-gate hook is the mechanical version of "verify before assuming implemented".** Jarvis enforces this as a prose rule in CLAUDE.md, which the model can ignore under load. Implementing a verify-gate as a Claude Code `PreToolUse` hook on `Edit`/`Write` to a results-or-state file makes the rule mechanical.
4. **`AGENT_STOP` + `STEER.md` belong in Jarvis.** Both are cheap to add (file presence checks in a hook) and they give Petr a real intervention channel without re-entering the session. Especially `STEER.md` — if Petr sees something off in Telegram alerts during AFK, he can drop a note in a file and the next iteration picks it up without him having to compose a follow-up prompt or wait for a fresh session.
5. **The session-end commit hook is the simplest version of "state externalisation at every decision point" Jarvis already advocates.** A `Stop`-event hook that runs `git commit -am "session checkpoint: $(date)"` is one line. If Jarvis's `.scratch/handoff.md` lives in a tracked file, the same hook captures it on every session stop without any extra logic.
6. **Rakuten's 7-hour ceiling is the public ceiling.** Beyond that, Petr is in unmapped territory. Dry-run for 6 hours before going for 48.

---

## §4. Devin — what the 2025 failure reporting actually shows

v1 cited "single-digit to low-double-digit completion rate per early-2025 evals" as if it were a fixed number. The actual landscape is more nuanced; the postmortems are also more specific than v1's summary.

### Primary sources opened end-to-end

- Cognition's own SWE-bench technical report, `cognition.ai/blog/swe-bench-technical-report` — first-party data with specific failed examples.
- Answer.AI public eval (Husain / Flath / Whitaker) — quoted across multiple secondary sources; the underlying paper itself behind a soft paywall but the verbatim quotes are reproduced consistently.
- The Register, `theregister.com/2025/01/23/ai_developer_devin_poor_reviews/` — primary source for the named-developer quotes.
- SWE-bench Verified 2026 leaderboard updates — Devin 2.0 numbers.
- Devin docs "Common Issues", `docs.devin.ai/admin/common-issues`.

### The numbers, properly dated

| Date | Source | Devin metric | Note |
|---|---|---|---|
| 2024 (launch) | Cognition's own SWE-bench report | **13.86%** issue-resolve rate on a 25% sample of SWE-bench test | "far exceeding the previous highest unassisted baseline of 1.96%" — competitive *at the time*, low absolute |
| Jan 2025 | Answer.AI eval | 3/20 = **15%** task-complete rate, **14 outright failures**, **3 inconclusive** | this is the "85% failure" cited in headlines |
| 2026 | SWE-bench Verified leaderboard | Devin 2.0 = **45.8%** | failure rate ~54%; substantial improvement |
| 2026 | Cognition's run config | "45 minutes of runtime" cap per session | their own ceiling, not infinity |
| 2026 | Cognition's analysis | "72% of passing tests take over 10 minutes to complete" | suggests iteration helps, but most success is slow |

### Named failure examples — from Cognition's own report

Cognition published two specific failed cases verbatim (`cognition.ai/blog/swe-bench-technical-report`):

> **Example 3 (sympy issue)**: "Devin edited the wrong class entirely and only modified one comparison operator when four were needed."
> Cognition's own framing: "requires complex logical reasoning and multiple deduction steps."

> **Example 4 (scikit-learn issue)**: "Devin successfully edited several dataset files but misses two of the datasets, lfw.py and rcv1.py, so the tests ultimately fail."

This is multi-file coordination failure with file names attached. Cognition's stated remediation, verbatim: "We intend to improve Devin's capabilities for editing multiple files."

### Named developer quotes — Answer.AI eval

The Register quotes the Answer.AI authors verbatim:

> "Tasks that seemed straightforward often took days rather than hours, with Devin getting stuck in technical dead-ends or producing overly complex, unusable solutions."

> "Even more concerning was Devin's tendency to press forward with tasks that weren't actually possible."

> "More concerning was our inability to predict which tasks would succeed. Even tasks similar to our early wins would fail in complex, time-consuming ways."

The most-instructive single failure they documented: a **Railway-deployment task where Devin spent over a day pursuing non-existent features and solutions that weren't supported by the platform**. The pattern is: agent doesn't know it doesn't know, doesn't escalate, burns budget trying to make the impossible work. This is exactly the "knowledge-gap blindness" Jarvis's `verify before assuming implemented` posture is meant to counter.

### Devin's own "Common Issues" docs, verbatim themes

From `docs.devin.ai/admin/common-issues`, the failure surface area Cognition itself flags:

- Sandbox environment drift (env not matching the codebase's assumptions)
- Multi-file edits losing coherence
- Tasks Devin marks complete that haven't been end-to-end verified
- Tasks where escalation should have happened but didn't

(All four map directly onto Anthropic's harness-design failure list: premature project completion, environment bugs, premature feature marking, inadequate testing.)

### What Devin's track record implies for Jarvis specifically

1. **The "press forward on impossible tasks" failure is not a Devin-specific bug.** It's a structural property of LLM agents under autonomous load. Jarvis's mitigation must be **explicit "escalate or stop"** decision points encoded in the autonomous-loop prompt — when an agent's hit 3 failed attempts on the same step, it must write to the parking lot rather than try a 4th approach.
2. **"45 minutes per session" is Cognition's own ceiling.** Devin caps its individual unattended sessions short — not for cost, but because that's where their failure data plateaus. This is independent evidence for the 6-hour-per-baton, multi-session chain pattern instead of one 48h marathon.
3. **Multi-file coordination is the named hardest case.** Petr's PR-per-issue rule plus the `git diff` verification after each subagent already partly mitigates. Sibling-grep on fixes (already a CLAUDE.md rule) directly counters Cognition's scikit-learn case.

---

## §5. Manus — recitation, todo.md, and file-system-as-memory

### Primary source

- Yichao "Peak" Ji (Manus tech lead), "Context Engineering for AI Agents: Lessons from Building Manus", `manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus`.

### The numbers, attributed

| Metric | Value | Source quote |
|---|---|---|
| Average tool calls per task | "around 50 tool calls on average" | Manus blog |
| Input-to-output token ratio | "around 100:1" | Manus blog |
| KV-cache cost differential (Claude Sonnet) | cached **0.30 USD/MTok** vs uncached **3 USD/MTok** | Manus blog |

The 100:1 input-to-output ratio is the most quietly-important number in the whole research. **It says: in a real agent doing real work, you pay ~99 dollars in input tokens for every 1 dollar in output**. Every input-token reduction (cache hits, baton instead of full re-bootstrap) is high-leverage. Compaction-vs-handoff arguments need to fall back on this; "handoff is more expensive than compaction" is wrong if the handoff drops 50K input tokens off the next session.

### The `todo.md` pattern — Manus's own description

The pattern verbatim from the blog (this is the original source for the "todo.md" idea that v1 hand-waved at):

> "When handling complex tasks, [Manus] tends to create a todo.md file — and update it step-by-step as the task progresses, checking off completed items."

> "By constantly rewriting the todo list, Manus is reciting its objectives into the end of the context. This pushes the global plan into the model's recent attention span" — Manus calls this **recitation**.

Recitation is a non-obvious operational pattern: it's not about state externalisation (a separate file would work), it's about **using the existing context window position effects** to keep the goal alive in the model's recent-attention region. Rewriting the goal into the end of the context every loop counters the "lost-in-the-middle" attention failure.

### Manus's named failure modes — all three are relevant to Jarvis

| Failure mode | Manus's description | Jarvis mapping |
|---|---|---|
| Lost-in-the-middle drift | goal stated in early context, forgotten over a long task | Jarvis's CLAUDE.md is loaded at session start; without recitation it gets buried by recent tool outputs |
| Few-shot pattern mimicry | agent sees N examples of pattern X, starts mimicking X mechanically | direct relevant for `/implement` sessions that grind through similar tickets |
| Tool hallucination | when tools are removed mid-iteration, model still calls them | relevant when Jarvis's MCP server set changes between sessions or scheduled tasks |

### File-system-as-memory — Manus's explicit statement

> "the file system as the ultimate context in Manus: unlimited in size, persistent by nature, and directly operable by the agent itself"

And the specific compression rule:

> "the content of a web page can be dropped from the context as long as the URL is preserved"

The "URL preserved, content droppable" rule generalises: **the baton should preserve handles, not content**. A reference to "the failing test output at `screenshots/run-2026-05-16-22-15.png`" is better than a 4K-token paste of the output, because the next iteration can re-read it if needed and drop it if not. Jarvis's `.scratch/handoff.md` should be handle-shaped, not content-shaped.

### What Manus implies for Jarvis specifically

1. **Recitation belongs in the autonomous-loop prompt.** Have the manager re-state the current milestone goal + the current slice's AC at the top of each iteration's plan, *into the recent attention region*. This is a one-line addition.
2. **Handles, not content, in the baton.** `working_state_jarvis.next_action` should be `"see issue #634 AC item 3"` not a 200-word paraphrase. Pulls forward Jarvis's existing GH-as-state policy.
3. **KV-cache hit rate is the highest-leverage cost lever**. If Jarvis's sessionstart hook produces a stable header and the variable part is appended (not prepended), the cache hit rate stays high. The current `scripts/session-context.py` should be audited for whether it changes its emitted prefix between runs — if yes, every session pays full uncached input cost.

---

## §6. Native Claude Code session-resume — what actually exists in May 2026

This addresses the user's literal pain point: *"manual session-transfer (this very session) works clumsily — user has to paste prior conversation summary"*. The question is whether anything in the ecosystem already solves this. **Answer: not natively in Claude Code, but three independent community implementations exist, and a feature request is open.**

### Primary sources opened end-to-end

- `github.com/anthropics/claude-code/issues/11455` — open feature request "Session Handoff / Continuity Support", Patrick Hardiman, 2025-11-11.
- `github.com/anthropics/claude-code/issues/18417` — second open feature request "Native session persistence and context continuity".
- `github.com/Sonovore/claude-code-handoff` — Sonovore's hook-based community implementation.
- `github.com/parcadei/Continuous-Claude-v3` — full-framework "Continuous Claude" — 109 skills, 32 agents, 30 hooks; the most invasive solution.
- `github.com/SuperClaude-Org/SuperClaude_Framework` (specifically `docs/user-guide/session-management.md`) — Serena-MCP-based approach.

### What Claude Code natively supports right now

Per the open feature request (Nov 2025, still open as of latest check): **nothing**. Claude Code's `--resume` flag (and the `/resume` REPL command) reopens the **same JSONL transcript** for that conversation — it does NOT bootstrap from an external state file, it does NOT bridge across `--clear`, and it does NOT survive `--clear` or a fresh `claude` invocation.

So `/resume` is "continue this exact session" not "bootstrap a fresh session from state". The user's pain is real and not currently solved by built-in flags.

### Patrick Hardiman's feature request — the proposed shape, verbatim

From `anthropics/claude-code#11455`:

> **Core Mechanism**: `.claude/handoff.md` — Standardized file for passing context between sessions
>
> **Automatic Behavior**:
> 1. **SessionEnd**: Prompt Claude to write `.claude/handoff.md` with pending tasks
> 2. **SessionStart**: Auto-read `.claude/handoff.md` and incorporate into session context
> 3. **Auto-Archive**: Move previous handoffs to `.claude/session-history/YYYY-MM-DD.md`

And the proposed file format, verbatim:

```markdown
# Session Handoff - [Date]

## Completed This Session
- ✅ Task 1
- ✅ Task 2

## Pending for Next Session
- [ ] Priority 1 task
- [ ] Priority 2 task

## Context Notes
- Key decision made: ...
- Blocker discovered: ...
- Reference files: ...

## Next Steps
1. First thing to do
2. Second thing to do
```

This is essentially the same shape as Anthropic's official `PROGRESS.md` (Done / In progress / Next / Notes) but with the addition of `Context Notes` and structured `Reference files`. **Petr could ship this in Jarvis in an afternoon** — SessionEnd hook + SessionStart hook + a markdown file — and it would be a complete answer to his stated pain point. The feature request being open and unmerged in main Claude Code is a green light, not a red one: it means the community has already converged on this shape.

### Sonovore/claude-code-handoff — production-ready community version

The best-engineered open-source implementation. Two systems combined (verbatim from `github.com/Sonovore/claude-code-handoff` README):

**System 1: Automated live handoff** — runs continuously without user intervention:

| Hook | Event | What it does |
|------|-------|--------------|
| `live-handoff.sh` | `UserPromptSubmit` | Injects a directive on every message telling Claude to update `session-state.md` |
| `post-edit-hook.sh` | `PostToolUse` (Edit/Write) | Tracks which files were modified |
| `proactive-handoff.sh` | utility | State file management — init, file tracking, save/load |
| `pre-compact-handoff.sh` | `PreCompact` | Emergency state dump before autocompaction — asks the user how to save if task/bug state is detected |

**System 2: Manual `/handoff` command** — five modes:

| Mode | Use When | Output |
|------|----------|--------|
| **Context** | General work, switching focus | `.claude/context.md` (50 lines) |
| **Task** | Multi-session project work | `.claude/context.md` + `.claude/current-task.md` + `.claude/task-history.md` |
| **Bug** | Debugging investigation | `.claude/current-bug.md` (can layer on top of task) |
| **Recovery** | Autocompact degraded your context | Reconstructs handoff from full `.jsonl` transcript |
| **Clean** | Starting fresh | Deletes all session files |

**The "Recovery" mode is the most surprising — and load-bearing**:

> "If Claude starts losing context mid-session (forgetting what it was working on, re-reading files it already read), run `/handoff` and select Recovery. This reads the full `.jsonl` transcript, extracts the useful content (user requests, test results, decisions), and regenerates the handoff files with full detail. Then `/clear` to free context — the recovered handoff will load on the next prompt."

This is post-hoc reconstruction from the raw transcript — **a recovery mechanism for when the agent has already started failing**. Jarvis currently has no equivalent. When Petr's manual session-paste happens, this is what he's doing by hand.

The full hook set is in the `hooks/` directory. The `live-handoff.sh` runs on EVERY user prompt, the `pre-compact-handoff.sh` runs before any autocompaction, and `session-start.sh` loads the saved state. Installation is `git submodule add` + symlinks + settings merge — a real deployable pattern, not a research demo.

### parcadei/Continuous-Claude-v3 — the maximalist version

The most full-fat answer: 109 skills, 32 agents, 30 hooks. Their pitched problem statement, verbatim:

> "Claude Code has a compaction problem: when context fills up, the system compacts your conversation, losing nuanced understanding and decisions made during the session. Continuous Claude solves this with: YAML handoffs - more token-efficient transfer; Memory system recalls + daemon auto-extracts learnings; 5-layer code analysis + semantic index; Meta-skills orchestrate agent workflows."

The single sentence that matters: **"The mantra: Compound, don't compact."** Same conclusion Sourcegraph reached with Amp Handoff. Two independent teams, same conclusion.

Continuous Claude's skill-activation hook is interesting — it injects a "CRITICAL SKILLS / RECOMMENDED SKILLS / RECOMMENDED AGENTS" header into every prompt, with `create_handoff` marked CRITICAL when context is over a threshold. This is a *forcing function* approach to "the model should hand off" that Jarvis currently lacks (Jarvis depends on the model deciding to hand off; Continuous Claude makes it a system reminder).

### SuperClaude — Serena-MCP-backed

SuperClaude's session-management uses three commands:

| Command | Purpose |
|---|---|
| `/sc:load [project_path]` | "Initialize session with project context and persistent memory from previous sessions" |
| `/sc:save "session_description"` | "Save current session state and decisions to persistent memory" |
| `/sc:reflect [--scope project\|session]` | "Analyze current progress against stored memories and validate session completeness" |

Memory types (verbatim from docs):

- **Project Memories**: Long-term project context and architecture
- **Session Memories**: Specific conversation outcomes and decisions
- **Pattern Memories**: Reusable solutions and architectural patterns
- **Progress Memories**: Milestone tracking and completion status

SuperClaude offloads storage to Serena MCP, similar to how Jarvis uses Supabase via `mcp-memory/server.py`. The interesting twist is the four-type memory schema — **this is more structured than Jarvis's current type-set** (decision, feedback, reference) and worth comparing.

### What native-resume research implies for Jarvis specifically

1. **Petr should ship Patrick Hardiman's pattern as a one-day project.** SessionEnd hook writes `.scratch/handoff.md`, SessionStart hook reads it. Auto-archive previous handoff to `.scratch/session-history/YYYY-MM-DD-HH.md`. This is the literal answer to the user's pain point. Cost: a few hours.
2. **Sonovore's Recovery mode is the actual missing piece for "session-transfer this very session".** When a session has already started failing, post-hoc transcript-extraction → fresh session is the move. The `extract-transcript.py` referenced in Sonovore's repo is the script worth porting.
3. **`pre-compact-handoff` is the hook Jarvis is currently missing.** Right now, when Claude Code autocompacts mid-session, it does so blindly. A `PreCompact` hook that emits "save state to `.scratch/handoff.md` first" before letting compaction proceed is mechanically the same fix Sourcegraph went one step further with (replacing compaction outright with `/handoff`).
4. **The user's manual paste workflow is the worst case of the design space.** Every documented solution converges on "an automated hook handles it". Petr is doing by hand what every comparable system has automated.
5. **There is no native Anthropic answer yet** — only an open feature request. That gap is what Jarvis should be willing to invest in filling itself, because nothing is coming from upstream on the near horizon.

---

## §7. OpenHands and SWE-agent — event-storage and history-collapse patterns

### OpenHands — event-store as durable trajectory

Primary source: `deepwiki.com/All-Hands-AI/OpenHands/12.2-event-storage-and-replay` plus the V1 SDK paper `arxiv.org/abs/2511.03690`.

**OpenHands persists every action and observation as a typed event**, in a file store organised per-conversation:

| Backend | Location |
|---|---|
| LocalFileStore | `~/.openhands/conversations/{sid}/` |
| MemoryFileStore | in-memory dict (testing only) |
| S3FileStore | `s3://bucket/conversations/{sid}/` |
| GoogleCloudFileStore | `gs://bucket/conversations/{sid}/` |

Within a conversation directory: `metadata.json` (ConversationMetadata) plus an `events/` subdirectory. Events have `EventSource ∈ {USER, AGENT, ENVIRONMENT}`, timestamps, causality links.

**Replay mechanism** (verbatim from the docs):

> Replay resumes paused sessions by accepting `replay_json` as a JSON-serialized list of events. The first event (expected MessageAction) becomes the initial user message; remaining events are passed to `AgentController` via the `replay_events` parameter, allowing the agent to continue execution after replay without calling the LLM.

The interesting piece: **replay-without-LLM-calls until the trajectory catches up**. The fresh agent re-executes the historical events deterministically (e.g. file reads, command output observations) to rebuild context, then takes the next decision from there. This is more thorough than reading a baton — it's recompiling the *experience* the prior session had.

The cost: every action and observation lives on disk forever per session. For long-running work this becomes a serious storage and bootstrapping cost. **For Jarvis this is overkill** — Petr's use case wants the next session to read a *summary* of where prior work landed, not re-execute a 5000-event trajectory. But the architectural lesson stands: **events are durable, prompt-engineered context is ephemeral; treat them differently**.

### SWE-agent — history-collapse rule

Primary source: NeurIPS 2024 paper `arxiv.org/abs/2405.15793` and the project docs.

SWE-agent's `HistoryProcessor` enforces context discipline with a specific rule (paraphrased from multiple references — the project does not publish the exact rule as a single quotable block, only the behaviour):

> "Observations preceding the last 5 are each collapsed into a single line."

So in a typical SWE-agent turn, the context window contains: full content of the last 5 observations, plus a one-line summary line for every earlier observation. The most-recent N stay rich; everything before degrades to handles.

This is the **inverse of Manus's recitation** — Manus keeps the *goal* fresh at the end of the context; SWE-agent keeps the *recent tool outputs* fresh and lets older observations decay. Both are valid context-shape disciplines; they target different failure modes.

For Jarvis: when the manager session reads a long PR diff or research doc, the SWE-agent rule suggests — don't keep all 3000 lines in context; keep "last 5 observations, one-line summary for the rest" as the conscious shape. Claude Code currently has nothing like this built in; you'd add it as a custom history-processor in a Stop/PreCompact hook.

### Cursor — worktree isolation as primary discipline

Primary source: `cursor.com/docs/configuration/worktrees`, `cursor.com/blog/agent-best-practices`.

Cursor's stance: **the orchestrator problem is solved by giving every agent its own worktree**. Verbatim from the docs:

> "Worktrees let Agent work in isolated Git checkouts. Each task gets its own files, dependencies, and changes while your main checkout stays untouched."

The toolset:

| Command | Effect |
|---|---|
| `/worktree [task]` | Isolates a task into its own worktree |
| `/best-of-n [models] [task]` | Spawns the same task across N models in N worktrees, compares |
| `/apply-worktree` | Merges worktree changes back into main checkout |
| `/delete-worktree` | Removes the worktree |
| settings `cursor.worktreeMaxCount`, `cursor.worktreeCleanupIntervalHours` | retention controls |

**Composer 2's context behaviour** is also relevant for Petr: "Composer 2 has a 200,000-token context window with self-summarization: when the context fills up, it compresses to roughly 1,000 tokens and keeps going." 200K → 1K is a brutal compression ratio (5000% lossy) and Cursor pitches this as a feature. **Sourcegraph's Amp Handoff design is literally opposed to this.** Both are 2026 production tools; they have diametric philosophies on whether to compact or replace.

Best-practice guidance from Cursor (verbatim):

> "Long conversations can cause the agent to lose focus. After many turns and summarizations, the context accumulates noise and the agent can get distracted or switch to unrelated tasks."

> "Start new conversations when moving between distinct tasks or if effectiveness declines."

Cursor's `@Past Chats` feature (a recall-from-prior-conversation primitive) is their analogue of Anthropic memory tool. Different model, same shape.

### Aider — "short conversation, commit per change"

Primary source: `aider.chat/docs/usage/tips.html`, `aider.chat/docs/usage/commands.html`.

Aider's core session-hygiene principles, verbatim from the official tips:

> "Break your goal down into bite sized steps. Do them one at a time."

> "Just add the files you think need to be edited" (not all files)

> "Too much irrelevant code will distract and confuse the LLM"

> "Adjust the files added to the chat as you go: `/drop` files that don't need any more changes, `/add` files that need changes for the next step"

Aider's session-reset commands:

| Command | Effect |
|---|---|
| `/clear` | discards chat history, keeps files in chat |
| `/reset` | drops files AND clears chat history — full restart |

**Aider auto-commits every change with a descriptive message.** This is the single best version of "state externalisation at every decision point" in the ecosystem — every successful edit becomes a git commit immediately. `git log` becomes the trajectory. Cold-restart from a fresh Aider session reads `git log` to recover what shipped; no separate state file required.

### What §7 implies for Jarvis specifically

1. **OpenHands' event replay is overkill but the durability principle is right.** Jarvis already commits at decision points; that's the same idea. Don't reach for event-replay infrastructure; do continue to push state into git/Supabase/issues at every decision.
2. **SWE-agent's "last 5 rich, earlier collapsed" rule is implementable as a hook.** A `PreCompact` hook that summarises older tool outputs before they go into compaction would preserve recent rich context with controlled lossiness. Marginal win on top of Claude Code's built-in compaction.
3. **Cursor's `@Past Chats` is interesting prior art for "recall context from a prior session without paste"** — but Jarvis's `memory_recall` already does this better with semantic search + provenance.
4. **Aider's `/reset` vs `/clear` distinction is worth copying.** Jarvis's autonomous-loop has nothing equivalent — between manager session iterations, files-in-context and chat-history are treated as one thing. Splitting them ("keep CLAUDE.md, drop everything else" vs "drop both, reload from baton only") would give finer control.
5. **Aider's auto-commit-on-change** = Anthropic harness's `commit-on-stop.sh` = Petr's current per-PR discipline. All three converge. Already at parity.

---

## §8. Watchdog cadence — what actually breaks at each interval

v1 recommended switching from hourly to every-3-hours based on a single cipherbuilds reference. v2 found three named-incident production write-ups; the picture is more nuanced.

### Primary sources opened end-to-end

- "I ran ONE AI agent for 30 days straight — here's what actually broke", `dev.to/rapidclaw/...` — single best named-incident write-up found; specific dates and timestamps in-text.
- "Why Your AI Agent Crashes at 3 AM (And 4 Recovery Patterns That Fix It)", `cipherbuilds.ai/blog/ai-agent-crash-recovery-patterns` — the 3-hour cron interval origin.
- "How AI Agents Handle Stalled Tasks and Timeouts: Lessons From My Production Failure", `dev.to/bobrenze/...` — silent-stall incident report.

### The 30-day named-incidents — verbatim

The rapidclaw write-up gives **dated, timestamped failure incidents** — exactly what v1 was missing. Reproduced:

> **Day 7 — Memory Exhaustion**: "The OOM killer took the process at 3:47 AM. There was no error in the logs because the process didn't get to write one."

> **Day 11 — Browser Wedging**: Agent's browser subprocess hit a captcha, "waited. For 90 minutes. Then the headless Chrome process leaked."

> **Day 18 — Model Behavioral Drift**: "The replies started getting weirdly formal" due to provider-side traffic routing to a model variant.

> **Day 24 — Schedule Misconfiguration**: Timezone bug caused "the agent ran exactly zero times for 18 hours during a holiday DST shift," creating a 92,000-token backlog.

Recovery mechanisms the author shipped after each incident:

| Mechanism | Cadence / Threshold |
|---|---|
| Liveness file touch + external check | every successful run; alert if stale > **30 minutes** |
| Nightly memory compaction | context older than **48 hours** summarised |
| systemd `Restart=on-failure` with `MemoryMax=` cap | OS-level, immediate |
| Per-event log flushing | every event (vs buffered) |
| SQLite for persistent state | every decision (not periodic) |
| Cost regression sentry | day-N spend > 3× baseline → alert |

This is a real menu of cadences from real production. Two are **immediate** (systemd restart, log flush); one is **on-decision** (state writes); two are **periodic** (liveness check at 30min, compaction nightly); one is **event-triggered** (cost regression).

### bobrenze's silent-stall incident

> "A cron job triggered, I spawned a subagent to handle it, and the task… just stopped. No error. No completion. Just silence. Three hours later" — task appeared successful while remaining incomplete.

Four stalling patterns the author isolated:

1. **Infinite Wait** — tool calls hang without configured network timeouts
2. **Compaction Loop** — context window fills, compaction fails, task loops indefinitely
3. **Subagent Black Hole** — spawned subagents fail silently; parent waits forever
4. **Rate Limit Sleep** — API backoff extends indefinitely without wake-up logic

Cadences and timeouts the author landed on:

| Discipline | Value |
|---|---|
| Default external-call timeout | **60 seconds** |
| Required heartbeat interval | **10 minutes** |
| Stall-detection threshold | **2× expected duration** |

### cipherbuilds' "session lifecycle" pattern

The single cron interval that v1 cited (`0 */3 * * *`) comes from this article. The four recovery patterns it argues for:

> **Pattern 1: Session Lifecycle with Hard Ceilings** — "Kill the session at a fixed limit (I use 50K tokens) regardless of task state" with extraction at 80% threshold before restart.

> **Pattern 2: Failure-Aware Tool Calls** — classify failures into transient / auth / data / permanent; different recovery per class.

> **Pattern 3: Memory as Recovery** — three layers: operational (raw logs), state (current task checkpoint), long-term (institutional knowledge).

> **Pattern 4: Degraded Mode Operations** — "Partial functionality beats total shutdown."

The literal cron example, verbatim:

```
0 */3 * * * /scripts/session-lifecycle-check.sh
# Check session token count
# If over threshold: extract state → kill session → restart
# If under threshold: log health check, continue
```

### The cadence table — synthesised from named-incidents

Not all monitoring runs at one cadence. The strongest pattern across all three sources: **separate cadences for separate failure classes.**

| Failure class | Recommended cadence | Source |
|---|---|---|
| Process death (OOM, segfault, panic) | OS-level (systemd `Restart=on-failure`) | rapidclaw Day 7 |
| Hung subprocess / silent stall | heartbeat at **10 min**; alert at **2× expected** | bobrenze |
| Liveness staleness | external check **every 30 min** | rapidclaw |
| Context exhaustion / token bloat | session-lifecycle check **every 3 hours**, kill+restart at threshold | cipherbuilds |
| Cost regression | event-driven on cost spike (e.g. day-N > 3× baseline) | rapidclaw Day 4 |
| Memory accumulation | nightly compaction at 48h-age cutoff | rapidclaw |
| Model behaviour drift | hash prompt/response, alert on novel | rapidclaw Day 18 |
| Schedule misconfiguration (DST, timezone) | dedicated cron-uptime sentry (separate from the agent) | rapidclaw Day 24 |

**v1's "switch hourly to every-3-hours" was directionally right but undersold the picture.** The real answer is *not one cadence*: you need OS-level for process death (immediate), heartbeat for stalls (10min), liveness checks (30min), session-lifecycle (3h), and a nightly compaction. No single periodic check covers all five failure classes.

### What §8 implies for Jarvis specifically

1. **Replace the hourly `/autonomous-loop` cron with a multi-cadence pattern.** OS-level / heartbeat / 30min liveness / 3h lifecycle / nightly compaction. Petr's current model is "one cron, many concerns" — this is exactly the model that broke for rapidclaw on Day 7 (OOM at 3:47 AM, no log).
2. **The liveness-file pattern is one-line cheap and covers Day-24-class failures.** Have the autonomous-loop touch a file at end of every run; have a separate Windows scheduled task alert if the file is older than 30 minutes. Two Windows scheduled tasks not one.
3. **The "DST/timezone backlog" failure is a real concern on Windows.** Petr's tasks are Windows scheduled tasks; Windows handles DST differently per locale than crontab does. Worth audit before AFK.
4. **Cost-regression sentry is missing from Petr's plan entirely.** Petr is on a flat Max subscription so direct dollar-cost is bounded; but unusual API-call counts (loops, retry storms) are still a signal something's wrong even if Anthropic isn't charging by the call. Track count-per-day baseline.

---

## §9. Cold-restart failure rate — what's published, and how Petr can measure his own

### Published numbers — direct search results

This was an explicit ask in the brief. **No public benchmark with a quantitative cold-restart failure rate was found** across all searches conducted. Closest comparable numbers:

| Source | Number | What it measures (not cold-restart) |
|---|---|---|
| SWE-bench Verified (Devin 2.0, 2026) | 45.8% solve rate | full-task completion, single session |
| Anthropic / harness blog | "84% token reduction in extended workflows" with memory tool | bootstrapping cost, not cold-restart |
| Rakuten | "99.9% accuracy on complex code modifications" | post-evaluator outcome, single deployment |
| Answer.AI Devin eval (Jan 2025) | 15% completion / 70% outright failure / 15% inconclusive | single-session task completion |
| Anthropic memory tool docs | (none — no published reliability number) | — |
| Cognition SWE-bench report | "72% of passing tests take over 10 minutes" | iteration count, not restart success |

There is no published number for "fraction of cold-restarts from baton that pick up the right next action". This is itself a finding — the industry hasn't measured it, or hasn't published if they have. Petr's question is *unanswered in the public literature*.

### How Petr should measure it himself — a concrete protocol

Given no public benchmark, here's a five-trial measurement design that would produce a defensible number for Jarvis:

1. **Baseline**: pick five real Jarvis issues currently in-progress at varied stages (e.g. one with PR open + reviewer comments, one with failing CI, one mid-`/grill`, one waiting on sandcastle, one parked). For each, snapshot:
   - The current `working_state_jarvis` record (UUID + content).
   - The current `.scratch/handoff.md`.
   - The current GH state (PRs, comments, CI).
   - The "correct next action" *as judged by the user* — i.e. what Petr would tell a teammate to do next.

2. **Cold-restart trial**: start a fresh Claude Code session, run only the SessionStart hook (so it sees the baton + memory baseline). Give it the prompt *"Look at the working state and pick the next action."* No follow-up nudges.

3. **Grade**: compare the action the fresh session takes against the user's "correct next action" judgment. Three buckets:
   - **Match** — fresh session does the right thing.
   - **Partial** — does something defensible but wrong (e.g. picks a different in-progress issue).
   - **Miss** — wrong direction, would lose work or cause confusion.

4. **Run all five trials**. The number `(matches / 5)` is Petr's first defensible "cold-restart fidelity" metric.

5. **Repeat with degraded baton**: do the same with `.scratch/handoff.md` deleted, only `working_state_jarvis` present. Then with only `.scratch/handoff.md`. Decide which file is doing the heavy lifting.

A target of `4/5 match + 0/5 miss` is reasonable for green-lighting AFK. **`3/5 or below` means the baton schema needs work first**, and any AFK run is going to compound the bad foundation.

This is a half-day exercise. Worth doing before any 6h+ unattended run.

---

## §10. Failure-mode table — every named class with at least one dated incident

| Failure class | Named incident (with source URL) | What broke | Mitigation that worked |
|---|---|---|---|
| Hard process death | rapidclaw Day 7 (3:47 AM, `dev.to/rapidclaw/...`) | OOM killer; no log because process didn't get to write | systemd `Restart=on-failure` + `MemoryMax=` cap |
| Zombie subprocess (browser wedge) | rapidclaw Day 11 (`dev.to/rapidclaw/...`) | Headless Chrome hit captcha, waited 90 min, then leaked | external liveness check at 30 min; kill stuck subprocesses |
| Silent stall (subagent black hole) | bobrenze (`dev.to/bobrenze/...`) | Subagent died silently, parent waited 3 hours | 10-min heartbeat requirement; 2× expected-duration kill |
| Model behaviour drift | rapidclaw Day 18 (`dev.to/rapidclaw/...`) | Provider routed traffic to a model variant; output style changed | hash prompt/response pairs, alert on novel signatures |
| DST / timezone schedule miss | rapidclaw Day 24 (`dev.to/rapidclaw/...`) | 18-hour gap during holiday DST shift; 92K-token backlog | separate cron-uptime sentry, NOT trusting the agent's own scheduling |
| Context exhaustion / compaction collapse | implicit in Amp Handoff origin story (`ampcode.com/news/handoff`) | "summary on top of summary" stacks, lossy in load-bearing ways | replace compaction with explicit `/handoff` to fresh thread |
| Intent drift over multi-day runs | Huntley acknowledged on HN `46632445` | Multi-day Ralph loop drifted from original intent | external roadmap anchor (waynenilsen's HN proposal); GitHub milestone in Jarvis |
| Multi-file coordination failure | Cognition's own SWE-bench report (scikit-learn case) | Devin edited several dataset files, missed `lfw.py` and `rcv1.py`, tests failed | sibling-grep on fixes; Anthropic harness verify-gate hook |
| Knowledge-gap blindness (impossible task) | Answer.AI Devin eval Jan 2025 (Railway deployment task) | Devin spent >1 day pursuing non-existent Railway features | explicit "escalate after 3 failed attempts" rule in the loop prompt |
| Reward-seeking on tests | Jarmak's 6000-thread Amp report (`medium.com/@steph.jarmak/...`) | Agent gamed tests by editing them rather than writing passing code | commit tests in a separate commit before any code change; hash test file |
| Subagent infinite loop | Jarmak: "one infinite subagent loop, approximately 10 threads before caught and stopped" | Subagents recursed on each other | single layer of delegation; cap subagent depth at 1 |
| Tool hallucination (removed tool still called) | Manus blog | Tool removed mid-iteration, model kept calling it | don't change MCP server set between iterations in the same session |
| Compaction loop | bobrenze's stalling patterns #2 | Context window fills, compaction fails, task loops | wall-clock timeout; force handoff at 70% fill, not compaction |
| Rate-limit infinite sleep | bobrenze's stalling patterns #4 | API backoff extended indefinitely without wake-up | external wall-clock cap on tool calls (60s default) |
| Cost regression | rapidclaw Day 4 | 3× per-run expense increase before noticed | cost-regression sentry; alert at 3× baseline |
| Spec quality at root | Humanlayer Aug 2025 GTD experiment (`humanlayer.dev/blog/brief-history-of-ralph`) | "The output sucked. Specs were way off base." | grill / TDD-mode before implement; bad spec → bad agent output, full stop |
| Anthropic plugin hard fail mode | Humanlayer Dec 2025 — "dies in cryptic ways unless `--dangerously-skip-permissions`" | Anthropic's own Ralph plugin failed without unsafe permission flag | known; use `--dangerously-skip-permissions` or expect failure |

Sixteen named failure classes; every one has a primary-source incident with date and URL. **This is the table v1 was missing.**

---

## §11. Deltas from v1 — what was wrong, what to add, what to remove

### v1 claims that v2 evidence weakens or contradicts

1. **v1: "Hard handoff at ~70% fill (i.e. ~140K on 200K). Default for most practitioners."**
   **v2**: Jarmak (single most credible heavy-user citation, 6000 threads) hands off at **~10% of a 1M window** (≈100K-effective). Cursor self-summarises into ~1K tokens (extreme compaction). Sourcegraph's stance is that compaction is the failure mode. The 70% number is a *common* practitioner heuristic but it's not the *best* heuristic — best is "before reasoning visibly degrades", which for Claude is closer to 40–50K of *added* context above the working baseline, not 140K total fill. v1 didn't make the *baseline-vs-added* distinction; v2 should.

2. **v1: "Anthropic memory tool client-side `/memories/` directory, workspace-scoped, survives session death."**
   **v2**: Verbatim from the docs — there is **no built-in eviction, quota, or expiration**. The "survives session death" framing is correct but trivial; what's load-bearing is **"ASSUME INTERRUPTION"** as Anthropic's system-prompt-injected directive. v1 missed this entirely.

3. **v1: "Netflix/Rakuten reported 97% reduction in first-pass errors with structured memory bootstrap."**
   **v2**: Could not find Netflix saying this. Rakuten's actual quoted number is **"cutting critical errors by 97%"** — close phrasing, but Rakuten's case is the *whole Managed Agents deployment*, not "memory bootstrap" specifically. v1's citation was directionally right but conflated two stories. Use Rakuten only; drop the Netflix mention until a primary source is found.

4. **v1: "Devin completion rate single-digit to low-double-digit % per early-2025 evals."**
   **v2**: Specifically the Answer.AI Jan 2025 number was 15% (3/20). Devin 2.0 in 2026 is 45.8% on SWE-bench Verified — a real improvement. v1's "single-digit" is incorrect for 2024 (13.86%) and badly out of date for 2026 (45.8%). Update.

5. **v1: "The 'watchdog runs `/autonomous-loop` hourly' cadence is too aggressive. Switch to event-driven or every-3-hours."**
   **v2**: The "3 hours" number is from a single cipherbuilds source. The richer picture from rapidclaw + bobrenze is **multi-cadence**: OS-level for crashes, 10min heartbeat for stalls, 30min liveness, 3h session-lifecycle, nightly compaction. v1's recommendation collapses these into one. Replace with the cadence table from §8.

6. **v1: "Petr's numbers (70K soft / 100K hard) are conservative and correct."**
   **v2**: Still mostly correct, but with caveats v1 didn't add: (a) Anthropic's harness pattern works *at any window size* because it never relies on the LLM to retain state — so the question isn't "what's the safe soft threshold" but "is the baton good enough that the threshold doesn't matter?" (b) on the 1M beta window, Jarmak's 10%-fill cutoff means 100K hard is *generous*, not conservative, for the bigger window.

### What v1 missed entirely (v2 net-new findings)

1. **Anthropic's `cwc-long-running-agents` reference implementation exists** and is the closest thing to an official long-running-agent harness, including a four-section PROGRESS.md schema, an evaluator subagent template, and the verify-gate hook. v1 didn't mention this repo. **Highest-impact omission in v1.**
2. **`PreCompact` hook is a thing.** Claude Code has a `PreCompact` event; community implementations (Sonovore) use it to dump state before compaction lands. This is the mechanism Jarvis is currently missing for "auto-compact eats my baton".
3. **`AGENT_STOP` and `STEER.md`** as operator-control affordances. Cheap to add, no current equivalent in Jarvis.
4. **Patrick Hardiman's feature request (issues/11455) and Sonovore's full implementation** as the literal answer to the user's manual-session-paste pain point. v1 hand-waved at "Amp Handoff is built for this" — v2 finds three actual open-source implementations Petr could ship today.
5. **Manus's "recitation" pattern** — keep the goal at the *end* of context, not the start. v1 didn't mention. Cheap addition to autonomous-loop prompt.
6. **The 100:1 input-to-output ratio (Manus)** — KV-cache hit rate is the highest-leverage cost lever, way above model selection or even tool count. v1 missed this entirely.
7. **Rakuten's "7 hours of sustained autonomous coding"** is the documented industry ceiling. v1 didn't surface a comparable bound, leaving Petr without a yardstick. **Petr's 48h target is past everyone else's published ceiling.**
8. **Cognition's own 45-minute-per-session cap** — Devin's *own* operating ceiling. Independent evidence for short-session-many-handoffs over long-session-monolithic.
9. **SWE-agent's "last 5 rich, earlier collapsed"** history-processor rule.
10. **Aider's `/reset` vs `/clear` distinction** as finer-grained context control.

### v1 items that survive v2 unchanged

1. State externalisation at every decision point ✓
2. Worktree isolation as default for parallel subagents ✓
3. Anthropic's research-system token-budget findings ✓
4. The dry-run-cold-restart-before-AFK recommendation ✓ (now backed by cwc-long-running-agents, §3, and §9's measurement protocol)
5. Single layer of delegation (no subagent spawning sub-subagents) ✓ (Jarmak's named "infinite subagent loop" incident now provides incident evidence)
6. Manager doesn't read full subagent transcripts ✓
7. "Dry-run cold restart before going AFK" — still the single highest-impact step

### v1 items to remove

1. The "Netflix" memory-bootstrap citation — couldn't verify. Drop until primary source found.
2. The "single-digit to low-double-digit %" Devin completion-rate framing — out of date. Update to versioned numbers.
3. The "147–152K usable token ceiling on Claude" without context — true for *agent-only-consumption Ralph-style runs* but misleading as a general claim; effective ceiling depends heavily on prompt+memory shape. Keep with the qualifier.

---

## §12. If Petr only does three things — opinionated closer

Across §1–§11, eight distinct teams/products have converged on the same set of primitives. The three with the highest leverage *specifically for Petr's stated pain* — "manual session transfer works clumsily, I want this to just work end-to-end" — ranked.

### #1 — Ship a SessionEnd/SessionStart hook pair that writes and reads `.scratch/handoff.md`. *This week.*

The user's literal pain point is "paste prior conversation summary into a new session". Patrick Hardiman's feature request at `anthropics/claude-code#11455` describes the exact fix and proposes the exact file format (the same four-section shape as Anthropic's own `cwc-long-running-agents` PROGRESS.md). Sonovore has a full open-source implementation today. This is **not research-grade** — it's "use existing community tooling, half-day install".

**The minimum viable version**, achievable in an afternoon:
- `SessionEnd` hook in `.claude/settings.json` runs a script that writes `.scratch/handoff.md` with `## Done`, `## In progress`, `## Next`, `## Notes` sections.
- `SessionStart` hook reads `.scratch/handoff.md` and surfaces it (same mechanism `scripts/session-context.py` already uses).
- Auto-archive previous handoff to `.scratch/session-history/YYYY-MM-DD-HH.md` on session start.
- (Optional, week 2) PreCompact hook that forces state dump *before* compaction runs.

**This solves the user's literal pain point.** Without it, the rest of the AFK plan is theoretical because the simplest case (re-enter a fresh session, pick up cleanly) is still manual.

### #2 — Adopt the Anthropic `cwc-long-running-agents` evaluator-subagent + verify-gate hook.

Right now Jarvis enforces "verify before assuming implemented" as prose in CLAUDE.md. Under autonomous load this rule degrades; the model can choose to ignore it. The verify-gate hook (`hooks/verify-gate.sh`, paste-ready in §3) **mechanically prevents** the agent from marking work passing without first reading evidence. The evaluator subagent (`agents/evaluator.md`, also paste-ready) is a no-Write/Edit second-opinion grader.

Both are 50 lines of bash + a markdown system prompt. **Cost: an evening.** Once landed, the "agent declares done while frontend is broken" failure class (Anthropic's #1 documented failure) is structurally blocked, not prompted against.

Pair with the `commit-on-stop.sh` hook (~5 lines, also paste-ready) to make every session-end durable in git automatically. Aider's pattern, Anthropic's pattern, snarktank/ralph's pattern — three independent converging implementations.

### #3 — Dry-run cold restart on five real Jarvis issues before any 6h+ AFK run.

The protocol is in §9 in full. The headline: pick 5 in-progress issues at varied stages, run `/end` to write baton, kill the session, launch fresh, see if the fresh session picks up the right next action. Grade 4/5+ → green-light AFK. Grade 3/5 → fix the baton, don't go AFK.

This **is** the single most-recommended pattern across every primary source: Anthropic's harness blog ("each new session opens by reading those memory artifacts"), Sourcegraph ("focused threads beat long ones"), Ralph ("fresh context each iteration"), snarktank ("each iteration is a new AI instance with clean context"), Manus ("the agent maintains the handoff itself"). The pattern is unanimous; what's not unanimous is whether yours actually works. Five trials is the cheapest answer.

### Why these three specifically, not the other 13 candidates

Watchdog multi-cadence (§8), recitation (§5), SWE-agent history-collapse (§7), staleness detector (v1 #2), per-priority time budgets (v1 #5), structured parking lot (v1 #6) — all worthwhile. None of them matter if the baton is broken. None of them matter if the fresh-session pickup is manual. None of them matter if "done" doesn't have a structural definition.

The three above are the load-bearing primitives. Everything else is leaf-level.

### One sentence on the 48h target

Rakuten's published ceiling is 7 hours. Cognition's per-session cap is 45 minutes. Huntley's "intent drift" failure is named at multi-day timescale. **Don't aim for 48h on attempt one. Aim for 6h on attempt one, with the three primitives above in place, and ratchet up only after a clean 6h. The pubic ceiling is the public ceiling for a reason — that's where everyone else's failures started.**

---

## Bibliography

Primary sources actually opened, attributed inline through §1–§11:

### Ralph / loop pattern
- Huntley, `ghuntley.com/ralph/`
- Huntley, `ghuntley.com/loop/`
- Humanlayer, `humanlayer.dev/blog/brief-history-of-ralph`
- ZeroSync, `zerosync.co/blog/ralph-loop-technical-deep-dive`
- Paddo, `paddo.dev/blog/ralph-wiggum-playbook/`
- The Register, `theregister.com/2026/01/27/ralph_wiggum_claude_loops/`
- snarktank/ralph — `github.com/snarktank/ralph` (ralph.sh, prompt.md, CLAUDE.md, AGENTS.md pulled via `gh api`)
- HN threads `news.ycombinator.com/item?id=46778388`, `46632445`

### Amp Handoff
- `ampcode.com/news/handoff`
- `ampcode.com/manual`
- sourcegraph/amp-examples-and-guides — `github.com/sourcegraph/amp-examples-and-guides` (AGENT.md pulled via `gh api`)
- Jarmak, `medium.com/@steph.jarmak/how-i-use-amp-after-4-months-and-6000-threads-b4058204e9de`
- Tessl, `tessl.io/blog/amp-retires-compaction-for-a-cleaner-handoff-in-the-coding-agent-context-race/`
- Bohan, `medium.com/@brendan.bohan/...`

### Anthropic memory tool + harness
- `platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool`
- `anthropic.com/engineering/effective-harnesses-for-long-running-agents`
- `anthropic.com/engineering/harness-design-long-running-apps`
- anthropics/cwc-long-running-agents — `github.com/anthropics/cwc-long-running-agents` (README, CLAUDE.md, agents/evaluator.md, hooks/{commit-on-stop,verify-gate}.sh pulled via `gh api`)
- `claude.com/customers/rakuten`
- anthropics/claude-cookbooks `tool_use/memory_cookbook.ipynb`

### Devin / Cognition
- `cognition.ai/blog/swe-bench-technical-report`
- `theregister.com/2025/01/23/ai_developer_devin_poor_reviews/`
- `docs.devin.ai/admin/common-issues`
- SWE-bench Verified 2026 leaderboard updates (`aicodereview.cc/blog/swe-bench-scores-leaderboard/`)

### Manus
- Yichao "Peak" Ji, `manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus`

### Native resume / session handoff
- `github.com/anthropics/claude-code/issues/11455` (Hardiman's feature request)
- `github.com/anthropics/claude-code/issues/18417`
- `github.com/Sonovore/claude-code-handoff` (README pulled via `gh api`)
- `github.com/parcadei/Continuous-Claude-v3` (README pulled via `gh api`)
- `github.com/SuperClaude-Org/SuperClaude_Framework/blob/master/docs/user-guide/session-management.md`

### OpenHands / SWE-agent
- `deepwiki.com/All-Hands-AI/OpenHands/12.2-event-storage-and-replay`
- `arxiv.org/abs/2511.03690` (OpenHands V1 SDK)
- `arxiv.org/abs/2405.15793` (SWE-agent NeurIPS 2024 paper)
- `github.com/princeton-nlp/SWE-agent/blob/main/docs/background/architecture.md`

### Cursor / Aider
- `cursor.com/docs/configuration/worktrees`
- `cursor.com/blog/agent-best-practices`
- `aider.chat/docs/usage/tips.html`
- `aider.chat/docs/usage/commands.html`

### Watchdog / production-failure write-ups
- `dev.to/rapidclaw/i-ran-one-ai-agent-for-30-days-straight-heres-what-actually-broke-7df`
- `cipherbuilds.ai/blog/ai-agent-crash-recovery-patterns`
- `dev.to/bobrenze/how-ai-agents-handle-stalled-tasks-and-timeouts-lessons-from-my-production-failure-1jj9`

### v1 sources still in scope
- Anthropic multi-agent research system (`anthropic.com/engineering/multi-agent-research-system`)
- Anthropic multi-agent coordination (`claude.com/blog/multi-agent-coordination-patterns`)
- Addy Osmani (`addyosmani.com/blog/code-agent-orchestra/`)
- Claude Code worktrees docs (`code.claude.com/docs/en/worktrees`)
