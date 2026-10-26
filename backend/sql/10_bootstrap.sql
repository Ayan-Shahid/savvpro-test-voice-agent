-- Every deploy, after GoTrue has migrated `auth` and before `alembic upgrade head`. Idempotent.
\set ON_ERROR_STOP on
grant usage on schema auth to astro_owner;
grant references, trigger on auth.users to astro_owner;
grant select (id, email, raw_user_meta_data) on auth.users to astro_owner;
grant select (id, user_id, created_at, updated_at, user_agent, ip, aal), delete on auth.sessions to astro_owner;
