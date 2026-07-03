import { run, claudeCode } from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

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
// (prompt.md §"Memory provenance"). Format: <run-name>-<UTC-yyyymmdd-hhmmss>.
// Stays opaque (no secrets) so it's safe to embed in memory rows and PR bodies.
const runId =
  process.env.SANDCASTLE_RUN_ID ??
  `jarvis-worker-${new Date().toISOString().replace(/[-:T.Z]/g, "").slice(0, 14)}`;

// Slice 4 (#541): when the PowerShell watchdog invokes us, it sets
// SANDCASTLE_RESULT_FILE so we dump the RunResult JSON for it to parse
// (commits, branch, iterations, usage). Direct stdout capture is too noisy
// — sandcastle interleaves agent output with orchestrator logs.
const resultFile = process.env.SANDCASTLE_RESULT_FILE;
// `??` does not coalesce empty string -- guard against blank env vars
// silently producing maxIterations=0 (zero-iteration silent run).
const maxIterations = Math.max(1, Number(process.env.SANDCASTLE_MAX_ITERATIONS) || 1);

const result = await run({
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
  branchStrategy: { type: "merge-to-head" },
  hooks: {
    sandbox: {
      onSandboxReady: [
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
        { command: "mkdir -p $(git rev-parse --git-path info)" },
        { command: "echo /.mcp.json >> $(git rev-parse --git-path info/exclude)" },
        { command: "cp /opt/sandcastle/container-mcp.json .mcp.json" },
      ],
    },
  },
});

if (resultFile) {
  await mkdir(dirname(resultFile), { recursive: true });
  await writeFile(
    resultFile,
    JSON.stringify(
      {
        runId,
        branch: result.branch,
        commits: result.commits,
        completionSignal: result.completionSignal,
        logFilePath: result.logFilePath,
        preservedWorktreePath: result.preservedWorktreePath,
        iterations:
          result.iterations?.map((it) => ({
            sessionId: it.sessionId,
            usage: it.usage,
          })) ?? [],
      },
      null,
      2,
    ),
    "utf8",
  );
}
