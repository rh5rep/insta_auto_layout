create table if not exists public.review_events (
  event_id text primary key,
  schema_version integer not null default 2,
  created_at timestamptz not null,
  reviewer_id text not null,
  project_id text not null,
  batch_id text not null,
  concept_id text null,
  variant_id text null,
  target jsonb not null,
  status text not null,
  rating double precision null,
  reason_tags jsonb not null default '[]'::jsonb,
  note text null,
  generation_context jsonb not null default '{}'::jsonb
);

create index if not exists review_events_project_batch_idx
  on public.review_events (project_id, batch_id, created_at);

create table if not exists public.derived_feedback (
  project_id text primary key,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null
);
