"""365Scores API client. No auth, no anti-bot, just plain HTTP."""

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


class Client365:
    """HTTP client for 365Scores API."""

    def __init__(self, hebrew: bool = False):
        params = {**DEFAULT_PARAMS}
        if hebrew:
            params["langId"] = "2"
        self._http = httpx.Client(
            base_url=BASE_URL,
            params=params,
            headers=_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._http.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
