-- Personalization: session memory, recipients, wishlist, order history

alter table public.profiles
  add column if not exists session_facts jsonb default '{}'::jsonb;

-- Gift contacts / recipients
create table if not exists public.recipients (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  relationship text,
  birthday date,
  anniversary date,
  city text,
  interests text[] default '{}',
  avoid text[] default '{}',
  notes text,
  last_gift_summary text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists recipients_user_id_idx on public.recipients(user_id);

alter table public.recipients enable row level security;

drop policy if exists "Users read own recipients" on public.recipients;
create policy "Users read own recipients"
  on public.recipients for select using (auth.uid() = user_id);

drop policy if exists "Users insert own recipients" on public.recipients;
create policy "Users insert own recipients"
  on public.recipients for insert with check (auth.uid() = user_id);

drop policy if exists "Users update own recipients" on public.recipients;
create policy "Users update own recipients"
  on public.recipients for update using (auth.uid() = user_id);

drop policy if exists "Users delete own recipients" on public.recipients;
create policy "Users delete own recipients"
  on public.recipients for delete using (auth.uid() = user_id);

drop trigger if exists recipients_updated_at on public.recipients;
create trigger recipients_updated_at
  before update on public.recipients
  for each row execute function public.handle_updated_at();

-- Saved wishlist (hearted products)
create table if not exists public.wishlist_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  product_id text,
  name text not null,
  url text,
  image text,
  price text,
  currency text default 'LKR',
  created_at timestamptz default now(),
  unique (user_id, product_id)
);

create index if not exists wishlist_items_user_id_idx on public.wishlist_items(user_id);

alter table public.wishlist_items enable row level security;

drop policy if exists "Users read own wishlist" on public.wishlist_items;
create policy "Users read own wishlist"
  on public.wishlist_items for select using (auth.uid() = user_id);

drop policy if exists "Users insert own wishlist" on public.wishlist_items;
create policy "Users insert own wishlist"
  on public.wishlist_items for insert with check (auth.uid() = user_id);

drop policy if exists "Users update own wishlist" on public.wishlist_items;
create policy "Users update own wishlist"
  on public.wishlist_items for update using (auth.uid() = user_id);

drop policy if exists "Users delete own wishlist" on public.wishlist_items;
create policy "Users delete own wishlist"
  on public.wishlist_items for delete using (auth.uid() = user_id);

-- Minimal order history (saved after successful checkout)
create table if not exists public.order_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  recipient_name text,
  recipient_id uuid references public.recipients(id) on delete set null,
  items_summary text,
  order_ref text,
  grand_total numeric,
  currency text default 'LKR',
  ordered_at timestamptz default now()
);

create index if not exists order_history_user_id_idx on public.order_history(user_id);

alter table public.order_history enable row level security;

drop policy if exists "Users read own orders" on public.order_history;
create policy "Users read own orders"
  on public.order_history for select using (auth.uid() = user_id);

drop policy if exists "Users insert own orders" on public.order_history;
create policy "Users insert own orders"
  on public.order_history for insert with check (auth.uid() = user_id);
