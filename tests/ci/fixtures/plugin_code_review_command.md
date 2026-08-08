---
allowed-tools: Bash(gh issue view:*), Bash(gh search:*), Bash(gh issue list:*), Bash(gh pr comment:*), Bash(gh pr diff:*), Bash(gh pr view:*), Bash(gh pr list:*), Bash(python -m py_compile:*), Bash(python3 -m py_compile:*), Bash(bash -n:*), Bash(node --check:*), Bash(git show:*), Bash(git blame:*), Bash(git log:*), Bash(wc:*), Write(//tmp/**)
description: Code review a pull request
disable-model-invocation: false
---

Provide a code review for the given pull request.

To do this, follow these steps precisely:

1. Use a Haiku agent to check if the pull request (a) is closed, (b) is a draft, (c) does not need a code review (eg. because it is an automated pull request, or is very simple and obviously ok), or (d) already has a code review from you **for the current head commit**. If so, do not proceed.
   - For (d), the check MUST be head-aware, not "any prior review from me". Resolve the head SHA and its committer time via `gh pr view <n> --json headRefOid,commits` — the last element of `commits` is the head commit and carries its `committedDate` (do NOT use `gh api` for this; it is not in this command's allowed tools) — and compare against the creation time of your latest `### Code review` comment. Only skip when a prior review comment exists whose `created_at` is **at or after** the head commit's committer time — i.e. it already reviewed this exact head. A review from an EARLIER head commit does NOT count: the code has changed since, so you must proceed and post a fresh review. Skipping on a stale prior review is the root cause of redrobot#1324 — the run reports `is_error=false` but posts nothing for the new head, and the downstream freshness-anchored merge gate then fail-closes on a genuinely clean PR.
2. Use another Haiku agent to give you a list of file paths to (but not the contents of) any relevant guideline files from the codebase. Look for, in order: (a) the root CLAUDE.md, (b) CLAUDE.md files in the directories whose files the pull request modified, (c) any SOUL.md, AGENTS.md, or similar persona/identity files that encode behavioral rules (these often live separately from coding rules and are still normative for review), (d) SKILL.md or agent-definition files for any skill/subagent the PR modifies. Treat all of these as normative when reviewing — many projects split rules across several markdown files
3. Use a Haiku agent to view the pull request, and ask the agent to return a summary of the change
3.5. Use a Haiku agent to scan all existing comments on the PR for HTML markers in the format `<!-- DESIGN_LOCKED: <UUID> | <topic> -->`. These markers are posted by the rework skill (rework/SKILL.md section 3b) when it skips a design-level finding because a `record_decision` entry already resolved that question. Collect each match as a locked design topic: `<UUID>: <topic>`. If any locked topics are found, prepend the following block to the prompt of EACH of the 12 reviewer agents in step 4:

   ```
   DESIGN-LOCKED decisions for this PR -- do NOT raise findings about these topics (they were already resolved via record_decision and the rework skill would skip them again):
   <UUID>: <topic>
   ... (one per line)
   ```

   This prevents the reviewer from re-raising architectural decisions that have already been recorded and accepted, which was the root cause of PR #1256 requiring 7 rework rounds (the reviewer oscillated on the same design choice across rounds 3-7). If no DESIGN_LOCKED markers are found, skip the preamble entirely.
4. Then, launch 12 parallel reviewer agents (Sonnet for #1–6, #10, #11 — semantic review; Haiku for #7–9 and #12 — mechanical / pattern-based). Prepend the following tool-discipline block to the prompt of EACH of the 12 reviewer agents, verbatim (same delivery mechanism as the DESIGN-LOCKED block in step 3.5 — a preamble that is not prepended never reaches the subagent's context):

   ```
   TOOL DISCIPLINE (binding, regardless of task pressure): your Bash usage is restricted to this command's allowed-tools allowlist — gh issue view/list, gh search, gh pr comment/diff/view/list, python -m py_compile, bash -n, node --check, git show, git blame, git log, wc. NEVER run `gh api`, `curl`, `wget`, `git fetch`, `gh pr checkout`, or any other network/mutation command — in headless CI these are DENIED, and every denial hard-fails the consuming repo's review gate even when the PR under review is clean. Do not fall back to WebFetch for GitHub data either. For reading file contents, do NOT use Bash (`grep`, `cat`, `find` are not allowlisted) — use the native Read, Grep, and Glob tools instead; for file contents at a specific commit, `git show <sha>:<path>` is allowlisted. If data you want is unreachable with these tools, note the gap in your findings and move on — do NOT improvise around the allowlist.
   ```

   The agents should do the following, then return a list of issues and the reason each issue was flagged (eg. CLAUDE.md adherence, bug, historical git context, diff coherence, cross-device, smoke, structural growth, simplification opportunity, AC conformance, integration coverage, etc.):
   a. Agent #1: Audit the changes to make sure they compily with the CLAUDE.md and any related guideline files (SOUL.md, AGENTS.md, SKILL.md) found in step 2. Note that these files are guidance for Claude as it writes code, so not all instructions will be applicable during code review.
   b. Agent #2: Read the file changes in the pull request, then do a shallow scan for obvious bugs. Avoid reading extra context beyond the changes, focusing just on the changes themselves. Focus on large bugs, and avoid small issues and nitpicks. Ignore likely false positives.
   c. Agent #3: Read the git blame and history of the code modified, to identify any bugs in light of that historical context
   d. Agent #4: Read previous pull requests that touched these files, and check for any comments on those pull requests that may also apply to the current pull request. Use EXACTLY this discovery recipe — every step is within the allowed tools: (1) for each changed file, run `git log --format='%H %s' -- <file>` and harvest PR numbers from the `(#NNN)` suffixes in squash-merge commit subjects; (2) if subjects carry no PR numbers, fall back to `gh pr list --state merged --search "<file>"` or `gh search prs`; (3) for each discovered prior PR, read its comments with `gh pr view <NNN> --comments`. Known limitation, BY DESIGN: inline line-level review comments live behind `gh api .../pulls/<n>/comments`, which is NOT in the allowed tools and is deliberately unreachable — the conversation comments and review bodies that `gh pr view <NNN> --comments` returns are the sanctioned and sufficient signal for this check. Do NOT attempt `gh api`, `curl`, `WebFetch`, `git fetch`, or `gh pr checkout` to get more; if the accessible comments yield nothing applicable, return no findings for that PR.
   e. Agent #5 (logic-leak): Look at the diff. For each modified file that existed before this PR, identify any *feature-specific* check, format-specific branch, environment-specific path, product-vertical conditional, or one-off business rule that has been added to a file that was previously generic/shared (utilities, base classes, framework glue, schema, shared models). Flag as: "feature logic from <X> added to shared module <Y>". Do NOT flag legitimate new arguments, hooks, extension points, or strategy/adapter slots that the existing module clearly was designed to accept; do NOT flag changes localized to a clearly feature-specific module that already lives under a feature directory. Also read code comments in the modified files for any normative guidance the changes contradict (the previous-version responsibility of this agent) — surface those as the same kind of issue.
   f. Agent #6 (diff-coherence): Read the PR title, body, and commit messages (`gh pr view <n> --json title,body,commits`). Extract every concrete claim about what changed — files added/modified/deleted, behaviors implemented, tests added, configs touched. Then run `gh pr diff` and verify each claim against the actual diff. Flag two mismatch classes: (1) **claimed-missing** — the PR or commit asserts a change was made but the diff shows no corresponding edit (a common subagent-fabrication signature, where the agent reports completion but the work didn't land); (2) **silent-scope-creep** — substantive edits in the diff that aren't acknowledged anywhere in the PR/commit narrative. Do NOT flag normal restatements, files renamed mid-cycle (claim points to old path, diff to new path), or claims about outcomes ("CI passes") that are not file-level. Cite both the claim source (PR body line / commit SHA) and the diff evidence.
   g. Agent #7 (cross-device integrity, Haiku): Read the diff via `gh pr diff` and scan ONLY the added or modified lines for portability hazards that will break on a teammate's machine: (a) **hardcoded absolute paths** that include a specific user or device — `/home/<name>/`, `/Users/<name>/`, `C:\Users\<name>\`, `/mnt/c/Users/<name>/`, `/opt/<custom>/`; (b) **hardcoded usernames** appearing as bare identifiers in code or config (e.g., a literal `petrk`, `ubuntu`, `azureuser` outside of an example/comment); (c) **OS-specific assumptions** that aren't guarded — bare `\r\n` literals, `os.path.join` mixed with literal `\\`, calls to `cmd.exe` / `powershell` / `/bin/bash` without a platform check, hardcoded drive letters; (d) **ports / hostnames / network paths** like `localhost:5432` or `\\server\share` that look like the author's local setup. Skip example files, fixtures, docs, and clearly-labeled test data. Cite line + the suspect substring.
   h. Agent #8 (smoke / static-check, Haiku): For every script-like file changed in the PR (`.py`, `.sh`, `.bash`, `.js`, `.mjs`, `.cjs`, `.ts` ONLY when standalone — skip lib/source modules of large apps), run a syntax-only static check via the appropriate Bash command and report failures: Python — `python -m py_compile <file>` (or `python3` fallback); shell — `bash -n <file>`; JavaScript — `node --check <file>`. Do NOT execute scripts. Do NOT install dependencies. Do NOT run TypeScript compilation (compile passes elsewhere in CI). For Python files, also flag obvious import errors visible from the diff alone (e.g., `from foo import bar` where `bar` was simultaneously deleted from `foo` in the same PR). Skip generated files, vendored code, and minified bundles. Cite the failing file + parser/compiler error message.
   i. Agent #9 (structural growth tripwire, Haiku): For each non-deleted file in the PR, compute its post-PR line count and the delta vs base. Use `gh pr view <n> --json files,headRefOid,baseRefOid` to get the file list + head SHA + base SHA, then for each file run `git show <head_sha>:<file> | wc -l` (post-PR) and `git show <base_sha>:<file> 2>/dev/null | wc -l` (pre-PR; treat missing as 0 for new files). Flag any file that (a) crosses 1000 lines in this PR (was <1000, now ≥1000), or (b) grew by ≥300 lines in this PR and is now ≥800 lines. One line per finding: `<file>: <prev>→<new> lines (+<delta>)`. Skip lockfiles (`*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `Cargo.lock`), JSON/YAML fixtures and snapshots, generated files (under `generated/`, `build/`, `dist/`, `*.pb.go`, `*.gen.*`), vendored code, and minified bundles. No prose, no remediation advice — the file-size signal is purely mechanical; the human decides what to do.
   j. Agent #10 (simplification scout, Sonnet): Read the diff. Identify the 1–2 largest contiguous net-add chunks (≥30 added lines each, in the same function or hunk). For each, ask one question: *could the same behavior be expressed in noticeably less code without changing the public interface or weakening correctness?* Look for: a new helper that wraps a 3-line operation already used inline elsewhere; a new conditional whose "true" branch matches the existing default; a new flag added to a function that already takes 5+ args; a new wrapper class that holds a single method; deeply-nested error handling that could be a single guard at the top; copy-paste of an existing helper because the existing one is in the "wrong" module. Cite the chunk (file + line range) and propose the simpler shape in 1–2 lines of pseudocode or prose. **Do NOT propose restructuring whose risk dominates the gain** — a working 80-line function is fine if the simpler version would touch >3 callers or weaken type safety / runtime validation / error handling. Skip if the chunk encodes a genuinely new capability with no existing analog. Output AT MOST 2 findings per PR — this is opportunity-spotting, not exhaustive review.
   k. Agent #11 (AC-conformance, Sonnet): This is the *did-the-PR-solve-the-assigned-problem* gate — the one lens that judges the change against its intent rather than its quality. Find the issue this PR closes: read the PR body for a `Closes #NNN` / `Fixes #NNN` / `Resolves #NNN` reference (`gh pr view <n> --json body,title`), then `gh issue view <NNN>` to read it. If no issue is linked AND the PR is non-trivial, flag once: "no linked issue — cannot verify against acceptance criteria" (skip this for `[no-issue]`-marked, `refactor:`-titled, or hotfix PRs that the project allows to bypass issue-linking). Otherwise, extract the issue's **acceptance criteria** — the explicit AC / "Definition of Done" / checkbox list / "должно работать"-style requirements written by the issue author. For EACH concrete AC item, judge from `gh pr diff` whether the change addresses it, and bucket each as: **satisfied** (diff shows the behavior), **not-addressed** (no diff evidence the requirement was implemented), or **contradicted** (diff does something the AC forbids). Flag only **not-addressed** and **contradicted** items, one finding per item, citing the AC text verbatim and the diff evidence (or its absence). Treat the AC as the authoritative spec — the issue author owns its correctness; your job is conformance, not whether the AC itself was wise. **Flag only when an AC is *concretely* unmet** (mirror of the diff-coherence rule): a requirement is "not-addressed" only if it names a specific behavior/file/output that is genuinely absent from the diff — NOT when the AC is met via a different-but-equivalent implementation, satisfied in a helper the diff adds elsewhere, or covered by an existing mechanism the PR wires into. Do NOT infer unstated requirements; judge ONLY what the AC literally says (unstated-but-obvious gaps are the issue author's spec error, out of scope here). Do NOT flag AC items that are clearly out of scope for this PR when the issue is explicitly sliced (the PR body or issue says "part of #NNN" / "slice N of M").
   l. Agent #12 (integration tripwire, Haiku): Mechanically check for *dangling ends* — co-changes the diff's own footprint obligates but the project's guideline files require rather than the linked AC. Read the root and per-directory CLAUDE.md / AGENTS.md (paths from step 2) and look specifically for an "integration checklist" / "обязательно для каждого изменения"-style section that enumerates cross-cutting co-change rules. Then, from `gh pr diff`, apply each such rule mechanically as a touched-X-implies-touched-Y check. Typical rules (use the project's actual list, not these defaults): a backend endpoint/route was added or changed → is a frontend caller updated in the same diff? a shared data model / schema / DTO was changed → are its consumers (API schema, frontend types, serialization) updated? a config key was added → is it present across all config files / `.env.example` / environments the checklist names? a `planning/` or `sandbox/` pipeline stage was touched → does the diff reflect the downstream stage it feeds? One finding per dangling end: "changed `<X>` but the integration checklist requires also updating `<Y>` — no such edit in the diff", citing the checklist line. This is a pattern check, not a semantic one — flag only when the rule is *written down* in a guideline file AND the obligated co-change is mechanically absent. If the project has no integration-checklist section, this agent returns no findings. Do NOT re-derive completeness from the AC (that is agent #11's job) and do NOT invent integration rules the guidelines don't state.
5. For each issue found in #4, launch a parallel Haiku agent that takes the PR, issue description, and list of CLAUDE.md files (from step 2), and returns a score to indicate the agent's level of confidence for whether the issue is real or false positive. To do that, the agent should score each issue on a scale from 0-100, indicating its level of confidence. For issues that were flagged due to CLAUDE.md instructions, the agent should double check that the CLAUDE.md actually calls out that issue specifically. The scale is (give this rubric to the agent verbatim):
   a. 0: Not confident at all. This is a false positive that doesn't stand up to light scrutiny, or is a pre-existing issue.
   b. 25: Somewhat confident. This might be a real issue, but may also be a false positive. The agent wasn't able to verify that it's a real issue. If the issue is stylistic, it is one that was not explicitly called out in the relevant CLAUDE.md.
   c. 50: Moderately confident. The agent was able to verify this is a real issue, but it might be a nitpick or not happen very often in practice. Relative to the rest of the PR, it's not very important.
   d. 75: Highly confident. The agent double checked the issue, and verified that it is very likely it is a real issue that will be hit in practice. The existing approach in the PR is insufficient. The issue is very important and will directly impact the code's functionality, or it is an issue that is directly mentioned in the relevant CLAUDE.md.
   e. 100: Absolutely certain. The agent double checked the issue, and confirmed that it is definitely a real issue, that will happen frequently in practice. The evidence directly confirms this.
6. Filter out any issues with a score less than 80. Separate the remaining issues into two buckets:
   - **Code review bucket**: findings from agents #1–9, #11, and #12.
   - **Simplification bucket**: findings from agent #10.

   Every finding surviving this filter is by construction sub-MAJOR: this command never instructs any agent to emit a CRITICAL/MAJOR/BLOCKING verdict, so everything in either bucket is informational-or-fix-worthy but never merge-blocking on its own (the two-gate model's block path is reserved for a severity class this plugin does not produce). Both buckets — including an empty result — feed the JSON findings block in step 8; do not skip step 8 even when both buckets are empty, since the block must still be emitted (empty).
7. Use a Haiku agent to repeat the eligibility check from #1, to make sure that the pull request is still eligible for code review.
8. Finally, comment back on the pull request: compose the comment body with the Write tool, then post it with `gh pr comment <n> --body-file <path>`.

   **Posting discipline (MANDATORY — read before posting):**
   - **Compose the ENTIRE comment body with the Write tool at the fixed literal path `/tmp/code-review-comment.md`**, then post it once with `gh pr comment <n> --body-file /tmp/code-review-comment.md`. The Write tool and this exact `gh pr comment --body-file` form are pre-authorized and always work. This is the ONLY permitted posting mechanism: the body must never travel as a shell string argument, because backticks, `$(...)`, `$VAR`, and backslashes in review prose get shell-evaluated — mangled comments at best, command substitution at worst.
   - **FORBIDDEN posting mechanisms** — do not use any of these, they will mangle or shell-evaluate the body:
     - `--body` / `-b` string flags (`gh pr comment <n> --body "..."` in any quoting style);
     - shell-based file assembly (`echo`/`printf`/`cat` with heredocs or redirects to build the comment file) — the file is built by the Write tool only;
     - variable-interpolated or computed paths (`--body-file "$TMPDIR/..."`, `--body-file $(mktemp)`) — the path is the fixed literal `/tmp/code-review-comment.md`.
   - **Do NOT post probe/scratch comments.** No `test`, `PLACEHOLDER`, `ping`, "checking auth", or any other comment to verify that posting works or to check formatting. It does work. Compose the real comment in full via Write, then post it once. Probe comments leak onto the PR, get parsed by the downstream merge gate, and are pure noise.
   - **Post the review as EXACTLY ONE comment, always.** There is no second comment, ever, for any reason — the simplification section and the JSON findings block both live INSIDE this one `### Code review` comment (see template below), never in a follow-up comment, never posted "separately via API". This replaces the older two-comment design (`/tmp/code-review-simplification.md` no longer exists) precisely because agents kept collapsing to one comment anyway and silently dropping the simplification content — folding it in removes the ambiguity. Write the entire comment body to `/tmp/code-review-comment.md` in one Write call and post it in a single `gh pr comment --body-file` call.
   - **If a code permalink won't format**, do NOT retry by posting test comments or alternate fragments. Fall back to a plain `path/to/file.py:L120-L125` citation inside the one comment. A correctly-posted plain-path finding beats a perfectly-formatted permalink you posted three broken attempts to reach.
   - **If you are unsure whether you already posted**, run `gh pr view <n> --json comments` and check — do not post a probe to find out.

   Compose the single comment as follows, regardless of which buckets from step 6 are non-empty:

   a. **Code review bucket non-empty** → open with the `### Code review` header and the "Found N issues:" format (see template below). This is the merge-gate signal — downstream CI parses this exact header/line.
   b. **Code review bucket empty** → open with the `### Code review` header and "No issues found." (see template below).
   c. **Always** append the JSON findings block (step 8.1 below) after the numbered list / "No issues found." line, regardless of bucket contents.
   d. **Simplification bucket non-empty** → append a `### Simplification opportunities` section AFTER the JSON block, inside the same comment (see template below). This section is informational and does NOT block merge — the merge-gate parser only recognizes the `### Code review` header and the CRITICAL/MAJOR/BLOCKING severity/pass vocabulary; a `### Simplification opportunities` heading further down the same comment body is inert to it.
   e. **Simplification bucket empty** → omit the `### Simplification opportunities` section entirely (no empty-section placeholder).

   ### 8.1 JSON findings block (machine-parseable, informational, never gates)

   Immediately after the `### Code review` numbered list (or the "No issues found." line), embed one HTML-comment-wrapped JSON block listing every finding from BOTH buckets (Code review bucket + Simplification bucket) — this is a superset view for a downstream review-debt collector, distinct from the human-facing sections above. Emit it in **every** case, including when both buckets are empty (`"findings": []`).

   Schema (`schema_version: 1`):

   ```
   <!-- code-review-findings
   {
     "schema_version": 1,
     "findings": [
       {
         "severity": "MEDIUM",
         "rule": "diff-coherence",
         "file": "path/to/file.ts",
         "line": 42,
         "description": "one-line description of the finding"
       }
     ]
   }
   -->
   ```

   Field rules:
   - `severity` — derived from which bucket produced the finding, NOT a per-finding judgment call: every **Code review bucket** finding (agents #1–9, #11, #12) gets `"MEDIUM"`; every **Simplification bucket** finding (agent #10) gets `"INFO"`. This is a coarse, bucket-level severity — never CRITICAL/MAJOR/BLOCKING (this plugin does not produce that class) and never a finer-grained per-issue severity (out of scope for this contract; a future collector-side refinement can re-derive finer severity from `rule` if needed).
   - `rule` — a stable slug identifying which agent flagged the finding, one of: `guideline-compliance` (#1), `bug-scan` (#2), `git-blame-context` (#3), `prior-pr-comments` (#4), `logic-leak` (#5), `diff-coherence` (#6), `cross-device` (#7), `smoke-static-check` (#8), `structural-growth` (#9), `simplification-scout` (#10), `ac-conformance` (#11), `integration-tripwire` (#12). Use exactly these slugs so the downstream collector can key on them without fuzzy-matching prose.
   - `file` / `line` — same file path and line number cited in the human-readable finding directly above. If a finding spans a range, use the range's start line.
   - `description` — the same one-line description used in the numbered list / simplification entry above it (no need to duplicate the full permalink here — `file`/`line` already localize it).
   - When there are zero findings across both buckets, emit `"findings": []` — the block itself is still present, never omitted.

   **Degradation contract (documented behavior, AC6):** the block is generated by an LLM composing markdown, so a downstream collector MUST treat a missing block, an unparseable block (invalid JSON), or a block with an unrecognized `schema_version` as **zero findings for this PR**, not as an error to surface to the user — log/skip and fail closed on ambiguity, do not block on it. This block is informational-only in the same sense as the Simplification section: its absence or malformation must never affect the merge-gate verdict (step 5 of the two-gate model reads only the `### Code review` header and CRITICAL/MAJOR/BLOCKING vocabulary, never this block).

   When writing comments, keep in mind to:
   - Keep your output brief
   - Avoid emojis
   - Link and cite relevant code, files, and URLs

Examples of false positives, for steps 4 and 5:

- Pre-existing issues
- Something that looks like a bug but is not actually a bug
- Pedantic nitpicks that a senior engineer wouldn't call out
- Issues that a linter, typechecker, or compiler would catch (eg. missing or incorrect imports, type errors, broken tests, formatting issues, pedantic style issues like newlines). No need to run these build steps yourself -- it is safe to assume that they will be run separately as part of CI.
- General code quality issues (eg. lack of test coverage, general security issues, poor documentation), unless explicitly required in CLAUDE.md
- Issues that are called out in CLAUDE.md, but explicitly silenced in the code (eg. due to a lint ignore comment)
- Changes in functionality that are likely intentional or are directly related to the broader change
- Real issues, but on lines that the user did not modify in their pull request
- Diff-coherence false positives: a file renamed during the change (claim names old path, diff shows new path is the same edit), refactor-induced relocations of the same logic, or PR-body wording that is descriptive ("cleanup", "small fixes") rather than a concrete change-list — these are not fabrication. Only flag claimed-missing when a *specific* file/behavior/test is asserted and absent.
- Cross-device false positives: example values inside docs / fixtures / tests / `.example` files / commented-out illustration code, paths inside `if platform == "..."` branches that DO have a sibling fallback, deliberately-platform-specific scripts under a clearly-named directory (`scripts/windows/`, `tools/macos/`), and CI-runner-default usernames in workflow files (`runner`, `ubuntu` are GitHub-hosted defaults, not personal-machine leaks).
- Smoke / static-check false positives: parser errors caused by the file genuinely being a different language than its extension suggests (jinja2 with `.py` extension, ERB templates), files explicitly listed in lint-ignore configs, intentional partial scripts meant to be sourced (not run standalone). Only flag when the parser/compiler error message is concrete and specific to the diff.
- Logic-leak false positives: new arguments, hooks, extension points, or strategy/adapter slots that the existing module clearly was designed to accept (the module's docstring or existing API shape signals "extend me here"); changes localized to a clearly feature-specific module that already lives under a feature directory; logic placements that the relevant CLAUDE.md explicitly endorses; thin glue code added to a shared module to route to a feature module (routing is not leaking).
- Structural growth false positives: files that already crossed the 1k line threshold before this PR (pre-existing technical debt — not introduced by the change); lockfiles, snapshots, JSON fixtures, generated code, and vendored sources (the Haiku agent should already skip these, but if one slips through it is FP); legitimate large rewrites where the diff is a net deletion or near-zero net change despite touching many lines.
- Simplification false positives: opportunities whose realization would require touching more than 3 unrelated callers; simplifications that remove type safety, runtime validation, or error handling; simplifications where the longer form is clearer to a reader unfamiliar with the codebase; opportunities already covered by a code-review-bucket finding (don't double-flag the same chunk); style preferences that have no measurable simplicity gain.
- AC-conformance false positives: an AC item met via a different-but-equivalent implementation (the requirement is satisfied, just not the way you expected); a requirement covered by a helper the diff adds elsewhere or by an existing mechanism the PR wires into; AC items explicitly out of scope for a sliced PR ("part of #NNN", "slice N of M"); unstated requirements you inferred rather than the AC literally naming them (those are the issue author's spec gap, not a review finding); a missing linked issue on a PR the project allows to bypass issue-linking (`[no-issue]`, `refactor:` title, hotfix). Only flag **not-addressed** when the AC names a specific behavior/file/output genuinely absent from the diff, or **contradicted** when the diff does what the AC forbids.
- Integration false positives: a co-change obligation you derived yourself rather than one written in a guideline file's integration checklist; the obligated co-change actually being present elsewhere in the diff; rules satisfied by an existing call-site the PR doesn't need to touch; AC-completeness gaps (those belong to agent #11, not here); projects with no integration-checklist section (this agent should return nothing, not improvise rules).

Notes:

- Do not check build signal or attempt to build or typecheck the app. These will run separately, and are not relevant to your code review.
- Use `gh` to interact with Github (eg. to fetch a pull request), rather than web fetch. Posting is ONLY the single step-8 `gh pr comment --body-file` call — inline review comments require `gh api`, which is not in the allowed tools
- Make a todo list first
- You must cite and link each bug (eg. if referring to a CLAUDE.md, you must link it)
- For your final comment, follow the following format precisely (assuming for this example that you found 3 code-review-bucket issues and 2 simplification-bucket opportunities — everything below is ONE comment):

---

### Code review

Found 3 issues:

1. <brief description of bug> (CLAUDE.md says "<...>")

<link to file and line with full sha1 + line range for context, note that you MUST provide the full sha and not use bash here, eg. https://github.com/anthropics/claude-code/blob/1d54823877c4de72b2316a64032a54afc404e619/README.md#L13-L17>

2. <brief description of bug> (some/other/CLAUDE.md says "<...>")

<link to file and line with full sha1 + line range for context>

3. <brief description of bug> (bug due to <file and code snippet>)

<link to file and line with full sha1 + line range for context>

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": [
    {"severity": "MEDIUM", "rule": "guideline-compliance", "file": "src/auth.ts", "line": 67, "description": "<brief description of bug>"},
    {"severity": "MEDIUM", "rule": "bug-scan", "file": "src/utils.ts", "line": 23, "description": "<brief description of bug>"},
    {"severity": "MEDIUM", "rule": "diff-coherence", "file": "src/handler.ts", "line": 101, "description": "<brief description of bug>"},
    {"severity": "INFO", "rule": "simplification-scout", "file": "src/parser.ts", "line": 40, "description": "<one-line description of the simpler shape>"},
    {"severity": "INFO", "rule": "simplification-scout", "file": "src/format.ts", "line": 12, "description": "<one-line description of the simpler shape>"}
  ]
}
-->

### Simplification opportunities

Informational — does not block merge. The standard review gate parses only the `### Code review` header and CRITICAL/MAJOR/BLOCKING severity vocabulary; this section and the JSON block above are both inert to it.

1. <one-line description of the simpler shape> (in `<file>` lines L<start>-L<end>)

<link to file and line range with full sha1>

2. <one-line description of the simpler shape> (in `<file>` lines L<start>-L<end>)

<link to file and line range with full sha1>

🤖 Generated with [Claude Code](https://claude.ai/code)

<sub>- If this code review was useful, please react with 👍. Otherwise, react with 👎.</sub>

---

- Or, if you found no issues in either bucket (still ONE comment, JSON block still present with an empty array):

---

### Code review

No issues found. Checked for bugs and CLAUDE.md compliance.

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": []
}
-->

🤖 Generated with [Claude Code](https://claude.ai/code)

---

- Or, if the code review bucket is empty but the simplification bucket is non-empty (still ONE comment — "No issues found" plus the JSON block plus the simplification section, never a bare simplification-only comment):

---

### Code review

No issues found. Checked for bugs and CLAUDE.md compliance.

<!-- code-review-findings
{
  "schema_version": 1,
  "findings": [
    {"severity": "INFO", "rule": "simplification-scout", "file": "src/parser.ts", "line": 40, "description": "<one-line description of the simpler shape>"}
  ]
}
-->

### Simplification opportunities

Informational — does not block merge. The standard review gate parses only the `### Code review` header and CRITICAL/MAJOR/BLOCKING severity vocabulary; this section and the JSON block above are both inert to it.

1. <one-line description of the simpler shape> (in `<file>` lines L<start>-L<end>)

<link to file and line range with full sha1>

🤖 Generated with [Claude Code](https://claude.ai/code)

---

- When linking to code, follow the following format precisely, otherwise the Markdown preview won't render correctly: https://github.com/anthropics/claude-cli-internal/blob/c21d3c10bc8e898b7ac1a2d745bdc9bc4e423afe/package.json#L10-L15
  - Requires full git sha
  - You must provide the full sha. Commands like `https://github.com/owner/repo/blob/$(git rev-parse HEAD)/foo/bar` will not work, since your comment will be directly rendered in Markdown.
  - Repo name must match the repo you're code reviewing
  - # sign after the file name
  - Line range format is L[start]-L[end]
  - Provide at least 1 line of context before and after, centered on the line you are commenting about (eg. if you are commenting about lines 5-6, you should link to `L4-7`)