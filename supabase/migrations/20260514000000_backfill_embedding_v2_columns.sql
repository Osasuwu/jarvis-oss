-- Backfill: dual-embedding read-path columns (#242)
--
-- mcp-memory/schema.sql lines 2074-2083 added `embedding_v2`, `embedding_model_v2`,
-- `embedding_version_v2` + partial HNSW index, but no paired migration file
-- was written at the time. Cloud DB therefore never received the columns,
-- and every subsequent migration that touches `match_memories_v2` has been
-- unappliable. Caught during M42 milestone-close AC verification (2026-05-26)
-- when applying 20260516000000_memory_deriver_slice1 failed on
-- `column m.embedding_v2 does not exist`.
--
-- This backfill mirrors the schema.sql block exactly. The match_memories_v2
-- RPC itself is NOT defined here — 20260516000000_memory_deriver_slice1
-- creates it (with review-gate filters), then 20260520000001 amends the
-- signature for `include_unreviewed`. Keep RPC creation in the migration
-- that owns its semantics; this file owns only the missing storage.
--
-- Timestamp `20260514000000` placed before deriver_slice1 (20260516000000)
-- to encode the actual logical dependency order.

alter table memories add column if not exists embedding_v2 vector(1024);
alter table memories add column if not exists embedding_model_v2 text;
alter table memories add column if not exists embedding_version_v2 text;

-- Partial HNSW index — only rows with populated v2 get indexed. Keeps the
-- structure small while SECONDARY is unset (zero rows indexed initially).
create index if not exists idx_memories_embedding_v2_hnsw
  on memories using hnsw (embedding_v2 vector_cosine_ops)
  with (m = 16, ef_construction = 64)
  where embedding_v2 is not null;
