-- Lower the level threshold: was 1 level per 100 points (never moved in
-- practice, since a single confirmed contract gives at most 5 points).
-- Now 1 level per 20 points - a couple of well-rated contracts actually
-- moves the needle.
drop view public_profiles;
alter table profiles drop column level;
alter table profiles add column level integer generated always as (1 + floor(points / 20.0)::int) stored;
create view public_profiles as
  select id, name, points, level, created_at from profiles;
grant select on public_profiles to anon, authenticated;

-- Let the proposer delete their own rejected reward proposal.
create or replace function delete_shop_item(p_profile_id uuid, p_pin text, p_item_id uuid)
returns boolean
language plpgsql security definer set search_path = public, extensions as $delete_shop_item$
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  delete from shop_items where id = p_item_id and proposer_id = p_profile_id and status = 'rejected';
  if not found then raise exception 'item not deletable by this profile'; end if;
  return true;
end;
$delete_shop_item$;

grant execute on function delete_shop_item(uuid, text, uuid) to anon, authenticated;
