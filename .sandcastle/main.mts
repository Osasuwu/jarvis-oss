import { run, claudeCode } from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { execFileSync } from "node:child_process";
import {
  buildCompletionPayload,
  buildResultFile,
  classifyCompletion,
  completionSeverity,
  emitCompletionEvent,
  readLogTail,
  scrubCompletionPayload,
  scrubText,
  writeResultFile,
} from "./completion.mts";

// Jarvis sandcastle entry — slices 1 + 2 of epic #534. Manual smoke loop on Main PC.
// Run: npm run sandcastle  (or: npx tsx .sandcastle/main.mts)
//
// Prereqs + decisions: see .sandcastle/README.md and decisions 894ac658,
// 436f9549, 228a2d9b, 0c3017c6 referenced from epic #534. Watchdog + schedule
// land in slice 4 — this file stays config-only.

// Agent model + auth. Slice 5 (#543) introduces multi-tier escalation: the PS1
// watchdog re-invokes us with SANDCASTLE_AGENT_{MODEL,BASE_URL,AUTH_TOKEN}
// overridden when retrying on a smaller Ollama model (Tier 1) or a remote
// DeepSeek/Claude API endpoint (Tier 2). Issue #972 adds a third auth path —
// the Claude subscription Agent SDK credit. Two mutually exclusive modes
// (SANDCASTLE_AGENT_AUTH_MODE):
//   "endpoint"     — ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN point Claude Code
//                    at an Anthropic-compatible endpoint: local Ollama (Tier
//                    0/1) or remote DeepSeek/Claude API (Tier 2). Pay-per-token
//                    or local. The slice-1/2 behavior.
//   "subscription" — CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`)
//                    authenticates Claude Code with the Max subscription, so
//                    headless usage draws the monthly Agent SDK credit (API
//                    rates, hard-stop on exhaustion) instead of the metered API.
//                    ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
//                    and the CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY} provider
//                    switches MUST be unset here — they all resolve auth before
//                    CLAUDE_CODE_OAUTH_TOKEN in Claude Code's order and would
//                    silently route to PAID pay-per-token billing or a cloud
//                    provider (real money). Enforced by the denylist guard below.
//                    The two modes never share env keys.
//
// Default: honor an explicit watchdog/operator SANDCASTLE_AGENT_AUTH_MODE; else
// "endpoint" whenever the watchdog is driving an endpoint tier
// (SANDCASTLE_AGENT_BASE_URL set) or no OAuth token is present; "subscription"
// only for an un-driven manual run that carries CLAUDE_CODE_OAUTH_TOKEN. This
// keeps the nightly Ollama/DeepSeek tiers byte-for-byte unchanged even if a
// token sits in .env, while letting a manual `npm run sandcastle` exercise the
// credit.
const oauthToken = process.env.CLAUDE_CODE_OAUTH_TOKEN;
// Treat a blank value (`SANDCASTLE_AGENT_AUTH_MODE=` in a misconfigured .env)
// the same as unset: `??` only coalesces null/undefined, so without the
// trim()+`||` a blank would skip auto-detection and throw the opaque `got ""`.
// `||` lets "" fall through to the BaseUrl/OAuth auto-detect below instead.
const explicitAuthMode = process.env.SANDCASTLE_AGENT_AUTH_MODE?.trim();
const authMode =
  explicitAuthMode ||
  (process.env.SANDCASTLE_AGENT_BASE_URL
    ? "endpoint"
    : oauthToken
      ? "subscription"
      : "endpoint");
if (authMode !== "endpoint" && authMode !== "subscription") {
  throw new Error(
    `SANDCASTLE_AGENT_AUTH_MODE must be "endpoint" or "subscription", got "${authMode}".`,
  );
}
const subscription = authMode === "subscription";

const agentModel =
  process.env.SANDCASTLE_AGENT_MODEL ??
  (subscription
    ? // Regular Opus 4.8 — NOT the 1M-context variant (extra billing per owner
      // policy; AFK slices never need 1M).
      "claude-opus-4-8"
    : (process.env.OLLAMA_MODEL ?? "qwen3-coder:30b"));

// Thinking effort. Only meaningful on the subscription/Claude path — the
// endpoint path may front Ollama, which 400s on an unknown --effort flag — so
// it stays undefined for "endpoint" to preserve current behavior exactly.
const EFFORT_LEVELS = ["low", "medium", "high", "max"] as const;
type Effort = (typeof EFFORT_LEVELS)[number];
const agentEffort: Effort | undefined = subscription
  ? (() => {
      const e = process.env.SANDCASTLE_AGENT_EFFORT ?? "medium";
      if (!EFFORT_LEVELS.includes(e as Effort)) {
        throw new Error(
          `SANDCASTLE_AGENT_EFFORT must be one of ${EFFORT_LEVELS.join(", ")}, got "${e}".`,
        );
      }
      return e as Effort;
    })()
  : undefined;
if (!subscription && process.env.SANDCASTLE_AGENT_EFFORT) {
  // Endpoint mode may front Ollama, which 400s on --effort, so the flag is
  // dropped. Warn rather than fail silently — an operator who set it deserves
  // to know it had no effect.
  console.warn(
    "[sandcastle] SANDCASTLE_AGENT_EFFORT is set but ignored in endpoint mode " +
      "(only the subscription/Claude path supports --effort).",
  );
}

// Endpoint-mode connection. Unused in subscription mode, but still computed
// unconditionally so one startup code path covers both modes (the Ollama env
// vars are only *consumed* on the endpoint branch below). Tier 0/1 use the
// literal "ollama" auth token (Ollama's native Anthropic endpoint ignores it);
// Tier 2 carries a real API key (DeepSeek / Claude API).
const agentBaseUrl =
  process.env.SANDCASTLE_AGENT_BASE_URL ??
  process.env.OLLAMA_BASE_URL ??
  "http://host.docker.internal:11434";
const agentAuthToken = process.env.SANDCASTLE_AGENT_AUTH_TOKEN ?? "ollama";
if (subscription && !oauthToken) {
  // Unreachable via auto-detection (it only picks "subscription" when a token is
  // present) but reachable via the explicit SANDCASTLE_AGENT_AUTH_MODE=subscription
  // override — this guards that case so we never fall through to unauthenticated
  // or paid-API billing.
  throw new Error(
    "SANDCASTLE_AGENT_AUTH_MODE=subscription requires CLAUDE_CODE_OAUTH_TOKEN " +
      "(generate once on the host with `claude setup-token`). Refusing to run — " +
      "without it Claude Code would fall back to paid API billing or fail auth.",
  );
}
// The guard above guarantees oauthToken is set whenever `subscription` is true.
// TS cannot carry that narrowing across the intervening for-loop and the authEnv
// ternary, so pin it to a non-null string here rather than asserting `!` at the
// distant use site (where the safety is non-obvious).
const oauthTokenStr: string = subscription ? oauthToken! : "";
if (subscription) {
  // Real-money guard: withholding these from authEnv (below) is not enough.
  // If any are already set on the HOST process (leftover endpoint-mode run, a
  // sourced .env, the commented Tier-2 block in .env.example), an ambient
  // passthrough can leak them into the container, where they OUTRANK
  // CLAUDE_CODE_OAUTH_TOKEN in Claude Code's auth order and silently route the
  // run to paid pay-per-token billing (or a cloud-provider account). Refuse to
  // start instead.
  //
  // Denylist = every var that resolves auth BEFORE step 5 (CLAUDE_CODE_OAUTH_TOKEN)
  // in Claude Code's precedence chain (docs: code.claude.com/docs/en/authentication,
  // /env-vars, /third-party-integrations — verified 2026-06-18):
  //   1. CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY} — flip auth to a cloud provider,
  //      bypassing the subscription token entirely (and billing AWS/GCP/Azure).
  //   2. ANTHROPIC_AUTH_TOKEN — gateway/proxy bearer token.
  //   3. ANTHROPIC_API_KEY — Console pay-per-token key (highest billing risk).
  //   + ANTHROPIC_BASE_URL — redirects all calls to a custom endpoint; not strictly
  //      an auth step, but a set base-url is a misconfig that must not ride along.
  // NOT included on purpose: CLAUDE_API_KEY / CLAUDE_BASE_URL are NOT Claude Code
  // env vars (the ANTHROPIC_-prefixed ones are the real names) — Claude Code's auth
  // resolution never reads them, so blocking them would be cargo-cult. Provider
  // sub-keys (AWS_*, ANTHROPIC_VERTEX_*, ANTHROPIC_FOUNDRY_*) only matter once a
  // CLAUDE_CODE_USE_* flag is set, which we already block, so the flag is the
  // sufficient gate.
  const billingOverrideKeys = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
  ];
  for (const key of billingOverrideKeys) {
    if (process.env[key]) {
      throw new Error(
        `SANDCASTLE_AGENT_AUTH_MODE=subscription: ${key} is set in the host ` +
          "environment — unset it first. It takes auth precedence over " +
          "CLAUDE_CODE_OAUTH_TOKEN and would silently route this run to paid " +
          "pay-per-token billing or a cloud provider (real money).",
      );
    }
  }
}

// Auth env injected into the container — exactly one of the two paths, so the
// ANTHROPIC_* vars are absent on the subscription path (precedence guard above).
const authEnv: Record<string, string> = subscription
  ? { CLAUDE_CODE_OAUTH_TOKEN: oauthTokenStr }
  : { ANTHROPIC_BASE_URL: agentBaseUrl, ANTHROPIC_AUTH_TOKEN: agentAuthToken };

// Token never printed; mode/model/effort are safe and useful for "how much did
// it eat" run accounting.
console.error(
  `[sandcastle] auth=${authMode} model=${agentModel}` +
    (agentEffort ? ` effort=${agentEffort}` : "") +
    (subscription
      ? " billing=max-agent-sdk-credit"
      : ` endpoint=${agentBaseUrl}`),
);
// When the watchdog retries on the same issue (escalation chain, AC #3),
// it sets SANDCASTLE_TARGET_ISSUE so prompt.md can pin the agent to that
// issue instead of picking afresh from the queue.
const targetIssue = process.env.SANDCASTLE_TARGET_ISSUE ?? "";

// Rework mode: when SANDCASTLE_TARGET_PR=<N> is set, the container runs
// /rework on the existing PR branch instead of a fresh pick+implement cycle.
// The env var is forwarded into the container; prompt.md branches on its
// presence (see issue #637, decision 69b7eddb).
const targetPr = process.env.SANDCASTLE_TARGET_PR ?? "";
const ghToken = process.env.GH_TOKEN;
if (!ghToken) {
  throw new Error(
    "GH_TOKEN is required (sandcastle agent claims issues + opens PRs via gh). " +
      "Set it in .sandcastle/.env — see .sandcastle/.env.example.",
  );
}

// Memory MCP bridge env (slice 2, issue #540). Forwarded into the container so
// the in-container memory MCP server (/opt/mcp-memory/server.py) can reach
// Supabase. SUPABASE_KEY MUST be the anon key — service-role is banned per
// decision 228a2d9b. VOYAGE_API_KEY is optional (recall degrades to keyword).
const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_KEY;
const voyageKey = process.env.VOYAGE_API_KEY ?? "";
if (!supabaseUrl || !supabaseKey) {
  throw new Error(
    "SUPABASE_URL and SUPABASE_KEY are required for the memory MCP bridge. " +
      "Set them in .sandcastle/.env — anon key only, never service-role. " +
      "See .sandcastle/.env.example for details.",
  );
}

// Stable per-run identifier consumed by the agent's source_provenance tags
// (prompt.md §"Memory provenance") AND, as of #1118, the fresh-path branch
// pin below. Format: <run-name>-<UTC-yyyymmdd-hhmmss>. Stays opaque (no
// secrets) so it's safe to embed in memory rows, PR bodies, and branch names.
//
// AC4 (#1118) — a malformed pin must never reach the supervisor's run() call.
// Validated here, BEFORE it's used to build a branch name, because an
// operator-overridden SANDCASTLE_RUN_ID feeds directly into `task/<runId>`.
const RUN_ID_PATTERN = /^[a-zA-Z0-9_.-]+$/;
if (
  process.env.SANDCASTLE_RUN_ID &&
  !RUN_ID_PATTERN.test(process.env.SANDCASTLE_RUN_ID)
) {
  throw new Error(
    `SANDCASTLE_RUN_ID must match ${RUN_ID_PATTERN} (it is used verbatim in ` +
      `the git branch name "task/<runId>") — got ` +
      `${JSON.stringify(process.env.SANDCASTLE_RUN_ID)}.`,
  );
}
const runId =
  process.env.SANDCASTLE_RUN_ID ??
  `jarvis-worker-${new Date().toISOString().replace(/[-:T.Z]/g, "").slice(0, 14)}`;

// AC4 — same gate for the rework pin: SANDCASTLE_TARGET_PR must be a bare
// positive integer before it's shelled out to `gh pr view` below.
if (targetPr && !/^\d+$/.test(targetPr)) {
  throw new Error(
    `SANDCASTLE_TARGET_PR must be a positive integer PR number — got ` +
      `${JSON.stringify(targetPr)}.`,
  );
}

// AC1/AC2 (#1118) — branch placement enforced by construction across the
// whole spawn path, supervisor-side (CONTEXT.md "AFK spawn substrate": the
// native sandcastle branch pin is belt-only; this supervisor is the
// authority — upstream Windows worktree bugs, mattpocock/sandcastle#855/#849,
// make library-level pinning alone untrustworthy on this host). Two cases:
//
//   - Rework (SANDCASTLE_TARGET_PR set): pin to the PR's OWN branch, fetched
//     BEFORE run() so a malformed/closed/deleted PR fails loud pre-spawn
//     rather than inside the container. No new branch is ever created.
//   - Fresh: pin to `task/<runId>`, reusing the run-id mechanism that
//     already exists for memory provenance. A watchdog re-drive that passes
//     the same SANDCASTLE_RUN_ID reuses this exact branch — that IS the
//     "re-drive reuses the pinned root branch" behavior (AC2), with no
//     separate root-task-id concept needed.
let pinnedBranch: string;
if (targetPr) {
  pinnedBranch = execFileSync(
    "gh",
    ["pr", "view", targetPr, "--json", "headRefName", "--jq", ".headRefName"],
    { encoding: "utf8" },
  ).trim();
  if (!pinnedBranch) {
    throw new Error(
      `SANDCASTLE_TARGET_PR=${targetPr}: gh pr view returned no headRefName ` +
        "(PR closed, branch deleted, or lookup failed) — refusing to spawn " +
        "with an unresolved rework pin.",
    );
  }
} else {
  pinnedBranch = `task/${runId}`;
}

// Slice 4 (#541): when the PowerShell watchdog invokes us, it sets
// SANDCASTLE_RESULT_FILE so we dump the RunResult JSON for it to parse
// (commits, branch, iterations, usage). Direct stdout capture is too noisy
// — sandcastle interleaves agent output with orchestrator logs. S2 (#1120)
// makes the result file UNCONDITIONAL: when the env var is absent we still
// write to a per-run path under .sandcastle/runtime/ (gitignored), so every
// run — success, agent fault, or infra fault — leaves a durable artifact.
const resultFile =
  process.env.SANDCASTLE_RESULT_FILE ?? `.sandcastle/runtime/${runId}/result.json`;

// S2 completion contract (#1120): executor-set provenance for the completion
// event. Emission is gated on SANDCASTLE_TASK_ID — the PowerShell watchdog
// sets none of these, so its runs only write the result file (that lane is
// covered by the watchdog's own outcome recording).
const taskId = process.env.SANDCASTLE_TASK_ID ?? "";
const lineageKey = process.env.SANDCASTLE_LINEAGE_KEY ?? "";
const attemptRaw = process.env.SANDCASTLE_ATTEMPT;
const attempt = attemptRaw !== undefined && attemptRaw !== "" ? Number(attemptRaw) : null;
const tier = process.env.SANDCASTLE_TIER ?? null;
const goal = process.env.SANDCASTLE_GOAL ?? "";
// Repo the container is working in — the events table's `repo` column.
// Defaults to the primary repo; the executor overrides for redrobot.
const repo = process.env.SANDCASTLE_REPO ?? "your-username/your-repo";

// Secrets the fail-loud scrubber must strip from failure text before anything
// is persisted. The agent is instructed never to print them, but a crash tail
// is untrusted input (Protect-LogTail precedent, Run-Sandcastle.ps1).
const knownSecrets = [supabaseUrl, supabaseKey, ghToken, voyageKey, oauthTokenStr].filter(
  Boolean,
) as string[];
// AC3 — pinned runs are single-iteration. Branch placement is now pinned on
// every run (fresh or rework; see pinnedBranch above), so this always
// overrides any operator-set SANDCASTLE_MAX_ITERATIONS rather than reading
// it. resumeSession (unused here) is documented as incompatible with
// maxIterations > 1 anyway, so 1 is also the library's own safe default.
if (
  process.env.SANDCASTLE_MAX_ITERATIONS &&
  Number(process.env.SANDCASTLE_MAX_ITERATIONS) !== 1
) {
  console.warn(
    `[sandcastle] SANDCASTLE_MAX_ITERATIONS=${process.env.SANDCASTLE_MAX_ITERATIONS} ` +
      "is set but ignored — pinned runs are always single-iteration (AC3, #1118).",
  );
}
const maxIterations = 1;

// AC6 (#1118) — mattpocock/sandcastle#868 reports the docker sandbox freezing
// Claude Code startup on Docker Engine >=29. That freeze is HEAD-mode-specific.
// Our config runs WORKTREE mode (branchStrategy { type: "branch" }), whose git
// mounts land differently (main `.git` -> /.sandcastle-parent-git, a gitdir-
// override file -> /home/agent/workspace/.git) and do NOT trip the duplicate-
// inode bug. Verified empirically on Docker Engine 29.5.2: the container builds
// its mounts and Claude Code boots cleanly (boot smoke, session 8c3bb91f,
// 2026-07-08). So we WARN rather than block — the original hard `throw` here was
// a false blocker that grounded the whole AFK loop on this host. If a future
// sandcastle release switches us to head mode, or Claude Code startup begins
// freezing inside the container on >=29, this is the first place to look.
// `docker version --format` reads the server (Engine) version — what sandcastle
// actually talks to — not the CLI client. A docker-down error still throws here
// (execFileSync), which is correct: docker is required regardless.
const dockerServerVersion = execFileSync(
  "docker",
  ["version", "--format", "{{.Server.Version}}"],
  { encoding: "utf8" },
).trim();
const dockerMajor = Number(dockerServerVersion.split(".")[0]);
if (Number.isFinite(dockerMajor) && dockerMajor >= 29) {
  console.warn(
    `[sandcastle] Docker Engine ${dockerServerVersion} (>=29) detected. ` +
      "Worktree-mode is verified OK on this host (mattpocock/sandcastle#868 is " +
      "head-mode-specific); if Claude Code startup freezes inside the container, " +
      "that bug is the first suspect.",
  );
}

// ---------------------------------------------------------------------------
// Run + completion durability (S2, #1120)
// ---------------------------------------------------------------------------
// Every path below — success, agent fault, infra fault, crash — ends with a
// result file written and, when the executor set SANDCASTLE_TASK_ID, a
// completion event emitted. A thrown run re-raises AFTER both are durable so
// the process still exits non-zero (the watchdog keys off the exit code).
let runError: unknown = null;
let result: {
  commits: string[];
  branch?: string;
  completionSignal?: boolean;
  logFilePath?: string;
  preservedWorktreePath?: string;
  iterations?: Array<{ sessionId: string; usage?: unknown }>;
} | null = null;
let pinnedBranchExists = false;

try {
  result = await run({
    name: "jarvis-worker",
    sandbox: docker({
      imageName: "sandcastle:jarvis",
      env: {
        // Auth path (issue #972): exactly one of
        //   endpoint mode     → ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN (Ollama
        //                       Tier 0/1 via the native Anthropic endpoint, or
        //                       remote DeepSeek/Claude API Tier 2, slice 5 #543), or
        //   subscription mode → CLAUDE_CODE_OAUTH_TOKEN (Max Agent SDK credit).
        // ANTHROPIC_* and CLAUDE_CODE_OAUTH_TOKEN are never set together — the
        // former take precedence in Claude Code auth order and would route the
        // subscription path to paid pay-per-token billing.
        ...authEnv,
        // Forward host-side gh credentials so the agent can claim issues + open PRs.
        GH_TOKEN: ghToken,
        // Memory MCP bridge — Claude Code expands ${...} in the project-scope
        // .mcp.json (copied from /opt/sandcastle/container-mcp.json by the
        // onSandboxReady hook below) from these container env vars.
        SUPABASE_URL: supabaseUrl,
        SUPABASE_KEY: supabaseKey,
        VOYAGE_API_KEY: voyageKey,
        // Per-run id for the agent's source_provenance tags. See prompt.md.
        SANDCASTLE_RUN_ID: runId,
        // Forced-target issue for slice-5 escalation retries. Empty string =
        // free pick from queue (default behavior).
        SANDCASTLE_TARGET_ISSUE: targetIssue,
        // Rework mode: when non-empty, the container branches into rework path
        // on this PR number instead of the fresh pick+implement cycle (#637).
        SANDCASTLE_TARGET_PR: targetPr,
      },
    }),
    agent: claudeCode(agentModel, agentEffort ? { effort: agentEffort } : undefined),
    promptFile: "./.sandcastle/prompt.md",
    maxIterations,
    // AC1/AC2 — was the hardcoded { type: "merge-to-head" }. Every run (fresh
    // or rework) now lands its commits on pinnedBranch by construction; for
    // rework this is the PR's existing branch (`gh pr view`-fetched above), so
    // no new branch is ever created for that path.
    branchStrategy: { type: "branch", branch: pinnedBranch },
    hooks: {
      sandbox: {
        onSandboxReady: [
          // AC7 — sandcastle runs all onSandboxReady hooks CONCURRENTLY
          // (Effect.all, concurrency "unbounded" — verified in package source;
          // see CONTEXT.md "AFK spawn substrate"), so order-dependent setup
          // must be a single chained command, never separate hook objects —
          // three sibling hooks here previously raced on which ran first.
          //
          // Sandcastle's own SandboxLifecycle already propagates host git
          // user.name/user.email via `git config --global` before user hooks
          // run, so explicit overrides here are redundant. Repo-local
          // `git config` (without --global) would write to the worktree's
          // parent .git/config which on Windows bind-mounts races on the
          // .lock file (#607 v2 / Workshop PC4 repro 2026-05-13).
          // Override the worktree's .mcp.json with the container-scoped version
          // (memory MCP only). The host .mcp.json registers many host-only
          // servers that would fail inside the sterile container. Sandcastle
          // uses copy-on-write worktrees so this never touches the host repo.
          // Adding the path to info/exclude first ensures `git add -A` inside
          // the agent loop cannot accidentally stage the override.
          //
          // `.git` in a worktree is a *file* (gitdir pointer), not a directory,
          // so `>> .git/info/exclude` opens via the shell and fails with ENOTDIR.
          // `git rev-parse --git-path info/exclude` resolves the actual shared
          // info/exclude path inside the parent .git directory (#607).
          {
            command:
              "mkdir -p $(git rev-parse --git-path info) && " +
              "echo /.mcp.json >> $(git rev-parse --git-path info/exclude) && " +
              "cp /opt/sandcastle/container-mcp.json .mcp.json",
          },
        ],
      },
    },
  });

  // AC1 — supervisor is the enforcement authority, not the library or the
  // agent (CONTEXT.md "AFK spawn substrate"): the native branchStrategy pin
  // above is the belt, this is the suspenders. PR head ref must equal
  // pinnedBranch regardless of what the agent did inside the container.
  //
  // Zero commits classifies as infra fault, not a failed agent attempt — no
  // push, no PR, no tier escalation (same CONTEXT.md section). A missing
  // local branch ref is the same signal: the library failed to materialize
  // the pin on the host side.
  pinnedBranchExists = (() => {
    try {
      execFileSync("git", ["rev-parse", "--verify", `refs/heads/${pinnedBranch}`], {
        stdio: "ignore",
      });
      return true;
    } catch {
      return false;
    }
  })();

  if (result.commits.length === 0 || !pinnedBranchExists) {
    console.error(
      `[sandcastle] 0 commits or missing local branch ${pinnedBranch} — ` +
        "classifying as infra fault, skipping push/PR (no tier escalation).",
    );
  } else {
    execFileSync(
      "git",
      ["push", "origin", `${pinnedBranch}:refs/heads/${pinnedBranch}`],
      { stdio: "inherit" },
    );
    if (targetPr) {
      // Rework — the PR already exists on this branch; the push above is the
      // whole story. No new branch, no new PR (AC2).
      console.error(
        `[sandcastle] pushed ${result.commits.length} commit(s) to existing ` +
          `PR #${targetPr} (${pinnedBranch}).`,
      );
    } else {
      // `--jq '.[0].number // empty'`: on an empty array `.[0].number` is jq
      // `null`, which gh prints as the literal string "null" — truthy in JS and
      // would falsely read as "PR already exists", silently suppressing
      // `gh pr create` on every fresh run (the exact bug this PR exists to
      // close). `// empty` makes the null case emit nothing instead. Belt-and-
      // suspenders: we STILL require a positive integer below, so a future jq
      // regression can't re-open the hole.
      const existingPrRaw = execFileSync(
        "gh",
        ["pr", "list", "--head", pinnedBranch, "--json", "number", "--jq", ".[0].number // empty"],
        { encoding: "utf8" },
      ).trim();
      const existingPrNum = Number(existingPrRaw);
      const existingPr =
        Number.isInteger(existingPrNum) && existingPrNum > 0 ? existingPrNum : null;
      if (existingPr) {
        console.error(
          `[sandcastle] PR #${existingPr} already open for ${pinnedBranch} — ` +
            "not creating a duplicate (re-drive on the same run id).",
        );
      } else {
        execFileSync("gh", ["pr", "create", "--head", pinnedBranch, "--fill"], {
          stdio: "inherit",
        });
        // Belt-and-suspenders (this PR's thesis: the supervisor is the
        // enforcement authority, not the agent). `--fill` derives the body
        // from the commit message; prompt.md step 6 mandates `Closes #<N>`
        // there, but that relies on agent compliance. If the closing keyword
        // is missing, the merged PR silently won't auto-close its issue (the
        // #948 failure mode). We can't inject it here — the supervisor doesn't
        // know which issue a free-pick fresh run claimed — but we can make the
        // omission LOUD instead of silent so the orchestrator catches it at
        // review time rather than after a stale issue accumulates.
        // This runs AFTER the PR is created — the run has already succeeded.
        // Wrap in try/catch so a transient `gh pr view` failure (API hiccup,
        // rate limit, read-after-write replication lag right after `gh pr
        // create`) degrades to its own warning instead of throwing past the
        // resultFile-write at the end of this script, which would surface a
        // successful run to the watchdog as a hard infra fault and risk a
        // spurious tier-escalation retry. The check is advisory — it must not
        // be able to crash a run that already committed + pushed + opened a PR.
        try {
          const createdBody = execFileSync(
            "gh",
            ["pr", "view", pinnedBranch, "--json", "body", "--jq", ".body"],
            { encoding: "utf8" },
          );
          if (!/\b(clos|fix|resolv)(e|es|ed)?\s+#\d+/i.test(createdBody)) {
            console.error(
              `[sandcastle] WARNING: PR for ${pinnedBranch} has no ` +
                "Closes/Fixes/Resolves #<N> keyword in its body — merging it will " +
                "NOT auto-close the issue (#948 failure mode). The agent's commit " +
                "message dropped the closing keyword; flag for the orchestrator.",
            );
          }
        } catch (err) {
          console.error(
            `[sandcastle] WARNING: could not verify Closes #<N> keyword for ` +
              `${pinnedBranch} — \`gh pr view\` failed: ${
                err instanceof Error ? err.message : String(err)
              }. PR creation itself succeeded; skipping the advisory check.`,
          );
        }
      }
    }
  }
} catch (err) {
  runError = err;
}

// ---- classification + PR-visibility verification ----
const runCompleted = runError === null;
const errorText =
  runError instanceof Error ? `${runError.message}\n${runError.stack ?? ""}` : String(runError ?? "");

const logTail = await readLogTail(result?.logFilePath, 12);

const classification = classifyCompletion({
  runCompleted,
  // The supervisor never observes the container's own exit code (a throw is
  // the crash signal); text signatures carry OOM/billing detection instead.
  exitCode: null,
  commitsCount: result?.commits.length ?? 0,
  pinnedBranchExists,
  logTail,
  errorText,
});

// PR-visibility verification — the completion event is emitted only after a
// verified PR reference (or an explicit false). A gh failure here is a parse
// fault → pr_evidence=null, the one case the contract reserves it for.
let pr: number | false = false;
let prEvidence: boolean | null = false;
if (runCompleted && result && result.commits.length > 0 && pinnedBranchExists) {
  try {
    const raw = execFileSync(
      "gh",
      ["pr", "list", "--head", pinnedBranch, "--json", "number", "--jq", ".[0].number // empty"],
      { encoding: "utf8" },
    ).trim();
    const num = Number(raw);
    pr = Number.isInteger(num) && num > 0 ? num : false;
    prEvidence = pr !== false;
  } catch {
    prEvidence = null; // parse fault — could not verify PR visibility
  }
}

const failureReason = scrubText(
  `${errorText}${logTail ? `\n${logTail}` : ""}`,
  knownSecrets,
).slice(0, 2000);

// ---- unconditional result file (all three outcomes) ----
const resultData = buildResultFile({
  runId,
  taskId: taskId || null,
  lineageKey: lineageKey || null,
  attempt,
  tier,
  goal,
  outcome: classification.outcome,
  failureClass: classification.failureClass,
  exit: classification.exit,
  branch: result?.branch ?? pinnedBranch,
  commits: result?.commits ?? [],
  pr,
  prEvidence,
  failureReason,
  completionSignal: result?.completionSignal ?? false,
  logFilePath: result?.logFilePath ?? null,
  preservedWorktreePath: result?.preservedWorktreePath ?? null,
  iterations:
    result?.iterations?.map((it) => ({
      sessionId: it.sessionId,
      usage: it.usage,
    })) ?? [],
});
await writeResultFile(resultFile, resultData);

// ---- idempotent completion event (gated on executor-set task id) ----
if (taskId) {
  const eventType = classification.eventType;
  const scrubbed = scrubCompletionPayload(buildCompletionPayload(resultData), knownSecrets);
  if (!scrubbed.safe) {
    // Fail-loud (#1092): never emit an unscrubbed secret into the events
    // inbox. The result file is already durable — the S4 sweeper is the
    // backstop for a missing event.
    console.error(
      "[sandcastle] REFUSING completion event: scrubber found a residual secret " +
        `value in the payload for task ${taskId} (fail-loud, #1092). ` +
        "The result file was still written.",
    );
  } else {
    const title =
      eventType === "task_done"
        ? `Sandcastle task ${taskId} completed on ${pinnedBranch}`
        : `Sandcastle task ${taskId} ${classification.outcome} (` +
          `${classification.failureClass ?? "unclassified"})`;
    try {
      await emitCompletionEvent({
        supabaseUrl,
        supabaseKey,
        repo,
        eventType,
        title,
        severity: completionSeverity(eventType, prEvidence),
        payload: scrubbed.scrubbed,
        dedupKey: `${eventType}:${taskId}:a${attempt ?? 0}`,
      });
    } catch (err) {
      // The result file is already durable — the S4 sweeper re-emits from it
      // (idempotent dedup key). A failed emit must not crash a run that
      // otherwise succeeded (the watchdog would misread a non-zero exit).
      console.error(
        `[sandcastle] WARNING: completion event emission failed for task ${taskId} ` +
          `(result file still durable): ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }
}

if (runError) {
  // Re-throw AFTER the result file + event are durable — the watchdog relies
  // on a non-zero exit to detect the crash.
  throw runError;
}
