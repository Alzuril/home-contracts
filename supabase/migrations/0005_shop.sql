create table shop_items (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  price integer not null check (price > 0),
  proposer_id uuid not null references profiles(id),
  status text not null default 'pending' check (status in ('pending','active','rejected')),
  reject_reason text,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

create table shop_purchases (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references shop_items(id),
  buyer_id uuid not null references profiles(id),
  price_paid integer not null,
  purchased_at timestamptz not null default now()
);

alter table shop_items enable row level security;
alter table shop_purchases enable row level security;

revoke all on shop_items, shop_purchases from anon, authenticated;
grant select on shop_items to anon, authenticated;
grant select on shop_purchases to anon, authenticated;

create policy "shop_items readable by anyone" on shop_items for select using (true);
create policy "shop_purchases readable by anyone" on shop_purchases for select using (true);

create or replace function propose_shop_item(p_proposer_id uuid, p_pin text, p_title text, p_price int)
returns shop_items
language plpgsql security definer set search_path = public, extensions as $propose_shop_item$
declare v_item shop_items;
begin
  if not verify_pin(p_proposer_id, p_pin) then raise exception 'invalid pin'; end if;
  if p_price <= 0 then raise exception 'price must be positive'; end if;
  insert into shop_items (title, price, proposer_id, status)
  values (p_title, p_price, p_proposer_id, 'pending')
  returning * into v_item;
  return v_item;
end;
$propose_shop_item$;

create or replace function vote_shop_item(
  p_profile_id uuid, p_pin text, p_item_id uuid, p_approve boolean, p_reject_reason text default null
) returns shop_items
language plpgsql security definer set search_path = public, extensions as $vote_shop_item$
declare v_item shop_items;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update shop_items
    set status = case when p_approve then 'active' else 'rejected' end,
        reject_reason = case when p_approve then null else p_reject_reason end,
        decided_at = now()
    where id = p_item_id and status = 'pending' and proposer_id != p_profile_id
    returning * into v_item;
  if v_item.id is null then raise exception 'item not votable by this profile'; end if;
  return v_item;
end;
$vote_shop_item$;

create or replace function buy_shop_item(p_profile_id uuid, p_pin text, p_item_id uuid)
returns shop_purchases
language plpgsql security definer set search_path = public, extensions as $buy_shop_item$
declare
  v_item shop_items;
  v_points int;
  v_purchase shop_purchases;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  select * into v_item from shop_items where id = p_item_id and status = 'active';
  if v_item.id is null then raise exception 'item not available'; end if;
  select points into v_points from profiles where id = p_profile_id;
  if v_points < v_item.price then raise exception 'not enough points'; end if;
  update profiles set points = points - v_item.price where id = p_profile_id;
  insert into shop_purchases (item_id, buyer_id, price_paid)
  values (p_item_id, p_profile_id, v_item.price)
  returning * into v_purchase;
  return v_purchase;
end;
$buy_shop_item$;

grant execute on function propose_shop_item(uuid, text, text, int) to anon, authenticated;
grant execute on function vote_shop_item(uuid, text, uuid, boolean, text) to anon, authenticated;
grant execute on function buy_shop_item(uuid, text, uuid) to anon, authenticated;
