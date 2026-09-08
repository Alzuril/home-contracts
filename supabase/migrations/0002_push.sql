create or replace function save_push_subscription(
  p_profile_id uuid, p_pin text, p_endpoint text, p_p256dh text, p_auth text
) returns push_subscriptions
language plpgsql security definer set search_path = public as $save_push_subscription$
declare v_sub push_subscriptions;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  insert into push_subscriptions (profile_id, endpoint, p256dh, auth)
  values (p_profile_id, p_endpoint, p_p256dh, p_auth)
  on conflict (endpoint) do update
    set profile_id = excluded.profile_id, p256dh = excluded.p256dh, auth = excluded.auth
  returning * into v_sub;
  return v_sub;
end; $save_push_subscription$;

grant execute on function save_push_subscription(uuid, text, text, text, text) to anon, authenticated;
