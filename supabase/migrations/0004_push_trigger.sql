create extension if not exists pg_net with schema extensions;

create or replace function public.trigger_send_push()
returns trigger
language plpgsql
security definer
set search_path = public, extensions
as $trigger_send_push$
begin
  perform net.http_post(
    url := 'https://vpqwptaqsqwdwwrjdzkh.supabase.co/functions/v1/send-push',
    headers := '{"Content-Type": "application/json"}'::jsonb,
    body := jsonb_build_object('type', 'INSERT', 'table', 'contracts', 'record', to_jsonb(NEW))
  );
  return NEW;
end;
$trigger_send_push$;

drop trigger if exists contracts_after_insert_send_push on contracts;

create trigger contracts_after_insert_send_push
after insert on contracts
for each row execute function public.trigger_send_push();
