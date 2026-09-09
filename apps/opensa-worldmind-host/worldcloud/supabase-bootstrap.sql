-- SPECTER WorldCloud / Supabase bootstrap
-- Apply after worldcloud/schema.sql.
-- The promoted browser payload lives in specter-public. Source/base and working revisions remain private.

insert into storage.buckets (id, name, public, file_size_limit)
values
  ('specter-base',   'specter-base',   false, 52428800),
  ('specter-world',  'specter-world',  false, 52428800),
  ('specter-public', 'specter-public', true,  52428800)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit;

-- Browser clients may only read the promoted/public bucket. Anonymous writes are intentionally absent.
do $$
begin
  create policy "specter public assets read"
    on storage.objects for select
    to anon, authenticated
    using (bucket_id = 'specter-public');
exception
  when duplicate_object then null;
end $$;

-- Optional authenticated reads for private source/working buckets are deliberately NOT granted here.
-- Trusted publisher/agent services should use the service-role credential, which bypasses RLS.
-- Never expose the service-role credential to Vite/browser code.
