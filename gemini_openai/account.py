"""Multi-account (Google `/u/N/`) support for gemini_webapi.

Google multi-login selects an account by an index in the URL path:
    https://gemini.google.com/u/5/app
    https://gemini.google.com/u/5/_/BardChatUi/data/.../StreamGenerate

gemini_webapi hardcodes the default (index 0) endpoints. To target another
signed-in account we swap the module-level `Endpoint` reference in each module
that uses it for a prefixed shim. The shared cookie jar authenticates every
signed-in account simultaneously; the `/u/N/` path is what disambiguates, and
the access token (SNlM0e) fetched from `/u/N/app` is account-specific.
"""

from __future__ import annotations

import sys

import gemini_webapi.client  # noqa: F401  (ensure module is imported)
import gemini_webapi.utils.get_access_token  # noqa: F401
import gemini_webapi.utils.rotate_1psidts  # noqa: F401
from gemini_webapi.constants import Endpoint as _E, Headers as _H

# NOTE: resolve the real MODULE objects from sys.modules, not via attribute
# access. `gemini_webapi.utils.get_access_token` resolves to the re-exported
# *function* (the package __init__ does `from .get_access_token import
# get_access_token`), so `import ... as x` would bind the function and our
# patches would silently no-op. sys.modules always holds the module.
_client = sys.modules["gemini_webapi.client"]
_gat = sys.modules["gemini_webapi.utils.get_access_token"]
_rot = sys.modules["gemini_webapi.utils.rotate_1psidts"]

_current: str | None = None
_jar_installed = False


def apply_authuser(index: str | int | None) -> None:
    """Point gemini_webapi at Google account `index` (None = default u/0)."""
    global _current
    if index is None or str(index) == "":
        return
    n = str(index)
    if _current == n:
        return
    prefix = f"https://gemini.google.com/u/{n}"

    class _EndpointShim:
        GOOGLE = str(_E.GOOGLE)
        INIT = f"{prefix}/app"
        GENERATE = f"{prefix}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        ROTATE_COOKIES = str(_E.ROTATE_COOKIES)
        UPLOAD = str(_E.UPLOAD)
        BATCH_EXEC = f"{prefix}/_/BardChatUi/data/batchexecute"

    for mod in (_client, _gat, _rot):
        mod.Endpoint = _EndpointShim
    _current = n


def install_full_jar(jar_getter) -> None:
    """Make the init request use the FULL google.com cookie jar.

    gemini_webapi's token fetch only sets __Secure-1PSID/1PSIDTS, which cannot
    authenticate a non-default account and is fragile even for the default one.
    We replace `_send_request` so the session carries every auth cookie the
    browser would send; the returned session is reused for generation, so all
    requests inherit the full jar. `jar_getter()` is called fresh each init so
    re-inits pick up rotated cookies from the browser.
    """
    global _jar_installed
    if _jar_installed:
        return

    async def _send_request(client, cookies, verbose=False):  # noqa: ANN001
        client.cookies.clear()
        jar = {}
        try:
            jar = jar_getter() or {}
        except Exception:  # noqa: BLE001
            jar = {}
        if jar:
            for k, v in jar.items():
                client.cookies.set(k, v, domain=".google.com")
        else:
            # fallback: original two-cookie behaviour
            if hasattr(cookies, "items"):
                for k, v in cookies.items():
                    client.cookies.set(k, v, domain=".google.com")
            else:
                client.cookies.update(cookies)
        resp = await client.get(_gat.Endpoint.INIT, headers=_H.GEMINI.value)
        resp.raise_for_status()
        return resp

    _gat._send_request = _send_request
    _jar_installed = True
