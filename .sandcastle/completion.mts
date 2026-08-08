// Sandcastle completion durability (S2, #1120).
//
// Ports the watchdog's failure classifier + fail-loud scrubber
// (scripts/sandcastle/Run-Sandcastle.ps1: Test-IsOOM / Test-IsProviderBilling /
// Get-SandcastleErrorClass / Protect-LogTail) into the supervisor so a run can
// close itself: an unconditional result file plus an idempotent
// `task_done`/`task_failed` completion event. Decisions: be3679dd
// (event-driven completion), aa4959d8 (0.12.0), 3c0f2953 (AC lock); research
// 74ce63b5.
//
// The result-file contract is pinned by the shared fixture
// tests/fixtures/sandcastle-result.json on BOTH sides: the TS writer here and
// the Python reader agents/sandcastle_result.py. The one-sided check script is
// .sandcastle/check-result-contract.mts (node type-stripping, no test runner).
//
// Emission is gated on a task id being present (executor-set
// SANDCASTLE_TASK_ID); the PowerShell watchdog does not set one, so
// watchdog-driven runs write the result file but never emit — that lane is
// covered by the watchdog's own outcome recording.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

// ---------------------------------------------------------------------------
// Result-file contract (schemaVersion=2)
// ---------------------------------------------------------------------------

export const RESULT_FILE_SCHEMA_VERSION = 2;

// Required camelCase fields of a schemaVersion=2 result file. The watchdog
// (Run-Sandcastle.ps1 Invoke-Sandcastle) keeps reading the original seven;
// the new fields carry the completion contract for the orchestrator-side
// reader and the S4 sweeper.
export const RESULT_FILE_REQUIRED_FIELDS = [
  "schemaVersion",
  "runId",
  "taskId",
  "lineageKey",
  "attempt",
  "tier",
  "goal",
  "outcome",
  "failureClass",
  "exit",
  "branch",
  "commits",
  "pr",
  "prEvidence",
  "failureReason",
  "completionSignal",
  "logFilePath",
  "preservedWorktreePath",
  "iterations",
] as const;

export const OUTCOMES = {
  SUCCESS: "success",
  AGENT_FAULT: "agent_fault",
  INFRA_FAULT: "infra_fault",
} as const;

export type Outcome = (typeof OUTCOMES)[keyof typeof OUTCOMES];

// Tagged-error class names are the literal tokens the sandcastle library
// embeds in thrown error messages (node_modules/@ai-hero/sandcastle/dist/
// errors.js) — the watchdog matches them verbatim, so these keep the exact
// casing. Not substrings: order matters only for display; the match is exact.
export const SANDBOX_ERROR_CLASSES = [
  "AgentIdleTimeoutError",
  "ContainerStartTimeoutError",
  "MergeToHostTimeoutError",
  "WorktreeError",
  "DockerError",
  "SyncError",
  "SessionCaptureError",
  "PromptError",
  "AgentError",
  "ExecError",
] as const;

// Classes that mean the run never got a fair shot — container never booted,
// worktree never materialized, sync/session capture died, host docker down.
// These burn NO agent attempt and never escalate tier.
export const INFRA_ERROR_CLASSES = [
  "ContainerStartTimeoutError",
  "MergeToHostTimeoutError",
  "WorktreeError",
  "DockerError",
  "SyncError",
  "SessionCaptureError",
] as const;

// Failure-class string written to the result file + event payload. Null
// means no specific class (a clean run, or an unclassifiable throw).
export const FAILURE_CLASSES = {
  OOM: "oom",
  PROVIDER_BILLING: "provider_billing",
  ZERO_COMMITS: "zero-commits-on-windows-host",
  MISSING_BRANCH: "missing-pinned-branch",
} as const;

// --- watchdog classifier inputs, ported 1:1 (case-insensitive) ------------

// Run-Sandcastle.ps1 $script:OOMSignatures. Exit 137 (SIGKILL) is Linux OOM.
const OOM_SIGNATURES = [
  "out of memory",
  "oom",
  "cuda out of memory",
  "model requires more system memory",
  "model load failed",
  "failed to load model",
  "unable to allocate",
];

// Run-Sandcastle.ps1 $script:ProviderBillingSignatures.
const BILLING_SIGNATURES = [
  "insufficient balance",
  "payment required",
  "insufficient funds",
];

// The two narrow HTTP-402 branches from the watchdog (#956 review): a context
// word (http/status/error/code) followed by ONLY structured separators before
// 402, or the HTTP status line itself. Kept as exact ports so a signature
// change stays a single edit away on either side.
const BILLING_402_CONTEXT = /\b(?:http|status|error|code)[ \t:="'{} ,]{0,6}\b402\b/i;
const BILLING_402_STATUS_LINE = /\bhttp\/\d[\d.]*\s+402\b/i;

export function testOOM(text: string, exitCode: number | null): boolean {
  if (exitCode === 137) return true; // SIGKILL on Linux OOM
  if (!text) return false;
  for (const sig of OOM_SIGNATURES) {
    if (text.toLowerCase().includes(sig)) return true;
  }
  return false;
}

export function testProviderBilling(text: string): boolean {
  if (!text) return false;
  for (const sig of BILLING_SIGNATURES) {
    if (text.toLowerCase().includes(sig)) return true;
  }
  return BILLING_402_CONTEXT.test(text) || BILLING_402_STATUS_LINE.test(text);
}

export function getSandcastleErrorClass(text: string): string | null {
  if (!text) return null;
  for (const cls of SANDBOX_ERROR_CLASSES) {
    if (text.includes(cls)) return cls;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

export interface ClassifyInput {
  /** Whether `run()` returned normally (no throw). */
  runCompleted: boolean;
  /** The run() result's exit code, when known. */
  exitCode: number | null;
  commitsCount: number;
  pinnedBranchExists: boolean;
  /** Scrubbed log tail ("" when no log file is reachable). */
  logTail: string;
  /** Thrown error text ("" on a clean run). */
  errorText: string;
}

export interface CompletionClassification {
  outcome: Outcome;
  failureClass: string | null;
  eventType: "task_done" | "task_failed";
  exit: number;
  /** Infra faults burn no agent attempt and never escalate tier. */
  infra: boolean;
}

export function classifyCompletion(input: ClassifyInput): CompletionClassification {
  const { runCompleted, exitCode, commitsCount, pinnedBranchExists, logTail, errorText } = input;
  if (runCompleted) {
    if (commitsCount > 0 && pinnedBranchExists) {
      return {
        outcome: OUTCOMES.SUCCESS,
        failureClass: null,
        eventType: "task_done",
        exit: 0,
        infra: false,
      };
    }
    // Zero commits on the Windows host is an infra fault (upstream
    // mattpocock/sandcastle#855) — the library failed to materialize the
    // branch pin on the host side, not the agent's fault. Burns no attempt.
    const failureClass = pinnedBranchExists
      ? FAILURE_CLASSES.ZERO_COMMITS
      : FAILURE_CLASSES.MISSING_BRANCH;
    return {
      outcome: OUTCOMES.INFRA_FAULT,
      failureClass,
      eventType: "task_failed",
      exit: exitCode ?? 0,
      infra: true,
    };
  }

  // The run threw. Classify from reason + log tail, most specific first.
  const text = [errorText, logTail].filter(Boolean).join("\n");
  if (testProviderBilling(text)) {
    return {
      outcome: OUTCOMES.INFRA_FAULT,
      failureClass: FAILURE_CLASSES.PROVIDER_BILLING,
      eventType: "task_failed",
      exit: exitCode ?? 1,
      infra: true,
    };
  }
  if (testOOM(text, exitCode)) {
    return {
      outcome: OUTCOMES.INFRA_FAULT,
      failureClass: FAILURE_CLASSES.OOM,
      eventType: "task_failed",
      exit: exitCode ?? 137,
      infra: true,
    };
  }
  const errorClass = getSandcastleErrorClass(text);
  if (errorClass) {
    const infra = (INFRA_ERROR_CLASSES as readonly string[]).includes(errorClass);
    return {
      outcome: infra ? OUTCOMES.INFRA_FAULT : OUTCOMES.AGENT_FAULT,
      failureClass: errorClass,
      eventType: "task_failed",
      exit: exitCode ?? 1,
      infra,
    };
  }
  // Unknown throw — fail toward infra (do not burn an agent attempt).
  return {
    outcome: OUTCOMES.INFRA_FAULT,
    failureClass: null,
    eventType: "task_failed",
    exit: exitCode ?? 1,
    infra: true,
  };
}

// ---------------------------------------------------------------------------
// Fail-loud scrubber (Protect-LogTail port, #1092 precedent)
// ---------------------------------------------------------------------------

const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/gh[pousr]_[A-Za-z0-9]{20,}/g, "<GH-TOKEN-REDACTED>"],
  [/github_pat_[A-Za-z0-9_]{20,}/g, "<GH-TOKEN-REDACTED>"],
  [/sk-ant-[A-Za-z0-9\-_]{20,}/g, "<ANTHROPIC-KEY-REDACTED>"],
  // Generic sk- keys (DeepSeek, OpenRouter, ...) — must run AFTER the sk-ant-
  // pattern above to avoid partial-replacement artifacts.
  [/sk-[A-Za-z0-9\-_]{32,}/g, "<API-KEY-REDACTED>"],
];

/** Strip known literal secrets + generic token shapes. Port of Protect-LogTail. */
export function scrubText(text: string, knownSecrets: readonly string[]): string {
  let out = text;
  for (const secret of knownSecrets) {
    if (secret) out = out.split(secret).join("<SECRET-REDACTED>");
  }
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    out = out.replace(pattern, replacement);
  }
  return out;
}

export interface ScrubResult {
  scrubbed: Record<string, unknown>;
  /** False when a known secret value survives the scrub — abort emission. */
  safe: boolean;
}

/**
 * Fail-loud payload scrub (the #1092 precedent: an unscrubbable payload aborts
 * emission, never emits unscrubbed). Replaces secret literals in every string
 * value, then verifies no known secret value remains anywhere in the payload.
 * Short secrets (<8 chars) are still replaced but not verified — the watchdog
 * only ever passes long values (URLs, API keys, tokens), so this gap is
 * theoretical; a short value would false-positive on ordinary prose.
 */
export function scrubCompletionPayload(
  payload: Record<string, unknown>,
  knownSecrets: readonly string[],
): ScrubResult {
  const scrubbed: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    scrubbed[key] = typeof value === "string" ? scrubText(value, knownSecrets) : value;
  }
  const serialized = JSON.stringify(scrubbed);
  for (const secret of knownSecrets) {
    if (secret && secret.length >= 8 && serialized.includes(secret)) {
      return { scrubbed, safe: false };
    }
  }
  return { scrubbed, safe: true };
}

// ---------------------------------------------------------------------------
// Result-file construction
// ---------------------------------------------------------------------------

export interface ResultFileInput {
  runId: string;
  taskId: string | null;
  lineageKey: string | null;
  attempt: number | null;
  tier: string | null;
  goal: string;
  outcome: Outcome;
  failureClass: string | null;
  exit: number;
  branch: string;
  commits: string[];
  pr: number | false | null;
  prEvidence: boolean | null;
  failureReason: string;
  completionSignal: boolean;
  logFilePath: string | null;
  preservedWorktreePath: string | null;
  iterations: Array<{ sessionId: string; usage?: unknown }>;
}

export function buildResultFile(input: ResultFileInput): Record<string, unknown> {
  return {
    schemaVersion: RESULT_FILE_SCHEMA_VERSION,
    runId: input.runId,
    taskId: input.taskId,
    lineageKey: input.lineageKey,
    attempt: input.attempt,
    tier: input.tier,
    goal: input.goal,
    outcome: input.outcome,
    failureClass: input.failureClass,
    exit: input.exit,
    branch: input.branch,
    commits: input.commits,
    pr: input.pr,
    prEvidence: input.prEvidence,
    failureReason: input.failureReason,
    completionSignal: input.completionSignal,
    logFilePath: input.logFilePath,
    preservedWorktreePath: input.preservedWorktreePath,
    iterations: input.iterations,
  };
}

/**
 * Reconstruct the completion-event payload from a result file. Carries the
 * 8-field completion contract plus the routing fields the orchestrator reads
 * (pr_evidence / exit_confirmed / goal / failure_reason), matching
 * agents/task_dispatch.py poll_completions payload shapes so a duplicate
 * host-side emission dedups cleanly on the same dedup key.
 */
export function buildCompletionPayload(
  data: Record<string, unknown>,
): Record<string, unknown> {
  const eventType = data.outcome === OUTCOMES.SUCCESS ? "task_done" : "task_failed";
  const pr = data.pr ?? false;
  const payload: Record<string, unknown> = {
    task_id: data.taskId ?? null,
    attempt: data.attempt ?? 0,
    lineage_key: data.lineageKey ?? null,
    branch: data.branch ?? "",
    pr,
    exit: data.exit ?? 1,
    failure_class: data.failureClass ?? null,
    tier: data.tier ?? null,
    pr_evidence: data.prEvidence ?? null,
    exit_confirmed: eventType === "task_failed",
    goal: data.goal ?? "",
  };
  if (eventType === "task_done") {
    payload.closing_ref = pr;
  } else {
    payload.failure_reason = data.failureReason ?? "";
  }
  return payload;
}

/** Severity mirrors agents/task_dispatch.py _severity_for. */
export function completionSeverity(eventType: string, prEvidence: boolean | null): string {
  if (eventType === "task_done" && prEvidence === true) return "info";
  return "medium";
}

// ---------------------------------------------------------------------------
// Emission (idempotent upsert on dedup_key, with retry)
// ---------------------------------------------------------------------------

export interface EmitCompletionEventInput {
  supabaseUrl: string;
  supabaseKey: string;
  repo: string;
  eventType: "task_done" | "task_failed";
  title: string;
  severity: string;
  payload: Record<string, unknown>;
  dedupKey: string;
  maxRetries?: number;
  /** Injectable for tests; defaults to global fetch (Node >=18). */
  fetchImpl?: typeof fetch;
}

export async function emitCompletionEvent(input: EmitCompletionEventInput): Promise<void> {
  const { supabaseUrl, supabaseKey, repo, eventType, title, severity, payload, dedupKey } = input;
  const maxRetries = input.maxRetries ?? 3;
  const fetchImpl = input.fetchImpl ?? fetch;
  const url = `${supabaseUrl.replace(/\/+$/, "")}/rest/v1/events?on_conflict=dedup_key`;
  const body = {
    event_type: eventType,
    severity,
    repo,
    source: "sandcastle:supervisor",
    title,
    payload,
    dedup_key: dedupKey,
  };
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const res = await fetchImpl(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: supabaseKey,
          Authorization: `Bearer ${supabaseKey}`,
          Prefer: "resolution=merge-duplicates,return=representation",
        },
        body: JSON.stringify(body),
      });
      if (res.ok) return;
      // 409 = duplicate dedup_key under an older PostgREST without
      // merge-duplicates resolution — an idempotent re-observation, not an
      // error (the events unique index absorbs it, #953 AC1/AC9).
      if (res.status === 409) return;
      throw new Error(`PostgREST upsert failed: HTTP ${res.status} ${res.statusText}`);
    } catch (err) {
      if (attempt >= maxRetries) throw err;
      await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
    }
  }
}

// ---------------------------------------------------------------------------
// File helpers
// ---------------------------------------------------------------------------

export async function writeResultFile(
  path: string,
  data: Record<string, unknown>,
): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(data, null, 2), "utf8");
}

/** Last N non-empty lines of a log file — the watchdog's Get-LogTail port. */
export async function readLogTail(
  logFilePath: string | null | undefined,
  lines = 12,
): Promise<string> {
  if (!logFilePath) return "";
  try {
    const content = await readFile(logFilePath, "utf8");
    const nonEmpty = content
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    return nonEmpty.slice(-lines).join("\n");
  } catch {
    return "";
  }
}
