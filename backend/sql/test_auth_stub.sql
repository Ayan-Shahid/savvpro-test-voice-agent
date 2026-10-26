-- TESTS ONLY: the slice of GoTrue's `auth` schema our migrations touch, so DB tests run without GoTrue.
create table if not exists auth.users (
  id uuid primary key,
  email text unique,
  raw_user_meta_data jsonb not null default '{}',
  raw_app_meta_data jsonb not null default '{}',
  created_at timestamptz not null default now()
);
do $$ begin create type auth.aal_level as enum ('aal1', 'aal2', 'aal3'); exception when duplicate_object then null; end $$;
create table if not exists auth.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users on delete cascade,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  user_agent text,
  ip inet,
  aal auth.aal_level
);
