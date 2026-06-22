-- Kapruka Gift Concierge: cached web trends
-- A small global cache the daily refresh job (api/refresh_trends.py) writes to
-- via the service-role key. The concierge reads it with the anon key.
-- Run in Supabase SQL editor or via supabase db push.

create table if not exists public.trends_cache (
  id text primary key default 'global',
  data jsonb not null default '{}'::jsonb,
  source text,
  refreshed_at timestamptz default now()
);

alter table public.trends_cache enable row level security;

-- Public read: the concierge fetches cached trends with the anon key.
drop policy if exists "Anyone can read trends" on public.trends_cache;
create policy "Anyone can read trends"
  on public.trends_cache for select using (true);

-- No anon writes. The refresh job uses the service-role key, which bypasses RLS.

-- Seed the single global row so the first upsert has something to merge into.
insert into public.trends_cache (id, data, source)
  values ('global', '{}'::jsonb, 'seed')
  on conflict (id) do nothing;
