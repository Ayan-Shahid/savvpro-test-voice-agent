"""Calls, bookings (with the two exclusion constraints) and the waitlist.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UP = """
create table astro.calls (
  id                uuid primary key default uuidv7(),
  user_id           uuid not null references astro.profiles on delete cascade,
  channel           astro.call_channel not null,
  status            astro.call_status not null default 'connecting',
  outcome           astro.call_outcome,
  stt_provider      text not null,
  tts_provider      text not null,
  snapshot_version  text not null,
  started_at        timestamptz not null default now(),
  ended_at          timestamptz,
  end_reason        text,
  turns             smallint not null default 0,
  booking_id        uuid,
  check ((status = 'ended') = (ended_at is not null))
);
create index ix_calls_user_started on astro.calls (user_id, started_at desc);
create unique index one_live_call_per_user on astro.calls (user_id) where status <> 'ended';

create table astro.call_turns (
  call_id         uuid references astro.calls on delete cascade,
  seq             smallint,
  role            astro.turn_role,
  user_id         uuid not null,
  text            text,
  stt_confidence  real,
  intent          text,
  state_before    text not null,
  state_after     text not null,
  trace           jsonb,
  latency_ms      jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  primary key (call_id, seq, role)
);
create index ix_call_turns_user_created on astro.call_turns (user_id, created_at);

create table astro.bookings (
  id               uuid primary key default uuidv7(),
  kind             astro.booking_kind not null default 'observation',
  user_id          uuid references astro.profiles on delete cascade,
  telescope_id     smallint not null references astro.telescopes,
  target_id        uuid references astro.targets,
  period           tstzrange not null,
  night            date not null,
  status           astro.booking_status not null,
  hold_expires_at  timestamptz,
  source           astro.booking_source not null,
  call_id          uuid references astro.calls on delete set null,
  weather_pending  boolean not null default false,
  checks           jsonb not null default '[]',
  idempotency_key  text,
  cancel_reason    text,
  created_at       timestamptz not null default now(),
  confirmed_at     timestamptz,
  cancelled_at     timestamptz,
  updated_at       timestamptz not null default now(),
  version          integer not null default 1,

  constraint period_half_open  check (lower_inc(period) and not upper_inc(period) and not isempty(period)),
  constraint period_on_grid    check (extract(epoch from lower(period))::bigint % 900 = 0
                                      and extract(epoch from upper(period))::bigint % 900 = 0),
  constraint length_in_policy  check (kind = 'maintenance'
                                      or upper(period) - lower(period) between interval '30 minutes'
                                                                           and interval '240 minutes'),
  constraint maintenance_has_no_user check ((kind = 'maintenance') = (user_id is null)),
  constraint observation_has_target  check ((kind = 'observation') = (target_id is not null)),
  constraint held_has_expiry         check (status <> 'held' or hold_expires_at is not null),

  constraint no_telescope_overlap exclude using gist (telescope_id with =, period with &&)
    where (status in ('held', 'confirmed')),
  constraint no_person_overlap exclude using gist (user_id with =, period with &&)
    where (status in ('held', 'confirmed') and kind = 'observation')
);
create index ix_bookings_user_start on astro.bookings (user_id, lower(period) desc);
create index ix_bookings_telescope_night on astro.bookings (telescope_id, night);
create index ix_bookings_hold_expiry on astro.bookings (hold_expires_at) where status = 'held';
create unique index uq_bookings_idempotency on astro.bookings (user_id, idempotency_key)
  where idempotency_key is not null;
alter table astro.calls add constraint fk_calls_booking_id_bookings
  foreign key (booking_id) references astro.bookings on delete set null;

create table astro.waitlist_entries (
  id                  uuid primary key default uuidv7(),
  seq                 bigint generated always as identity,
  user_id             uuid not null references astro.profiles on delete cascade,
  telescope_id        smallint not null references astro.telescopes,
  target_id           uuid not null references astro.targets,
  night               date not null,
  wanted              tstzrange not null,
  flex_minutes        smallint not null default 0 check (flex_minutes between 0 and 60),
  grade               astro.allocation_grade not null,
  status              astro.waitlist_status not null default 'waiting',
  offered_booking_id  uuid references astro.bookings on delete set null,
  offer_expires_at    timestamptz,
  joined_at           timestamptz not null default now(),
  check (status <> 'offered' or (offered_booking_id is not null and offer_expires_at is not null))
);
create unique index one_active_entry on astro.waitlist_entries (user_id, telescope_id, night)
  where status in ('waiting', 'offered');
create index heap_order on astro.waitlist_entries (telescope_id, night, grade, joined_at, seq)
  where status = 'waiting';
"""


def upgrade() -> None:
    run(UP)


def downgrade() -> None:
    run("alter table astro.calls drop constraint fk_calls_booking_id_bookings")
    run("drop table astro.waitlist_entries, astro.bookings, astro.call_turns, astro.calls")
