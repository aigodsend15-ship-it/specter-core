-- SPECTER WorldCloud v1 persistence schema (PostgreSQL / Supabase compatible)
-- Base assets stay immutable in object storage. This database stores the live, auditable world overlay.

create extension if not exists pgcrypto;

create table if not exists world_revisions (
  id uuid primary key default gen_random_uuid(),
  parent_id uuid references world_revisions(id),
  revision_no bigint generated always as identity unique,
  status text not null check (status in ('proposed','validating','promoted','rejected','rolled_back')),
  manifest_path text,
  manifest_sha256 text check (manifest_sha256 is null or manifest_sha256 ~ '^[0-9a-f]{64}$'),
  created_by text not null,
  created_at timestamptz not null default now(),
  promoted_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create unique index if not exists one_promoted_revision
  on world_revisions ((status)) where status = 'promoted';

create table if not exists world_agents (
  id text primary key,
  display_name text not null,
  role text not null,
  enabled boolean not null default true,
  capability_version integer not null default 1,
  capabilities text[] not null default '{}',
  budget jsonb not null default '{}'::jsonb,
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists world_mutations (
  id uuid primary key default gen_random_uuid(),
  revision_id uuid not null references world_revisions(id) on delete cascade,
  agent_id text not null references world_agents(id),
  capability text not null,
  kind text not null,
  object_key text not null,
  payload jsonb not null,
  bounds jsonb,
  provenance jsonb not null default '{}'::jsonb,
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  validation jsonb not null default '{}'::jsonb,
  accepted boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists world_mutations_revision_idx on world_mutations(revision_id);
create index if not exists world_mutations_agent_idx on world_mutations(agent_id, created_at desc);
create index if not exists world_mutations_object_idx on world_mutations(object_key);

create table if not exists world_objects (
  id uuid primary key default gen_random_uuid(),
  stable_key text not null unique,
  owner_agent_id text references world_agents(id),
  revision_id uuid not null references world_revisions(id),
  asset_key text not null,
  transform jsonb not null,
  collider jsonb,
  nav jsonb,
  properties jsonb not null default '{}'::jsonb,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists world_objects_revision_idx on world_objects(revision_id);
create index if not exists world_objects_owner_idx on world_objects(owner_agent_id);

create table if not exists agent_memories (
  id bigint generated always as identity primary key,
  agent_id text not null references world_agents(id) on delete cascade,
  memory_type text not null,
  importance real not null default 0.5 check (importance between 0 and 1),
  content jsonb not null,
  world_revision_id uuid references world_revisions(id),
  created_at timestamptz not null default now(),
  expires_at timestamptz
);

create index if not exists agent_memories_agent_idx on agent_memories(agent_id, created_at desc);

create table if not exists agent_projects (
  id uuid primary key default gen_random_uuid(),
  owner_agent_id text not null references world_agents(id),
  title text not null,
  goal jsonb not null,
  status text not null check (status in ('planned','active','blocked','validating','completed','abandoned')),
  priority real not null default 0.5 check (priority between 0 and 1),
  resources jsonb not null default '{}'::jsonb,
  plan jsonb not null default '[]'::jsonb,
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists world_events (
  id bigint generated always as identity primary key,
  event_type text not null,
  agent_id text references world_agents(id),
  revision_id uuid references world_revisions(id),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists world_events_created_idx on world_events(created_at desc);

-- RLS: public clients may observe promoted world state, never mutate it directly.
alter table world_revisions enable row level security;
alter table world_agents enable row level security;
alter table world_mutations enable row level security;
alter table world_objects enable row level security;
alter table agent_memories enable row level security;
alter table agent_projects enable row level security;
alter table world_events enable row level security;

-- Supabase-specific policies. Safe to run there; on plain Postgres these can be omitted.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute $p$create policy "public read promoted revisions" on world_revisions for select to anon using (status = 'promoted')$p$;
    execute $p$create policy "public read active objects" on world_objects for select to anon using (active = true and revision_id in (select id from world_revisions where status = 'promoted'))$p$;
    execute $p$create policy "public read enabled agents" on world_agents for select to anon using (enabled = true)$p$;
  end if;
exception
  when duplicate_object then null;
end $$;

-- No INSERT/UPDATE/DELETE policy is granted to anon/authenticated users here.
-- Trusted agent writes must use a service role or a validated server-side RPC.
