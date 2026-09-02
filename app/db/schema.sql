-- Run this once against your Supabase project (SQL editor or `supabase db execute`).
-- Generic across websites: only `sites` gains a row and `pdp_data.raw` absorbs
-- site-specific fields when a new adapter is added; no other table changes.

create extension if not exists "pgcrypto";

create table if not exists clients (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    email text unique not null,
    created_at timestamptz not null default now()
);

create table if not exists plans (
    id uuid primary key default gen_random_uuid(),
    name text unique not null,
    requests_per_minute int not null default 30,
    daily_quota int not null default 1000,
    monthly_quota int not null default 20000,
    duration_days int not null default 30,
    price_cents int not null default 0,
    created_at timestamptz not null default now()
);

create table if not exists api_keys (
    id uuid primary key default gen_random_uuid(),
    client_id uuid not null references clients (id) on delete cascade,
    plan_id uuid not null references plans (id),
    key_hash text unique not null,
    key_prefix text not null, -- first 8 chars, shown in dashboards so a key is recognizable without exposing it
    status text not null default 'active' check (status in ('active', 'suspended', 'expired')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create index if not exists idx_api_keys_client on api_keys (client_id);

create table if not exists sites (
    id uuid primary key default gen_random_uuid(),
    site_key text unique not null, -- e.g. 'shopee_br'
    display_name text not null,
    base_domain text not null,
    is_active boolean not null default true
);

insert into sites (site_key, display_name, base_domain)
values
    ('shopee_br', 'Shopee Brazil', 'shopee.com.br'),
    ('shopee_th', 'Shopee Thailand', 'shopee.co.th'),
    ('shopee_vn', 'Shopee Vietnam', 'shopee.vn')
on conflict (site_key) do nothing;

create table if not exists usage_logs (
    id uuid primary key default gen_random_uuid(),
    api_key_id uuid references api_keys (id) on delete set null,
    site_id uuid references sites (id),
    request_url text not null,
    status text not null check (
        status in ('success', 'failed', 'captcha_blocked', 'rate_limited', 'quota_exceeded')
    ),
    response_time_ms int,
    created_at timestamptz not null default now()
);

create index if not exists idx_usage_logs_key_time on usage_logs (api_key_id, created_at);
create index if not exists idx_usage_logs_time on usage_logs (created_at);

create table if not exists pdp_data (
    id uuid primary key default gen_random_uuid(),
    site_id uuid not null references sites (id),
    usage_log_id uuid references usage_logs (id) on delete set null,
    product_url text not null,
    external_product_id text,
    title text,
    price numeric,
    currency text,
    rating numeric,
    sold_count int,
    image_urls text[] default '{}',
    raw jsonb not null default '{}'::jsonb, -- site-specific extras live here
    scraped_at timestamptz not null default now()
);

create index if not exists idx_pdp_data_site on pdp_data (site_id, scraped_at);
