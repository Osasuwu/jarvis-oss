// One-sided contract check for the shared result-file fixture (S2, #1120).
// Node runs this via type stripping (the repo has no TS test runner): the
// fixture tests/fixtures/sandcastle-result.json must carry every required
// field of the TS writer AND be reproducible by buildResultFile — a fixture
// the writer would not produce is a contract break, not a fixture update.
// The mirror Python check lives in tests/sandcastle/test_sandcastle_result.py.

import { readFile } from "node:fs/promises";
import {
  RESULT_FILE_REQUIRED_FIELDS,
  buildCompletionPayload,
  buildResultFile,
  classifyCompletion,
  completionSeverity,
  emitCompletionEvent,
  scrubCompletionPayload,
} from "./completion.mts";

const fixturePath = new URL("../tests/fixtures/sandcastle-result.json", import.meta.url);
const fixture: Record<string, unknown> = JSON.parse(await readFile(fixturePath, "utf8"));

let failures = 0;
const fail = (msg: string) => {
  failures += 1;
  console.error(`FAIL: ${msg}`);
};

// 1. The fixture carries every required field.
for (const field of RESULT_FILE_REQUIRED_FIELDS) {
  if (!(field in fixture)) fail(`fixture missing required field ${field}`);
}

// 2. buildResultFile reproduces the fixture exactly.
const rebuilt = buildResultFile({
  runId: fixture.runId as string,
  taskId: fixture.taskId as string | null,
  lineageKey: fixture.lineageKey as string | null,
  attempt: fixture.attempt as number | null,
  tier: fixture.tier as string | null,
  goal: fixture.goal as string,
  outcome: fixture.outcome as "success",
  failureClass: fixture.failureClass as string | null,
  exit: fixture.exit as number,
  branch: fixture.branch as string,
  commits: fixture.commits as string[],
  pr: fixture.pr as number | false | null,
  prEvidence: fixture.prEvidence as boolean | null,
  failureReason: fixture.failureReason as string,
  completionSignal: fixture.completionSignal as boolean,
  logFilePath: fixture.logFilePath as string | null,
  preservedWorktreePath: fixture.preservedWorktreePath as string | null,
  iterations: fixture.iterations as Array<{ sessionId: string; usage?: unknown }>,
});
if (JSON.stringify(rebuilt) !== JSON.stringify(fixture)) {
  fail("buildResultFile does not reproduce the fixture");
}

// 3. The success fixture classifies as task_done with PR evidence.
const classification = classifyCompletion({
  runCompleted: true,
  exitCode: 0,
  commitsCount: (fixture.commits as string[]).length,
  pinnedBranchExists: true,
  logTail: "",
  errorText: "",
});
if (classification.eventType !== "task_done") {
  fail(`expected task_done, got ${classification.eventType}`);
}

// 4. Payload reconstruction carries the 8-field completion contract.
const payload = buildCompletionPayload(fixture);
for (const field of ["task_id", "attempt", "lineage_key", "branch", "pr", "exit", "failure_class", "tier"]) {
  if (!(field in payload)) fail(`payload missing contract field ${field}`);
}
if (payload.pr !== 1234) fail(`expected pr 1234, got ${payload.pr}`);
if (completionSeverity("task_done", true) !== "info") fail("task_done with evidence should be info");

// 5. Fail-loud scrubber flags a residual secret value. A secret hiding in a
// non-string value survives scrubText (only string values are scrubbed) — the
// residual check must abort emission (#1092).
const secret = "sk-secret-abcdefghijklmnopqrstuvwxyz0123456789";
const { safe } = scrubCompletionPayload({ failure_reason: "boom", nested: [secret] }, [secret]);
if (safe) fail("scrubber should flag a residual secret");

// 6. emitCompletionEvent retries transient failures, then succeeds (the
// idempotent upsert on dedup_key is what makes duplicate emission a no-op).
const emitBase = {
  supabaseUrl: "https://test.supabase.co",
  supabaseKey: "test-key",
  repo: "Osasuwu/jarvis",
  eventType: "task_done" as const,
  title: "task done",
  severity: "info",
  payload: { task_id: "t1", pr_evidence: true },
  dedupKey: "task_done:t1:a0",
  maxRetries: 3,
};
let attempts = 0;
const flakyFetch = (async (_url: string, _init: RequestInit) => {
  attempts += 1;
  if (attempts < 3) throw new Error("transient network failure");
  return { ok: true, status: 200, statusText: "OK" } as Response;
}) as typeof fetch;
await emitCompletionEvent({ ...emitBase, fetchImpl: flakyFetch });
if (attempts !== 3) fail(`expected 3 attempts (2 retries), got ${attempts}`);

// 7. A 409 (duplicate dedup_key) is an idempotent success, never an error.
const conflictFetch = (async () => {
  return { ok: false, status: 409, statusText: "Conflict" } as Response;
}) as typeof fetch;
await emitCompletionEvent({ ...emitBase, maxRetries: 0, fetchImpl: conflictFetch });

// 8. A persistent failure surfaces after retries are exhausted.
const deadFetch = (async () => {
  throw new Error("provider down");
}) as typeof fetch;
try {
  await emitCompletionEvent({ ...emitBase, maxRetries: 1, fetchImpl: deadFetch });
  fail("emit should throw after retries exhausted");
} catch {
  // expected
}

if (failures > 0) {
  console.error(`check-result-contract: ${failures} failure(s)`);
  process.exit(1);
}
console.log("check-result-contract: fixture validates against TS writer");
