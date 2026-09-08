alter table contracts add column if not exists due_date date;

drop function if exists create_contract(uuid, text, text, text, uuid);

create or replace function create_contract(
  p_author_id uuid, p_pin text, p_title text, p_description text,
  p_due_date date default null, p_assignee_id uuid default null
) returns contracts
language plpgsql security definer set search_path = public, extensions as $create_contract_v2$
declare
  v_contract contracts;
begin
  if not verify_pin(p_author_id, p_pin) then
    raise exception 'invalid pin';
  end if;
  insert into contracts (title, description, due_date, author_id, assignee_id, status)
  values (p_title, coalesce(p_description, ''), p_due_date, p_author_id, p_assignee_id,
          case when p_assignee_id is null then 'open' else 'accepted' end)
  returning * into v_contract;
  return v_contract;
end;
$create_contract_v2$;

grant execute on function create_contract(uuid, text, text, text, date, uuid) to anon, authenticated;
