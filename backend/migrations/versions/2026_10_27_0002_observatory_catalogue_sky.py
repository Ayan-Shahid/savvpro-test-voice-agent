"""Observatory, catalogue and precomputed sky.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UP = """
create table astro.sites (
  id           smallint generated always as identity primary key,
  slug         text not null unique,
  name         text not null,
  lat_deg      numeric(8,5) not null check (lat_deg between -90 and 90),
  lon_deg      numeric(8,5) not null check (lon_deg between -180 and 180),
  elevation_m  integer not null,
  tz           text not null
);

create table astro.telescopes (
  id                  smallint generated always as identity primary key,
  site_id             smallint not null references astro.sites,
  code                text not null unique,
  name                text not null,
  kind                astro.telescope_kind not null,
  min_altitude_deg    numeric(4,1) not null,
  sun_max_alt_bright  numeric(4,1),
  sun_max_alt_faint   numeric(4,1),
  sun_avoid_deg       numeric(4,1) not null,
  moon_avoid_deg      numeric(4,1) not null,
  max_cloud_pct       smallint,
  max_pwv_mm          numeric(3,1),
  max_rain_pct        smallint not null,
  max_wind_kmh        smallint not null,
  min_minutes         smallint not null default 30,
  max_minutes         smallint not null default 240,
  display_order       smallint not null,
  active              boolean not null default true
);

create table astro.targets (
  id              uuid primary key default uuidv7(),
  slug            text not null unique,
  name            text not null,
  kind            astro.target_kind not null,
  ephemeris_body  text,
  ra_hours        numeric(9,6),
  dec_deg         numeric(9,6),
  magnitude       numeric(4,1),
  catalogue_ids   text[] not null default '{}',
  check ((ephemeris_body is null) <> (ra_hours is null and dec_deg is null))
);

create table astro.target_aliases (
  alias      text primary key check (alias = lower(alias)),
  target_id  uuid not null references astro.targets on delete cascade,
  kind       astro.alias_kind not null
);
create index ix_target_aliases_target_id on astro.target_aliases (target_id);
create index ix_target_aliases_trgm on astro.target_aliases using gin (alias gin_trgm_ops);

create table astro.telescope_capabilities (
  telescope_id   smallint references astro.telescopes,
  target_kind    astro.target_kind,
  allowed        boolean not null,
  max_magnitude  numeric(4,1),
  primary key (telescope_id, target_kind)
);

create table astro.policies (
  key         text primary key,
  value       jsonb not null,
  version     integer not null default 1,
  updated_at  timestamptz not null default now()
);

-- `grid` holds the brain's 5-minute sample grid for the night (Sun, Moon and every target, tenths of a
-- degree, local noon to local noon). It replaces the per-row ephemeris_samples table from the first draft:
-- the brain reads whole nights, so one JSONB value per night is ~70 kB instead of 7,000 rows.
create table astro.nights (
  site_id         smallint references astro.sites,
  night           date,
  sunset          timestamptz not null,
  nautical_dusk   timestamptz not null,
  astro_dusk      timestamptz not null,
  astro_dawn      timestamptz not null,
  nautical_dawn   timestamptz not null,
  sunrise         timestamptz not null,
  moon_illum_pct  numeric(5,2) not null,
  grid            jsonb not null,
  computed_at     timestamptz not null default now(),
  primary key (site_id, night)
);

create table astro.visibility_windows (
  target_id        uuid references astro.targets on delete cascade,
  night            date,
  min_alt_deg      smallint,
  windows          tstzmultirange not null,
  transit_at       timestamptz,
  transit_alt_deg  numeric(4,1),
  sun_sep_min_deg  numeric(5,1) not null,
  primary key (target_id, night, min_alt_deg)
);

create table astro.weather_hours (
  site_id        smallint references astro.sites,
  hour_start     timestamptz check (extract(epoch from hour_start)::bigint % 3600 = 0),
  cloud_pct      smallint not null check (cloud_pct between 0 and 100),
  cloud_type     astro.cloud_type not null,
  pwv_mm         numeric(4,1) not null,
  rain_pct       smallint not null check (rain_pct between 0 and 100),
  wind_kmh       smallint not null,
  seed           bigint not null,
  scenario       text,
  primary key (site_id, hour_start)
);
"""


def upgrade() -> None:
    run(UP)


def downgrade() -> None:
    run(
        "drop table astro.weather_hours, astro.visibility_windows, astro.nights, astro.policies, "
        "astro.telescope_capabilities, astro.target_aliases, astro.targets, astro.telescopes, astro.sites"
    )
