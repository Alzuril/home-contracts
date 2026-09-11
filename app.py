import asyncio
from urllib.parse import quote

import js
from pyscript import document
from pyodide.ffi import create_proxy, to_js
from supabase_client import SupabaseClient, SupabaseError, pyodide_fetcher
from config import SUPABASE_URL, SUPABASE_ANON_KEY, VAPID_PUBLIC_KEY

client = SupabaseClient(SUPABASE_URL, SUPABASE_ANON_KEY, pyodide_fetcher)

current_profile = None
current_pin = None
profiles_cache = {}
active_tasks_tab = "given"

AVATAR_COLORS = ["var(--avatar-1)", "var(--avatar-2)", "var(--avatar-3)", "var(--avatar-4)", "var(--avatar-5)"]

STATUS_LABELS = {
    "open": "відкрито", "accepted": "прийнято",
    "done_pending_confirm": "чекає підтвердження", "confirmed": "виконано",
}


def avatar_color(profile_id):
    # Python's built-in hash() is randomized per process (Pyodide restarts
    # on every page load), so it must not be used here - it would give a
    # different color each time. sum-of-codepoints is deterministic.
    digest = sum(ord(ch) for ch in profile_id)
    return AVATAR_COLORS[digest % len(AVATAR_COLORS)]


def avatar_html(name, profile_id, small=False):
    initial = (name or "?")[0].upper()
    cls = "avatar small" if small else "avatar"
    return f'<div class="{cls}" style="background:{avatar_color(profile_id)}">{initial}</div>'


def stars_html(rating):
    return "".join("★" if i <= rating else "☆" for i in range(1, 6))


def rating_chip_html(c):
    if not c.get("rating"):
        return ""
    return f'<div><span class="points-chip">{stars_html(c["rating"])}</span></div>'


def format_date(value):
    if not value:
        return None
    try:
        y, m, d = value[:10].split("-")
        return f"{d}.{m}.{y}"
    except Exception:
        return value


def dates_meta_html(c):
    parts = [f"Створено {format_date(c['created_at'])}"]
    if c.get("due_date"):
        parts.append(f"Дедлайн {format_date(c['due_date'])}")
    return f'<span class="card-meta">{" · ".join(parts)}</span>'


async def refresh_profiles_cache():
    global profiles_cache
    rows = await client.select("public_profiles", "?select=id,name,points,level")
    profiles_cache = {p["id"]: p for p in rows}


def name_of(profile_id):
    return profiles_cache.get(profile_id, {}).get("name", "?")


async def refresh_current_profile():
    global current_profile
    await refresh_profiles_cache()
    current_profile = profiles_cache[current_profile["id"]]


# ---- auth screens ----

async def render_login(event=None):
    app = document.getElementById("app")
    existing = await client.select("public_profiles", "?select=id&limit=1")
    if not existing:
        await render_signup()
        return
    app.innerHTML = """
      <h1>Увійти</h1>
      <input id="name-input" placeholder="Твоє ім'я" autocomplete="off" />
      <input id="pin-input" type="password" inputmode="numeric" maxlength="4" placeholder="PIN" />
      <button id="login-btn" class="btn-primary">Увійти</button>
      <p id="login-error" class="field-error"></p>
      <button id="to-signup-btn" class="link-btn">Ще нема профілю? Зареєструватися</button>
    """
    document.getElementById("login-btn").addEventListener("click", create_proxy(on_login_click))
    document.getElementById("to-signup-btn").addEventListener("click", create_proxy(render_signup))


async def render_signup(event=None):
    app = document.getElementById("app")
    app.innerHTML = """
      <h1>Створити профіль</h1>
      <input id="signup-name" placeholder="Твоє ім'я" autocomplete="off" />
      <input id="signup-pin" type="password" inputmode="numeric" maxlength="4" placeholder="Вигадай 4-значний PIN" />
      <button id="signup-btn" class="btn-primary">Створити</button>
      <p id="signup-error" class="field-error"></p>
      <button id="to-login-btn" class="link-btn">Вже є профіль? Увійти</button>
    """
    document.getElementById("signup-btn").addEventListener("click", create_proxy(on_signup_click))
    document.getElementById("to-login-btn").addEventListener("click", create_proxy(render_login))


async def on_signup_click(event):
    global current_profile, current_pin
    name = document.getElementById("signup-name").value.strip()
    pin = document.getElementById("signup-pin").value
    error_el = document.getElementById("signup-error")
    error_el.innerText = ""
    if not name:
        error_el.innerText = "Введи ім'я"
        return
    if not (len(pin) == 4 and pin.isdigit()):
        error_el.innerText = "PIN має бути рівно 4 цифри"
        return
    try:
        profile = await client.rpc("create_profile", {"p_name": name, "p_pin": pin})
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    current_pin = pin
    current_profile = profile
    save_session()
    await refresh_profiles_cache()
    await render_board()
    await enable_push()


async def on_login_click(event):
    global current_profile, current_pin
    name = document.getElementById("name-input").value.strip()
    pin = document.getElementById("pin-input").value
    error_el = document.getElementById("login-error")
    error_el.innerText = ""
    if not name:
        error_el.innerText = "Введи ім'я"
        return
    matches = await client.select("public_profiles", f"?name=eq.{quote(name)}&select=id,name,points,level")
    if not matches:
        error_el.innerText = "Профіль з таким іменем не знайдено"
        return
    profile = matches[0]
    try:
        ok = await client.rpc("verify_pin", {"p_profile_id": profile["id"], "p_pin": pin})
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    if not ok:
        error_el.innerText = "Невірний PIN"
        return
    current_pin = pin
    current_profile = profile
    save_session()
    await refresh_profiles_cache()
    await render_board()
    await enable_push()


async def do_logout(event=None):
    global current_profile, current_pin, _current_screen
    current_profile = None
    current_pin = None
    _current_screen = None
    clear_session()
    document.getElementById("modal-root").innerHTML = ""
    await render_login()


# ---- remembered session (localStorage) ----

def save_session():
    try:
        js.localStorage.setItem("hc_profile_id", current_profile["id"])
        js.localStorage.setItem("hc_pin", current_pin)
    except Exception:
        pass


def clear_session():
    try:
        js.localStorage.removeItem("hc_profile_id")
        js.localStorage.removeItem("hc_pin")
    except Exception:
        pass


async def try_auto_login(event=None):
    global current_profile, current_pin
    try:
        saved_id = js.localStorage.getItem("hc_profile_id")
        saved_pin = js.localStorage.getItem("hc_pin")
    except Exception:
        saved_id, saved_pin = None, None
    if not saved_id or not saved_pin:
        await render_login()
        return
    try:
        ok = await client.rpc("verify_pin", {"p_profile_id": saved_id, "p_pin": saved_pin})
    except SupabaseError:
        ok = False
    if not ok:
        clear_session()
        await render_login()
        return
    profiles = await client.select("public_profiles", f"?id=eq.{saved_id}&select=id,name,points,level")
    if not profiles:
        clear_session()
        await render_login()
        return
    current_pin = saved_pin
    current_profile = profiles[0]
    await refresh_profiles_cache()
    await render_board()
    await enable_push()


# ---- in-app back navigation (History API) ----

_current_screen = None
_suppress_history_push = False


def push_screen(name):
    global _current_screen
    if _suppress_history_push or _current_screen == name:
        _current_screen = name
        return
    _current_screen = name
    try:
        js.history.pushState(to_js({"screen": name}, dict_converter=js.Object.fromEntries), "", "")
    except Exception:
        pass


async def on_popstate(event):
    global _suppress_history_push, _current_screen
    state = event.state
    screen = None
    if state:
        try:
            screen = state.to_py().get("screen")
        except Exception:
            screen = None
    renderer = {"board": render_board, "menu": render_menu, "profile": render_profile,
                "history": render_history, "tasks": render_tasks, "shop": render_shop}.get(screen)
    if not renderer or current_profile is None:
        return
    _current_screen = screen
    _suppress_history_push = True
    try:
        await renderer()
    finally:
        _suppress_history_push = False


js.window.addEventListener("popstate", create_proxy(on_popstate))


# ---- swipe right to open menu ----

_touch_start_x = None
_touch_start_y = None


def on_touch_start(event):
    global _touch_start_x, _touch_start_y
    touches = event.touches
    if touches.length == 0:
        return
    t = touches.item(0)
    _touch_start_x = t.clientX
    _touch_start_y = t.clientY


async def on_touch_end(event):
    global _touch_start_x, _touch_start_y
    start_x, start_y = _touch_start_x, _touch_start_y
    _touch_start_x, _touch_start_y = None, None
    if start_x is None or current_profile is None or _current_screen == "menu":
        return
    touches = event.changedTouches
    if touches.length == 0:
        return
    t = touches.item(0)
    dx = t.clientX - start_x
    dy = t.clientY - start_y
    if dx > 80 and abs(dy) < 60 and document.getElementById("modal-overlay") is None:
        await render_menu()


document.addEventListener("touchstart", create_proxy(on_touch_start), to_js(
    {"passive": True}, dict_converter=js.Object.fromEntries
))
document.addEventListener("touchend", create_proxy(on_touch_end))


# ---- shared shell ----

def topbar_html(title):
    return f"""
      <div class="topbar">
        <button id="menu-btn" class="icon-btn" aria-label="Меню">☰</button>
        <h1>{title}</h1>
      </div>
    """


def wire_topbar():
    document.getElementById("menu-btn").addEventListener("click", create_proxy(render_menu))


# ---- board ----

def contract_card_html(c):
    buttons = ""
    is_author = c["author_id"] == current_profile["id"]
    is_assignee = c.get("assignee_id") == current_profile["id"]
    if c["status"] == "open" and not is_author:
        buttons = f'<button class="accept-btn" data-id="{c["id"]}">Прийняти</button>'
    elif c["status"] == "accepted" and is_assignee:
        buttons = (
            f'<button class="complete-btn" data-id="{c["id"]}">Завершити</button>'
            f'<button class="decline-btn secondary" data-id="{c["id"]}">Відмовитись</button>'
        )
    elif c["status"] == "done_pending_confirm" and is_author:
        buttons = f'<button class="confirm-btn" data-id="{c["id"]}">Підтвердити</button>'
    counterpart_id = c["assignee_id"] if is_author else c["author_id"]
    counterpart_label = "Виконавець:" if is_author else "від"
    counterpart = (
        f'<span class="card-meta">{avatar_html(name_of(counterpart_id), counterpart_id, small=True)} {counterpart_label} {name_of(counterpart_id)}</span>'
        if counterpart_id else ""
    )
    description = f'<p class="card-description">{c["description"]}</p>' if c.get("description") else ""
    return f"""
      <div class="contract-card" data-id="{c['id']}">
        <strong>{c['title']}</strong>
        {description}
        {rating_chip_html(c)}
        <div class="status-pill status-{c['status']}">{STATUS_LABELS[c['status']]}</div>
        {counterpart}
        {dates_meta_html(c)}
        <div class="actions">{buttons}</div>
      </div>
    """


async def render_board(event=None):
    push_screen("board")
    app = document.getElementById("app")
    contracts = await client.select(
        "contracts",
        "?status=in.(open,accepted,done_pending_confirm)&order=created_at.desc",
    )
    cards = "".join(contract_card_html(c) for c in contracts)
    app.innerHTML = f"""
      {topbar_html(f"Привіт, {current_profile['name']}")}
      <div class="stat-row">
        <div class="stat-tile"><div class="stat-value">{current_profile['points']}</div><div class="stat-label">Балів</div></div>
        <div class="stat-tile"><div class="stat-value">{current_profile['level']}</div><div class="stat-label">Рівень</div></div>
      </div>
      <h2>Дошка контрактів</h2>
      <div id="contracts-list">{cards or '<p class="empty-note">Порожньо. Натисни + внизу, щоб кинути перший контракт.</p>'}</div>
    """
    wire_topbar()
    document.getElementById("contracts-list").addEventListener("click", create_proxy(on_board_click))
    show_fab()


async def on_board_click(event):
    global current_profile
    target = event.target
    contract_id = target.getAttribute("data-id")
    if not contract_id:
        return
    classes = target.classList
    if classes.contains("confirm-btn"):
        open_confirm_modal(contract_id)
        return
    action_map = {
        "accept-btn": "accept_contract", "decline-btn": "decline_contract",
        "complete-btn": "complete_contract",
    }
    fn_name = next((v for k, v in action_map.items() if classes.contains(k)), None)
    if not fn_name:
        return
    try:
        await client.rpc(fn_name, {
            "p_contract_id": contract_id, "p_profile_id": current_profile["id"], "p_pin": current_pin,
        })
    except SupabaseError as exc:
        document.getElementById("app").querySelector("h1").insertAdjacentHTML(
            "afterend", f'<p class="field-error">Помилка: {exc.body}</p>'
        )
        return
    await refresh_current_profile()
    await render_board()


# ---- create-contract modal (FAB) ----

def show_fab():
    root = document.getElementById("modal-root")
    root.innerHTML = '<button id="fab-btn" class="fab" aria-label="Кинути контракт">+</button>'
    document.getElementById("fab-btn").addEventListener("click", create_proxy(lambda e: open_create_modal()))


def open_create_modal():
    root = document.getElementById("modal-root")
    root.innerHTML = """
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-card">
          <h2>Новий контракт</h2>
          <input id="new-title" class="title-input" placeholder="Введіть контракт..." autocomplete="off" />
          <textarea id="new-description" placeholder="Опис (необов'язково)" rows="3"></textarea>
          <label class="field-label" for="new-due-date">Дедлайн (необов'язково)</label>
          <input id="new-due-date" type="date" />
          <button id="submit-contract-btn" class="btn-primary">Додати контракт</button>
          <p id="create-error" class="field-error"></p>
          <button id="cancel-modal-btn" class="link-btn">Скасувати</button>
        </div>
      </div>
    """
    document.getElementById("submit-contract-btn").addEventListener("click", create_proxy(on_create_click))
    document.getElementById("cancel-modal-btn").addEventListener("click", create_proxy(lambda e: show_fab()))


async def on_create_click(event):
    title = document.getElementById("new-title").value
    description = document.getElementById("new-description").value.strip()
    due_date = document.getElementById("new-due-date").value or None
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
            "p_description": description,
            "p_due_date": due_date,
        })
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    await render_board()


# ---- confirm-with-rating modal ----

confirm_target_id = None
selected_rating = 0


def render_star_picker():
    return "".join(
        f'<button type="button" class="star-btn{" filled" if i <= selected_rating else ""}" data-value="{i}">★</button>'
        for i in range(1, 6)
    )


def open_confirm_modal(contract_id):
    global confirm_target_id, selected_rating
    confirm_target_id = contract_id
    selected_rating = 0
    render_confirm_modal()


def render_confirm_modal():
    root = document.getElementById("modal-root")
    disabled = "disabled" if selected_rating == 0 else ""
    root.innerHTML = f"""
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-card">
          <h2>Оціни виконання</h2>
          <div class="star-picker" id="star-picker">{render_star_picker()}</div>
          <button id="submit-rating-btn" class="btn-primary" {disabled}>Підтвердити</button>
          <p id="rating-error" class="field-error"></p>
          <button id="cancel-rating-btn" class="link-btn">Скасувати</button>
        </div>
      </div>
    """
    document.getElementById("star-picker").addEventListener("click", create_proxy(on_star_click))
    document.getElementById("submit-rating-btn").addEventListener("click", create_proxy(on_submit_rating))
    document.getElementById("cancel-rating-btn").addEventListener("click", create_proxy(lambda e: show_fab()))


def on_star_click(event):
    global selected_rating
    value = event.target.getAttribute("data-value")
    if not value:
        return
    selected_rating = int(value)
    render_confirm_modal()


async def on_submit_rating(event):
    error_el = document.getElementById("rating-error")
    error_el.innerText = ""
    if selected_rating == 0:
        error_el.innerText = "Постав оцінку"
        return
    try:
        await client.rpc("confirm_contract", {
            "p_contract_id": confirm_target_id,
            "p_profile_id": current_profile["id"],
            "p_pin": current_pin,
            "p_rating": selected_rating,
        })
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    await refresh_current_profile()
    await render_board()


# ---- menu ----

async def render_menu(event=None):
    push_screen("menu")
    app = document.getElementById("app")
    document.getElementById("modal-root").innerHTML = ""
    app.innerHTML = f"""
      {topbar_html("Меню")}
      <button class="menu-row" id="nav-board">
        <span class="menu-icon" style="background:var(--accent-soft);color:var(--accent)">📋</span>
        Дошка контрактів <span class="chev">›</span>
      </button>
      <button class="menu-row" id="nav-profile">
        <span class="menu-icon" style="background:var(--blue-soft);color:var(--blue)">👤</span>
        Профіль <span class="chev">›</span>
      </button>
      <button class="menu-row" id="nav-tasks">
        <span class="menu-icon" style="background:var(--green-soft);color:var(--green)">🔁</span>
        Усі контракти <span class="chev">›</span>
      </button>
      <button class="menu-row" id="nav-history">
        <span class="menu-icon" style="background:var(--amber-soft);color:var(--amber)">📜</span>
        Історія виконаного <span class="chev">›</span>
      </button>
      <button class="menu-row" id="nav-shop">
        <span class="menu-icon" style="background:var(--purple-soft);color:var(--purple)">🎁</span>
        Магазин <span class="chev">›</span>
      </button>
      <button class="menu-row logout" id="nav-logout">
        <span class="menu-icon">↩</span>
        Вийти <span class="chev">›</span>
      </button>
    """
    wire_topbar()
    document.getElementById("nav-board").addEventListener("click", create_proxy(render_board))
    document.getElementById("nav-profile").addEventListener("click", create_proxy(render_profile))
    document.getElementById("nav-tasks").addEventListener("click", create_proxy(render_tasks))
    document.getElementById("nav-history").addEventListener("click", create_proxy(render_history))
    document.getElementById("nav-shop").addEventListener("click", create_proxy(render_shop))
    document.getElementById("nav-logout").addEventListener("click", create_proxy(do_logout))


# ---- profile ----

async def render_profile(event=None):
    push_screen("profile")
    app = document.getElementById("app")
    contracts = await client.select("contracts")
    mine = current_profile["id"]
    completed_by_me = len([c for c in contracts if c["status"] == "confirmed" and c["assignee_id"] == mine])
    given_by_me = len([c for c in contracts if c["author_id"] == mine])
    app.innerHTML = f"""
      {topbar_html("Профіль")}
      <div class="profile-header">
        {avatar_html(current_profile['name'], mine)}
        <div>
          <div class="profile-name">{current_profile['name']}</div>
          <div class="profile-sub">Рівень {current_profile['level']}</div>
        </div>
      </div>
      <div class="stat-row">
        <div class="stat-tile"><div class="stat-value">{current_profile['points']}</div><div class="stat-label">Балів</div></div>
        <div class="stat-tile"><div class="stat-value">{completed_by_me}</div><div class="stat-label">Виконано</div></div>
        <div class="stat-tile"><div class="stat-value">{given_by_me}</div><div class="stat-label">Створено</div></div>
      </div>
    """
    wire_topbar()


# ---- history ----

async def render_history(event=None):
    push_screen("history")
    app = document.getElementById("app")
    contracts = await client.select("contracts")
    mine = current_profile["id"]
    done = [c for c in contracts if c["status"] == "confirmed" and mine in (c["author_id"], c["assignee_id"])]
    done.sort(key=lambda c: c["created_at"], reverse=True)
    rows = "".join(f"""
      <div class="contract-card">
        <strong>{c['title']}</strong>
        {f'<p class="card-description">{c["description"]}</p>' if c.get("description") else ""}
        {rating_chip_html(c)}
        <span class="card-meta">{avatar_html(name_of(c['assignee_id']), c['assignee_id'], small=True)} виконав(ла) {name_of(c['assignee_id'])}</span>
        {dates_meta_html(c)}
      </div>
    """ for c in done)
    app.innerHTML = f"""
      {topbar_html("Історія виконаного")}
      {rows or '<p class="empty-note">Ще нічого не підтверджено.</p>'}
    """
    wire_topbar()


# ---- tasks (who owes whom) ----

async def render_tasks(event=None):
    global active_tasks_tab
    push_screen("tasks")
    app = document.getElementById("app")
    contracts = await client.select("contracts")
    mine = current_profile["id"]
    given = sorted([c for c in contracts if c["author_id"] == mine], key=lambda c: c["created_at"], reverse=True)
    received = sorted([c for c in contracts if c["author_id"] != mine], key=lambda c: c["created_at"], reverse=True)
    active_list = given if active_tasks_tab == "given" else received

    def row(c):
        other_id = c["assignee_id"] if active_tasks_tab == "given" else c["author_id"]
        other_label = "Виконавець:" if active_tasks_tab == "given" else "від"
        other = (
            f'<span class="card-meta">{avatar_html(name_of(other_id), other_id, small=True)} {other_label} {name_of(other_id)}</span>'
            if other_id else '<span class="card-meta">ще ніхто не взяв</span>'
        )
        return f"""
          <div class="contract-card">
            <strong>{c['title']}</strong>
            {f'<p class="card-description">{c["description"]}</p>' if c.get("description") else ""}
            {rating_chip_html(c)}
            <div class="status-pill status-{c['status']}">{STATUS_LABELS[c['status']]}</div>
            {other}
            {dates_meta_html(c)}
          </div>
        """

    rows = "".join(row(c) for c in active_list) or '<p class="empty-note">Тут поки порожньо.</p>'
    app.innerHTML = f"""
      {topbar_html("Усі контракти")}
      <div class="tabs">
        <button class="tab-btn {'active' if active_tasks_tab == 'given' else ''}" id="tab-given">Я дав</button>
        <button class="tab-btn {'active' if active_tasks_tab == 'received' else ''}" id="tab-received">Мені дали</button>
      </div>
      {rows}
    """
    wire_topbar()

    def set_tab(tab):
        async def handler(e):
            global active_tasks_tab
            active_tasks_tab = tab
            await render_tasks()
        return handler

    document.getElementById("tab-given").addEventListener("click", create_proxy(set_tab("given")))
    document.getElementById("tab-received").addEventListener("click", create_proxy(set_tab("received")))


# ---- shop ----

SHOP_STATUS_LABELS = {"pending": "на голосуванні", "active": "у магазині", "rejected": "відхилено"}

reject_target_id = None


def shop_item_card_html(item):
    is_proposer = item["proposer_id"] == current_profile["id"]
    buttons = ""
    meta = ""
    if item["status"] == "pending":
        if is_proposer:
            meta = '<span class="card-meta">Очікує голосу другої людини</span>'
        else:
            buttons = (
                f'<button class="approve-item-btn" data-id="{item["id"]}">Погодити</button>'
                f'<button class="reject-item-btn secondary" data-id="{item["id"]}">Відхилити</button>'
            )
    elif item["status"] == "active":
        can_afford = current_profile["points"] >= item["price"]
        disabled = "" if can_afford else "disabled"
        buttons = f'<button class="buy-item-btn" data-id="{item["id"]}" {disabled}>Купити</button>'
        if not can_afford:
            meta = '<span class="card-meta">Не вистачає балів</span>'
    elif item["status"] == "rejected" and item.get("reject_reason"):
        meta = f'<span class="card-meta">Причина: {item["reject_reason"]}</span>'
    return f"""
      <div class="contract-card" data-id="{item['id']}">
        <strong>{item['title']}</strong>
        <div><span class="points-chip">★ {item['price']}</span></div>
        <div class="status-pill status-shop-{item['status']}">{SHOP_STATUS_LABELS[item['status']]}</div>
        {meta}
        <div class="actions">{buttons}</div>
      </div>
    """


async def render_shop(event=None):
    push_screen("shop")
    app = document.getElementById("app")
    items = await client.select("shop_items", "?order=created_at.desc")
    cards = "".join(shop_item_card_html(i) for i in items)
    app.innerHTML = f"""
      {topbar_html("Магазин")}
      <div id="shop-list">{cards or '<p class="empty-note">Порожньо. Натисни + внизу, щоб запропонувати плюшку.</p>'}</div>
    """
    wire_topbar()
    document.getElementById("shop-list").addEventListener("click", create_proxy(on_shop_click))
    show_shop_fab()


async def on_shop_click(event):
    global current_profile
    target = event.target
    item_id = target.getAttribute("data-id")
    if not item_id:
        return
    classes = target.classList
    if classes.contains("reject-item-btn"):
        open_reject_modal(item_id)
        return
    if classes.contains("approve-item-btn"):
        try:
            await client.rpc("vote_shop_item", {
                "p_profile_id": current_profile["id"], "p_pin": current_pin,
                "p_item_id": item_id, "p_approve": True,
            })
        except SupabaseError:
            pass
        await render_shop()
        return
    if classes.contains("buy-item-btn"):
        try:
            await client.rpc("buy_shop_item", {
                "p_profile_id": current_profile["id"], "p_pin": current_pin, "p_item_id": item_id,
            })
        except SupabaseError as exc:
            document.getElementById("app").querySelector("h1").insertAdjacentHTML(
                "afterend", f'<p class="field-error">Помилка: {exc.body}</p>'
            )
            return
        await refresh_current_profile()
        await render_shop()


def show_shop_fab():
    root = document.getElementById("modal-root")
    root.innerHTML = '<button id="fab-btn" class="fab" aria-label="Запропонувати плюшку">+</button>'
    document.getElementById("fab-btn").addEventListener("click", create_proxy(lambda e: open_propose_modal()))


def open_propose_modal():
    root = document.getElementById("modal-root")
    root.innerHTML = """
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-card">
          <h2>Запропонувати плюшку</h2>
          <input id="new-item-title" class="title-input" placeholder="Наприклад: Пляшка вина" autocomplete="off" />
          <input id="new-item-price" type="number" min="1" value="10" placeholder="Ціна в балах" />
          <button id="submit-item-btn" class="btn-primary">Запропонувати</button>
          <p id="item-error" class="field-error"></p>
          <button id="cancel-item-modal-btn" class="link-btn">Скасувати</button>
        </div>
      </div>
    """
    document.getElementById("submit-item-btn").addEventListener("click", create_proxy(on_propose_item_click))
    document.getElementById("cancel-item-modal-btn").addEventListener("click", create_proxy(lambda e: show_shop_fab()))


async def on_propose_item_click(event):
    title = document.getElementById("new-item-title").value
    price = document.getElementById("new-item-price").value
    error_el = document.getElementById("item-error")
    error_el.innerText = ""
    if not title.strip():
        error_el.innerText = "Вкажи назву"
        return
    try:
        price_int = int(price)
    except ValueError:
        error_el.innerText = "Вкажи ціну в балах"
        return
    if price_int <= 0:
        error_el.innerText = "Ціна має бути більше нуля"
        return
    try:
        await client.rpc("propose_shop_item", {
            "p_proposer_id": current_profile["id"],
            "p_pin": current_pin,
            "p_title": title,
            "p_price": price_int,
        })
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    await render_shop()


def open_reject_modal(item_id):
    global reject_target_id
    reject_target_id = item_id
    root = document.getElementById("modal-root")
    root.innerHTML = """
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-card">
          <h2>Відхилити пропозицію</h2>
          <textarea id="reject-reason" placeholder="Причина (необов'язково)" rows="3"></textarea>
          <button id="submit-reject-btn" class="btn-primary">Відхилити</button>
          <p id="reject-error" class="field-error"></p>
          <button id="cancel-reject-btn" class="link-btn">Скасувати</button>
        </div>
      </div>
    """
    document.getElementById("submit-reject-btn").addEventListener("click", create_proxy(on_submit_reject))
    document.getElementById("cancel-reject-btn").addEventListener("click", create_proxy(lambda e: show_shop_fab()))


async def on_submit_reject(event):
    reason = document.getElementById("reject-reason").value.strip()
    error_el = document.getElementById("reject-error")
    error_el.innerText = ""
    try:
        await client.rpc("vote_shop_item", {
            "p_profile_id": current_profile["id"],
            "p_pin": current_pin,
            "p_item_id": reject_target_id,
            "p_approve": False,
            "p_reject_reason": reason or None,
        })
    except SupabaseError as exc:
        error_el.innerText = f"Помилка: {exc.body}"
        return
    await render_shop()


# ---- push ----

async def enable_push():
    if not hasattr(js.navigator, "serviceWorker"):
        return
    registration = await js.navigator.serviceWorker.register("./sw.js")
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


asyncio.ensure_future(try_auto_login())
