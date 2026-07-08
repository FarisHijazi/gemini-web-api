"""Lazy, shared gemini_webapi client with auto-refresh and re-init on failure.

A single GeminiClient is shared across all requests. It is initialized once on
first use and kept alive with auto_refresh so the rotating __Secure-1PSIDTS
cookie stays fresh for a long-running server.
"""

from __future__ import annotations

import asyncio

from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import AuthError

from . import config


class GeminiManager:
    def __init__(self) -> None:
        self._client: GeminiClient | None = None
        self._lock = asyncio.Lock()

    async def _make_client(self) -> GeminiClient:
        # Route to the configured Google multi-login account (u/N) before init,
        # so the access-token fetch and all requests target the right account.
        # Also make the session carry the full google.com cookie jar, which is
        # required to authenticate a non-default account (and hardens auth for
        # the default one so tokens refresh without frequent re-login).
        from .account import apply_authuser, install_full_jar

        apply_authuser(config.AUTHUSER)
        install_full_jar(config.get_full_jar)

        psid, psidts = config.get_cookies()
        if not psid:
            raise AuthError(
                "No Gemini credentials. Set GEMINI_1PSID/GEMINI_1PSIDTS or log in "
                "to gemini.google.com in local Chrome."
            )
        proxy = config.os.getenv("GEMINI_PROXY") or None
        client = GeminiClient(psid, psidts, proxy=proxy)
        await client.init(timeout=120, auto_refresh=True, refresh_interval=540)
        return client

    async def get(self) -> GeminiClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await self._make_client()
        return self._client

    async def reset(self) -> None:
        """Drop the client so the next get() re-initializes (e.g. after AuthError)."""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:  # noqa: BLE001
                    pass
            self._client = None

    async def generate(self, *args, **kwargs):
        """generate_content with one automatic re-init retry on auth failure."""
        client = await self.get()
        try:
            return await client.generate_content(*args, **kwargs)
        except AuthError:
            await self.reset()
            client = await self.get()
            return await client.generate_content(*args, **kwargs)


manager = GeminiManager()
