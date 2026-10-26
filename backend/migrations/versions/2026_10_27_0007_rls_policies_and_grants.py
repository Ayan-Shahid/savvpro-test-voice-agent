"""Row-level security on every astro table, plus the narrow grants that go with each policy.

Every table: ENABLE + FORCE RLS. astro_owner gets an all-rows policy because FORCE applies RLS to the owner
too and the security-definer functions run as the owner (the owner could disable RLS anyway, so this grants
nothing new). Application roles never own anything and never bypass RLS.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""

from sqlexec import run

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

REFERENCE = ("sites", "telescopes", "telescope_capabilities", "policies", "targets", "target_aliases", "semesters")
SKY = ("nights", "visibility_windows", "weather_hours")
PERSONAL = (
    "profiles",
    "user_settings",
    "allocations",
    "bookings",
    "waitlist_entries",
    "calls",
    "call_turns",
    "notifications",
    "reports",
    "audit_log",
)
ALL_TABLES = REFERENCE + SKY + PERSONAL

ME = "(select astro.uid())"
ADMIN = "(select astro.is_admin())"


def _sql() -> str:
    out = [
        "grant usage on schema astro to anon, authenticated, astro_worker",
        "grant usage on all sequences in schema astro to authenticated, astro_worker",
        "grant execute on function astro.jwt(), astro.uid(), astro.is_admin() to anon, authenticated, astro_worker",
    ]
    for t in ALL_TABLES:
        out += [
            f"alter table astro.{t} enable row level security",
            f"alter table astro.{t} force row level security",
            f"create policy {t}_owner on astro.{t} for all to astro_owner using (true) with check (true)",
        ]

    # Reference and sky data: readable by everyone; admins edit the catalogue, the worker writes the sky.
    for t in REFERENCE + SKY:
        out += [
            f"grant select on astro.{t} to anon, authenticated, astro_worker",
            f"create policy {t}_read on astro.{t} for select to anon, authenticated, astro_worker using (true)",
        ]
    for t in ("telescopes", "targets", "target_aliases", "policies", "semesters"):
        out += [
            f"grant insert, update, delete on astro.{t} to authenticated",
            f"create policy {t}_admin on astro.{t} for all to authenticated using ({ADMIN}) with check ({ADMIN})",
        ]
    for t in SKY:
        out += [
            f"grant insert, update, delete on astro.{t} to astro_worker",
            f"create policy {t}_worker on astro.{t} for all to astro_worker using (true) with check (true)",
        ]

    out += [
        # profiles
        "grant select on astro.profiles to authenticated, astro_worker",
        "grant update (display_name, affiliation) on astro.profiles to authenticated",
        f"create policy profiles_select on astro.profiles for select to authenticated using (id = {ME} or {ADMIN})",
        f"create policy profiles_update on astro.profiles for update to authenticated using (id = {ME}) "
        f"with check (id = {ME})",
        "create policy profiles_worker on astro.profiles for select to astro_worker using (true)",
        # user_settings
        "grant select on astro.user_settings to authenticated, astro_worker",
        "grant update (voice, speech_rate, input_mode, barge_in, captions, reduced_motion, high_contrast, "
        "time_format, date_order, email_confirmations, keep_transcripts) on astro.user_settings to authenticated",
        f"create policy settings_select on astro.user_settings for select to authenticated using (user_id = {ME})",
        f"create policy settings_update on astro.user_settings for update to authenticated "
        f"using (user_id = {ME}) with check (user_id = {ME})",
        "create policy settings_worker on astro.user_settings for select to astro_worker using (true)",
        # allocations
        "grant select on astro.allocations to authenticated, astro_worker",
        "grant insert, update, delete on astro.allocations to authenticated",
        f"create policy allocations_select on astro.allocations for select to authenticated "
        f"using (user_id = {ME} or {ADMIN})",
        f"create policy allocations_admin on astro.allocations for all to authenticated "
        f"using ({ADMIN}) with check ({ADMIN})",
        "create policy allocations_worker on astro.allocations for select to astro_worker using (true)",
        # bookings
        "grant select, insert on astro.bookings to authenticated",
        "grant update (status, cancel_reason, cancelled_at, confirmed_at, hold_expires_at, checks, updated_at, "
        "version) on astro.bookings to authenticated",
        "grant select, insert, update on astro.bookings to astro_worker",
        f"create policy bookings_select on astro.bookings for select to authenticated using (user_id = {ME} or {ADMIN})",
        f"create policy bookings_insert_hold on astro.bookings for insert to authenticated "
        f"with check (user_id = {ME} and kind = 'observation' and status = 'held' and source in ('voice', 'web'))",
        f"create policy bookings_update_own on astro.bookings for update to authenticated "
        f"using (user_id = {ME}) with check (user_id = {ME})",
        f"create policy bookings_admin on astro.bookings for all to authenticated using ({ADMIN}) with check ({ADMIN})",
        "create policy bookings_worker on astro.bookings for all to astro_worker using (true) with check (true)",
        # waitlist
        "grant select, insert on astro.waitlist_entries to authenticated",
        "grant update (status) on astro.waitlist_entries to authenticated",
        "grant select, insert, update, delete on astro.waitlist_entries to astro_worker",
        f"create policy waitlist_select on astro.waitlist_entries for select to authenticated "
        f"using (user_id = {ME} or {ADMIN})",
        f"create policy waitlist_insert on astro.waitlist_entries for insert to authenticated "
        f"with check (user_id = {ME} and status = 'waiting')",
        f"create policy waitlist_update on astro.waitlist_entries for update to authenticated "
        f"using (user_id = {ME}) with check (user_id = {ME} and status in ('cancelled', 'accepted'))",
        "create policy waitlist_worker on astro.waitlist_entries for all to astro_worker using (true) with check (true)",
        # calls
        "grant select, insert on astro.calls to authenticated",
        "grant update (status, outcome, ended_at, end_reason, turns, booking_id) on astro.calls to authenticated",
        "grant select, update on astro.calls to astro_worker",
        f"create policy calls_select on astro.calls for select to authenticated using (user_id = {ME} or {ADMIN})",
        f"create policy calls_insert on astro.calls for insert to authenticated with check (user_id = {ME})",
        f"create policy calls_update on astro.calls for update to authenticated "
        f"using (user_id = {ME}) with check (user_id = {ME})",
        "create policy calls_worker on astro.calls for all to astro_worker using (true) with check (true)",
        # call_turns
        "grant select, insert on astro.call_turns to authenticated",
        "grant select, update (text, trace), delete on astro.call_turns to astro_worker",
        f"create policy turns_select on astro.call_turns for select to authenticated using (user_id = {ME} or {ADMIN})",
        f"create policy turns_insert_own on astro.call_turns for insert to authenticated "
        f"with check (user_id = {ME} and exists (select 1 from astro.calls c "
        f"where c.id = call_id and c.user_id = {ME}))",
        "create policy turns_worker on astro.call_turns for all to astro_worker using (true) with check (true)",
        # notifications (outbox)
        "grant select, insert on astro.notifications to authenticated",
        "grant select, update on astro.notifications to astro_worker",
        f"create policy notifications_select on astro.notifications for select to authenticated "
        f"using (user_id = {ME} or {ADMIN})",
        f"create policy notifications_insert on astro.notifications for insert to authenticated "
        f"with check (user_id = {ME} and status = 'pending')",
        "create policy notifications_worker on astro.notifications for all to astro_worker "
        "using (true) with check (true)",
        # reports
        "grant select on astro.reports to authenticated",
        "grant select, insert, delete on astro.reports to astro_worker",
        f"create policy reports_select on astro.reports for select to authenticated using (user_id = {ME} or {ADMIN})",
        "create policy reports_worker on astro.reports for all to astro_worker using (true) with check (true)",
        # audit: append only through astro.audit(); admins read
        "grant select on astro.audit_log to authenticated",
        f"create policy audit_admin_read on astro.audit_log for select to authenticated using ({ADMIN})",
    ]

    definer = {
        "astro.busy_intervals(timestamptz, timestamptz)": "authenticated",
        "astro.expire_lapsed_holds(smallint, tstzrange)": "authenticated",
        "astro.my_sessions()": "authenticated",
        "astro.revoke_session(uuid)": "authenticated",
        "astro.waitlist_position(uuid)": "authenticated",
        "astro.audit(text, text, text, jsonb)": "authenticated, astro_worker",
        "astro.handle_new_user()": None,
    }
    for fn, roles in definer.items():
        out.append(f"revoke execute on function {fn} from public")
        if roles:
            out.append(f"grant execute on function {fn} to {roles}")
    return ";\n".join(out) + ";"


def upgrade() -> None:
    run(_sql())


def downgrade() -> None:
    stmts = []
    for t in ALL_TABLES:
        stmts.append(
            f"do $$ declare p record; begin for p in select policyname from pg_policies "
            f"where schemaname = 'astro' and tablename = '{t}' loop "
            f"execute format('drop policy %I on astro.{t}', p.policyname); end loop; end $$"
        )
        stmts.append(f"alter table astro.{t} no force row level security")
        stmts.append(f"alter table astro.{t} disable row level security")
    stmts.append("revoke all on all tables in schema astro from anon, authenticated, astro_worker")
    stmts.append("revoke all on all sequences in schema astro from authenticated, astro_worker")
    stmts.append("revoke usage on schema astro from anon, authenticated, astro_worker")
    run(";\n".join(stmts) + ";")
