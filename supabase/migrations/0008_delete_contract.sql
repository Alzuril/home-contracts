-- Author can delete their own contract only while it's still open (nobody
-- has accepted it yet) - deleting after someone started working on it
-- would be unfair to the assignee.
create or replace function delete_contract(p_profile_id uuid, p_pin text, p_contract_id uuid)
returns boolean
language plpgsql security definer set search_path = public, extensions as $delete_contract$
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  delete from contracts where id = p_contract_id and author_id = p_profile_id and status = 'open';
  if not found then raise exception 'contract not deletable by this profile'; end if;
  return true;
end;
$delete_contract$;

grant execute on function delete_contract(uuid, text, uuid) to anon, authenticated;
