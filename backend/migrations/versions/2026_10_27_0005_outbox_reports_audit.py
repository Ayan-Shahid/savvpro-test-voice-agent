"""Notification outbox, PDF reports and the append-only audit log.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UP = """
create table astro.notifications (
  id          uuid primary key default uuidv7(),
  user_id     uuid not null references astro.profiles on delete cascade,
  kind        astro.notification_kind not null,
  booking_id  uuid,
  payload     jsonb not null,
  dedupe_key  text not null unique,
  status      astro.outbox_status not null default 'pending',
  attempts    smallint not null default 0,
  last_error  text,
  created_at  timestamptz not null default now(),
  sent_at     timestamptz
);
create index ix_notifications_pending on astro.notifications (status, created_at)
  where status in ('pending', 'failed');
create index ix_notifications_user on astro.notifications (user_id);

create table astro.reports (
  id            uuid primary key default uuidv7(),
  booking_id    uuid not null unique,
  user_id       uuid not null references astro.profiles on delete cascade,
  storage_path  text not null,
  sha256        bytea not null,
  bytes         integer not null,
  created_at    timestamptz not null default now()
);
create index ix_reports_user on astro.reports (user_id);

create table astro.audit_log (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  actor_id    uuid,
  actor_role  text not null,
  action      text not null,
  entity      text not null,
  entity_id   text not null,
  data        jsonb not null default '{}'
);
"""


def upgrade() -> None:
    run(UP)


def downgrade() -> None:
    run("drop table astro.audit_log, astro.reports, astro.notifications")
