import webpush from "npm:web-push@3.6.7";
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const vapidPublicKey = Deno.env.get("VAPID_PUBLIC_KEY")!;
const vapidPrivateKey = Deno.env.get("VAPID_PRIVATE_KEY")!;

webpush.setVapidDetails("mailto:example@example.com", vapidPublicKey, vapidPrivateKey);

Deno.serve(async (req) => {
  const payload = await req.json();
  const contract = payload.record;
  const supabase = createClient(supabaseUrl, serviceRoleKey);

  const { data: existing } = await supabase
    .from("notifications_log")
    .select("id")
    .eq("contract_id", contract.id)
    .maybeSingle();
  if (existing) {
    return new Response(JSON.stringify({ skipped: "already notified" }), { status: 200 });
  }

  let recipientQuery = supabase.from("push_subscriptions").select("*");
  if (contract.assignee_id) {
    recipientQuery = recipientQuery.eq("profile_id", contract.assignee_id);
  } else {
    recipientQuery = recipientQuery.neq("profile_id", contract.author_id);
  }
  const { data: subscriptions } = await recipientQuery;

  const notificationPayload = JSON.stringify({
    title: "Новий контракт!",
    body: `${contract.title} — ${contract.points} балів`,
  });

  for (const sub of subscriptions ?? []) {
    try {
      await webpush.sendNotification(
        { endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } },
        notificationPayload
      );
    } catch (err) {
      console.error("push failed for", sub.endpoint, err);
    }
  }

  await supabase.from("notifications_log").insert({ contract_id: contract.id });

  return new Response(JSON.stringify({ notified: subscriptions?.length ?? 0 }), { status: 200 });
});
