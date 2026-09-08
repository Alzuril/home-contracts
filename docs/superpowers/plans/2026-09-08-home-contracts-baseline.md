# Домашні контракти — Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-person gamified household-chores tracker ("contracts"), Python (PyScript) frontend on GitHub Pages, Supabase backend, with points/level and push notifications.

**Architecture:** Static site (`index.html` + `app.py` via PyScript/Pyodide, no build step) hosted on GitHub Pages, talking directly to a Supabase Postgres project over its REST API. All mutations go through PIN-checked `SECURITY DEFINER` Postgres RPC functions instead of direct table writes, so a stolen anon key can't bypass the PIN. A Supabase Database Webhook + Edge Function sends Web Push notifications on new contracts.

**Tech Stack:** PyScript/Pyodide, vanilla HTML/CSS, Supabase (Postgres, RPC, Database Webhooks, Edge Functions on Deno), Web Push API + Service Worker, pytest (for the parts that run under plain CPython), Supabase CLI + Docker for local dev/testing.

## Global Constraints

- No custom backend server — only Supabase (free tier) + static GitHub Pages hosting.
- Auth is profile-select + 4-digit PIN, not full Supabase Auth (per design spec).
- Every data mutation must go through a PIN-checked RPC function — no direct anon INSERT/UPDATE grants on `profiles`, `contracts`, `notifications_log`, `push_subscriptions`.
- Contract status machine is exactly: `open → accepted → done_pending_confirm → confirmed`, with decline returning to `open` from `accepted` or `done_pending_confirm`.
- Points are awarded exactly once per contract, only at the `confirmed` transition.
- `level` is derived from `points` (`level = 1 + floor(points / 100)`), never stored independently of that formula.
- No unit tests for the PyScript UI itself (per spec) — verify UI tasks manually in a real browser. Automated tests are used only where they're cheap and meaningful: SQL/RPC layer (via `psql`) and the pure-Python `supabase_client.py` request/response logic (via `pytest`).
- Local dev/testing of the database happens against the Supabase CLI's local stack (Docker) before anything is pushed to the hosted free-tier project.

---

## File Structure

```
C:\Personal\Projects\home-contracts\
├── index.html                  # page shell, loads Pyodide/PyScript, mounts app.py
├── app.py                      # PyScript app: screens, event handlers, DOM updates
├── supabase_client.py          # thin Supabase REST/RPC client, framework-agnostic
├── style.css                   # all styling
├── sw.js                       # service worker: push event -> notification
├── manifest.json               # PWA manifest (installable "Add to Home Screen")
├── config.py                   # SUPABASE_URL / SUPABASE_ANON_KEY / VAPID_PUBLIC_KEY constants
├── README.md
├── .gitignore
├── tests/
│   └── test_supabase_client.py # pytest, mocked fetcher, no browser/pyodide needed
├── supabase/
│   ├── config.toml             # created by `supabase init`
│   ├── migrations/
│   │   └── 0001_init.sql       # schema, RLS, RPC functions
│   └── functions/
│       └── send-push/
│           └── index.ts        # Edge Function: Database Webhook -> Web Push
└── docs/superpowers/plans/2026-09-08-home-contracts-baseline.md   # this file
```

Rationale: `app.py` owns all DOM/event wiring, `supabase_client.py` owns all HTTP concerns and is the only file with an automated test suite outside the SQL layer, `config.py` isolates the two secrets/constants the frontend needs so nothing else hardcodes them.

---

## Task 1: Repo scaffolding and Supabase CLI init

**Files:**
- Create: `C:\Personal\Projects\home-contracts\README.md`
- Create: `C:\Personal\Projects\home-contracts\.gitignore`
- Create: `C:\Personal\Projects\home-contracts\supabase\config.toml` (via CLI)

**Interfaces:** None yet — this task only sets up tooling.

- [ ] **Step 1: Init git repo**

```bash
cd "C:/Personal/Projects/home-contracts"
git init
```

Expected: `Initialized empty Git repository in C:/Personal/Projects/home-contracts/.git/`

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.env
supabase/.branches
supabase/.temp
```

- [ ] **Step 3: Write `README.md`**

```markdown
# Домашні контракти

Two-person gamified household chore tracker. PyScript frontend (GitHub Pages),
Supabase backend. See `docs/superpowers/plans/2026-09-08-home-contracts-baseline.md`
for the implementation plan.

## Local dev prerequisites

- Docker Desktop running (for `supabase start`)
- [Supabase CLI](https://supabase.com/docs/guides/cli) installed
- Python 3.11+ with `pytest`, `pytest-asyncio`, `responses` installed (`pip install -r requirements-dev.txt`)
- Any static file server for the frontend, e.g. `python -m http.server 8000`
```

- [ ] **Step 4: Write `requirements-dev.txt`**

```
pytest==8.3.3
pytest-asyncio==0.24.0
responses==0.25.3
```

- [ ] **Step 5: Install Supabase CLI and init project**

```bash
supabase init
```

Expected: creates `supabase/config.toml` and `supabase/.gitignore`. If `supabase` command is not found, install it first (`scoop install supabase` on Windows, or download the release binary) — this is an environment prerequisite, not part of this plan.

- [ ] **Step 6: Commit**

```bash
git add README.md .gitignore requirements-dev.txt supabase/config.toml supabase/.gitignore
git commit -m "chore: scaffold repo and supabase CLI project"
```

---

## Task 2: Database schema, RLS, and PIN-checked RPC functions

**Files:**
- Create: `C:\Personal\Projects\home-contracts\supabase\migrations\0001_init.sql`

**Interfaces:**
- Produces (used by `supabase_client.py` in Task 4 and `app.py` from Task 6 onward):
  - View `public_profiles(id uuid, name text, points int, level int, created_at timestamptz)` — readable by anon.
  - Table `contracts(id, title, description, points, author_id, assignee_id, status, created_at, updated_at)` — readable by anon.
  - RPC `verify_pin(p_profile_id uuid, p_pin text) returns boolean`
  - RPC `create_contract(p_author_id uuid, p_pin text, p_title text, p_description text, p_points int, p_assignee_id uuid default null) returns contracts`
  - RPC `accept_contract(p_contract_id uuid, p_profile_id uuid, p_pin text) returns contracts`
  - RPC `decline_contract(p_contract_id uuid, p_profile_id uuid, p_pin text) returns contracts`
  - RPC `complete_contract(p_contract_id uuid, p_profile_id uuid, p_pin text) returns contracts`
  - RPC `confirm_contract(p_contract_id uuid, p_profile_id uuid, p_pin text) returns contracts` (awards points to `assignee_id`)
  - All RPCs raise a Postgres exception (`invalid pin`, `contract not available`, `contract not declinable by this profile`, `contract not completable by this profile`, `contract not confirmable by this profile`) on failure, which PostgREST turns into an HTTP 400 with that message in the body.

- [ ] **Step 1: Start local Supabase stack**

```bash
cd "C:/Personal/Projects/home-contracts"
supabase start
```

Expected: prints a table including `API URL`, `DB URL`, `anon key`. Note the `DB URL` (looks like `postgresql://postgres:postgres@127.0.0.1:54322/postgres`) — export it for later steps:

```bash
export DB_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"
```

- [ ] **Step 2: Write the migration file**

`supabase/migrations/0001_init.sql`:

```sql
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
  points integer not null check (points > 0),
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

create or replace function verify_pin(p_profile_id uuid, p_pin text)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1 from profiles
    where id = p_profile_id and pin_hash = crypt(p_pin, pin_hash)
  );
$$;

create or replace function create_contract(
  p_author_id uuid, p_pin text, p_title text, p_description text,
  p_points int, p_assignee_id uuid default null
) returns contracts
language plpgsql security definer set search_path = public as $$
declare
  v_contract contracts;
begin
  if not verify_pin(p_author_id, p_pin) then
    raise exception 'invalid pin';
  end if;
  insert into contracts (title, description, points, author_id, assignee_id, status)
  values (p_title, coalesce(p_description, ''), p_points, p_author_id, p_assignee_id,
          case when p_assignee_id is null then 'open' else 'accepted' end)
  returning * into v_contract;
  return v_contract;
end;
$$;

create or replace function accept_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public as $$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set assignee_id = p_profile_id, status = 'accepted', updated_at = now()
    where id = p_contract_id and status = 'open' and author_id != p_profile_id
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not available'; end if;
  return v_contract;
end; $$;

create or replace function decline_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public as $$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set assignee_id = null, status = 'open', updated_at = now()
    where id = p_contract_id and assignee_id = p_profile_id
      and status in ('accepted','done_pending_confirm')
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not declinable by this profile'; end if;
  return v_contract;
end; $$;

create or replace function complete_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public as $$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set status = 'done_pending_confirm', updated_at = now()
    where id = p_contract_id and assignee_id = p_profile_id and status = 'accepted'
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not completable by this profile'; end if;
  return v_contract;
end; $$;

create or replace function confirm_contract(p_contract_id uuid, p_profile_id uuid, p_pin text)
returns contracts language plpgsql security definer set search_path = public as $$
declare v_contract contracts;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  update contracts set status = 'confirmed', updated_at = now()
    where id = p_contract_id and author_id = p_profile_id and status = 'done_pending_confirm'
    returning * into v_contract;
  if v_contract.id is null then raise exception 'contract not confirmable by this profile'; end if;
  update profiles set points = points + v_contract.points where id = v_contract.assignee_id;
  return v_contract;
end; $$;

grant execute on function verify_pin(uuid, text) to anon, authenticated;
grant execute on function create_contract(uuid, text, text, text, int, uuid) to anon, authenticated;
grant execute on function accept_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function decline_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function complete_contract(uuid, uuid, text) to anon, authenticated;
grant execute on function confirm_contract(uuid, uuid, text) to anon, authenticated;
```

- [ ] **Step 3: Apply the migration locally**

```bash
supabase db reset
```

Expected: output ends with `Finished supabase db reset` and no SQL errors.

- [ ] **Step 4: Seed two test profiles and verify the `level` generated column**

```bash
psql "$DB_URL" -c "insert into profiles (name, pin_hash, points) values ('Alice', crypt('1234', gen_salt('bf')), 0), ('Bob', crypt('5678', gen_salt('bf')), 250) returning name, points, level;"
```

Expected:
```
 name  | points | level
-------+--------+-------
 Alice |      0 |     1
 Bob   |    250 |     3
```

- [ ] **Step 5: Test `verify_pin` (SQL)**

```bash
psql "$DB_URL" -c "select verify_pin(id, '1234') as correct_pin, verify_pin(id, '0000') as wrong_pin from profiles where name = 'Alice';"
```

Expected:
```
 correct_pin | wrong_pin
-------------+-----------
 t           | f
```

- [ ] **Step 6: Test full contract lifecycle via RPC (SQL)**

```bash
ALICE=$(psql "$DB_URL" -tAc "select id from profiles where name='Alice'")
BOB=$(psql "$DB_URL" -tAc "select id from profiles where name='Bob'")

psql "$DB_URL" -c "select * from create_contract('$ALICE', '1234', 'Dishes', 'Wash the dishes', 20);"
CONTRACT=$(psql "$DB_URL" -tAc "select id from contracts where title='Dishes'")

psql "$DB_URL" -c "select status, assignee_id from accept_contract('$CONTRACT', '$BOB', '5678');"
psql "$DB_URL" -c "select status from complete_contract('$CONTRACT', '$BOB', '5678');"
psql "$DB_URL" -c "select status from confirm_contract('$CONTRACT', '$ALICE', '1234');"
psql "$DB_URL" -c "select name, points, level from profiles where name = 'Bob';"
```

Expected (last query): Bob now has `points = 270`, `level = 3` (250 + 20 = 270, unchanged level since threshold is 300).

- [ ] **Step 7: Test that a wrong PIN is rejected (SQL)**

```bash
psql "$DB_URL" -c "select create_contract('$ALICE', '0000', 'Should fail', '', 10);"
```

Expected: `ERROR:  invalid pin`

- [ ] **Step 8: Reset the seeded test data (keep schema, drop test rows) so the DB is clean for later manual testing**

```bash
psql "$DB_URL" -c "delete from contracts; delete from profiles;"
```

- [ ] **Step 9: Commit**

```bash
git add supabase/migrations/0001_init.sql
git commit -m "feat: schema, RLS, and PIN-checked RPC functions for contracts"
```

---

## Task 3: Push subscription and notification-log RPCs

**Files:**
- Modify: `C:\Personal\Projects\home-contracts\supabase\migrations\0001_init.sql` is already applied — add a new migration file instead.
- Create: `C:\Personal\Projects\home-contracts\supabase\migrations\0002_push.sql`

**Interfaces:**
- Consumes: `push_subscriptions` table, `verify_pin()` from Task 2.
- Produces: RPC `save_push_subscription(p_profile_id uuid, p_pin text, p_endpoint text, p_p256dh text, p_auth text) returns push_subscriptions`, used by `app.py` in Task 8 to register a subscription after `sw.js` obtains it from the browser.

- [ ] **Step 1: Write the migration**

`supabase/migrations/0002_push.sql`:

```sql
create or replace function save_push_subscription(
  p_profile_id uuid, p_pin text, p_endpoint text, p_p256dh text, p_auth text
) returns push_subscriptions
language plpgsql security definer set search_path = public as $$
declare v_sub push_subscriptions;
begin
  if not verify_pin(p_profile_id, p_pin) then raise exception 'invalid pin'; end if;
  insert into push_subscriptions (profile_id, endpoint, p256dh, auth)
  values (p_profile_id, p_endpoint, p_p256dh, p_auth)
  on conflict (endpoint) do update
    set profile_id = excluded.profile_id, p256dh = excluded.p256dh, auth = excluded.auth
  returning * into v_sub;
  return v_sub;
end; $$;

grant execute on function save_push_subscription(uuid, text, text, text, text) to anon, authenticated;
```

- [ ] **Step 2: Apply and test**

```bash
supabase db reset
ALICE=$(psql "$DB_URL" -tAc "insert into profiles (name, pin_hash) values ('Alice', crypt('1234', gen_salt('bf'))) returning id;")
psql "$DB_URL" -c "select profile_id, endpoint from save_push_subscription('$ALICE', '1234', 'https://fcm.googleapis.com/fake/endpoint', 'p256dh-fake', 'auth-fake');"
```

Expected: one row with `profile_id` matching `$ALICE` and `endpoint = https://fcm.googleapis.com/fake/endpoint`.

- [ ] **Step 3: Clean up test data**

```bash
psql "$DB_URL" -c "delete from push_subscriptions; delete from profiles;"
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0002_push.sql
git commit -m "feat: RPC for registering web push subscriptions"
```

---

## Task 4: `supabase_client.py` — framework-agnostic REST/RPC client

**Files:**
- Create: `C:\Personal\Projects\home-contracts\supabase_client.py`
- Test: `C:\Personal\Projects\home-contracts\tests\test_supabase_client.py`

**Interfaces:**
- Produces (used by `app.py` from Task 6 onward and `sw.js`-triggered code in Task 8):
  - `class SupabaseError(Exception)` with `.status: int` and `.body: dict | str`
  - `class SupabaseClient(url: str, anon_key: str, fetcher: Callable)`
    - `async def rpc(self, fn_name: str, params: dict) -> dict | list`
    - `async def select(self, table: str, query: str = "") -> list`
  - Fetcher signature the client expects: `async def fetcher(method: str, url: str, headers: dict, body: str | None) -> tuple[int, dict | list]`

- [ ] **Step 1: Write the failing test**

`tests/test_supabase_client.py`:

```python
import pytest
from supabase_client import SupabaseClient, SupabaseError


class FakeFetcher:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.calls = []

    async def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.status, self.body


@pytest.mark.asyncio
async def test_rpc_success_returns_body():
    fetcher = FakeFetcher(200, {"id": "abc", "status": "open"})
    client = SupabaseClient("https://x.supabase.co", "anon-key", fetcher)

    result = await client.rpc("create_contract", {"p_title": "Dishes"})

    assert result == {"id": "abc", "status": "open"}
    method, url, headers, body = fetcher.calls[0]
    assert method == "POST"
    assert url == "https://x.supabase.co/rest/v1/rpc/create_contract"
    assert headers["apikey"] == "anon-key"
    assert headers["Authorization"] == "Bearer anon-key"
    assert body == '{"p_title": "Dishes"}'


@pytest.mark.asyncio
async def test_rpc_error_raises_supabase_error():
    fetcher = FakeFetcher(400, {"message": "invalid pin"})
    client = SupabaseClient("https://x.supabase.co", "anon-key", fetcher)

    with pytest.raises(SupabaseError) as exc_info:
        await client.rpc("accept_contract", {"p_contract_id": "abc"})

    assert exc_info.value.status == 400
    assert exc_info.value.body == {"message": "invalid pin"}


@pytest.mark.asyncio
async def test_select_builds_get_request():
    fetcher = FakeFetcher(200, [{"id": "abc"}])
    client = SupabaseClient("https://x.supabase.co", "anon-key", fetcher)

    result = await client.select("contracts", "?status=eq.open")

    assert result == [{"id": "abc"}]
    method, url, headers, body = fetcher.calls[0]
    assert method == "GET"
    assert url == "https://x.supabase.co/rest/v1/contracts?status=eq.open"
    assert body is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "C:/Personal/Projects/home-contracts"
pytest tests/test_supabase_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'supabase_client'`

- [ ] **Step 3: Write the implementation**

`supabase_client.py`:

```python
import json


class SupabaseError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Supabase error {status}: {body}")
        self.status = status
        self.body = body


class SupabaseClient:
    def __init__(self, url, anon_key, fetcher):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self._fetcher = fetcher

    def _headers(self, with_content_type=False):
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
        }
        if with_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    async def rpc(self, fn_name, params):
        url = f"{self.url}/rest/v1/rpc/{fn_name}"
        status, body = await self._fetcher(
            "POST", url, self._headers(with_content_type=True), json.dumps(params)
        )
        if status >= 400:
            raise SupabaseError(status, body)
        return body

    async def select(self, table, query=""):
        url = f"{self.url}/rest/v1/{table}{query}"
        status, body = await self._fetcher("GET", url, self._headers(), None)
        if status >= 400:
            raise SupabaseError(status, body)
        return body
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_supabase_client.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Add a `pyodide_fetcher` for real browser use (not covered by pytest — it depends on the `pyodide` module, only importable inside a Pyodide runtime)**

Append to `supabase_client.py`:

```python
async def pyodide_fetcher(method, url, headers, body):
    """Fetcher implementation for use inside PyScript/Pyodide. Not unit-tested
    here because it requires the `pyodide` module, which only exists inside
    a Pyodide runtime — exercised instead by the manual browser checks in
    later tasks."""
    from pyodide.http import pyfetch

    kwargs = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = body
    response = await pyfetch(url, **kwargs)
    payload = await response.json()
    return response.status, payload
```

- [ ] **Step 6: Commit**

```bash
git add supabase_client.py tests/test_supabase_client.py
git commit -m "feat: add framework-agnostic Supabase REST/RPC client"
```

---

## Task 5: PyScript app shell and config

**Files:**
- Create: `C:\Personal\Projects\home-contracts\config.py`
- Create: `C:\Personal\Projects\home-contracts\index.html`
- Create: `C:\Personal\Projects\home-contracts\app.py`
- Create: `C:\Personal\Projects\home-contracts\style.css`

**Interfaces:**
- Consumes: `SupabaseClient`, `pyodide_fetcher` from `supabase_client.py` (Task 4).
- Produces: a `#app` DOM element that later tasks render screens into, and a module-level `client = SupabaseClient(...)` in `app.py` that later tasks reuse.

- [ ] **Step 1: Write `config.py`**

Get the local Supabase `API URL` and `anon key` from `supabase status` (run in Task 2's terminal) and fill them in:

```python
SUPABASE_URL = "http://127.0.0.1:54321"  # replace with hosted project URL before deploying
SUPABASE_ANON_KEY = "REPLACE_WITH_LOCAL_ANON_KEY_FROM_SUPABASE_STATUS"
VAPID_PUBLIC_KEY = ""  # filled in during Task 8
```

- [ ] **Step 2: Write `index.html`**

```html
<!DOCTYPE html>
<html lang="uk">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Домашні контракти</title>
  <link rel="stylesheet" href="style.css" />
  <link rel="manifest" href="manifest.json" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@pyscript/core@0.4.32/dist/core.css" />
  <script type="module" src="https://cdn.jsdelivr.net/npm/@pyscript/core@0.4.32/dist/core.js"></script>
</head>
<body>
  <div id="app">Завантаження...</div>
  <script type="py" src="app.py" config='{"files": {"config.py": "config.py", "supabase_client.py": "supabase_client.py"}}'></script>
</body>
</html>
```

- [ ] **Step 3: Write minimal `app.py`**

```python
from pyscript import document
from supabase_client import SupabaseClient, pyodide_fetcher
from config import SUPABASE_URL, SUPABASE_ANON_KEY

client = SupabaseClient(SUPABASE_URL, SUPABASE_ANON_KEY, pyodide_fetcher)


def render_root():
    app = document.getElementById("app")
    app.innerText = "Домашні контракти — завантажено"


render_root()
```

- [ ] **Step 4: Write minimal `style.css`**

```css
body {
  font-family: system-ui, sans-serif;
  margin: 0;
  padding: 1rem;
  background: #1a1a1a;
  color: #eee;
}

#app {
  max-width: 480px;
  margin: 0 auto;
}
```

- [ ] **Step 5: Serve locally and verify manually**

```bash
python -m http.server 8000
```

Open `http://localhost:8000/` in a browser. Expected: after Pyodide finishes loading (a few seconds on first load), the page text changes from "Завантаження..." to "Домашні контракти — завантажено". Confirm via browser DevTools console: no red errors.

- [ ] **Step 6: Commit**

```bash
git add config.py index.html app.py style.css
git commit -m "feat: PyScript app shell loading Supabase client"
```

---

## Task 6: Profile select + PIN screen

**Files:**
- Modify: `C:\Personal\Projects\home-contracts\app.py`

**Interfaces:**
- Consumes: `client.select`, `client.rpc` from Task 4; `render_root` pattern from Task 5.
- Produces: module-level `current_profile: dict | None` (set after successful PIN check) and `render_login()`, used by Task 7 to gate the contract board behind login.

- [ ] **Step 1: Replace `app.py` with the login screen**

```python
from pyscript import document
from pyodide.ffi import create_proxy
from supabase_client import SupabaseClient, SupabaseError, pyodide_fetcher
from config import SUPABASE_URL, SUPABASE_ANON_KEY

client = SupabaseClient(SUPABASE_URL, SUPABASE_ANON_KEY, pyodide_fetcher)

current_profile = None


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
    global current_profile
    profiles = await client.select("public_profiles", f"?id=eq.{profile_id}&select=id,name,points,level")
    current_profile = profiles[0]
    await render_board_placeholder()


async def render_board_placeholder():
    app = document.getElementById("app")
    app.innerText = f"Привіт, {current_profile['name']}! (рівень {current_profile['level']})"


await render_login()
```

- [ ] **Step 2: Seed two real profiles for manual testing**

```bash
export DB_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"
psql "$DB_URL" -c "insert into profiles (name, pin_hash) values ('Alice', crypt('1234', gen_salt('bf'))), ('Bob', crypt('5678', gen_salt('bf')));"
```

- [ ] **Step 3: Verify manually in the browser**

Refresh `http://localhost:8000/`. Expected: a dropdown with "Alice"/"Bob" and a PIN field. Select Alice, type `1234`, click "Увійти" — page shows `Привіт, Alice! (рівень 1)`. Reload, select Alice, type `0000` — page shows `Невірний PIN` and stays on the login screen.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: profile select + PIN login screen"
```

---

## Task 7: Contract board — list, create

**Files:**
- Modify: `C:\Personal\Projects\home-contracts\app.py`

**Interfaces:**
- Consumes: `current_profile`, `client` from Task 6.
- Produces: `render_board()` (replaces `render_board_placeholder`), used as the main screen by Task 8's accept/decline buttons and Task 9's complete/confirm buttons rendered on the same contract cards.

- [ ] **Step 1: Replace `render_board_placeholder` with the real board**

In `app.py`, replace the `render_board_placeholder` function and its call site with:

```python
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


def contract_card_html(c):
    return f"""
      <div class="contract-card" data-id="{c['id']}">
        <strong>{c['title']}</strong> — {c['points']} балів
        <div>Статус: {c['status']}</div>
      </div>
    """


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
```

- [ ] **Step 2: Keep the PIN in memory for reuse on later actions**

In `on_login_click`, right after `global current_profile`, add a second global and store the entered PIN:

```python
global current_profile, current_pin
current_pin = pin
```

And declare it at module level near `current_profile = None`:

```python
current_profile = None
current_pin = None
```

- [ ] **Step 3: Wire login success to the real board**

In `on_login_click`, change the final call from `await render_board_placeholder()` to `await render_board()`.

- [ ] **Step 4: Verify manually in the browser**

Refresh, log in as Alice, create a contract titled "Dishes" with 20 points. Expected: the board re-renders showing the "Dishes" card with `Статус: open`. Check via `psql`:

```bash
psql "$DB_URL" -c "select title, status, points from contracts;"
```

Expected: one row, `Dishes | open | 20`.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: contract board with list and create"
```

---

## Task 8: Accept / decline actions

**Files:**
- Modify: `C:\Personal\Projects\home-contracts\app.py`

**Interfaces:**
- Consumes: `contract_card_html`, `render_board`, `client`, `current_profile`, `current_pin` from Task 7.
- Produces: buttons wired via event delegation on `#contracts-list`, reused by Task 9 for the complete/confirm buttons on the same cards.

- [ ] **Step 1: Extend `contract_card_html` with action buttons based on status and viewer**

Replace `contract_card_html` in `app.py`:

```python
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
```

- [ ] **Step 2: Wire event delegation for accept/decline in `render_board`**

Add after the existing `create-btn` listener in `render_board`:

```python
    document.getElementById("contracts-list").addEventListener("click", create_proxy(on_board_click))
```

- [ ] **Step 3: Add the delegated click handler**

```python
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
```

- [ ] **Step 4: Verify manually in the browser (both roles)**

Log in as Alice, create "Dishes" (20 points). Log out is not built yet — instead open a second browser tab/profile by reloading and logging in as Bob in a private/incognito window (so both sessions coexist). As Bob: click "Прийняти" on the Dishes card. Expected: status changes to `accepted`, and Bob's card now shows "Завершив"/"Відмовитись" buttons. Click "Завершив". Expected: status becomes `done_pending_confirm`. Switch to Alice's window, reload, expected: Alice's card shows "Підтвердити". Click it. Expected: status becomes `confirmed`, and the header now shows Bob's — no, Alice's own — points/level unchanged, since points go to the assignee. Verify Bob's points increased by 20:

```bash
psql "$DB_URL" -c "select name, points, level from profiles where name = 'Bob';"
```

Expected: `points` increased by 20 from its value before this test.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: accept/decline/complete/confirm actions on contract board"
```

---

## Task 9: Service worker registration and push subscription

**Files:**
- Create: `C:\Personal\Projects\home-contracts\sw.js`
- Create: `C:\Personal\Projects\home-contracts\manifest.json`
- Modify: `C:\Personal\Projects\home-contracts\app.py`
- Modify: `C:\Personal\Projects\home-contracts\index.html`

**Interfaces:**
- Consumes: `client`, `current_profile`, `current_pin` from Task 8.
- Produces: a registered service worker at scope `/`, and a `push_subscriptions` row per logged-in profile per device. `VAPID_PUBLIC_KEY` from `config.py` (Task 5) must be a real key by the time this task runs — generate it in Task 10 first if not already done, or generate it now (see Step 1).

- [ ] **Step 1: Generate a VAPID keypair**

```bash
npx web-push generate-vapid-keys
```

Expected: prints a `Public Key` and `Private Key`. Put the public key into `config.py`'s `VAPID_PUBLIC_KEY`. Save the private key somewhere safe (a password manager or local `.env` — it's needed again in Task 10, not committed to git).

- [ ] **Step 2: Write `manifest.json`**

```json
{
  "name": "Домашні контракти",
  "short_name": "Контракти",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a1a",
  "theme_color": "#1a1a1a",
  "icons": []
}
```

- [ ] **Step 3: Write `sw.js`**

```javascript
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Домашні контракти", {
      body: data.body || "У тебе новий контракт",
    })
  );
});
```

- [ ] **Step 4: Register the service worker and request push permission from `app.py`**

Add to `app.py`, using the `js` module for browser APIs PyScript doesn't wrap:

```python
import js
from pyodide.ffi import to_js
from config import VAPID_PUBLIC_KEY


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
```

Call it at the end of `on_login_click`, after `await render_board()`:

```python
    await enable_push()
```

- [ ] **Step 5: Verify manually in the browser**

Note: `serviceWorker.register` and push subscription require a secure context — `http://localhost` is allowed by browsers as an exception, but the hosted version (Task 11) needs HTTPS, which GitHub Pages provides by default. Refresh, log in as Alice. Expected: the browser shows a native "Allow notifications?" prompt. Accept it. Confirm a row appeared:

```bash
psql "$DB_URL" -c "select profile_id, endpoint from push_subscriptions;"
```

Expected: one row with `profile_id` matching Alice's id.

- [ ] **Step 6: Commit**

```bash
git add sw.js manifest.json app.py config.py index.html
git commit -m "feat: service worker registration and push subscription storage"
```

---

## Task 10: Database Webhook + Edge Function for Web Push delivery

**Files:**
- Create: `C:\Personal\Projects\home-contracts\supabase\functions\send-push\index.ts`

**Interfaces:**
- Consumes: `push_subscriptions`, `notifications_log`, `contracts` tables from Tasks 2–3; the VAPID private key generated in Task 9 Step 1.
- Produces: an HTTP endpoint (`/functions/v1/send-push`) that a Supabase Database Webhook calls on `INSERT INTO contracts`, which sends a Web Push notification to the subscriptions of the intended recipient (the `assignee_id` if set, otherwise every profile except the author) and records the send in `notifications_log` to avoid duplicates.

- [ ] **Step 1: Write the Edge Function**

`supabase/functions/send-push/index.ts`:

```typescript
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
```

- [ ] **Step 2: Create the hosted Supabase project (free tier) if not already done**

Go to https://supabase.com/dashboard, create a new project on the free tier. Note the project's `API URL`, `anon key`, and `service_role key` (Settings → API).

- [ ] **Step 3: Push schema migrations to the hosted project**

```bash
supabase link --project-ref <your-project-ref>
supabase db push
```

Expected: output lists `0001_init.sql` and `0002_push.sql` as applied with no errors.

- [ ] **Step 4: Set Edge Function secrets on the hosted project**

```bash
supabase secrets set VAPID_PUBLIC_KEY="<public key from Task 9 Step 1>"
supabase secrets set VAPID_PRIVATE_KEY="<private key from Task 9 Step 1>"
```

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are provided automatically to every Edge Function by Supabase — no need to set them manually.

- [ ] **Step 5: Deploy the Edge Function**

```bash
supabase functions deploy send-push
```

Expected: output confirms deployment and prints the function URL (`https://<project-ref>.supabase.co/functions/v1/send-push`).

- [ ] **Step 6: Create the Database Webhook**

In the Supabase Dashboard → Database → Webhooks → Create a new webhook:
- Table: `contracts`
- Events: `INSERT`
- Type: HTTP Request → your deployed function URL
- HTTP method: POST
- Add header `Authorization: Bearer <service_role key>` (Edge Functions require auth by default)

- [ ] **Step 7: Update `config.py` to point at the hosted project**

```python
SUPABASE_URL = "https://<project-ref>.supabase.co"
SUPABASE_ANON_KEY = "<hosted anon key>"
VAPID_PUBLIC_KEY = "<public key from Task 9 Step 1>"
```

- [ ] **Step 8: Re-seed the two profiles on the hosted DB**

```bash
export HOSTED_DB_URL="<connection string from Dashboard → Settings → Database>"
psql "$HOSTED_DB_URL" -c "insert into profiles (name, pin_hash) values ('Alice', crypt('1234', gen_salt('bf'))), ('Bob', crypt('5678', gen_salt('bf')));"
```

- [ ] **Step 9: Verify manually end-to-end**

Serve the app locally against the hosted backend (`python -m http.server 8000`, since `config.py` now points at the hosted project), log in as Bob on one device and grant push permission (Task 9), then log in as Alice on another device/browser and create a contract assigned nowhere (open board). Expected: Bob's device shows a native push notification "Новий контракт!" within a few seconds. Confirm the dedupe row:

```bash
psql "$HOSTED_DB_URL" -c "select contract_id from notifications_log;"
```

Expected: exactly one row for that contract, even if the webhook fires more than once.

- [ ] **Step 10: Commit**

```bash
git add supabase/functions/send-push/index.ts config.py
git commit -m "feat: push notification delivery via Database Webhook + Edge Function"
```

---

## Task 11: Deploy to GitHub Pages

**Files:**
- Create: `C:\Personal\Projects\home-contracts\.github\workflows\deploy.yml` — not needed for a plain static site; GitHub Pages can serve directly from a branch. This task configures that instead.

**Interfaces:** None — deployment only, no code interfaces.

- [ ] **Step 1: Create the GitHub repo and push**

Ask for explicit confirmation before this step, since it creates a repository visible outside the local machine and pushes code to it.

```bash
gh repo create home-contracts --private --source=. --remote=origin
git push -u origin main
```

- [ ] **Step 2: Enable GitHub Pages**

In the repo on GitHub: Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder `/ (root)`. Save.

- [ ] **Step 3: Verify manually**

Wait ~1 minute, then open the URL GitHub Pages shows (`https://<username>.github.io/home-contracts/`). Expected: the login screen loads (same as the local manual checks in Task 6), served over HTTPS. Log in as Alice, confirm the board loads and push permission prompt appears on a phone.

- [ ] **Step 4: Commit** (nothing to commit — Pages config lives in repo settings, not files)

---

## Task 12: End-to-end two-phone manual test

**Files:** None — verification only.

**Interfaces:** None.

- [ ] **Step 1: Full contract lifecycle on two real Android phones**

On phone A (Alice): open the GitHub Pages URL, add to home screen (optional), log in, grant push permission. On phone B (Bob): same, log in as Bob, grant push permission.

- [ ] **Step 2: Create and deliver**

On phone A: create a contract "Take out trash", 15 points, left unassigned. Expected: phone B receives a push notification within a few seconds.

- [ ] **Step 3: Accept, complete, confirm**

On phone B: tap the notification (or open the app), accept the contract, then tap "Завершив". On phone A: reload the board, tap "Підтвердити" on Bob's card.

- [ ] **Step 4: Verify points**

On phone B: reload, confirm the header now shows 15 more points than before, and the level number is correct per `level = 1 + floor(points / 100)`.

- [ ] **Step 5: Verify decline path**

On phone A: create a second contract "Vacuum", 10 points, assigned to nobody. On phone B: accept it, then tap "Відмовитись". Expected: the card returns to `open` status and is visible again on phone A without an assignee.

If all five steps behave as expected, the baseline is complete.
