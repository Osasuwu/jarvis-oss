-- Slice 1 of memory-deriver epic (#549, issue #681)
-- Decision: 8f846597-2da0-44e0-af0c-0e65b3f36cbb
--   Always-gate: candidates land with requires_review=true; owner reviews via /learn.
-- Decision: 8963dbe7-88eb-4f15-a177-a7da6f421da8
--   Merge proposals: candidates with merge_targets referencing live memories.
-- ADR-0003: docs/adr/0003-implicit-memory-derivation.md
--
-- Adds requires_review and merge_targets columns to the memories table plus
-- two RPCs: memory_review_decide (single dispatcher for all review actions)
-- and memory_review_list (fetch pending rows).
--
-- Paired with mcp-memory/schema.sql per #326 schema-drift CI gate.

-- =========================================================================
-- 1. memories.requires_review — marks rows that need owner review before
--    they shape recall results (always-gate).
-- =========================================================================
ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS requires_review BOOLEAN NOT NULL DEFAULT false;

-- Partial index: efficient lookup of pending-review rows.
-- The set is small (<200 even in steady state), but the index is free at
-- this size and prevents seq-scans in /learn --status count queries.
CREATE INDEX IF NOT EXISTS idx_memories_requires_review
  ON memories(id) WHERE requires_review;

-- =========================================================================
-- 2. memories.merge_targets — for merge proposals emitted by the Dreamer
--    pipeline (S4). Non-null means "this candidate, if accepted, replaces
--    a set of existing memories atomically."
-- =========================================================================
ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS merge_targets UUID[];

-- Partial index: efficient lookup of merge-proposal candidates.
CREATE INDEX IF NOT EXISTS idx_memories_merge_targets
  ON memories(id) WHERE merge_targets IS NOT NULL;

-- =========================================================================
-- 3. memories.reject_reason — when memory_review_decide(action='reject')
--    persists why so /learn can surface top reject-reasons.
-- =========================================================================
ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS reject_reason TEXT;

-- =========================================================================
-- 4. memory_review_decide — single dispatcher routing on action.
--
-- Actions:
--   accept              — mark requires_review=false (simple approval)
--   accept_with_edit    — update content fields + mark reviewed
--   reject              — set reject_reason, mark reviewed (NOT deleted)
--   merge               — atomic: insert new memory + supersede all
--                         merge_targets in one transaction
--   approve             — approve a classifier queue action (legacy path)
--   reject_classifier   — reject a classifier queue action (legacy path)
--
-- Re-call on an already-reviewed row returns the prior outcome without
-- mutating state (idempotent).
-- =========================================================================
CREATE OR REPLACE FUNCTION memory_review_decide(
    action TEXT,
    candidate_id UUID,
    edited_name TEXT DEFAULT NULL,
    edited_description TEXT DEFAULT NULL,
    edited_content TEXT DEFAULT NULL,
    edited_tags TEXT[] DEFAULT NULL,
    reject_reason TEXT DEFAULT NULL,
    reviewer TEXT DEFAULT 'owner'
)
RETURNS JSONB
LANGUAGE plpgsql VOLATILE
AS $$
DECLARE
    v_candidate RECORD;
    v_prior JSONB;
    v_result JSONB;
    v_new_id UUID;
    v_superseded_count INT;
BEGIN
    -- Fetch the candidate row
    SELECT * INTO v_candidate FROM memories WHERE id = candidate_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'status', 'error',
            'error', 'candidate_not_found'
        );
    END IF;

    -- Idempotency: if already reviewed (requires_review=false), return prior outcome
    IF NOT v_candidate.requires_review THEN
        RETURN jsonb_build_object(
            'status', 'already_reviewed',
            'candidate_id', candidate_id,
            'prior_action', 'unknown'
        );
    END IF;

    -- Validate action
    IF action NOT IN ('accept', 'accept_with_edit', 'reject', 'merge', 'approve', 'reject_classifier') THEN
        RAISE EXCEPTION 'Unknown action: %. Valid actions: accept, accept_with_edit, reject, merge, approve, reject_classifier', action;
    END IF;

    -- Dispatch
    CASE action
        WHEN 'accept' THEN
            UPDATE memories
            SET requires_review = false
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'accept',
                'candidate_id', candidate_id
            );

        WHEN 'accept_with_edit' THEN
            IF edited_content IS NULL AND edited_name IS NULL AND edited_description IS NULL AND edited_tags IS NULL THEN
                RAISE EXCEPTION 'accept_with_edit requires at least one edited_* field';
            END IF;

            UPDATE memories
            SET requires_review = false,
                name = COALESCE(edited_name, name),
                description = COALESCE(edited_description, description),
                content = COALESCE(edited_content, content),
                tags = COALESCE(edited_tags, tags)
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'accept_with_edit',
                'candidate_id', candidate_id
            );

        WHEN 'reject' THEN
            UPDATE memories
            SET requires_review = false,
                reject_reason = COALESCE(memory_review_decide.reject_reason, 'no reason given')
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'reject',
                'candidate_id', candidate_id,
                'reject_reason', COALESCE(memory_review_decide.reject_reason, 'no reason given')
            );

        WHEN 'merge' THEN
            -- Validate that merge_targets is populated
            IF v_candidate.merge_targets IS NULL OR array_length(v_candidate.merge_targets, 1) IS NULL THEN
                RAISE EXCEPTION 'merge action requires candidate with non-empty merge_targets';
            END IF;

            -- Insert new canonical memory (same data, new id)
            INSERT INTO memories (
                project, name, type, description, content, tags,
                source_provenance, requires_review
            ) VALUES (
                v_candidate.project, v_candidate.name, v_candidate.type,
                v_candidate.description, v_candidate.content, v_candidate.tags,
                v_candidate.source_provenance, false
            )
            RETURNING id INTO v_new_id;

            -- Supersede all merge targets
            UPDATE memories
            SET superseded_by = v_new_id,
                valid_to = now(),
                expired_at = now()
            WHERE id = ANY(v_candidate.merge_targets)
              AND superseded_by IS NULL;

            GET DIAGNOSTICS v_superseded_count = ROW_COUNT;

            -- Add memory_links for each superseded target
            INSERT INTO memory_links (source_id, target_id, link_type, strength)
            SELECT v_new_id, unnest_id, 'supersedes', 1.0
            FROM unnest(v_candidate.merge_targets) AS unnest_id
            ON CONFLICT (source_id, target_id, link_type) DO NOTHING;

            -- Mark the merge proposal itself as reviewed
            UPDATE memories
            SET requires_review = false
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'merge',
                'candidate_id', candidate_id,
                'canonical_id', v_new_id,
                'superseded_count', v_superseded_count
            );

        WHEN 'approve' THEN
            -- Accept a classifier decision (legacy path via memory_review_queue)
            UPDATE memories
            SET requires_review = false
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'approve',
                'candidate_id', candidate_id
            );

        WHEN 'reject_classifier' THEN
            -- Reject a classifier decision
            UPDATE memories
            SET requires_review = false,
                reject_reason = COALESCE(memory_review_decide.reject_reason, 'rejected by classifier review')
            WHERE id = candidate_id;

            v_result := jsonb_build_object(
                'status', 'ok',
                'action', 'reject_classifier',
                'candidate_id', candidate_id,
                'reject_reason', COALESCE(memory_review_decide.reject_reason, 'rejected by classifier review')
            );
    END CASE;

    RETURN v_result;
END;
$$;

-- =========================================================================
-- 5. memory_review_list — returns pending rows in queue order.
--
-- Queue order (two-tier):
--   - Merge proposals (merge_targets IS NOT NULL) first, oldest within
--   - Plain candidates (requires_review=true, merge_targets IS NULL) second, oldest within
--
-- Parameters:
--   queue TEXT — which queue to list: 'candidate' (deriver/dreamer), 'classifier' (legacy)
--   project_filter TEXT — optional; filter by project scope (NULL = all)
--   limit_count INT — max rows (default 20)
-- =========================================================================
CREATE OR REPLACE FUNCTION memory_review_list(
    queue TEXT DEFAULT 'candidate',
    project_filter TEXT DEFAULT NULL,
    limit_count INT DEFAULT 20
)
RETURNS TABLE(
    id UUID,
    name TEXT,
    type TEXT,
    project TEXT,
    description TEXT,
    content TEXT,
    tags TEXT[],
    source_provenance TEXT,
    requires_review BOOLEAN,
    merge_targets UUID[],
    reject_reason TEXT,
    created_at TIMESTAMPTZ
)
LANGUAGE sql STABLE
AS $$
    SELECT m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.source_provenance, m.requires_review,
           m.merge_targets, m.reject_reason,
           m.created_at
    FROM memories m
    WHERE m.requires_review
      AND m.deleted_at IS NULL
      AND (project_filter IS NULL OR m.project = project_filter OR (m.project IS NULL AND project_filter = ''))
      AND (
        CASE queue
          WHEN 'candidate' THEN
            m.source_provenance NOT LIKE 'classifier:%'
          WHEN 'classifier' THEN
            m.source_provenance LIKE 'classifier:%'
          ELSE TRUE
        END
      )
    ORDER BY
      CASE WHEN m.merge_targets IS NOT NULL THEN 0 ELSE 1 END,
      m.created_at ASC
    LIMIT limit_count;
$$;
