import asyncio

import js
from pyscript import document
from pyodide.ffi import create_proxy, to_js
from supabase_client import SupabaseClient, SupabaseError, pyodide_fetcher
from config import SUPABASE_URL, SUPABASE_ANON_KEY, VAPID_PUBLIC_KEY

client = SupabaseClient(SUPABASE_URL, SUPABASE_ANON_KEY, pyodide_fetcher)

current_profile = None
current_pin = None


async def render_login():
    app = document.getElementById("app")
    profiles = await client.select("public_profiles", "?select=id,name")
    options = "".join(f'<option value="{p["id"]}">{p["name"]}</option>' for p in profiles)
    app.innerHTML = f"""
      <h1>Хто ти?</h1>
      <select id="profile-select">{options}</select>
      <input id="pin-input" type="password" inputmode="numeric" maxlength="4" placeholder="PIN" />
      <button id="login-btn">Увійти</button>
      <p id="login-error" style="color:#f66"></p>
    """
    document.getElementById("login-btn").addEventListener("click", create_proxy(on_login_click))


async def on_login_click(event):
    global current_profile, current_pin
    profile_id = document.getElementById("profile-select").value
    pin = document.getElementById("pin-input").value
    error_el = document.getElementById("login-error")
    error_el.innerText = ""
    try:
        ok = await client.rpc("verify_pin", {"p_profile_id": profile_id, "p_pin": pin})
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    if not ok:
        error_el.innerText = "Невірний PIN"
        return
    current_pin = pin
    profiles = await client.select("public_profiles", f"?id=eq.{profile_id}&select=id,name,points,level")
    current_profile = profiles[0]
    await render_board()
    await enable_push()


def contract_card_html(c):
    buttons = ""
    is_author = c["author_id"] == current_profile["id"]
    is_assignee = c.get("assignee_id") == current_profile["id"]
    if c["status"] == "open" and not is_author:
        buttons = f'<button class="accept-btn" data-id="{c["id"]}">Прийняти</button>'
    elif c["status"] == "accepted" and is_assignee:
        buttons = (
            f'<button class="complete-btn" data-id="{c["id"]}">Завершив</button>'
            f'<button class="decline-btn" data-id="{c["id"]}">Відмовитись</button>'
        )
    elif c["status"] == "done_pending_confirm" and is_author:
        buttons = f'<button class="confirm-btn" data-id="{c["id"]}">Підтвердити</button>'
    return f"""
      <div class="contract-card" data-id="{c['id']}">
        <strong>{c['title']}</strong> — {c['points']} балів
        <div>Статус: {c['status']}</div>
        <div class="actions">{buttons}</div>
      </div>
    """


async def render_board():
    app = document.getElementById("app")
    contracts = await client.select(
        "contracts",
        "?status=in.(open,accepted,done_pending_confirm)&order=created_at.desc",
    )
    cards = "".join(contract_card_html(c) for c in contracts)
    app.innerHTML = f"""
      <h1>Привіт, {current_profile['name']} (рівень {current_profile['level']}, {current_profile['points']} балів)</h1>
      <h2>Новий контракт</h2>
      <input id="new-title" placeholder="Назва" />
      <input id="new-points" type="number" min="1" value="10" />
      <button id="create-btn">Кинути контракт</button>
      <p id="create-error" style="color:#f66"></p>
      <h2>Дошка контрактів</h2>
      <div id="contracts-list">{cards or "<p>Порожньо</p>"}</div>
    """
    document.getElementById("create-btn").addEventListener("click", create_proxy(on_create_click))
    document.getElementById("contracts-list").addEventListener("click", create_proxy(on_board_click))


async def on_create_click(event):
    title = document.getElementById("new-title").value
    points = document.getElementById("new-points").value
    error_el = document.getElementById("create-error")
    error_el.innerText = ""
    if not title.strip():
        error_el.innerText = "Вкажи назву"
        return
    try:
        await client.rpc("create_contract", {
            "p_author_id": current_profile["id"],
            "p_pin": current_pin,
            "p_title": title,
            "p_description": "",
            "p_points": int(points),
        })
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    await render_board()


async def on_board_click(event):
    global current_profile
    target = event.target
    contract_id = target.getAttribute("data-id")
    if not contract_id:
        return
    classes = target.classList
    try:
        if classes.contains("accept-btn"):
            await client.rpc("accept_contract", {
                "p_contract_id": contract_id, "p_profile_id": current_profile["id"], "p_pin": current_pin,
            })
        elif classes.contains("decline-btn"):
            await client.rpc("decline_contract", {
                "p_contract_id": contract_id, "p_profile_id": current_profile["id"], "p_pin": current_pin,
            })
        elif classes.contains("complete-btn"):
            await client.rpc("complete_contract", {
                "p_contract_id": contract_id, "p_profile_id": current_profile["id"], "p_pin": current_pin,
            })
        elif classes.contains("confirm-btn"):
            await client.rpc("confirm_contract", {
                "p_contract_id": contract_id, "p_profile_id": current_profile["id"], "p_pin": current_pin,
            })
        else:
            return
    except SupabaseError as exc:
        document.getElementById("create-error").innerText = f"Помилка: {exc.body}"
        return
    profiles = await client.select("public_profiles", f"?id=eq.{current_profile['id']}&select=id,name,points,level")
    current_profile = profiles[0]
    await render_board()


async def enable_push():
    if not hasattr(js.navigator, "serviceWorker"):
        return
    registration = await js.navigator.serviceWorker.register("/sw.js")
    permission = await js.Notification.requestPermission()
    if permission != "granted":
        return
    subscription = await registration.pushManager.subscribe(to_js({
        "userVisibleOnly": True,
        "applicationServerKey": VAPID_PUBLIC_KEY,
    }, dict_converter=js.Object.fromEntries))
    sub_json = subscription.toJSON().to_py()
    await client.rpc("save_push_subscription", {
        "p_profile_id": current_profile["id"],
        "p_pin": current_pin,
        "p_endpoint": sub_json["endpoint"],
        "p_p256dh": sub_json["keys"]["p256dh"],
        "p_auth": sub_json["keys"]["auth"],
    })


asyncio.ensure_future(render_login())
