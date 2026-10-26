"""Enum types and the identity helpers every policy uses.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TYPES = """
create type astro.telescope_kind    as enum ('optical_refractor', 'deep_space_reflector', 'submm_dish');
create type astro.target_kind       as enum ('moon', 'planet', 'star', 'double_star', 'star_cluster', 'galaxy',
                                             'nebula', 'supernova_remnant', 'radio_source', 'molecular_cloud');
create type astro.alias_kind        as enum ('official', 'common', 'catalogue', 'mishearing');
create type astro.cloud_type        as enum ('clear', 'cirrus', 'cumulus', 'stratus');
create type astro.allocation_grade  as enum ('A', 'B', 'C');
create type astro.booking_kind      as enum ('observation', 'maintenance');
create type astro.booking_status    as enum ('held', 'confirmed', 'cancelled', 'expired', 'completed');
create type astro.booking_source    as enum ('voice', 'web', 'waitlist', 'admin');
create type astro.waitlist_status   as enum ('waiting', 'offered', 'accepted', 'expired', 'cancelled');
create type astro.call_status       as enum ('connecting', 'active', 'ended');
create type astro.call_outcome      as enum ('booked', 'cancelled_booking', 'info_only', 'abandoned', 'error');
create type astro.call_channel      as enum ('voice', 'text');
create type astro.turn_role         as enum ('caller', 'assistant');
create type astro.notification_kind as enum ('booking_confirmed', 'booking_cancelled', 'waitlist_offer',
                                             'report_ready', 'export_ready', 'weather_changed');
create type astro.outbox_status     as enum ('pending', 'sending', 'sent', 'failed');
"""

# Our own claim readers instead of GoTrue's auth.uid()/auth.jwt(): plain GoTrue (without the Supabase
# Postgres image) does not ship auth.jwt(), and owning these keeps policies independent of auth upgrades.
HELPERS = """
create function astro.jwt() returns jsonb
  language sql stable set search_path = ''
as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;

create function astro.uid() returns uuid
  language sql stable set search_path = ''
as $$ select nullif((select astro.jwt()) ->> 'sub', '')::uuid $$;

create function astro.is_admin() returns boolean
  language sql stable set search_path = ''
as $$
  select coalesce((select astro.jwt()) -> 'app_metadata' ->> 'role', '') = 'admin'
     and coalesce((select astro.jwt()) ->> 'aal', '') = 'aal2'
$$;

create function astro.set_updated_at() returns trigger
  language plpgsql set search_path = ''
as $$ begin new.updated_at := now(); return new; end $$;
"""


def upgrade() -> None:
    run(TYPES)
    run(HELPERS)


def downgrade() -> None:
    run("drop function astro.set_updated_at(), astro.is_admin(), astro.uid(), astro.jwt()")
    for t in (
        "outbox_status",
        "notification_kind",
        "turn_role",
        "call_channel",
        "call_outcome",
        "call_status",
        "waitlist_status",
        "booking_source",
        "booking_status",
        "booking_kind",
        "allocation_grade",
        "cloud_type",
        "alias_kind",
        "target_kind",
        "telescope_kind",
    ):
        run(f"drop type astro.{t}")
