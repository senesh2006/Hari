-- Kapruka Gift Concierge: storage for custom-product photo uploads
-- Buyers personalising a product (photo mug, photo cake…) upload an image that
-- gets attached to their order. Files live under custom-uploads/{user_id}/...
-- Run in the Supabase SQL editor.

-- Public bucket so Kapruka can fetch the photo via its URL at fulfilment time.
insert into storage.buckets (id, name, public)
  values ('custom-uploads', 'custom-uploads', true)
  on conflict (id) do nothing;

-- Signed-in users may upload only into their own {user_id}/ folder.
drop policy if exists "Users upload own custom images" on storage.objects;
create policy "Users upload own custom images"
  on storage.objects for insert to authenticated
  with check (
    bucket_id = 'custom-uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- And manage (replace/remove) their own uploads.
drop policy if exists "Users manage own custom images" on storage.objects;
create policy "Users manage own custom images"
  on storage.objects for update to authenticated
  using (bucket_id = 'custom-uploads' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "Users delete own custom images" on storage.objects;
create policy "Users delete own custom images"
  on storage.objects for delete to authenticated
  using (bucket_id = 'custom-uploads' and (storage.foldername(name))[1] = auth.uid()::text);

-- Public read (the bucket is public; this makes the intent explicit).
drop policy if exists "Anyone can read custom images" on storage.objects;
create policy "Anyone can read custom images"
  on storage.objects for select
  using (bucket_id = 'custom-uploads');
