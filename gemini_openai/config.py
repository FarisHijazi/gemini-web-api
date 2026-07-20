"""Configuration, cookie extraction, and model mapping.

Single source of truth for:
  - how we obtain Gemini auth cookies (env var first, then local Chrome)
  - how OpenAI-style model names map to gemini_webapi Model enums
  - server-level settings (optional API key, host/port)
"""

from __future__ import annotations

import glob
import os

from gemini_webapi.constants import Model

# --------------------------------------------------------------------------- #
# Server settings
# --------------------------------------------------------------------------- #
HOST = os.getenv("GEMINI_API_HOST", "0.0.0.0")
PORT = int(os.getenv("GEMINI_API_PORT", "8100"))
# Optional bearer token clients must present. Empty => no auth enforced.
API_KEY = os.getenv("GEMINI_API_KEY", "")
# Google multi-login account index (the N in gemini.google.com/u/N/app).
# Empty/None => default account (u/0).
AUTHUSER = os.getenv("GEMINI_AUTHUSER", "") or None
# Optional Chrome DevTools endpoint used to download finished Veo videos through
# a logged-in browser (the usercontent host needs a per-account OSID only the
# browser mints — see gemini_openai/video_bridge.py). Unset => skip the bridge
# and just return the browser-playable download_url.
CDP_URL = os.getenv("GEMINI_CDP_URL", "") or None

CHROME_DIR = os.path.expanduser("~/.config/google-chrome")


# --------------------------------------------------------------------------- #
# Auth cookies
# --------------------------------------------------------------------------- #
def _cookie_stores() -> list[str]:
    # Pin a specific profile with GEMINI_CHROME_PROFILE (e.g. "Profile 5").
    pin = os.getenv("GEMINI_CHROME_PROFILE")
    profiles = (
        [os.path.join(CHROME_DIR, pin)] if pin else glob.glob(os.path.join(CHROME_DIR, "*"))
    )
    paths: list[str] = []
    for prof in profiles:
        for name in ("Network/Cookies", "Cookies"):
            p = os.path.join(prof, name)
            if os.path.isfile(p):
                paths.append(p)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


def cookies_via_cdp() -> dict[str, str]:
    """Pull the `.google.com` cookie jar from a logged-in Chrome over CDP.

    Used automatically when the local Chrome cookie store can't be read (remote/
    headless host, encrypted store with a locked keyring, a Chrome that isn't the
    one this process can see). Requires GEMINI_CDP_URL to point at a Chrome
    started with `--remote-debugging-port`. Returns {} when unavailable.

    Runs the async CDP call on its own loop in a worker thread so this stays
    callable from sync code even while a server event loop is running.
    """
    if not CDP_URL:
        return {}
    import asyncio
    import threading

    box: dict[str, dict[str, str]] = {}

    def _run() -> None:
        try:
            from .video_bridge import fetch_cookies

            box["jar"] = asyncio.run(fetch_cookies(CDP_URL))
        except Exception:  # noqa: BLE001
            box["jar"] = {}

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(30)
    return box.get("jar", {})


def get_cookies() -> tuple[str | None, str | None]:
    """Return (secure_1psid, secure_1psidts).

    Priority:
      1. GEMINI_1PSID / GEMINI_1PSIDTS env vars (for headless/remote deploys)
      2. Local Chrome cookie store via browser_cookie3
    Either value may be None; gemini_webapi can often refresh 1PSIDTS itself
    given a valid 1PSID.
    """
    psid = os.getenv("GEMINI_1PSID")
    psidts = os.getenv("GEMINI_1PSIDTS")
    if psid:
        return psid, psidts

    try:
        import browser_cookie3
    except ImportError:
        return None, None

    wanted = ("__Secure-1PSID", "__Secure-1PSIDTS")
    best: dict[str, str] = {}
    for store in _cookie_stores():
        try:
            cj = browser_cookie3.chrome(cookie_file=store, domain_name=".google.com")
        except Exception:  # noqa: BLE001
            continue
        found = {c.name: c.value for c in cj if c.name in wanted}
        if "__Secure-1PSID" in found and len(found) >= len(best):
            best = found
        if all(k in found for k in wanted):
            break
    if not best.get("__Secure-1PSID"):
        # Local store unreadable — try a logged-in Chrome over CDP instead.
        jar = cookies_via_cdp()
        if jar.get("__Secure-1PSID"):
            return jar.get("__Secure-1PSID"), jar.get("__Secure-1PSIDTS")
    return best.get("__Secure-1PSID"), best.get("__Secure-1PSIDTS")


def get_full_jar() -> dict[str, str]:
    """Full `.google.com` (path=/) auth cookie jar from the selected profile.

    Required for Google multi-login: a non-default account (u/N) is authenticated
    by the shared SID/SAPISID/OSID session cookies, not by __Secure-1PSID alone.
    Returns {} when running from env cookies (no local browser).
    """
    if os.getenv("GEMINI_1PSID"):
        return {}
    try:
        import browser_cookie3
    except ImportError:
        return {}
    for store in _cookie_stores():
        try:
            cj = browser_cookie3.chrome(cookie_file=store, domain_name="google.com")
        except Exception:  # noqa: BLE001
            continue
        jar = {
            c.name: c.value
            for c in cj
            if c.domain.endswith(".google.com") and c.path == "/"
        }
        if "__Secure-1PSID" in jar:
            return jar
    # Local store unreadable — fall back to a logged-in Chrome over CDP.
    return cookies_via_cdp()


# --------------------------------------------------------------------------- #
# Model mapping
# --------------------------------------------------------------------------- #
# Canonical names are the gemini_webapi model_name strings; we add convenient
# aliases so OpenAI-oriented clients (which often hardcode gpt-* names) work.
_CANON: dict[str, Model] = {
    "gemini-3-pro": Model.BASIC_PRO,
    "gemini-3-flash": Model.BASIC_FLASH,
    "gemini-3-flash-thinking": Model.BASIC_THINKING,
    "gemini-3-pro-plus": Model.PLUS_PRO,
    "gemini-3-flash-plus": Model.PLUS_FLASH,
    "gemini-3-flash-thinking-plus": Model.PLUS_THINKING,
    "gemini-3-pro-advanced": Model.ADVANCED_PRO,
    "gemini-3-flash-advanced": Model.ADVANCED_FLASH,
    "gemini-3-flash-thinking-advanced": Model.ADVANCED_THINKING,
}

_ALIASES: dict[str, Model] = {
    # short forms
    "gemini-pro": Model.BASIC_PRO,
    "gemini-flash": Model.BASIC_FLASH,
    "gemini-thinking": Model.BASIC_THINKING,
    "pro": Model.BASIC_PRO,
    "flash": Model.BASIC_FLASH,
    "thinking": Model.BASIC_THINKING,
    # legacy gemini names -> nearest current
    "gemini-2.5-pro": Model.BASIC_PRO,
    "gemini-2.5-flash": Model.BASIC_FLASH,
    "gemini-1.5-pro": Model.BASIC_PRO,
    "gemini-1.5-flash": Model.BASIC_FLASH,
    # openai names -> sensible defaults so drop-in clients work
    "gpt-4": Model.BASIC_PRO,
    "gpt-4o": Model.BASIC_PRO,
    "gpt-4-turbo": Model.BASIC_PRO,
    "gpt-4o-mini": Model.BASIC_FLASH,
    "gpt-3.5-turbo": Model.BASIC_FLASH,
}

DEFAULT_MODEL = Model.BASIC_FLASH


def resolve_model(name: str | None) -> Model:
    if not name:
        return DEFAULT_MODEL
    key = name.strip().lower()
    if key in _CANON:
        return _CANON[key]
    if key in _ALIASES:
        return _ALIASES[key]
    # tolerate names with provider prefixes like "models/gemini-3-pro"
    key2 = key.split("/")[-1]
    return _CANON.get(key2) or _ALIASES.get(key2) or DEFAULT_MODEL


def list_public_models() -> list[str]:
    """Names advertised on /v1/models."""
    return list(_CANON.keys())
