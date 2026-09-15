-- KosFlow Finance v10.10 - Supabase schema
-- Jalankan seluruh file ini di Supabase > SQL Editor > New query > Run.

create extension if not exists pgcrypto;

create table if not exists public.devices (
  id uuid primary key default gen_random_uuid(),
  browser_device_id text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ollama_pairings (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null unique references public.devices(id) on delete cascade,
  bridge_url text not null default 'http://127.0.0.1:8788',
  model_name text,
  token_ciphertext text,
  token_iv text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.finance_settings (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null unique references public.devices(id) on delete cascade,
  cash numeric(18,2) not null default 0,
  allowance numeric(18,2) not null default 0,
  income_weekly_min numeric(18,2) not null default 0,
  income_weekly_max numeric(18,2) not null default 0,
  monthly_kos numeric(18,2) not null default 0,
  weeks integer not null default 2 check (weeks > 0),
  buffer numeric(18,2) not null default 0,
  weekly_needs numeric(18,2) not null default 0,
  risk text not null default 'medium' check (risk in ('low','medium','high')),
  stocks text,
  stock_lot_mode text not null default 'auto',
  stock_lot_budget numeric not null default 100000,
  stock_recommendation_count integer not null default 5,
  banks text,
  bank_interest_ranges jsonb not null default '["0.5-4","4-6"]'::jsonb,
  notes text,
  kos_source text not null default 'self' check (kos_source in ('self','parent','mixed')),
  kos_self_contribution numeric(18,2) not null default 0,
  kos_parent_contribution numeric(18,2) not null default 0,
  kos_cycle_start date,
  kos_funding_scope text not null default 'current_cycle' check (kos_funding_scope in ('current_cycle','ongoing')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.analyses (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.devices(id) on delete cascade,
  model_name text,
  input_json jsonb not null default '{}'::jsonb,
  result_json jsonb not null default '{}'::jsonb,
  ai_json jsonb,
  ai_raw text,
  created_at timestamptz not null default now()
);
create index if not exists analyses_device_created_idx on public.analyses(device_id, created_at desc);

create table if not exists public.market_snapshots (
  id bigint generated always as identity primary key,
  analysis_id uuid not null references public.analyses(id) on delete cascade,
  asset_type text not null check (asset_type in ('crypto','stock')),
  symbol text not null,
  price numeric(30,8),
  change_percent numeric(18,8),
  source text,
  source_url text,
  market_timestamp timestamptz,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists market_snapshots_analysis_idx on public.market_snapshots(analysis_id);

create table if not exists public.research_sources (
  id bigint generated always as identity primary key,
  analysis_id uuid not null references public.analyses(id) on delete cascade,
  category text not null check (category in ('bank','fund')),
  entity_name text,
  title text,
  snippet text,
  source_url text,
  official boolean not null default false,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists research_sources_analysis_idx on public.research_sources(analysis_id);

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at=now(); return new; end $$;

drop trigger if exists devices_touch on public.devices;
create trigger devices_touch before update on public.devices for each row execute function public.touch_updated_at();
drop trigger if exists ollama_pairings_touch on public.ollama_pairings;
create trigger ollama_pairings_touch before update on public.ollama_pairings for each row execute function public.touch_updated_at();
drop trigger if exists finance_settings_touch on public.finance_settings;
create trigger finance_settings_touch before update on public.finance_settings for each row execute function public.touch_updated_at();

-- Backend Wasmer memakai Service Role Key. Browser TIDAK mengakses tabel Supabase langsung.
alter table public.devices enable row level security;
alter table public.ollama_pairings enable row level security;
alter table public.finance_settings enable row level security;
alter table public.analyses enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.research_sources enable row level security;
-- Tidak membuat policy publik. Service role backend tetap dapat mengakses data.


-- v10.10 migration: multi-select range bunga bank
alter table public.finance_settings
  add column if not exists bank_interest_ranges jsonb
  not null default '["0.5-4","4-6"]'::jsonb;


-- v10.10 migration: pendapatan manual + pembagian pembayaran kos
alter table public.finance_settings
  add column if not exists income_weekly_min numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists income_weekly_max numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists kos_self_contribution numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists kos_parent_contribution numeric(18,2) not null default 0;


-- v10.10 migration: periode berlakunya pembagian pembayaran kos
alter table public.finance_settings
  add column if not exists kos_cycle_start date;
alter table public.finance_settings
  add column if not exists kos_funding_scope text
  not null default 'current_cycle';
