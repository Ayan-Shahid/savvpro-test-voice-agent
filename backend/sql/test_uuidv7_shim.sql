-- TESTS ONLY, and only on PostgreSQL < 18, which lacks the built-in uuidv7(). Production runs 18.
create or replace function public.uuidv7() returns uuid language sql volatile as $$
  select encode(set_bit(set_bit(overlay(uuid_send(gen_random_uuid()) placing
    substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3) from 1 for 6),
    52, 1), 53, 1), 'hex')::uuid
$$;
