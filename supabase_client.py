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


async def pyodide_fetcher(method, url, headers, body):
    """Fetcher implementation for use inside PyScript/Pyodide. Not unit-tested
    here because it requires the `pyodide` module, which only exists inside
    a Pyodide runtime - exercised instead by manual browser checks."""
    from pyodide.http import pyfetch

    kwargs = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = body
    response = await pyfetch(url, **kwargs)
    payload = await response.json()
    return response.status, payload
