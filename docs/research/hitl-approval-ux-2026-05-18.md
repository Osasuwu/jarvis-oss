# HITL approval UX and notification ergonomics for an autonomous solo-dev agent (2026-05-18)

Draft status — repo policy treats `docs/research/` as unfinished. Verify against primary sources before promoting to ground truth. All decisions belong in memory (`record_decision`), not in this file.

Scope: how an unattended agent (Jarvis) should wake, defer, or stay silent when working for a single developer across attended sessions, scheduled cron, and `/delegate` subagents. Mobile-first surface = Telegram bot plugin `plugin:0.0.6:telegram`. Existing SOUL.md gate: "final send" actions stay HITL until the digital-twin pillar lands.

---

## How to read this doc

- **§1 Cost of an interruption** — primary research on attention residue and the 23-min recovery figure; what the literature actually says vs. what the popular blog version says.
- **§2 Alert-fatigue lit (PagerDuty / ops world)** — the 50-alerts-per-week / 2–5%-actionable baseline; deduplication, suppression, dynamic urgency.
- **§3 Ambient-agent UX patterns** — LangChain's notify / question / review trichotomy and the Agent Inbox; bprigent's 7-pattern catalogue.
- **§4 Approval-button surfaces** — Slack interactive messages, Telegram inline keyboards, smartwatch glanceable.
- **§5 Trust-graduation patterns** — Stripe Radar thresholds; LaunchDarkly / Statsig guarded rollouts mapped onto agent capabilities.
- **§6 The severity ladder for Jarvis** — concrete P0–P3 spec.
- **§7 Batching matrix** — event type × interruption tier.
- **§8 Telegram inline-button approval flow** — end-to-end payload spec for the existing plugin.
- **§9 Trust-graduation criteria** — Tier 2 → 1 → 0 promotion rules with rollback signals.
- **§10 Proposals [B5-1 … B5-9]** — numbered, prioritised, single-liner each.
- **§11 Top 3 surprising findings.**
- **Bibliography** — every URL actually opened.

---

## §1. The cost of an interruption — what the research actually says

### §1.1 Leroy 2009, "attention residue"

Sophie Leroy, *Organizational Behavior and Human Decision Processes* 109 (2009) 168–181, "Why is it so hard to do my work? The challenge of attention residue when switching between work tasks." Primary source: ScienceDirect link in bibliography; full PDF mirrored at the U-Bothell faculty page.

Key finding (verbatim summary from the abstract + ResearchGate digest): when you switch tasks mid-flow, **part of your attention stays with the prior task** ("attention residue"); the subsequent task's performance is measurably worse. Residue is heaviest when the prior task is left unfinished AND when there's no time pressure to disengage. Counter-intuitively, *time pressure on the first task* reduces residue — having to wrap up forces a clean break.

Implication for Jarvis: an unanswered approval request IS an unfinished task for the user. Sending a Telegram message and walking away leaves residue on the user even if they "ignored" it. The cheap alert is not free.

### §1.2 Mark 2008, "the cost of interrupted work"

Gloria Mark, Daniela Gudith, Ulrich Klocke, CHI '08, "The Cost of Interrupted Work: More Speed and Stress." The "23 min 15 s to recover from an interruption" line is the canonical number cited everywhere — important nuance: in the original study, when workers were interrupted, they didn't immediately return to the interrupted task; they did **~2 intervening tasks first**. So the 23-min figure is *task-return latency*, not *cognitive-recovery time*. The cognitive-recovery time is probably worse (see oberien blog's careful re-read).

Mark, Iqbal, Czerwinski et al. CHI '16 "Email Duration, Batching and Self-interruption" (Microsoft Research, with 40 information workers, 12 days, biosensors): **longer daily email time = lower self-rated productivity + higher physiologically-measured stress**. People who poll on their own schedule (self-interruption) report higher productivity than those who get notification-driven interruptions. Batching email correlates with higher productivity but, surprisingly, **does NOT reduce measured stress** — the *anticipation* of unread mail is itself stressful.

Implication for Jarvis: digest rollups help productivity, but a known pending digest still costs stress. The cheapest notification is the one that **doesn't exist** — auto-resolve before alerting where possible.

### §1.3 Mark / Voida / Cardello 2012, "email vacation"

UC Irvine + US Army study, "A Pace Not Dictated by Electrons." 5-day cutoff from email → measurably lower heart-rate variability + reduced window-switching by ~50%. **Downside**: subjects felt isolated. Pure quiet is not a free lunch for a solo dev — Jarvis cannot just go silent; it must surface presence even when it has nothing to ask.

---

## §2. Alert-fatigue lit (PagerDuty + ops world)

PagerDuty's 2025 State of Digital Operations: avg on-call engineer gets **~50 alerts/week**; only **2–5% require human intervention**. That's the production-ops calibration target.

Mapped to a solo developer with no on-call rotation: total Jarvis-originated notifications should land well below this — order of **5–15 alerts/week** if Tier 1 batching works, with **≤2 wake-up Tier-0 interrupts/week** as the long-run steady state.

Recommended techniques (PagerDuty docs + OneUptime blog):

1. **Deduplication / event consolidation** — multiple alerts for the same root cause → one incident.
2. **Suppression of non-actionable alerts** — re-classify or delete; if nobody acts on it, it shouldn't ping.
3. **Severity-based routing** — SEV-1 wakes you; SEV-3 lands in a queue. Severity is the *technical* impact metric, *priority* is the *business* time-criticality. Jarvis only needs priority — for a solo dev there's no business metric distinct from "should you stop what you're doing right now."
4. **Quiet hours** — non-urgent alerts deferred to next business window. PagerDuty wraps this in "Dynamic Notifications."
5. **Event Intelligence filtering** — claimed 98% noise reduction on production data; the relevant principle for Jarvis is *the filter is mandatory, not optional*.

---

## §3. Ambient-agent UX patterns

### §3.1 LangChain "ambient agents" (Harrison Chase, Jan 2025)

Three HITL signal types:

| Pattern | Trigger | Latency | Affordance |
|---|---|---|---|
| **notify** | Agent saw something noteworthy but isn't asking for a decision | non-urgent; batchable | acknowledgment only |
| **question** | Agent needs information it doesn't have | moderately urgent; blocks progress | direct answer |
| **review** | Agent drafted a sensitive action and wants approval before executing | urgent; prevents irreversibility | approve / edit / reject-with-feedback |

And the **Agent Inbox** concept: a separate inbox-like surface (not a chat) holding "all open lines of communication between you and an agent." Priority-sorted; persistent until resolved; multi-user-visible. The crucial UX insight: an inbox is **explicitly resumable**; a chat thread visually decays.

### §3.2 bprigent's 7 patterns for human oversight

Catalogue (paraphrased):

1. **Overview panel** — current agent status, recent missions, inbox-zero indicator. (For Jarvis: a `jarvis status` Telegram command + the autonomous-loop digest.)
2. **Oversight flow** — the actual approve/edit interaction. Five resolution types: Communication, Validation, Decision, Context, Error. Jarvis's Tier-1 approval maps to "Decision"; "Context" is "I tried, I'm stuck on missing info" — important to model separately from "approve this drafted action."
3. **Activity log** — full history with filtering. (Jarvis: GitHub commit history + memory `record_decision` table + `events_list`. Largely already covered.)
4. **Work reports** — outcomes of completed missions. (Jarvis: `/end` skill output; the morning brief.)
5. **Event stream configuration** — "when [source] emits [event] where [condition]." (Jarvis: this is where the autonomous-loop trigger spec belongs.)
6. **Capacity & logic configuration** — what tools the agent has, what missions it can take.
7. **Human oversight configuration** — the variable-trigger rules. The promotion ladder.

For Jarvis the priority order is (2, 7, 1) — the others are already adequate or out of scope for the HITL question specifically.

---

## §4. Approval-button surfaces — comparative

### §4.1 Slack interactive messages

`block_actions` payloads, `action_id` + optional `value`, response URL allows the bot to **update the original message** to show "Approved by Petr at 14:32" instead of re-sending. Pattern is universal across Slack approval bots (Wrangle, Tines, Zapier).

Key insight: the **post-decision message edit is mandatory**. Without it, stale buttons fire again later when the user scrolls back, and approval audit trail dissolves into "did I actually click that?"

### §4.2 Telegram inline keyboards (relevant to Jarvis's plugin)

Bot API reference: https://core.telegram.org/bots/api#inlinekeyboardmarkup

- `InlineKeyboardMarkup.inline_keyboard` = 2D array of `InlineKeyboardButton`.
- `InlineKeyboardButton`: one of `url`, `callback_data`, `web_app`, `login_url`, `switch_inline_query`, `pay`, etc.
- **`callback_data` is 1–64 bytes, hard limit.** Bot API 7.0 also caps an inline keyboard at 100 total buttons. UTF-8 emoji can blow past 64 bytes once URL-encoded — strip emoji from `callback_data` (text is fine in the button label).
- Press → bot receives `Update.callback_query` with `id`, `from`, `data`, original `message`. Bot MUST call `answerCallbackQuery` (even if no visible reply) to clear the loading spinner.
- `editMessageText` / `editMessageReplyMarkup` to update the original message and disable the buttons after first click.

The grammY library (already used by `plugin:0.0.6:telegram`, `server.ts:19`) provides `InlineKeyboard` builder + `bot.on('callback_query:data')` handler. The plugin already wires `callback_query:data` (`server.ts:731`), so the surface exists; what's missing is a tool to *create* approval-shaped messages and a tool to *await* the callback.

### §4.3 Smartwatch / glanceable

NN/G + UCI-Bothell research: information must be parseable in ≤2 seconds; short labels (1–3 words); two-button max for actions. Watch notifications reduce phone-pickup events by ~68% and lower cognitive load ~23% vs. phone alerts for micro-decisions — IF the message is actionable from the wrist.

Translation: Jarvis approval requests delivered via Telegram should be designed so the **first line + 2 buttons** are decision-sufficient. Anything below the fold is for the "I'm at my desk and curious" case, not the "I'm walking and want to clear an approval" case.

---

## §5. Trust-graduation: rollout & fraud-detection patterns

### §5.1 Stripe Radar

ML score → threshold band:
- score above high threshold → **auto-block** (no human review).
- score in mid band → **route to human review** (the manual reviewer can override either direction).
- score below low threshold → **auto-allow**.

Thresholds are tunable. "Smart Refunds" surface only high-or-very-high confidence recommendations by default. Crucially, **the model continuously feeds back** — overrides train the next iteration.

Mapped to Jarvis: agent confidence in its drafted action becomes the score. Two cutoff knobs (auto-act / queue-for-review / auto-block-as-too-risky), tunable, with the user's accept/reject feedback becoming training data for the threshold itself (manual at first; eventually a `/calibrate-tier` skill).

### §5.2 LaunchDarkly / Statsig guarded rollouts

Percentage rollouts driven by *confidence scores*, not time. Statsig's "auto-rollback" trips on predictive pulse-metrics anomaly. LaunchDarkly's 2026 "Pre-Release Impact Forecast" estimates safe rollout velocity from historical deployment data.

Mapped to agent capability promotion: a capability (e.g. "auto-merge dependabot PRs") starts at Tier 2 (always ask), graduates to Tier 1 (batch-approve in digest) after N successful manual approvals with no reversal, graduates to Tier 0 (auto-act, log only) after M autonomous Tier-1 acts with no rollback. The dual-direction trigger — **rollback signal de-promotes** as fast as success promotes.

---

## §6. Severity ladder for Jarvis — concrete spec

Four tiers. Severity here = priority in PagerDuty terms (business-time-criticality); Jarvis has no separate "technical impact" axis.

| Tier | Name | Channel | Latency expectation | Sound/vibration | Use case |
|---|---|---|---|---|---|
| **P0** | wake-now | Telegram new message + Push (desktop) | <5 min | yes, both | irreversible action blocked on user; production breakage in a repo user owns; security incident |
| **P1** | next-active-session | Telegram new message (no DnD bypass) | <60 min during waking hours | normal Telegram | Tier-1 approval queue surfaced; PR ready for review; goal-deadline approaching |
| **P2** | digest | Edit to a single pinned "Jarvis digest" message; no new ping | <24 h | none — silent edit | informational; agent completed task without needing input; minor anomaly |
| **P3** | log-only | Memory event tag + `events_list` | none (poll on demand) | none | trace-level; not surfaced unless asked |

Examples per tier:

- **P0**: scheduled job hit a `delete` operation on a memory record not flagged reversible; sandcastle agent's PR breaks CI on `main`; credential about to expire in <24h.
- **P1**: drafted reply to a real human is ready for owner send (per SOUL.md "final send" rule); subagent finished a slice and PR is open; `/grill` chain found a contradiction in CONTEXT.md.
- **P2**: hourly autonomous-loop tick "nothing urgent, here's what I did"; new issue auto-triaged into a milestone; research draft completed.
- **P3**: every tool call; routine memory writes; debug spans.

The single "digest" message is critical — editing rather than re-sending P2 events keeps the chat from filling with noise. Telegram edits do NOT trigger push notifications (the plugin's tool doc confirms this), which is exactly the property required.

### §6.1 Quiet hours rules

- 23:00–07:00 local: **P0 only** (alarm-clock style); P1/P2 deferred to 07:00 batch.
- Calendar "focus" event detected (via Gmail/Calendar MCP, if available): P0 only; everything else deferred until event ends.
- Default working-hours regime otherwise.

---

## §7. Batching matrix — event type × interruption tier

| Event type | interrupt-now (P0) | batch-hourly (P1) | batch-daily (P2) | next-session (P3) |
|---|---|---|---|---|
| Subagent PR opened, CI green | | X | | |
| Subagent PR opened, CI red | | X | | |
| Sandcastle PR pushed to user-owned `main` branch | X | | | |
| Drafted email/reply for user send (SOUL.md gate) | | X | | |
| Goal deadline within 24h, no progress | | X | | |
| `/grill` blocker — agent stuck on missing context | | X | | |
| Tier-2 action blocked, agent requesting permission | | X | | |
| Memory delete attempted on non-reversible record | X | | | |
| External API quota >80% (Supabase, Voyage) | | | X | |
| Credential expires in <72h | | | X | |
| Credential expires in <24h | X | | | |
| Autonomous loop "nothing to do this tick" | | | | X (digest tail only) |
| New issue auto-triaged | | | X | |
| Research draft completed | | | X | |
| `record_decision` post-hoc backfill | | | X | |
| Routine memory writes | | | | X |
| CI/test failure on PR branch (not `main`) | | X | | |
| Force-push on `main` from non-Jarvis source | X | | | |
| Hookify rule fired with `block` decision | | X | | |
| Scheduled task failed twice in a row | | X | | |

Rule of thumb: **decay-aware deferral.** If a decision becomes irrelevant in <2h (e.g. "should I keep this 30-min CI run alive or kill it?"), it must be P0 — by the time the user wakes for a P1 batch, the choice is gone. If a decision is reversible and has no time pressure, it must be P2/P3 — no excuse to interrupt.

---

## §8. Telegram inline-button approval flow — end-to-end spec for the existing plugin

The plugin already imports `InlineKeyboard` from grammY (`server.ts:19`) and registers a `callback_query:data` handler (`server.ts:731`). Two new tools needed; rest is wiring.

### §8.1 New tool: `request_approval`

Inputs (MCP schema):

```json
{
  "chat_id": "string",
  "title": "string (≤80 chars, first line)",
  "summary": "string (markdown, shown below title)",
  "approval_id": "string (UUID; must be unique per session)",
  "tier": "P0 | P1",
  "buttons": [
    { "label": "string (≤20 chars)", "verdict": "approve | reject | edit | defer", "value": "string (≤32 bytes, app-specific)" }
  ],
  "expires_in_seconds": "integer (default 3600; max 86400)"
}
```

Plugin behaviour:

1. Builds `callback_data` per button as `appr:{approval_id}:{verdict}:{value_hash8}`; total ≤64 bytes (server keeps the value→hash map in memory + a Supabase backup table `pending_approvals` keyed by `approval_id`).
2. Sends the message via `bot.api.sendMessage(chat_id, body, { reply_markup })`.
3. Records `(approval_id, message_id, chat_id, expires_at)` in `pending_approvals`.
4. Returns the `approval_id` immediately. **Does not block** the calling Claude session — async by design.

### §8.2 New tool: `await_approval`

Inputs: `approval_id`, `timeout_seconds` (default 600, max 3600).

Plugin behaviour: polls or long-watches `pending_approvals` for a `resolved_at`/`verdict` row. Returns the verdict, the resolver's Telegram user id, the timestamp, and any free-text reply the user attached (parsed from a subsequent message in the same chat that quote-replies the approval message).

If timeout: returns `{ verdict: "timeout" }`. The agent decides next step (e.g. defer to next session, escalate to P0, hard-block).

### §8.3 Callback handler extension

The existing `callback_query:data` handler (`server.ts:731`) is extended:

```ts
bot.on('callback_query:data', async ctx => {
  const data = ctx.callbackQuery.data; // "appr:<uuid>:<verdict>:<hash>"
  if (!data.startsWith('appr:')) { /* existing behaviour */ return; }
  const [, approvalId, verdict, valueHash] = data.split(':');
  // 1. mark pending_approvals.resolved_at = now, verdict = verdict, resolver = ctx.from.id
  // 2. edit the original message: strip the buttons, append "✓ <verdict> by @<user> at <ts>"
  await ctx.editMessageReplyMarkup({ reply_markup: undefined });
  await ctx.editMessageText(originalBody + `\n\n— ${verdict} by @${ctx.from.username} at ${ts}`);
  // 3. answerCallbackQuery to clear the spinner
  await ctx.answerCallbackQuery({ text: `Recorded: ${verdict}` });
});
```

### §8.4 End-to-end example: subagent finished a slice, PR is open, drafted commit message awaits owner blessing

1. Subagent finishes `/implement #543`, opens PR #710.
2. Subagent (in its own session) calls `request_approval`:
   - `title`: `PR #710 ready — auto-merge?`
   - `summary` (markdown):

     ```
     **PR #710** — feat(approval): inline-keyboard tool

     - CI: green (7/7)
     - Copilot review: 0 comments
     - Touched: `plugins/telegram/server.ts` (+82 −3), `tests/test_approval_tool.py` (+140 −0)
     - Decision UUID: `a4b2c0…`
     ```
   - `tier`: `P1`
   - `buttons`:
     - `{ label: "Merge", verdict: "approve", value: "merge" }`
     - `{ label: "Review first", verdict: "defer", value: "review" }`
     - `{ label: "Reject", verdict: "reject", value: "reject" }`
   - `expires_in_seconds`: `86400`
3. Telegram message arrives on the user's phone with three buttons. The user is on the bus; taps **Merge**.
4. Callback handler edits the message → `"— approve by @owner at 14:32"`, buttons gone, no further pings.
5. Subagent's `await_approval` call returns `{ verdict: "approve", value: "merge", at: "14:32:07Z" }`.
6. Subagent calls `gh pr merge 710 --squash` and emits `record_decision` with `decision="merged PR #710 via owner approval"`, `actor="session:subagent-543:approval-flow"`.

Latency from "PR open" to "merged": ~30 seconds of owner attention. No keyboard, no laptop, no context switch beyond glancing at the watch.

### §8.5 Failure modes to design for

- **User taps twice fast** (double-fire): handler must be idempotent — first write to `pending_approvals.resolved_at` wins; second tap gets `answerCallbackQuery({ text: "Already resolved" })`.
- **Bot restart between send and callback**: state lives in Supabase, not memory.
- **Buttons stripped by Telegram client** (rare older clients): include text fallback `"Reply with: yes / no / defer"` in the summary, handler accepts message replies threading from the approval message id.
- **User wants to add a free-text comment with the approval**: detect a subsequent message in the same chat with `reply_to_message_id == approval message id`, attach as `feedback` on the verdict record.

---

## §9. Trust-graduation criteria — Tier 2 → 1 → 0

### §9.1 Definitions (reaffirmed)

- **Tier 0 (auto)**: agent acts, logs to memory, no notification (P2/P3 digest only).
- **Tier 1 (owner-queue)**: agent drafts, queues; owner approves in batch (P1 batch, hourly).
- **Tier 2 (blocked)**: agent must request explicit approval before drafting; resolves to a real-time P0/P1 alert with `request_approval`.

### §9.2 Promotion rules

A *capability* = a typed action class (e.g. "merge own-PR with CI green and 0 Copilot comments", "delete a memory record older than 90d marked reversible", "post a draft GitHub comment").

Promotion bookkeeping: maintain a per-capability counter `{capability, tier, success_streak, total_lifetime, last_rollback}` in memory under tag `tier_graduation`.

**Tier 2 → Tier 1**: after **20 consecutive manual approvals with no manual edit and no rollback**, AND lifetime ≥ 30 invocations, AND last rollback >60 days ago (or never).

**Tier 1 → Tier 0**: after **50 consecutive Tier-1 owner-approved acts with no post-hoc rollback within 7 days**, AND lifetime ≥ 100 invocations.

Both thresholds are deliberately conservative compared to Stripe Radar's ML-driven thresholds — solo dev volume is low, sample size matters more than recency.

### §9.3 Rollback / de-promotion signals

Any of the below trigger automatic Tier-N → Tier-(N+1) demotion for the capability, persisting until the streak rebuilds:

- Owner manually reverts a Tier-0 act via `git revert` / `memory_restore` / explicit "undo that" message within 7 days.
- Owner sends "стой" / "stop" / "halt that capability" in Telegram → immediate Tier 0 → Tier 2 (skip Tier 1), no questions.
- An `outcome_record` is filed against the capability with `severity ≥ medium`.
- A `/reflect` audit surfaces a pattern of mis-judgement on the capability (cross-session signal, not single event).

### §9.4 Per-capability examples

| Capability | Starting tier | Promotion sketch |
|---|---|---|
| Auto-merge own dependabot PRs with green CI | Tier 1 | → Tier 0 after 50 owner-approved clean merges |
| Auto-close stale `out-of-scope` issues with `wontfix` | Tier 2 | → Tier 1 after 20 manual approvals; → Tier 0 unlikely (irreversible-ish in social terms) |
| Send a Telegram message as Jarvis (own voice) | Tier 0 from day 1 | n/a — reversible (edit/delete) |
| Send email as the user (SOUL.md "final send") | **Tier 2 indefinitely** — gated by the digital-twin pillar, not by counter | promotion blocked until pillar lands; the counter starts only after that gate flips |
| Force-push to `main` | **Tier 2 indefinitely** — never auto | n/a |
| Open a new issue with `priority:critical` | Tier 1 | → Tier 0 after 50 manual approvals with no de-prioritisation by owner |

---

## §10. Proposals [B5-1 … B5-9]

| # | Title | Priority | One-liner |
|---|---|---|---|
| **B5-1** | Add `request_approval` + `await_approval` tools to `plugin:0.0.6:telegram` | **high** | Two new tools wrapping grammY's existing `InlineKeyboard` + the existing `callback_query:data` handler; `callback_data` budget ≤64 bytes via `appr:{uuid}:{verdict}:{hash}` with server-side value map. |
| **B5-2** | Pin a single "Jarvis digest" message per chat, edit-only for P2 | **high** | One per chat, posted at `/end` of the first session; thereafter `editMessageText` for hourly/daily roll-ups. Telegram message edits do not push, satisfying the silence requirement. |
| **B5-3** | Persist `pending_approvals` in Supabase | **high** | Schema: `approval_id PK`, `chat_id`, `message_id`, `tier`, `created_at`, `expires_at`, `resolved_at`, `verdict`, `resolver_user_id`, `feedback`. Survives bot restart, cross-device. |
| **B5-4** | Tier-graduation memory tag + per-capability counters | **medium** | `memory_store(tag="tier_graduation", key=<capability>)` carries `{tier, success_streak, last_rollback}`. `/reflect` and `/verify` increment/decrement; `record_decision` cites the counter UUID. |
| **B5-5** | Quiet-hours config in `config/device.json` | **medium** | `{"quiet_hours": {"start": "23:00", "end": "07:00", "timezone": "Europe/Moscow", "p0_overrides": true}}` consumed by `request_approval` to decide P0 wake vs. P1 defer. |
| **B5-6** | Decay-aware deferral on P1 batched approvals | **medium** | Every P1 approval carries `decays_in_seconds`. If user hasn't responded by `decays_in_seconds / 2`, the digest reminder mentions imminent decay; at decay, auto-resolves to a configured default ("merge if CI still green" / "abandon"). |
| **B5-7** | Rollback-listener: parse "стой / undo / halt" in Telegram → immediate Tier 0 → Tier 2 demotion | **medium** | Plugin watches incoming messages for a short keyword list; matches trigger a memory write under `tier_graduation` to flip the capability's tier and emit a `record_decision` event. |
| **B5-8** | `jarvis status` Telegram command (overview-panel pattern) | **low** | One-message snapshot of: current Tier-1 approval queue depth, active subagent count, time since last interaction, next scheduled-task fire. Maps to bprigent's "Overview Panel." |
| **B5-9** | `/reflect`-driven threshold calibration | **low** | Every `/reflect` run inspects the last 30 days of approvals + rollbacks per capability and proposes raising or lowering `success_streak` thresholds. Output goes to a Discussion thread, not auto-applied. |

Priority is relative to the HITL surface, not the project overall. B5-1, B5-2, B5-3 are the tracer-bullet slice; nothing else works without them.

---

## §11. Top 3 surprising findings about notification fatigue

1. **Batching email reduces self-reported productivity loss but does NOT reduce measured stress** (Mark et al. CHI '16). The popular advice that "batching makes you calmer" is wrong — the anticipation of a pending batch is itself a stressor. *Implication for Jarvis: the P1 batch should not exist unless it has something to say. An empty P1 ping is worse than no ping.*
2. **The "23 minutes to recover" figure is misread.** Mark's original number is *task-return latency* (which includes ~2 intervening tasks), not *cognitive-recovery time*. The cognitive cost is probably worse than the popular blogs report. *Implication: every Jarvis interrupt is more expensive than "23 minutes" suggests. The bar for P0 must be high.*
3. **Time pressure on the prior task reduces attention residue** (Leroy 2009). Counter-intuitively, having a hard deadline to close the prior task makes the switch cleaner. *Implication: Jarvis approval requests should carry an explicit decay deadline ("decides itself in 4h if you don't reply"). This is not a UX trick — it actually lowers the residue cost on the user, in the literature.*

A surprising **non-finding**: there is no published research on the optimal batching window length for non-urgent agent-originated decisions. The closest signal is Braze's empirical product data — digest notifications get 35% higher engagement than per-event alerts, opt-outs drop 28%. Microsoft's email research suggests **hourly batches** are the sweet spot for self-interruption-driven workers (which a solo dev is). Jarvis's P1 batch window defaults to 60 min until measured otherwise.

---

## Bibliography — primary sources opened

Sophie Leroy, attention residue:
- ScienceDirect: https://www.sciencedirect.com/science/article/abs/pii/S0749597809000399
- U-Bothell faculty mirror: https://www.uwb.edu/business/faculty/sophie-leroy/attention-residue
- ResearchGate digest: https://www.researchgate.net/publication/46489122

Gloria Mark / interruption cost:
- "The Cost of Interrupted Work: More Speed and Stress" (CHI '08): https://ics.uci.edu/~gmark/chi08-mark.pdf
- "Email Duration, Batching and Self-interruption" (Mark, Iqbal, Czerwinski et al., CHI '16): https://www.microsoft.com/en-us/research/wp-content/uploads/2016/06/Email20Duration20Camera20Ready20submission3-1.pdf
- "A Pace Not Dictated by Electrons" (Mark / Voida / Cardello, 2012): https://news.uci.edu/2012/05/03/jettisoning-work-email-reduces-stress/
- Careful re-read of the "23 min" number: https://blog.oberien.de/2023/11/05/23-minutes-15-seconds.html

PagerDuty alert fatigue:
- Alert-fatigue overview: https://www.pagerduty.com/resources/digital-operations/learn/alert-fatigue/
- Incident severity classification: https://www.pagerduty.com/resources/incident-management-response/learn/incident-severity-classification/
- Best-practices summary: https://drdroid.io/engineering-tools/best-practices-for-alerting-using-pagerduty
- OneUptime "Alert Fatigue Is Killing Your On-Call Team": https://oneuptime.com/blog/post/2026-03-05-alert-fatigue-ai-on-call/view

Ambient agents:
- LangChain "Introducing ambient agents" (Harrison Chase, Jan 2025): https://www.langchain.com/blog/introducing-ambient-agents
- bprigent "7 UX Patterns for Better Ambient AI Agents": https://www.bprigent.com/article/7-ux-patterns-for-human-oversight-in-ambient-ai-agents
- Karpathy `autoresearch` background: https://github.com/karpathy/autoresearch

Approval bots:
- Slack approval-workflow blueprint: https://api.slack.com/best-practices/blueprints/approval-workflows
- Slack interactive messages: https://api.slack.com/automation/interactive-messages
- Slack approval template (sample app): https://github.com/slackapi/template-announcement-approvals

Telegram Bot API (canonical):
- InlineKeyboardMarkup / CallbackQuery / answerCallbackQuery section: https://core.telegram.org/bots/api#inlinekeyboardmarkup
- `callback_data` 64-byte limit confirmed: https://docs.python-telegram-bot.org/en/v21.8/telegram.inlinekeyboardbutton.html
- protobuf+base85 callback_data optimisation: https://seroperson.me/2025/02/05/enhanced-telegram-callback-data/

Smartwatch / glanceable UX:
- NN/G "6 Types of Useful Smartwatch Interactions": https://www.nngroup.com/articles/smartwatch-interactions/
- Smartlet glanceable wrist alerts review: https://smartlet.io/blogs/magazine/glanceable-wrist-alerts-productivity-style
- Usability Geek smartwatch UX: https://usabilitygeek.com/smartwatch-ux-design-top-considerations/

Trust graduation / fraud detection / rollout:
- Stripe Radar docs: https://docs.stripe.com/radar
- Stripe Radar risk settings: https://docs.stripe.com/radar/risk-settings
- LaunchDarkly percentage rollouts: https://launchdarkly.com/docs/home/releases/percentage-rollouts
- LaunchDarkly guarded rollouts: https://launchdarkly.com/docs/home/releases/guarded-rollouts

Internal context (this repo):
- `config/SOUL.md` line 103 — "Sending as the user stays with the user until the 'digital twin' pillar is ready."
- `plugin:0.0.6:telegram` `server.ts` — uses grammY `InlineKeyboard`, has `callback_query:data` handler already wired.
