"""Profiles, settings, semesters and allocations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UP = """
create table astro.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text not null check (length(display_name) between 1 and 80),
  affiliation   text check (length(affiliation) <= 120),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger profiles_updated_at before update on astro.profiles
  for each row execute function astro.set_updated_at();

create table astro.user_settings (
  user_id              uuid primary key references astro.profiles on delete cascade,
  voice                text not null default 'flux-haley-en',
  speech_rate          numeric(3,2) not null default 1.00 check (speech_rate between 0.85 and 1.15),
  input_mode           text not null default 'hands_free' check (input_mode in ('hands_free', 'push_to_talk')),
  barge_in             boolean not null default true,
  captions             boolean not null default true,
  reduced_motion       text not null default 'system' check (reduced_motion in ('system', 'on', 'off')),
  high_contrast        boolean not null default false,
  time_format          text not null default '12h' check (time_format in ('12h', '24h')),
  date_order           text not null default 'DMY' check (date_order in ('DMY', 'MDY')),
  email_confirmations  boolean not null default true,
  keep_transcripts     boolean not null default true,
  updated_at           timestamptz not null default now()
);
create trigger user_settings_updated_at before update on astro.user_settings
  for each row execute function astro.set_updated_at();

create table astro.semesters (
  code       text primary key,
  starts_on  date not null,
  ends_on    date not null check (ends_on > starts_on)
);

create table astro.allocations (
  id             uuid primary key default uuidv7(),
  user_id        uuid not null references astro.profiles on delete cascade,
  semester_code  text not null references astro.semesters,
  grade          astro.allocation_grade not null,
  hours_granted  numeric(5,2) not null check (hours_granted >= 0),
  constraint uq_allocations_user_semester unique (user_id, semester_code)
);
"""


def upgrade() -> None:
    run(UP)


def downgrade() -> None:
    run("drop table astro.allocations, astro.semesters, astro.user_settings, astro.profiles")
