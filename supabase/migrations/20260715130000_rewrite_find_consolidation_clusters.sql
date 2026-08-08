-- #1187: rewrite find_consolidation_clusters to fix O(N^2) timeout (57014).
-- Prior self-join (`live a join live b on a.id < b.id and a.type = b.type`)
-- scaled quadratically and timed out once live memory volume grew past
-- ~2000 rows. Replaced with strict (type, project_key) partitioning, a bare
-- HNSW LATERAL probe (limit 40, no predicates inside the LATERAL) so the
-- planner reliably uses idx_memories_embedding_hnsw, and disjoint connected
-- components via label propagation over a temp edge table (the old
-- anchor-star clustering could place one memory in multiple overlapping
-- clusters). Verified against production (2054 live memories): ~2.9s total
-- runtime, 0 memories in multiple clusters, 0 clusters mixing type/
-- project_key, 0 clusters exceeding the cap-10 truncation.
--
-- `analyze live_tmp` before the edge-generation insert is required — without
-- it the planner uses default cardinality stats for the freshly populated
-- temp table and picks a plan that itself times out (57014), reproducing
-- the original bug one level down. uuid has no min() aggregate, so the
-- label-propagation running-minimum is resolved via a text cast.
--
-- Paired with mcp-memory/schema.sql per #326 schema-drift gate.

create or replace function find_consolidation_clusters(
  min_cluster_size int default 3,
  sim_threshold float default 0.80
)
returns table (
  cluster_id int,
  memory_id uuid,
  memory_name text,
  memory_type text,
  content text,
  similarity float,
  updated_at timestamptz
)
language plpgsql
as $$
declare
  i int := 0;
begin
  set local hnsw.ef_search = 80;

  create temp table if not exists live_tmp (
    id uuid primary key,
    name text,
    type text,
    project_key text,
    content text,
    updated_at timestamptz,
    embedding vector,
    label uuid
  ) on commit drop;
  truncate live_tmp;

  create temp table if not exists cc_edges (
    id_a uuid,
    id_b uuid,
    sim float
  ) on commit drop;
  truncate cc_edges;

  insert into live_tmp (id, name, type, project_key, content, updated_at, embedding, label)
  select m.id, m.name, m.type, m.project_key, m.content, m.updated_at, m.embedding, m.id
  from memories m
  where m.embedding is not null
    and m.expired_at is null
    and m.superseded_by is null
    and m.deleted_at is null
    and (m.valid_to is null or m.valid_to > now())
    and m.type not like '%\_archived' escape '\';

  analyze live_tmp;

  insert into cc_edges (id_a, id_b, sim)
  select a.id, nb.neighbor_id, nb.sim
  from live_tmp a
  cross join lateral (
    select b.id as neighbor_id, 1 - (b.embedding <=> a.embedding) as sim
    from memories b
    order by b.embedding <=> a.embedding
    limit 40
  ) nb
  join live_tmp b2 on b2.id = nb.neighbor_id
  where nb.neighbor_id <> a.id
    and b2.type = a.type
    and b2.project_key = a.project_key
    and nb.sim >= sim_threshold;

  analyze cc_edges;

  loop
    i := i + 1;
    with prop as (
      select e.id_a as id, least(la.label, lb.label) as new_label
      from cc_edges e
      join live_tmp la on la.id = e.id_a
      join live_tmp lb on lb.id = e.id_b
      where la.label <> lb.label
      union all
      select e.id_b as id, least(la.label, lb.label) as new_label
      from cc_edges e
      join live_tmp la on la.id = e.id_a
      join live_tmp lb on lb.id = e.id_b
      where la.label <> lb.label
    ),
    agg as (
      select id, min(new_label::text)::uuid as new_label from prop group by id
    )
    update live_tmp lt
    set label = agg.new_label
    from agg
    where agg.id = lt.id and agg.new_label < lt.label;

    exit when not found;
    if i > 1000 then
      raise exception 'find_consolidation_clusters: label propagation did not converge after % iterations', i;
    end if;
  end loop;

  return query
  with sims as (
    select id_a as id, max(sim) as best_sim from cc_edges group by id_a
    union all
    select id_b as id, max(sim) as best_sim from cc_edges group by id_b
  ),
  best as (
    select id, max(best_sim) as similarity from sims group by id
  ),
  comp_sizes as (
    select label, count(*) as comp_size from live_tmp group by label
  ),
  qualifying as (
    select lt.id, lt.name, lt.type, lt.content, lt.updated_at, lt.label,
           coalesce(b.similarity, 0) as similarity,
           row_number() over (partition by lt.label order by lt.updated_at desc, lt.id) as rn
    from live_tmp lt
    join comp_sizes cs on cs.label = lt.label
    left join best b on b.id = lt.id
    where cs.comp_size >= min_cluster_size
  ),
  capped as (
    select * from qualifying where rn <= 10
  )
  select
    dense_rank() over (order by capped.label)::int as cluster_id,
    capped.id as memory_id,
    capped.name as memory_name,
    capped.type as memory_type,
    capped.content as content,
    capped.similarity as similarity,
    capped.updated_at as updated_at
  from capped
  order by 1, capped.updated_at desc;
end;
$$;
