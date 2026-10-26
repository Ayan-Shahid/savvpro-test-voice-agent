"""Booking state machine trigger, allocation guard, and the few security-definer functions.

Custom SQLSTATEs raised here (mapped to HTTP errors in astro_api.errors):
  AV001 hold_expired, AV002 allocation_exhausted, AV003 illegal_transition, AV004 immutable_field.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UP = r"""
-- Allowed status changes: users may confirm or cancel their hold, and cancel a confirmed booking before it
-- starts. The worker (and definer functions running as the owner) may expire holds and complete bookings.
create function astro.booking_guard() returns trigger
  language plpgsql set search_path = ''
as $$
declare
  actor text := current_user;
  service boolean := current_user in ('astro_worker', 'astro_owner');
  ok boolean;
  granted numeric;
  used numeric;
begin
  if new.period <> old.period or new.telescope_id <> old.telescope_id or new.kind <> old.kind
     or new.user_id is distinct from old.user_id or new.target_id is distinct from old.target_id
     or new.created_at <> old.created_at or new.source <> old.source then
    raise exception 'booking % fields are immutable', old.id using errcode = 'AV004';
  end if;

  if new.status <> old.status then
    ok := case
      when old.status = 'held' and new.status = 'confirmed' then true
      when old.status = 'held' and new.status = 'cancelled' then true
      when old.status = 'held' and new.status = 'expired' then service
      when old.status = 'confirmed' and new.status = 'cancelled'
        then lower(old.period) > now() or service or (select astro.is_admin())
      when old.status = 'confirmed' and new.status = 'completed' then service
      else false
    end;
    if not ok then
      raise exception 'illegal booking transition % -> % by %', old.status, new.status, actor
        using errcode = 'AV003';
    end if;
  end if;

  if old.status = 'held' and new.status = 'confirmed' then
    if old.hold_expires_at <= now() then
      raise exception 'hold % expired', old.id using errcode = 'AV001';
    end if;
    new.hold_expires_at := null;
    new.confirmed_at := now();
    if new.kind = 'observation' then
      -- Serialise one person's confirmations so two simultaneous calls cannot both spend the same hours.
      perform pg_advisory_xact_lock(hashtextextended(new.user_id::text, 42));
      select a.hours_granted into granted
        from astro.allocations a join astro.semesters s on s.code = a.semester_code
       where a.user_id = new.user_id and new.night between s.starts_on and s.ends_on;
      select coalesce(sum(extract(epoch from upper(b.period) - lower(b.period)) / 3600), 0) into used
        from astro.bookings b join astro.semesters s on new.night between s.starts_on and s.ends_on
       where b.user_id = new.user_id and b.id <> new.id and b.kind = 'observation'
         and b.status in ('confirmed', 'completed') and b.night between s.starts_on and s.ends_on;
      if granted is null
         or used + extract(epoch from upper(new.period) - lower(new.period)) / 3600 > granted then
        raise exception 'allocation exhausted (% of % h used)', used, coalesce(granted, 0)
          using errcode = 'AV002';
      end if;
    end if;
  elsif new.status = 'held' and new.hold_expires_at is distinct from old.hold_expires_at then
    -- A hold may be extended, never past ten minutes from creation.
    new.hold_expires_at := least(new.hold_expires_at, old.created_at + interval '10 minutes');
  end if;

  if new.status = 'cancelled' and old.status <> 'cancelled' then
    new.cancelled_at := now();
  end if;
  new.updated_at := now();
  new.version := old.version + 1;
  return new;
end $$;

create trigger bookings_guard before update on astro.bookings
  for each row execute function astro.booking_guard();

-- Others' held and confirmed periods, without who or what. Own rows carry their id so the brain can ignore
-- the caller's current hold.
create function astro.busy_intervals(p_from timestamptz, p_to timestamptz)
  returns table (telescope text, starts_at timestamptz, ends_at timestamptz, kind text,
                 booking_id uuid, mine boolean)
  language sql stable security definer set search_path = ''
as $$
  select t.code, lower(b.period), upper(b.period),
         case when b.kind = 'maintenance' then 'maintenance'
              when b.status = 'held' then 'hold' else 'observation' end,
         case when b.user_id = (select astro.uid()) then b.id end,
         coalesce(b.user_id = (select astro.uid()), false)
    from astro.bookings b join astro.telescopes t on t.id = b.telescope_id
   where b.status in ('held', 'confirmed')
     and (b.status = 'confirmed' or b.hold_expires_at > now())
     and b.period && tstzrange(p_from, p_to)
     and p_to > p_from and p_to - p_from <= interval '62 days'
   order by t.code, lower(b.period)
$$;

-- The constraint cannot see the clock, so a lapsed hold still blocks until it is marked expired. A lapsed
-- hold in the way may belong to someone else, hence definer rights; it only ever touches lapsed holds.
create function astro.expire_lapsed_holds(p_telescope smallint, p_period tstzrange) returns integer
  language plpgsql security definer set search_path = ''
as $$
declare n integer;
begin
  if p_period is null or upper(p_period) - lower(p_period) > interval '1 day' then
    raise exception 'bad period' using errcode = '22023';
  end if;
  update astro.bookings set status = 'expired'
   where status = 'held' and hold_expires_at <= now() and period && p_period
     and (telescope_id = p_telescope or user_id = (select astro.uid()));
  get diagnostics n = row_count;
  return n;
end $$;

-- GoTrue inserts the user; we create the profile, settings and a grade C allocation for the current term.
create function astro.handle_new_user() returns trigger
  language plpgsql security definer set search_path = ''
as $$
begin
  insert into astro.profiles (id, display_name)
  values (new.id, coalesce(left(nullif(btrim(new.raw_user_meta_data ->> 'display_name'), ''), 80),
                           left(split_part(new.email, '@', 1), 80), 'Observer'))
  on conflict (id) do nothing;
  insert into astro.user_settings (user_id) values (new.id) on conflict do nothing;
  insert into astro.allocations (user_id, semester_code, grade, hours_granted)
  select new.id, s.code, 'C', 10 from astro.semesters s
   where current_date between s.starts_on and s.ends_on
  on conflict do nothing;
  return new;
end $$;

create trigger astro_on_auth_user_created after insert on auth.users
  for each row execute function astro.handle_new_user();

create function astro.my_sessions()
  returns table (id uuid, created_at timestamptz, updated_at timestamptz, user_agent text, ip text, aal text)
  language sql stable security definer set search_path = ''
as $$
  select s.id, s.created_at, s.updated_at, s.user_agent, host(s.ip), s.aal::text
    from auth.sessions s where s.user_id = (select astro.uid()) order by s.updated_at desc nulls last
$$;

create function astro.revoke_session(p_id uuid) returns boolean
  language plpgsql security definer set search_path = ''
as $$
declare n integer;
begin
  delete from auth.sessions where id = p_id and user_id = (select astro.uid());
  get diagnostics n = row_count;
  return n = 1;
end $$;

create function astro.waitlist_position(p_entry uuid) returns integer
  language sql stable security definer set search_path = ''
as $$
  select count(*)::integer + 1 from astro.waitlist_entries w, astro.waitlist_entries me
   where me.id = p_entry and me.user_id = (select astro.uid()) and me.status = 'waiting'
     and w.status = 'waiting' and w.telescope_id = me.telescope_id and w.night = me.night
     and (w.grade, w.joined_at, w.seq) < (me.grade, me.joined_at, me.seq)
$$;

create function astro.audit(p_action text, p_entity text, p_entity_id text, p_data jsonb default '{}')
  returns void
  language sql security definer set search_path = ''
as $$
  insert into astro.audit_log (actor_id, actor_role, action, entity, entity_id, data)
  values ((select astro.uid()), coalesce((select astro.jwt()) ->> 'role', 'service'),
          left(p_action, 64), left(p_entity, 64), left(p_entity_id, 128), coalesce(p_data, '{}'))
$$;
"""

DOWN = """
-- DROP TRIGGER on auth.users needs table ownership (GoTrue's); dropping the function cascades instead.
drop function astro.handle_new_user() cascade;
drop function astro.audit(text, text, text, jsonb), astro.waitlist_position(uuid), astro.revoke_session(uuid),
  astro.my_sessions(), astro.expire_lapsed_holds(smallint, tstzrange),
  astro.busy_intervals(timestamptz, timestamptz);
drop trigger bookings_guard on astro.bookings;
drop function astro.booking_guard();
"""


def upgrade() -> None:
    run(UP)


def downgrade() -> None:
    run(DOWN)
