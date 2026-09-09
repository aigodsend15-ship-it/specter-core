-- SPECTER WorldCloud v1 persistence schema (PostgreSQL / Supabase compatible)
-- Base assets are immutable in object storage. This database is the auditable live-world control plane.

create extension if not exists pgcrypto;

create table if not exists world_revisions (
  id uuid primary key default gen_random_uuid(),
  parent_id uuid references world_revisions(id),
  revision_no bigint generated always as identity unique,
  status text not null check (status in ('proposed','validating','candidate','promoted','superseded','rejected','rolled_back')),
  manifest_path text,
  manifest_sha256 text check (manifest_sha256 is null or manifest_sha256 ~ '^[0-9a-f]{64}$'),
  created_by text not null,
  created_at timestamptz not null default now(),
  promoted_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create unique index if not exists one_promoted_revision
  on world_revisions ((status)) where status = 'promoted';

create table if not exists world_runtime (
  singleton boolean primary key default true check (singleton),
  current_revision_id uuid references world_revisions(id),
  updated_at timestamptz not null default now()
);
insert into world_runtime(singleton) values (true) on conflict (singleton) do nothing;

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

-- Materialized current object state. Historical truth lives in world_mutations + immutable manifests.
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

create table if not exists world_validation_runs (
  id uuid primary key default gen_random_uuid(),
  revision_id uuid not null references world_revisions(id) on delete cascade,
  gate text not null,
  passed boolean not null,
  metrics jsonb not null default '{}'::jsonb,
  evidence_sha256 text check (evidence_sha256 is null or evidence_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  unique(revision_id, gate)
);

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

-- Replay protection for trusted agent proposals. Old nonces can be pruned after their timestamp window.
create table if not exists agent_nonces (
  agent_id text not null references world_agents(id) on delete cascade,
  nonce text not null,
  created_at timestamptz not null default now(),
  primary key(agent_id, nonce)
);
create index if not exists agent_nonces_created_idx on agent_nonces(created_at);

create table if not exists world_events (
  id bigint generated always as identity primary key,
  event_type text not null,
  agent_id text references world_agents(id),
  revision_id uuid references world_revisions(id),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists world_events_created_idx on world_events(created_at desc);

-- Atomic promotion. A candidate becomes the sole promoted revision; the previous promoted revision is retained as superseded.
create or replace function promote_world_revision(p_revision uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_status text;
begin
  perform pg_advisory_xact_lock(hashtext('specter-world-promotion'));
  select status into v_status from world_revisions where id = p_revision for update;
  if v_status is null then raise exception 'revision_not_found'; end if;
  if v_status <> 'candidate' then raise exception 'revision_not_candidate'; end if;
  if exists (
    select 1 from (
      values ('schema'), ('asset-license'), ('sha256'), ('collision'), ('navmesh'),
             ('placement-overlap'), ('object-budget'), ('texture-budget'), ('frame-time'), ('rollback')
    ) as required(gate)
    where not exists (
      select 1 from world_validation_runs v
      where v.revision_id = p_revision and v.gate = required.gate and v.passed
    )
  ) then
    raise exception 'validation_gates_incomplete';
  end if;

  update world_revisions set status = 'superseded' where status = 'promoted';
  update world_revisions set status = 'promoted', promoted_at = now() where id = p_revision;
  update world_runtime set current_revision_id = p_revision, updated_at = now() where singleton;
  insert into world_events(event_type, revision_id, payload)
    values ('revision_promoted', p_revision, jsonb_build_object('revision', p_revision));
  return p_revision;
end;
$$;
revoke all on function promote_world_revision(uuid) from public;

-- RLS: browser clients can observe only the current promoted world. They cannot mutate it.
alter table world_revisions enable row level security;
alter table world_runtime enable row level security;
alter table world_agents enable row level security;
alter table world_mutations enable row level security;
alter table world_objects enable row level security;
alter table world_validation_runs enable row level security;
alter table agent_memories enable row level security;
alter table agent_projects enable row level security;
alter table agent_nonces enable row level security;
alter table world_events enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    begin
      create policy "public read current revision" on world_revisions for select to anon
        using (id = (select current_revision_id from world_runtime where singleton));
    exception when duplicate_object then null; end;
    begin
      create policy "public read runtime pointer" on world_runtime for select to anon using (singleton);
    exception when duplicate_object then null; end;
    begin
      create policy "public read active current objects" on world_objects for select to anon
        using (active and revision_id = (select current_revision_id from world_runtime where singleton));
    exception when duplicate_object then null; end;
    begin
      create policy "public read enabled agents" on world_agents for select to anon using (enabled);
    exception when duplicate_object then null; end;
  end if;

  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function promote_world_revision(uuid) to service_role;
  end if;
end $$;

-- No INSERT/UPDATE/DELETE policy is granted to anon/authenticated users.
-- Trusted writes use service_role only from server/Edge Function/CI secrets; never from Vite/browser code.
