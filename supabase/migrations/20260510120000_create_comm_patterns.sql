-- comm_patterns: communication-pattern instances (#580, ADR 0004)
-- Schema-only slice. No data writing yet — extractor lands in #581.

create table if not exists comm_patterns (
  id uuid default gen_random_uuid() primary key,

  device text not null,
  session_id text not null,
  message_idx int not null,
  captured_at timestamptz not null,

  primary_label text not null check (primary_label in (
    'correction_wrong_direction',
    'correction_incomplete',
    'affirmation',
    'affirmation_with_redirect',
    'preference_directive',
    'meta_protocol'
  )),
  subtype text,
  confidence numeric(3,2) not null check (confidence >= 0 and confidence <= 1),

  anchor_quote text not null,
  redacted boolean not null default false,

  embedding vector(512),

  source_provenance text not null,
  created_at timestamptz default now()
);

create index if not exists idx_comm_patterns_label_captured
  on comm_patterns (primary_label, captured_at desc);

create unique index if not exists idx_comm_patterns_dedup
  on comm_patterns (device, session_id, message_idx);

create index if not exists idx_comm_patterns_no_embedding
  on comm_patterns (id) where embedding is null;

alter table comm_patterns enable row level security;

create policy "Allow all for authenticated" on comm_patterns
  for all using (true) with check (true);

create table if not exists comm_patterns_watermark (
  device text not null,
  session_id text not null,
  last_message_idx int not null default -1,
  updated_at timestamptz default now(),
  primary key (device, session_id)
);

alter table comm_patterns_watermark enable row level security;

create policy "Allow all for authenticated" on comm_patterns_watermark
  for all using (true) with check (true);
