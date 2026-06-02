"""365Scores API client. No auth, no anti-bot, just plain HTTP."""

import threading

import httpx

BASE_URL = "https://webws.365scores.com/web"
DEFAULT_PARAMS = {
    "appTypeId": "5",
    "langId": "1",
    "timezoneName": "Asia/Jerusalem",
}


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.365scores.com/",
    "Origin": "https://www.365scores.com",
}


# Process-wide shared httpx clients, one per language. httpx.Client is
# thread-safe and keeps a connection pool, so reusing a single client across all
# call sites (and worker threads) means we pay the TLS handshake to 365scores
# once instead of on every request. `Client365()` instances now just borrow the
# shared client; `close()`/`__exit__` are no-ops so the existing
# `with Client365() as c:` call sites keep the pool warm instead of tearing the
# connection down after every use.
_shared_lock = threading.Lock()
_shared_clients: dict[bool, httpx.Client] = {}


def _shared_client(hebrew: bool) -> httpx.Client:
    cli = _shared_clients.get(hebrew)
    if cli is not None:
        return cli
    with _shared_lock:
        cli = _shared_clients.get(hebrew)
        if cli is None:
            params = {**DEFAULT_PARAMS}
            if hebrew:
                params["langId"] = "2"
            cli = httpx.Client(
                base_url=BASE_URL,
                params=params,
                headers=_HEADERS,
                timeout=30.0,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
            _shared_clients[hebrew] = cli
    return cli


class Client365:
    """Thin handle over a process-wide shared 365Scores HTTP client."""

    def __init__(self, hebrew: bool = False):
        self._http = _shared_client(hebrew)

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._http.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def close(self):
        # No-op: the underlying client is shared process-wide and must outlive
        # any single `with Client365()` block. Closing it here would tear down
        # the connection pool every call — exactly what we're avoiding.
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
