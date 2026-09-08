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
