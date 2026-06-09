"""Async HTTP client for the Logseq local API."""

from __future__ import annotations

from typing import Any, Optional

import httpx


class LogseqError(Exception):
    """A Logseq API call failed."""


class LogseqClient:
    def __init__(self, url: str, token: str, timeout: float = 15.0) -> None:
        self._url = (url or "http://localhost:12315").rstrip("/")
        self._token = token
        self._http = httpx.AsyncClient(timeout=timeout)

    async def call(self, method: str, args: Optional[list[Any]] = None) -> Any:
        """Invoke a `logseq.*` method; return the decoded JSON result."""
        try:
            resp = await self._http.post(
                f"{self._url}/api",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json={"method": method, "args": args or []},
            )
        except httpx.RequestError as exc:
            raise LogseqError(f"network error calling {method}: {exc}") from exc

        if resp.status_code == 401:
            raise LogseqError("invalid Logseq API token")
        if resp.status_code >= 400:
            raise LogseqError(f"{method} failed ({resp.status_code}): {resp.text}")
        if not resp.content:
            return None
        return resp.json()

    async def aclose(self) -> None:
        await self._http.aclose()
