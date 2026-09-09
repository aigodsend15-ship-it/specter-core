-- Idempotent SPECTER WorldMind agent seed.
insert into world_agents(id, display_name, role, enabled, capability_version, capabilities, budget, state)
values
  ('forge', 'Forge', 'builder', true, 1,
    array['world.inspect','world.place_object','world.create_building','world.modify_owned','world.demolish_owned','world.create_interior'],
    '{"maxObjectsPerRevision":120,"maxTextureBytesPerRevision":33554432,"maxRadiusMeters":60}'::jsonb, '{}'),
  ('aether', 'Aether', 'planner', true, 1,
    array['world.inspect','world.create_nav','world.commit_revision'],
    '{"maxObjectsPerRevision":0,"maxRoadMetersPerRevision":0}'::jsonb, '{}'),
  ('nyx', 'Nyx', 'scout', true, 1,
    array['world.inspect','world.place_object','world.create_nav'],
    '{"maxObjectsPerRevision":40,"maxRadiusMeters":30}'::jsonb, '{}'),
  ('helios', 'Helios', 'governor', true, 1,
    array['world.inspect','world.create_job','world.commit_revision'],
    '{"maxConcurrentProjects":12}'::jsonb, '{}'),
  ('mason', 'Mason', 'builder', true, 1,
    array['world.inspect','world.place_object','world.create_building','world.modify_owned','world.demolish_owned'],
    '{"maxObjectsPerRevision":120,"maxRadiusMeters":60}'::jsonb, '{}'),
  ('aria', 'Aria', 'interior_designer', true, 1,
    array['world.inspect','world.place_object','world.create_interior','world.trade'],
    '{"maxObjectsPerRevision":80,"maxTextureBytesPerRevision":16777216,"maxRadiusMeters":35}'::jsonb, '{}')
on conflict (id) do update set
  display_name = excluded.display_name,
  role = excluded.role,
  enabled = excluded.enabled,
  capability_version = excluded.capability_version,
  capabilities = excluded.capabilities,
  budget = excluded.budget,
  updated_at = now();
