"""Consolidation + evolution review CLI — Phase 5.1d-β (#226), extended 5.2-γ (#235).

Owner-review companion to `consolidation-rollback.py`. Works through
`memory_review_queue` rows left in `status='pending'` by the weekly
`consolidation-merge-plan.py --apply` and `evolve-neighbors.py --apply`
runs (i.e. below the 0.85 confidence gate), without SQL:

  list      show pending rows (id / kind / decision / conf / subjects)
  diff      show what would change — canonical vs members (MERGE/SUPERSEDE)
            or per-neighbor tag/description evolutions (EVOLVE)
  approve   apply the stored plan (no Haiku re-call), transition to approved,
            backfill canonical embedding on MERGE, emit event
  reject    mark rejected (blocks re-planning forever), emit event

Consolidation (MERGE/SUPERSEDE_CONSOLIDATION) approve/reject paths are
single-transaction RPCs (`approve_consolidation` / `reject_consolidation`).
For MERGE, the canonical is written by the RPC with `embedding IS NULL`;
this script backfills via VoyageAI immediately after.

Evolution (EVOLVE) approve path calls `apply_evolution_plan` with a
`queue_meta={"status": "approved"}` audit row, then copies the RPC-built
snapshots back into the original pending row and deletes the audit
duplicate — preserves the original Haiku reasoning/history while giving
`rollback_evolution` the snapshots it needs. Reject is a pure status flip
(no rollback needed — nothing was applied while the row was pending).

Usage:
    python scripts/consolidation-review.py --list
    python scripts/consolidation-review.py --list --limit 50
    python scripts/consolidation-review.py --list --kind evolution
    python scripts/consolidation-review.py <queue_id> --diff
    python scripts/consolidation-review.py <queue_id> --approve
    python scripts/consolidation-review.py <queue_id> --reject --reason "off-topic"
    python scripts/consolidation-review.py ... --json   # any mode

Env: SUPABASE_URL, SUPABASE_KEY, VOYAGE_API_KEY (for MERGE approve).
.env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    for c in (here.parent / ".env", here.parent.parent / ".env"):
        if c.exists():
            load_dotenv(c, override=True)
            break
except ImportError:
    pass

import httpx
from supabase import create_client


VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
VOYAGE_TIMEOUT = 30.0


def _canonical_embed_text(name: str, description: str, tags: list[str], content: str) -> str:
    """Mirror of mcp-memory/server.py:_canonical_embed_text.

    Duplicated from consolidation-merge-plan.py so this script can backfill
    approved MERGE canonicals on the same axis. Both copies mirror server.py
    byte-for-byte (tracked in a follow-up issue if either drifts).
    """
    parts: list[str] = []
    if name:
        parts.append(name.replace("_", " "))
    if tags:
        parts.append("tags: " + ", ".join(tags))
    if description:
        parts.append(description)
    if content:
        parts.append(content)
    return "\n".join(p for p in parts if p).strip()


def embed_document(text: str, *, timeout: float = VOYAGE_TIMEOUT) -> list[float] | None:
    """Sync VoyageAI call. Returns None on any failure (caller logs + continues)."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key or not text:
        return None
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VOYAGE_MODEL, "input": [text], "input_type": "document"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
        return None


CONSOLIDATION_DECISIONS = ("MERGE", "SUPERSEDE_CONSOLIDATION")
EVOLUTION_DECISIONS = ("EVOLVE",)


def _kind_for_decision(decision: str) -> str:
    if decision in EVOLUTION_DECISIONS:
        return "evolution"
    if decision in CONSOLIDATION_DECISIONS:
        return "consolidation"
    return "unknown"


def _fetch_queue_row(client, queue_id: str) -> dict | None:
    rows = (
        client.table("memory_review_queue")
        .select(
            "id, decision, status, confidence, reasoning, classifier_model, "
            "consolidation_payload, evolution_payload, target_id, created_at, "
            "reviewed_at, reviewed_by, applied_at"
        )
        .eq("id", queue_id)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def _decisions_for_kind(kind: str) -> list[str]:
    if kind == "consolidation":
        return list(CONSOLIDATION_DECISIONS)
    if kind == "evolution":
        return list(EVOLUTION_DECISIONS)
    return list(CONSOLIDATION_DECISIONS) + list(EVOLUTION_DECISIONS)


def list_pending(client, limit: int, *, kind: str = "all") -> list[dict]:
    rows = (
        client.table("memory_review_queue")
        .select(
            "id, decision, confidence, reasoning, created_at, "
            "consolidation_payload, evolution_payload, classifier_model"
        )
        .eq("status", "pending")
        .in_("decision", _decisions_for_kind(kind))
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
    ) or []
    return rows


def _subjects_for_row(row: dict) -> str:
    """Short summary of what the pending row proposes to touch.

    Consolidation rows list member names; evolution rows list neighbor IDs
    (short-form) + action counts. Designed for one-line --list rendering.
    """
    decision = row.get("decision")
    if decision in CONSOLIDATION_DECISIONS:
        payload = row.get("consolidation_payload") or {}
        names = payload.get("member_names") or []
        extra = f" +{len(names) - 3}" if len(names) > 3 else ""
        return ", ".join(names[:3]) + extra
    if decision in EVOLUTION_DECISIONS:
        payload = row.get("evolution_payload") or {}
        proposals = payload.get("proposals") or []
        actionable = [
            p for p in proposals if p.get("action") in ("UPDATE_TAGS", "UPDATE_DESC", "UPDATE_BOTH")
        ]
        n_total = len(proposals)
        n_act = len(actionable)
        ids = [str(p.get("neighbor_id") or "")[:8] for p in actionable[:3]]
        extra = f" +{n_act - 3}" if n_act > 3 else ""
        id_str = ", ".join(i for i in ids if i) + extra
        return (
            f"{n_act}/{n_total} actionable — {id_str}"
            if id_str
            else f"{n_act}/{n_total} actionable"
        )
    return ""


def print_listing(rows: list[dict]) -> None:
    if not rows:
        print("No pending rows.")
        return
    print(f"{'id':36}  {'kind':13}  {'decision':24}  {'conf':>5}  {'created':19}  subjects")
    print("-" * 140)
    for r in rows:
        kind = _kind_for_decision(r["decision"])
        created = (r.get("created_at") or "")[:19].replace("T", " ")
        reasoning = (r.get("reasoning") or "").split("\n", 1)[0][:80]
        subjects = _subjects_for_row(r)
        print(
            f"{r['id']:36}  {kind:13}  {r['decision']:24}  "
            f"{float(r['confidence']):5.2f}  {created:19}  {subjects}"
        )
        if reasoning:
            print(f"{'':36}    why: {reasoning}")


def _fetch_members(client, member_ids: list[str]) -> list[dict]:
    if not member_ids:
        return []
    rows = (
        client.table("memories")
        .select("id, name, type, description, content, tags, created_at")
        .in_("id", member_ids)
        .execute()
        .data
    ) or []
    order = {mid: i for i, mid in enumerate(member_ids)}
    rows.sort(key=lambda r: order.get(r["id"], len(order)))
    return rows


def render_consolidation_diff(row: dict, members: list[dict]) -> str:
    payload = row.get("consolidation_payload") or {}
    lines: list[str] = []
    lines.append(f"Queue entry: {row['id']}")
    lines.append(
        f"Decision: {row['decision']}  confidence={float(row['confidence']):.2f}  "
        f"model={row.get('classifier_model') or '?'}"
    )
    if row.get("reasoning"):
        lines.append(f"Reasoning: {row['reasoning']}")
    lines.append("")

    if row["decision"] == "MERGE":
        lines.append("--- Proposed canonical (new row) ---")
        lines.append(f"name: {payload.get('canonical_name')}")
        lines.append(f"type: {payload.get('canonical_type')}")
        tags = payload.get("canonical_tags") or []
        lines.append(f"tags: {', '.join(tags) if tags else '(none)'}")
        if payload.get("canonical_description"):
            lines.append(f"description: {payload['canonical_description']}")
        lines.append("content:")
        for ln in (payload.get("canonical_content") or "").splitlines():
            lines.append(f"  {ln}")
    else:  # SUPERSEDE_CONSOLIDATION
        lines.append(
            f"--- Canonical (winner): {row.get('target_id') or payload.get('canonical_id')} ---"
        )

    lines.append("")
    lines.append(f"--- Members ({len(members)}) ---")
    for m in members:
        lines.append(
            f"* {m['name']} ({m['type']})  id={m['id']}  created={m.get('created_at', '')[:10]}"
        )
        tags = m.get("tags") or []
        if tags:
            lines.append(f"  tags: {', '.join(tags)}")
        if m.get("description"):
            lines.append(f"  description: {m['description']}")
        if m.get("content"):
            lines.append("  content:")
            for ln in (m.get("content") or "").splitlines():
                lines.append(f"    {ln}")
        lines.append("")
    return "\n".join(lines)


def render_evolution_diff(row: dict, neighbors_by_id: dict[str, dict]) -> str:
    """Per-neighbor old→new tags/description diff for a pending EVOLVE row.

    `neighbors_by_id` is keyed on neighbor_id (uuid string) so we can surface
    current tags/description as the "old" side. Unknown neighbors still
    render — we just say "(current state unavailable)" instead of dropping.
    """
    payload = row.get("evolution_payload") or {}
    proposals = payload.get("proposals") or []
    snapshots = payload.get("snapshots") or []

    lines: list[str] = []
    lines.append(f"Queue entry: {row['id']}")
    lines.append(
        f"Decision: {row['decision']}  confidence={float(row['confidence']):.2f}  "
        f"model={row.get('classifier_model') or '?'}"
    )
    if row.get("reasoning"):
        lines.append(f"Reasoning: {row['reasoning']}")
    lines.append("")
    lines.append(f"Source UPDATE queue id: {payload.get('update_queue_id')}")
    lines.append(f"Target (superseded):    {payload.get('target_id')}")
    lines.append(f"Candidate (UPDATE):     {payload.get('candidate_id')}")
    lines.append("")

    action_counts: dict[str, int] = {}
    for p in proposals:
        a = p.get("action", "?")
        action_counts[a] = action_counts.get(a, 0) + 1
    actions_summary = ", ".join(f"{k}={v}" for k, v in sorted(action_counts.items())) or "(none)"
    lines.append(f"Proposals: {len(proposals)} total  ({actions_summary})")
    lines.append("")

    actionable = [
        p for p in proposals if p.get("action") in ("UPDATE_TAGS", "UPDATE_DESC", "UPDATE_BOTH")
    ]
    if not actionable:
        lines.append("No actionable proposals (all KEEP).")
        return "\n".join(lines)

    lines.append(f"--- Actionable proposals ({len(actionable)}) ---")
    for p in actionable:
        nid = p.get("neighbor_id") or ""
        n = neighbors_by_id.get(nid) or {}
        name = n.get("name") or (nid[:8] if nid else "?")
        lines.append("")
        lines.append(f"* {name}  id={nid}  action={p.get('action')}")
        lines.append(f"  confidence: {float(p.get('confidence') or 0.0):.2f}")
        if p.get("reasoning"):
            lines.append(f"  reasoning:  {p['reasoning']}")
        if not n:
            lines.append("  (current neighbor state unavailable — row deleted?)")
        if p.get("new_tags") is not None:
            old_tags = ", ".join(n.get("tags") or []) or "(none)"
            new_tags = ", ".join(p["new_tags"]) or "(none)"
            lines.append("  tags:")
            lines.append(f"    old: {old_tags}")
            lines.append(f"    new: {new_tags}")
        if p.get("new_description") is not None:
            old_desc = n.get("description") or "(none)"
            lines.append("  description:")
            lines.append(f"    old: {old_desc}")
            lines.append(f"    new: {p['new_description']}")

    if snapshots:
        lines.append("")
        lines.append(f"--- {len(snapshots)} apply snapshot(s) already present on this row ---")
        lines.append("(plan previously applied; rollback_evolution can restore from these)")

    return "\n".join(lines)


# Back-compat alias — older call sites + tests may still import render_diff.
render_diff = render_consolidation_diff


def _write_event(
    client, *, event_type: str, severity: str, title: str, payload: dict
) -> str | None:
    try:
        resp = (
            client.table("events")
            .insert(
                {
                    "event_type": event_type,
                    "severity": severity,
                    "repo": "your-username/your-repo",
                    "source": "cli_review",
                    "title": title,
                    "payload": payload,
                }
            )
            .execute()
        )
        data = resp.data or []
        return data[0]["id"] if data else None
    except Exception as e:
        print(f"! event insert failed ({event_type}): {e}", file=sys.stderr)
        return None


def _emit_validation_error(err: dict, *, as_json: bool, msg: str) -> int:
    print(msg, file=sys.stderr)
    if as_json:
        print(json.dumps(err))
    return 1


def _fetch_and_validate_pending(client, queue_id: str, *, as_json: bool) -> tuple[dict | None, int]:
    """Common pre-action guard: row exists + status=pending.

    Returns (row, 0) on success; (None, exit_code) on failure (with stderr +
    JSON already emitted).
    """
    row = _fetch_queue_row(client, queue_id)
    if not row:
        return None, _emit_validation_error(
            {"status": "not_found", "queue_id": queue_id},
            as_json=as_json,
            msg=f"Queue entry {queue_id} not found",
        )

    if row["status"] != "pending":
        return None, _emit_validation_error(
            {"status": "not_pending", "queue_id": queue_id, "actual_status": row["status"]},
            as_json=as_json,
            msg=f"Queue entry {queue_id} has status={row['status']} (expected pending)",
        )

    return row, 0


def approve(client, queue_id: str, *, as_json: bool) -> int:
    row, code = _fetch_and_validate_pending(client, queue_id, as_json=as_json)
    if not row:
        return code

    decision = row["decision"]
    if decision in EVOLUTION_DECISIONS:
        return _approve_evolution_row(client, row, as_json=as_json)
    if decision not in CONSOLIDATION_DECISIONS:
        return _emit_validation_error(
            {"status": "unsupported_decision", "queue_id": queue_id, "decision": decision},
            as_json=as_json,
            msg=f"Queue entry {queue_id} has unsupported decision={decision}",
        )
    return _approve_consolidation_row(client, row, as_json=as_json)


def _approve_evolution_row(client, row: dict, *, as_json: bool) -> int:
    """Apply a pending EVOLVE plan + reconcile to the original pending row.

    `apply_evolution_plan(plan, queue_meta)` is audit-row-oriented: it
    inserts a NEW queue row with status=approved and the snapshots it
    just built. That doesn't fit a CLI approve where we want to flip the
    existing pending row in place (preserving its original reasoning /
    haiku_confidence / history). So: call the RPC to get the mutations +
    snapshots, then copy snapshots back onto the original row, update
    status, delete the audit duplicate. End state: exactly one queue row
    per plan, status=approved, with everything rollback_evolution needs.
    """
    queue_id = row["id"]
    payload = row.get("evolution_payload") or {}

    candidate_id = payload.get("candidate_id")
    target_id = payload.get("target_id")
    proposals = payload.get("proposals") or []

    if not candidate_id or not target_id:
        return _emit_validation_error(
            {
                "status": "invalid_payload",
                "queue_id": queue_id,
                "reason": "missing candidate_id or target_id",
            },
            as_json=as_json,
            msg=f"Queue entry {queue_id} evolution_payload missing candidate_id/target_id",
        )

    actionable_proposals = [
        {
            "neighbor_id": p["neighbor_id"],
            "action": p["action"],
            "new_tags": p.get("new_tags"),
            "new_description": p.get("new_description"),
            "confidence": p.get("confidence", 0.0),
            "reasoning": p.get("reasoning") or "",
        }
        for p in proposals
        if p.get("action") in ("UPDATE_TAGS", "UPDATE_DESC", "UPDATE_BOTH")
    ]
    if not actionable_proposals:
        return _emit_validation_error(
            {"status": "no_actionable_proposals", "queue_id": queue_id},
            as_json=as_json,
            msg=f"Queue entry {queue_id} has no actionable proposals — nothing to apply",
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_provenance = f"cli:review:approve:{today}"
    plan = {
        "decision": "EVOLVE",
        "update_queue_id": payload.get("update_queue_id"),
        "candidate_id": candidate_id,
        "target_id": target_id,
        "source_provenance": source_provenance,
        "proposals": actionable_proposals,
    }

    classifier_model = row.get("classifier_model") or ""
    if not classifier_model.strip():
        # RPC requires non-blank classifier_model; fall back to cli stamp
        # if somehow missing (defensive — real rows always have it).
        classifier_model = "cli_review"
    applied_at = datetime.now(timezone.utc).isoformat()
    queue_meta = {
        "decision": "EVOLVE",
        "status": "approved",
        "confidence": float(row.get("confidence") or 0.0),
        "reasoning": row.get("reasoning") or "",
        "classifier_model": classifier_model,
        "applied_at": applied_at,
    }

    try:
        resp = client.rpc(
            "apply_evolution_plan",
            {"plan": plan, "queue_meta": queue_meta},
        ).execute()
        rpc_out = resp.data or {}
    except Exception as e:
        msg = f"apply_evolution_plan failed: {e}"
        print(msg, file=sys.stderr)
        if as_json:
            print(json.dumps({"status": "rpc_failed", "queue_id": queue_id, "error": str(e)}))
        return 1

    audit_queue_id = rpc_out.get("queue_id")
    applied_count = int(rpc_out.get("applied_count") or 0)

    # Pull snapshots off the audit row the RPC just wrote — they're the
    # only thing rollback_evolution needs to restore the neighbors.
    snapshots: list = []
    if audit_queue_id:
        try:
            audit_rows = (
                client.table("memory_review_queue")
                .select("evolution_payload")
                .eq("id", audit_queue_id)
                .limit(1)
                .execute()
                .data
            ) or []
            if audit_rows:
                audit_payload = audit_rows[0].get("evolution_payload") or {}
                snapshots = audit_payload.get("snapshots") or []
        except Exception as e:
            # Non-fatal. Mutations already landed; without snapshots the
            # row just can't be rolled back via rollback_evolution. Warn
            # so owner sees it in the CLI output.
            print(
                f"! audit-row fetch for {audit_queue_id} failed: {e}",
                file=sys.stderr,
            )

    merged_payload = dict(payload)
    merged_payload["snapshots"] = snapshots
    merged_payload["source_provenance"] = source_provenance

    try:
        client.table("memory_review_queue").update(
            {
                "status": "approved",
                "reviewed_at": applied_at,
                "reviewed_by": "cli_review",
                "applied_at": applied_at,
                "evolution_payload": merged_payload,
            }
        ).eq("id", queue_id).execute()
    except Exception as e:
        print(
            f"! original-row status flip for {queue_id} failed: {e}",
            file=sys.stderr,
        )
        if as_json:
            print(
                json.dumps(
                    {
                        "status": "reconciliation_failed",
                        "queue_id": queue_id,
                        "audit_queue_id": audit_queue_id,
                        "applied_count": applied_count,
                        "error": str(e),
                    }
                )
            )
        return 1

    if audit_queue_id:
        try:
            client.table("memory_review_queue").delete().eq("id", audit_queue_id).execute()
        except Exception as e:
            # Non-fatal: duplicate audit row is harmless noise, the
            # original row is already correctly flipped to approved.
            print(
                f"! audit-row delete for {audit_queue_id} failed (non-fatal): {e}",
                file=sys.stderr,
            )

    event_payload = {
        "queue_id": queue_id,
        "decision": "EVOLVE",
        "applied_count": applied_count,
        "snapshot_count": len(snapshots),
        "source_provenance": source_provenance,
    }
    event_id = _write_event(
        client,
        event_type="evolution_applied",
        severity="info",
        title=f"Evolution approved via CLI — {applied_count} neighbor(s) updated",
        payload=event_payload,
    )

    out = {
        "status": "approved",
        "decision": "EVOLVE",
        "queue_id": queue_id,
        "applied_count": applied_count,
        "snapshot_count": len(snapshots),
        "event_id": event_id,
    }
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Approved queue entry {queue_id}")
        print("  decision:       EVOLVE")
        print(f"  applied_count:  {applied_count}")
        print(f"  snapshot_count: {len(snapshots)}")
        print(f"  event_id:       {event_id}")
    return 0


def _approve_consolidation_row(client, row: dict, *, as_json: bool) -> int:
    queue_id = row["id"]
    try:
        resp = client.rpc("approve_consolidation", {"queue_id": queue_id}).execute()
        result = resp.data or {}
    except Exception as e:
        msg = f"approve_consolidation failed: {e}"
        print(msg, file=sys.stderr)
        if as_json:
            print(json.dumps({"status": "rpc_failed", "queue_id": queue_id, "error": str(e)}))
        return 1

    canonical_id = result.get("canonical_id")
    decision = result.get("decision")
    embedded = None

    # MERGE: backfill embedding on synthesized canonical. SUPERSEDE doesn't
    # need a backfill — no new memory row was created.
    if decision == "MERGE" and canonical_id:
        payload = row.get("consolidation_payload") or {}
        text = _canonical_embed_text(
            payload.get("canonical_name") or "",
            payload.get("canonical_description") or "",
            list(payload.get("canonical_tags") or []),
            payload.get("canonical_content") or "",
        )
        emb = embed_document(text)
        if emb is not None:
            try:
                client.table("memories").update(
                    {
                        "embedding": emb,
                        "embedding_model": VOYAGE_MODEL,
                        "embedding_version": "v1",
                    }
                ).eq("id", canonical_id).execute()
                embedded = True
            except Exception as e:
                print(f"! embedding backfill failed for {canonical_id}: {e}", file=sys.stderr)
                embedded = False
        else:
            print(
                f"! VoyageAI returned no embedding for {canonical_id} — canonical left with embedding=NULL",
                file=sys.stderr,
            )
            embedded = False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_payload = {
        "queue_id": queue_id,
        "decision": decision,
        "canonical_id": canonical_id,
        "superseded_count": result.get("superseded_count"),
        "embedded": embedded,
        "source_provenance": f"cli:review:{today}",
    }
    event_id = _write_event(
        client,
        event_type="consolidation_applied",
        severity="info",
        title=f"Consolidation approved via CLI — {decision} ({canonical_id})",
        payload=event_payload,
    )

    out = {
        "status": "approved",
        "decision": decision,
        "canonical_id": canonical_id,
        "superseded_count": result.get("superseded_count"),
        "embedded": embedded,
        "queue_id": queue_id,
        "event_id": event_id,
    }
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Approved queue entry {queue_id}")
        print(f"  decision:         {decision}")
        print(f"  canonical_id:     {canonical_id}")
        print(f"  superseded_count: {result.get('superseded_count')}")
        print(f"  embedded:         {embedded}")
        print(f"  event_id:         {event_id}")
    return 0


def reject(client, queue_id: str, *, reason: str | None, as_json: bool) -> int:
    row, code = _fetch_and_validate_pending(client, queue_id, as_json=as_json)
    if not row:
        return code

    decision = row["decision"]
    if decision in EVOLUTION_DECISIONS:
        return _reject_evolution_row(client, row, reason=reason, as_json=as_json)
    if decision not in CONSOLIDATION_DECISIONS:
        return _emit_validation_error(
            {"status": "unsupported_decision", "queue_id": queue_id, "decision": decision},
            as_json=as_json,
            msg=f"Queue entry {queue_id} has unsupported decision={decision}",
        )
    return _reject_consolidation_row(client, row, reason=reason, as_json=as_json)


def _reject_consolidation_row(client, row: dict, *, reason: str | None, as_json: bool) -> int:
    queue_id = row["id"]
    try:
        rpc_args: dict = {"queue_id": queue_id}
        if reason:
            rpc_args["reason"] = reason
        resp = client.rpc("reject_consolidation", rpc_args).execute()
        result = resp.data or {}
    except Exception as e:
        print(f"reject_consolidation failed: {e}", file=sys.stderr)
        if as_json:
            print(json.dumps({"status": "rpc_failed", "queue_id": queue_id, "error": str(e)}))
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_payload = {
        "queue_id": queue_id,
        "decision": result.get("decision"),
        "reason": reason,
        "source_provenance": f"cli:review:{today}",
    }
    event_id = _write_event(
        client,
        event_type="consolidation_rejected",
        severity="info",
        title=f"Consolidation rejected via CLI — {result.get('decision')}",
        payload=event_payload,
    )

    out = {
        "status": "rejected",
        "decision": result.get("decision"),
        "queue_id": queue_id,
        "reason": reason,
        "event_id": event_id,
    }
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Rejected queue entry {queue_id}")
        print(f"  decision: {result.get('decision')}")
        print(f"  reason:   {reason or '(none)'}")
        print(f"  event_id: {event_id}")
    return 0


def _reject_evolution_row(client, row: dict, *, reason: str | None, as_json: bool) -> int:
    """Pure status flip — evolution reject needs no rollback.

    A pending EVOLVE row hasn't mutated any neighbor (the plan was
    queue_for_review'd, not apply_plan_to_db'd). So rejecting is just
    `status pending → rejected`, with the reason appended to reasoning
    for audit visibility (mirrors reject_consolidation's behaviour).
    """
    queue_id = row["id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    current_reasoning = row.get("reasoning") or ""
    if reason and reason.strip():
        tail = f"\n-- rejected: {reason.strip()}"
        new_reasoning = (current_reasoning + tail)[:1000]
    else:
        new_reasoning = current_reasoning

    try:
        client.table("memory_review_queue").update(
            {
                "status": "rejected",
                "reviewed_at": now_iso,
                "reviewed_by": "cli_review",
                "reasoning": new_reasoning,
            }
        ).eq("id", queue_id).execute()
    except Exception as e:
        print(f"evolution reject update failed: {e}", file=sys.stderr)
        if as_json:
            print(json.dumps({"status": "update_failed", "queue_id": queue_id, "error": str(e)}))
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_payload = {
        "queue_id": queue_id,
        "decision": "EVOLVE",
        "reason": reason,
        "source_provenance": f"cli:review:{today}",
    }
    event_id = _write_event(
        client,
        event_type="evolution_rejected",
        severity="info",
        title="Evolution rejected via CLI",
        payload=event_payload,
    )

    out = {
        "status": "rejected",
        "decision": "EVOLVE",
        "queue_id": queue_id,
        "reason": reason,
        "event_id": event_id,
    }
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Rejected queue entry {queue_id}")
        print("  decision: EVOLVE")
        print(f"  reason:   {reason or '(none)'}")
        print(f"  event_id: {event_id}")
    return 0


def _fetch_neighbors_by_id(client, neighbor_ids: list[str]) -> dict[str, dict]:
    """Load current memory rows for evolution-diff's "old side" rendering."""
    if not neighbor_ids:
        return {}
    rows = (
        client.table("memories")
        .select("id, name, type, description, tags, content, updated_at")
        .in_("id", neighbor_ids)
        .execute()
        .data
    ) or []
    return {r["id"]: r for r in rows}


def show_diff(client, queue_id: str, *, as_json: bool) -> int:
    row = _fetch_queue_row(client, queue_id)
    if not row:
        print(f"Queue entry {queue_id} not found", file=sys.stderr)
        if as_json:
            print(json.dumps({"status": "not_found", "queue_id": queue_id}))
        return 1

    decision = row["decision"]
    if decision in EVOLUTION_DECISIONS:
        return _show_evolution_diff(client, row, as_json=as_json)
    if decision in CONSOLIDATION_DECISIONS:
        return _show_consolidation_diff(client, row, as_json=as_json)

    print(
        f"Queue entry {queue_id} has unsupported decision={decision}",
        file=sys.stderr,
    )
    if as_json:
        print(
            json.dumps(
                {
                    "status": "unsupported_decision",
                    "queue_id": queue_id,
                    "decision": decision,
                }
            )
        )
    return 1


def _show_consolidation_diff(client, row: dict, *, as_json: bool) -> int:
    queue_id = row["id"]
    payload = row.get("consolidation_payload") or {}
    member_ids = list(payload.get("member_ids") or [])
    members = _fetch_members(client, member_ids)

    if as_json:
        print(
            json.dumps(
                {
                    "queue_id": queue_id,
                    "kind": "consolidation",
                    "decision": row["decision"],
                    "confidence": float(row["confidence"]),
                    "status": row["status"],
                    "reasoning": row.get("reasoning"),
                    "canonical_project": payload.get("canonical_project"),
                    "canonical_name": payload.get("canonical_name"),
                    "canonical_type": payload.get("canonical_type"),
                    "canonical_description": payload.get("canonical_description"),
                    "canonical_content": payload.get("canonical_content"),
                    "canonical_tags": payload.get("canonical_tags") or [],
                    "members": members,
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(render_consolidation_diff(row, members))
    return 0


def _show_evolution_diff(client, row: dict, *, as_json: bool) -> int:
    queue_id = row["id"]
    payload = row.get("evolution_payload") or {}
    proposals = payload.get("proposals") or []
    neighbor_ids = [p["neighbor_id"] for p in proposals if p.get("neighbor_id")]
    neighbors_by_id = _fetch_neighbors_by_id(client, neighbor_ids)

    if as_json:
        # Enrich proposals with current (old) tags/description so a JSON
        # consumer can diff without a second round-trip. Keeps the shape
        # close to what the text renderer shows.
        enriched: list[dict] = []
        for p in proposals:
            nid = p.get("neighbor_id") or ""
            n = neighbors_by_id.get(nid) or {}
            enriched.append(
                {
                    **p,
                    "current_name": n.get("name"),
                    "current_tags": n.get("tags") or [],
                    "current_description": n.get("description"),
                }
            )
        print(
            json.dumps(
                {
                    "queue_id": queue_id,
                    "kind": "evolution",
                    "decision": row["decision"],
                    "confidence": float(row["confidence"]),
                    "status": row["status"],
                    "reasoning": row.get("reasoning"),
                    "update_queue_id": payload.get("update_queue_id"),
                    "candidate_id": payload.get("candidate_id"),
                    "target_id": payload.get("target_id"),
                    "proposals": enriched,
                    "snapshots": payload.get("snapshots") or [],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(render_evolution_diff(row, neighbors_by_id))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("queue_id", nargs="?", help="memory_review_queue.id to operate on")
    p.add_argument(
        "--list",
        action="store_true",
        help="List pending consolidation + evolution rows",
    )
    p.add_argument("--limit", type=int, default=20, help="Rows shown by --list (default 20)")
    p.add_argument(
        "--kind",
        choices=("all", "consolidation", "evolution"),
        default="all",
        help="Filter --list by decision kind (default: all)",
    )
    p.add_argument(
        "--diff",
        action="store_true",
        help="Show canonical vs members (MERGE/SUPERSEDE) or neighbor diffs (EVOLVE)",
    )
    p.add_argument("--approve", action="store_true", help="Approve queue_id (applies the plan)")
    p.add_argument("--reject", action="store_true", help="Reject queue_id (blocks re-planning)")
    p.add_argument("--reason", help="Optional reason text (used with --reject)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = p.parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key:
        print("SUPABASE_URL / SUPABASE_KEY missing from env", file=sys.stderr)
        return 2

    client = create_client(sb_url, sb_key)

    action_flags = [args.list, args.diff, args.approve, args.reject]
    if sum(1 for f in action_flags if f) > 1:
        p.error("pick exactly one of --list / --diff / --approve / --reject")

    if args.list:
        rows = list_pending(client, args.limit, kind=args.kind)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            print_listing(rows)
        return 0

    if not args.queue_id:
        p.print_usage(sys.stderr)
        print("error: queue_id required unless --list is passed", file=sys.stderr)
        return 2

    if args.diff:
        return show_diff(client, args.queue_id, as_json=args.json)
    if args.approve:
        return approve(client, args.queue_id, as_json=args.json)
    if args.reject:
        return reject(client, args.queue_id, reason=args.reason, as_json=args.json)

    # No action flag — default to --diff for bare queue_id.
    return show_diff(client, args.queue_id, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
