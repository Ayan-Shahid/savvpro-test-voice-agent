"""Use the name Google (or any OAuth provider) supplies when creating a profile.

GoTrue stores provider profile data in raw_user_meta_data as full_name / name; email sign-ups use our own
display_name field. Only used for the display name, never for authorisation.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _fn(name_expr: str) -> str:
    return f"""
create or replace function astro.handle_new_user() returns trigger
  language plpgsql security definer set search_path = ''
as $$
begin
  insert into astro.profiles (id, display_name)
  values (new.id, coalesce({name_expr}, left(split_part(new.email, '@', 1), 80), 'Observer'))
  on conflict (id) do nothing;
  insert into astro.user_settings (user_id) values (new.id) on conflict do nothing;
  insert into astro.allocations (user_id, semester_code, grade, hours_granted)
  select new.id, s.code, 'C', 10 from astro.semesters s
   where current_date between s.starts_on and s.ends_on
  on conflict do nothing;
  return new;
end $$;
revoke execute on function astro.handle_new_user() from public;
"""


NEW = """left(nullif(btrim(coalesce(new.raw_user_meta_data ->> 'display_name',
                                   new.raw_user_meta_data ->> 'full_name',
                                   new.raw_user_meta_data ->> 'name', '')), ''), 80)"""
OLD = "left(nullif(btrim(new.raw_user_meta_data ->> 'display_name'), ''), 80)"


def upgrade() -> None:
    run(_fn(NEW))


def downgrade() -> None:
    run(_fn(OLD))
