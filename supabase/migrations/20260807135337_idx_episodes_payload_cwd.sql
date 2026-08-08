-- #1423: session_id is demoted to forensic grouping metadata — the
-- decision_list recovery key is (project, cwd, since), because a
-- resume/compaction always mints a new session_id. Mirrors the index
-- for the field that now carries the recovery-query load.
create index if not exists idx_episodes_payload_cwd
  on episodes ((payload->>'cwd'))
  where (payload->>'cwd') is not null;
