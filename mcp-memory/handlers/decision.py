"""Decision-trace handler — record_decision + name→UUID resolver helpers.

Part of #360 server.py split. Calls utilities (`_get_client`,
`_audit_log`, embedding helpers, `EMBEDDING_MODEL_*`, ...) via
the `server` module so test monkeypatches on those names
propagate at call time.
"""

from __future__ import annotations

import asyncio
import uuid

from datetime import datetime, timezone  # noqa: F401

from mcp.types import TextContent  # noqa: F401

import server  # noqa: F401  — late-bound for monkeypatch propagation
import write_scrubber  # #555: Tier-2 write-path secret-scrubber gate

# Strong references to fire-and-forget tasks. CPython holds only a weak ref to
# a bare ``asyncio.create_task`` result, so without an external strong ref the
# task can be GC-collected mid-flight before it completes (same pattern as
# write_scrubber._PENDING_BLOCK_LOGS). Discard via the done-callback below.
_PENDING_TASKS: set[asyncio.Task] = set()


def _pin_task(task: asyncio.Task) -> None:
    """Strong-ref *task* until completion so it can't be GC-collected mid-flight."""
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)


def _looks_like_uuid(s: str) -> bool:
    """True if s is a canonical UUID string (case-insensitive, hyphenated)."""
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _resolve_memory_refs(client, refs: list, project: str | None) -> tuple[list[str], list[str]]:
    """Normalize mixed memory names and UUIDs into canonical UUIDs (#325).

    Policy:
    - Inputs already shaped like a UUID pass through, canonicalized via
      uuid.UUID() (lower-cased, hyphenated).
    - Non-UUID strings are treated as memory names and resolved against
      the memories table, preferring the most recently updated live row
      (same join heuristic as scripts/backfill-outcome-memories.py).
    - When ``project`` is provided, the name lookup is scoped to that
      project. Otherwise the most-recent-live match across all projects
      wins.
    - Unresolvable names are returned separately so the handler can
      surface them without breaking the write.

    Returns (resolved_uuids, unresolved_names). Insertion order is
    preserved; resolved UUIDs are de-duplicated.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for ref in refs or []:
        if not isinstance(ref, str):
            continue
        ref_s = ref.strip()
        if not ref_s:
            continue
        if _looks_like_uuid(ref_s):
            canonical = str(uuid.UUID(ref_s))
            if canonical not in seen:
                resolved.append(canonical)
                seen.add(canonical)
            continue
        try:
            q = client.table("memories").select("id").eq("name", ref_s).is_("deleted_at", "null")
            if project is not None:
                q = q.eq("project", project)
            rows = q.order("updated_at", desc=True).limit(1).execute()
        except Exception:
            unresolved.append(ref_s)
            continue
        data = getattr(rows, "data", None)
        if not isinstance(data, list) or not data:
            unresolved.append(ref_s)
            continue
        uid = data[0].get("id") if isinstance(data[0], dict) else None
        if not uid or not _looks_like_uuid(uid):
            unresolved.append(ref_s)
            continue
        canonical = str(uuid.UUID(uid))
        if canonical not in seen:
            resolved.append(canonical)
            seen.add(canonical)
    return resolved, unresolved


async def _link_fok_judgments_to_outcomes(
    client,
    outcome_ids: list[str],
    memory_ids: list[str],
    decision_timestamp: str,
    project: str | None,
) -> None:
    """Retroactively link FOK judgments to outcomes via memory linkage (#445).

    Primary heuristic — time-window match:
    For each memory_id in memory_ids, find fok_judgments rows where:
    - recall_event_id references an events row whose payload.returned_ids contains that memory_id, AND
    - judged_at is within 30 minutes BEFORE the decision timestamp, AND
    - project matches (NULL-tolerant).

    For each match: UPDATE fok_judgments SET outcome_id = <first outcome_id> ONLY if outcome_id IS NULL.
    Fire-and-forget — failure doesn't block the decision write.
    """
    if not outcome_ids or not memory_ids or not decision_timestamp:
        return

    try:
        from datetime import datetime, timedelta

        # Use first outcome_id as the primary link target
        outcome_id = outcome_ids[0] if isinstance(outcome_ids, list) else outcome_ids

        # Parse decision timestamp
        decision_dt = datetime.fromisoformat(decision_timestamp.replace("Z", "+00:00"))
        cutoff_dt = decision_dt - timedelta(minutes=30)

        # Build a set of memory IDs to check (normalize to strings)
        mem_id_set = set(str(mid) for mid in memory_ids)

        # For each memory_id, find FOK judgments where that memory_id was in returned_ids
        for mem_id in mem_id_set:
            try:
                # Fetch FOK judgments within time window with no outcome_id
                rows = (
                    client.table("fok_judgments")
                    .select("id,recall_event_id,project")
                    .gte("judged_at", cutoff_dt.isoformat())
                    .lte("judged_at", decision_dt.isoformat())
                    .is_("outcome_id", "null")
                    .execute()
                )

                # Check each judgment to see if its recalled memory_ids include our target
                for row in rows.data or []:
                    judgment_id = row.get("id")
                    recall_event_id = row.get("recall_event_id")
                    foj_project = row.get("project")

                    if not judgment_id or not recall_event_id:
                        continue

                    # Optional project match
                    if project and foj_project and foj_project != project:
                        continue

                    try:
                        # Get the recall event to check returned_ids
                        event_data = (
                            client.table("events")
                            .select("payload")
                            .eq("id", recall_event_id)
                            .single()
                            .execute()
                        )
                        if not event_data.data:
                            continue

                        payload = event_data.data.get("payload") or {}
                        returned_ids = payload.get("returned_ids") or []
                        returned_ids_str = [str(rid) for rid in returned_ids]

                        # Check if this memory_id is in the returned set
                        if mem_id in returned_ids_str:
                            # Update the judgment with the outcome_id
                            client.table("fok_judgments").update({"outcome_id": outcome_id}).eq(
                                "id", judgment_id
                            ).execute()
                    except Exception:
                        # Skip individual check errors
                        pass
            except Exception:
                # Skip individual memory errors, continue with next memory
                pass
    except Exception:
        # Fire-and-forget: don't block decision recording
        pass


async def _handle_record_decision(args: dict) -> list[TextContent]:
    """Insert a 'decision_made' episode with structured payload (#252, #325).

    The episode is the agent's reasoning trace: what was decided, why,
    which memories/outcomes informed it, predicted confidence, and
    reversibility. /reflect reads these back via the episodes table to
    analyze whether the basis was sound when outcomes come in.

    ``memories_used`` accepts either UUIDs or memory names — names are
    resolved server-side so the payload always stores canonical UUIDs,
    keeping the Pillar 3 FK (``task_outcomes.memory_id``) joinable
    forward. Unresolved names are surfaced in the response text and
    preserved on ``payload.memories_used_unresolved`` for audit.
    """
    decision = (args.get("decision") or "").strip()
    rationale = (args.get("rationale") or "").strip()
    reversibility = args.get("reversibility")

    if not decision:
        return [TextContent(type="text", text="Error: decision is required")]
    if not rationale:
        return [TextContent(type="text", text="Error: rationale is required")]
    if reversibility not in ("reversible", "hard", "irreversible"):
        return [
            TextContent(
                type="text",
                text="Error: reversibility must be one of reversible|hard|irreversible",
            )
        ]

    confidence = args.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return [TextContent(type="text", text="Error: confidence must be a number")]
        if not (0.0 <= confidence <= 1.0):
            return [TextContent(type="text", text="Error: confidence must be in [0.0, 1.0]")]

    actor = args.get("actor") or "skill:unknown"
    project = args.get("project")

    client = server._get_client()

    # #555: Tier-2 write-path scrubber backstop. Scan the user-supplied text
    # BEFORE resolving refs / inserting the episode. AC names `rationale`; the
    # privacy invariant ("MCP writes still cannot land secrets") extends the
    # scan to every field that persists free text — `decision`,
    # `alternatives_considered`, `actor` (the symmetric partner of
    # `_handle_store`'s `source_provenance`, both namespace strings), and
    # `outcomes_referenced` (persisted raw into the episode payload below with
    # no UUID validation, so it is a free-text sink that must be scanned too).
    # `project` persists to the episode payload (line ~275) and can carry a
    # user path — scan it too, matching `_handle_store`'s gate.
    # `memories_used` is scanned BEFORE resolution: any entry that is not a real
    # UUID/name is preserved verbatim in `payload.memories_used_unresolved` AND
    # echoed in the success text, so an unresolved secret-shaped entry (e.g. a
    # raw `sk-ant-*` key) would otherwise bypass the gate and land in the DB.
    block = write_scrubber.check_write(
        client,
        {
            "decision": decision,
            "rationale": rationale,
            "alternatives_considered": args.get("alternatives_considered") or [],
            "actor": actor,
            "outcomes_referenced": args.get("outcomes_referenced") or [],
            "project": project,
            "memories_used": args.get("memories_used") or [],
        },
        write_path="record_decision",
    )
    if block is not None:
        return [TextContent(type="text", text=block)]

    resolved_memories, unresolved_memories = _resolve_memory_refs(
        client, args.get("memories_used") or [], project
    )

    payload = {
        "decision": decision,
        "rationale": rationale,
        "memories_used": resolved_memories,
        "outcomes_referenced": args.get("outcomes_referenced") or [],
        "alternatives_considered": args.get("alternatives_considered") or [],
        "reversibility": reversibility,
    }
    if unresolved_memories:
        payload["memories_used_unresolved"] = unresolved_memories
    if confidence is not None:
        payload["confidence"] = confidence
    if project:
        payload["project"] = project
    if bool(args.get("intentionally_empty")):
        payload["intentionally_empty"] = True

    try:
        result = (
            client.table("episodes")
            .insert(
                {
                    "actor": actor,
                    "kind": "decision_made",
                    "payload": payload,
                }
            )
            .execute()
        )
    except Exception as exc:
        # Privacy (#555): postgrest/httpx exception str() can embed the request
        # body (decision/rationale/etc.). If a secret ever slips the Tier-2 gate,
        # echoing {exc} to the MCP caller would leak it — surface the type only.
        return [TextContent(type="text", text=f"Error recording decision: {type(exc).__name__}")]

    if not result.data:
        return [TextContent(type="text", text="Failed to record decision.")]

    eid = result.data[0].get("id", "?")
    decision_timestamp = result.data[0].get("created_at")
    outcome_ids = args.get("outcomes_referenced") or []

    # Fire-and-forget FOK judgment linkage (#445). Await would block the
    # decision response on N+1 Supabase round-trips inside the linker; the
    # linker itself swallows exceptions, so a detached task is correct.
    if decision_timestamp and resolved_memories and outcome_ids:
        _pin_task(
            asyncio.create_task(
                _link_fok_judgments_to_outcomes(
                    client, outcome_ids, resolved_memories, decision_timestamp, project
                )
            )
        )

    # Dual-write to events_canonical substrate (C17, #477). Two-mode
    # coexistence — legacy episodes table stays for the cutover wave;
    # this is the substrate path. emit_event MUST NOT raise — it buffers
    # on failure and returns None so the legacy path still surfaces
    # success to the caller.
    try:
        from events_canonical import emit_event
    except Exception:  # noqa: BLE001 — substrate optional during rollout
        emit_event = None
    if emit_event is not None:
        canonical_payload = dict(payload)
        canonical_payload["episode_id"] = eid
        if decision_timestamp:
            canonical_payload["decision_made_at"] = decision_timestamp

        llm_meta = args.get("llm") or {}
        cost_tokens: int | None = None
        cost_usd: float | None = None
        if isinstance(llm_meta, dict) and llm_meta:
            # OTel GenAI semantic conventions verbatim — see
            # docs/design/c17-events-substrate.md §2.
            if "model" in llm_meta:
                canonical_payload["gen_ai.request.model"] = llm_meta["model"]
                canonical_payload["gen_ai.response.model"] = llm_meta.get(
                    "response_model", llm_meta["model"]
                )
            if "input_tokens" in llm_meta:
                canonical_payload["gen_ai.usage.input_tokens"] = llm_meta["input_tokens"]
            if "output_tokens" in llm_meta:
                canonical_payload["gen_ai.usage.output_tokens"] = llm_meta["output_tokens"]
            if "cost_usd" in llm_meta:
                canonical_payload["gen_ai.usage.cost_usd"] = llm_meta["cost_usd"]
                try:
                    cost_usd = float(llm_meta["cost_usd"])
                except (TypeError, ValueError):
                    cost_usd = None
            if "input_tokens" in llm_meta or "output_tokens" in llm_meta:
                try:
                    cost_tokens = int(llm_meta.get("input_tokens", 0)) + int(
                        llm_meta.get("output_tokens", 0)
                    )
                except (TypeError, ValueError):
                    cost_tokens = None
            if "provider" in llm_meta:
                canonical_payload["gen_ai.provider.name"] = llm_meta["provider"]
            if "operation" in llm_meta:
                canonical_payload["gen_ai.operation.name"] = llm_meta["operation"]

        try:
            emit_event(
                client,
                actor=actor,
                action="decision_made",
                payload=canonical_payload,
                outcome="success",
                cost_tokens=cost_tokens,
                cost_usd=cost_usd,
            )
        except Exception as exc:  # noqa: BLE001 — defense in depth
            # emit_event already buffers on its own exceptions — this
            # catches anything in the wrapper logic itself.
            import sys as _sys

            print(
                f"[decision.py] events_canonical dual-write skipped: {type(exc).__name__}",
                file=_sys.stderr,
            )

    msg = f"Decision recorded: episode {eid}"
    if unresolved_memories:
        msg += (
            f" (warning: {len(unresolved_memories)} memory name(s) did not "
            f"resolve to UUIDs: {unresolved_memories} — check spelling or "
            "pass memory UUID from recall)"
        )
    return [TextContent(type="text", text=msg)]
