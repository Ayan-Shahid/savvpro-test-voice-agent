-- First boot only, as the postgres superuser (run by infra/postgres/initdb.sh with passwords read from
-- /run/secrets and passed as psql variables). Creates roles, the two schemas and the extensions.
\set ON_ERROR_STOP on

create role supabase_auth_admin login noinherit createrole password :'auth_admin_password';
create role astro_owner  login password :'owner_password';
create role astro_api    login noinherit password :'api_password';
create role astro_worker login password :'worker_password';
create role authenticated nologin noinherit;
create role anon          nologin noinherit;
create role service_role  nologin noinherit;   -- exists only because GoTrue's migrations may grant to it
grant authenticated, anon to astro_api;          -- NOINHERIT: astro_api must SET ROLE to use either

create schema auth  authorization supabase_auth_admin;
create schema astro authorization astro_owner;
alter role supabase_auth_admin set search_path = auth;
alter role astro_owner  set search_path = astro, public;
alter role astro_api    set search_path = astro, public;
alter role astro_worker set search_path = astro, public;

revoke create on schema public from public;
revoke all on database :"dbname" from public;
grant connect on database :"dbname" to supabase_auth_admin, astro_owner, astro_api, astro_worker;
grant create on database :"dbname" to supabase_auth_admin;

create extension if not exists btree_gist with schema public;
create extension if not exists pg_trgm    with schema public;

-- Statement safety for the app roles.
alter role astro_api    set statement_timeout = '5s';
alter role astro_api    set idle_in_transaction_session_timeout = '10s';
alter role astro_worker set statement_timeout = '60s';
