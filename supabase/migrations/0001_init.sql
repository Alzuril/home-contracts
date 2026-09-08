create extension if not exists pgcrypto;

create table profiles (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  pin_hash text not null,
  points integer not null default 0 check (points >= 0),
  level integer generated always as (1 + floor(points / 100.0)::int) stored,
  created_at timestamptz not null default now()
);

create table contracts (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text not null default '',
  rating integer check (rating between 1 and 5),
  author_id uuid not null references profiles(id),
  assignee_id uuid references profiles(id),
  status text not null default 'open'
    check (status in ('open','accepted','done_pending_confirm','confirmed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table notifications_log (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null unique references contracts(id) on delete cascade,
  notified_at timestamptz not null default now()
);

create table push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references profiles(id) on delete cascade,
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);

create view public_profiles as
  select id, name, points, level, created_at from profiles;

alter table profiles enable row level security;
alter table contracts enable row level security;
alter table notifications_log enable row level security;
alter table push_subscriptions enable row level security;

revoke all on profiles, contracts, notifications_log, push_subscriptions from anon, authenticated;

grant select on public_profiles to anon, authenticated;
grant select on contracts to anon, authenticated;

create policy "contracts readable by anyone" on contracts for select using (true);

create or replace function create_profile(p_name text, p_pin text)
returns public_profiles
language plpgsql security definer set search_path = public, extensions as $create_profile$
declare v_id uuid;
begin
  if length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_pin !~ '^[0-9]{4}$' then
    raise exception 'pin must be exactly 4 digits';
  end if;
  begin
    insert into profiles (name, pin_hash) values (trim(p_name), crypt(p_pin, gen_salt('bf')))
    returning id into v_id;
  exception when unique_violation then
    raise exception 'name already taken';
  end;
  return (select pp from public_profiles pp where pp.id = v_id);
end; $create_profile$;

grant execute on function create_profile(text, text) to anon, authenticated;

create or replace function verify_pin(p_profile_id uuid, p_pin text)
returns boolean
language sql
security definer
set search_path = public, extensions
as $verify_pin$
  select exists (
    select 1 from profiles
    where id = p_profile_id and pin_hash = crypt(p_pin, pin_hash)
  );
$verify_pin$;

create or replace function create_contract(
  p_author_id uuid, p_pin text, p_title text, p_description text,
  p_assignee_id uuid default null
) returns contracts
language plpgsql security definer set search_path = public, extensions as $create_contract$
declare
  v_contract contracts;
begin
  if not verify_pin(p_author_id, p_pin) then
    raise exception 'invalid pin';
  end if;
  insert into contracts (title, description, author_id, assignee_id, status)
  values (p_title, coalesce(p_description, ''), p_author_id, p_assignee_id,
          case when p_assignee_id is null then 'open' else 'accepted' end)
  returning * into v_contract;
  return v_contract;
end;
$create_contract$;

create or replace function accept_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public, extensions as $accept_contract$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set assignee_id = p_profile_id, status = 'accepted', updated_at = now()
    where id = p_contract_id and status = 'open' and author_id != p_profile_id
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not available'; end if;
  return v_contract;
end; $accept_contract$;

create or replace function decline_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public, extensions as $decline_contract$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set assignee_id = null, status = 'open', updated_at = now()
    where id = p_contract_id and assignee_id = p_profile_id
      and status in ('accepted','done_pending_confirm')
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not declinable by this profile'; end if;
  return v_contract;
end; $decline_contract$;

create or replace function complete_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public, extensions as $complete_contract$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set status = 'done_pending_confirm', updated_at = now()
    where id = p_contract_id and assignee_id = p_profile_id and status = 'accepted'
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not completable by this profile'; end if;
  return v_contract;
end; $complete_contract$;

create or replace function confirm_contract(
  p_contract_id uuid, p_profile_id uuid, p_pin text, p_rating int
) returns contracts language plpgsql security definer set search_path = public, extensions as $confirm_contract$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  if p_rating not between 1 and 5 then raise exception 'rating must be between 1 and 5'; end if;
  update contracts set status = 'confirmed', rating = p_rating, updated_at = now()
    where id = p_contract_id and author_id = p_profile_id and status = 'done_pending_confirm'
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not confirmable by this profile'; end if;
  update profiles set points = points + p_rating where id = v_contract.assignee_id;
  return v_contract;
end; $confirm_contract$;

grant execute on function verify_pin(uuid, text) to anon, authenticated;
grant execute on function create_contract(uuid, text, text, text, uuid) to anon, authenticated;
grant execute on function accept_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function decline_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function complete_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function confirm_contract(uuid, uuid, text, int) to anon, authenticated;
