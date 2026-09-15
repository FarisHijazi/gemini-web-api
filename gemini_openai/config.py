"""Configuration, cookie extraction, and model mapping.

Single source of truth for:
  - how we obtain Gemini auth cookies (env var first, then local Chrome)
  - how OpenAI-style model names map to gemini_webapi model names
  - server-level settings (optional API key, host/port)
"""

from __future__ import annotations

import glob
import json
import os
import sys


# --------------------------------------------------------------------------- #
# Server settings
# --------------------------------------------------------------------------- #
HOST = os.getenv("GEMINI_API_HOST", "0.0.0.0")
PORT = int(os.getenv("GEMINI_API_PORT", "8100"))
# Chat-backend preference (both backends always load; this only decides routing):
#   "auto" (default) — use the Chrome extension for chat when a tab is connected,
#            otherwise fall back to the cookie/CDP gemini_webapi library.
#   "webapi" — always the cookie backend (chat + images + Veo video).
#   "chrome" — always the extension for chat (errors if no tab is connected).
# Media generation (images/video) always uses the cookie backend regardless, so a
# single server serves chat-via-extension AND cookie-based media at once.
BACKEND = os.getenv("GEMINI_BACKEND", "auto").strip().lower()
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


def _default_chrome_dir() -> str:
    """Chrome's user-data directory for this platform.

    macOS and Windows do not use the Linux path, and cookie discovery silently
    returns nothing when it is wrong.
    """
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if os.name == "nt":
        return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    return os.path.expanduser("~/.config/google-chrome")


CHROME_DIR = os.getenv("GEMINI_CHROME_DIR") or _default_chrome_dir()


# --------------------------------------------------------------------------- #
# Auth cookies
# --------------------------------------------------------------------------- #
def profile_accounts() -> dict[str, str]:
    """Chrome profile directory -> signed-in account email, from Local State."""
    try:
        with open(os.path.join(CHROME_DIR, "Local State"), encoding="utf-8") as f:
            cache = json.load(f).get("profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        return {}
    return {prof: (meta.get("user_name") or "") for prof, meta in cache.items()}


def account_of(store_path: str) -> str:
    """Account email behind a cookie-store path, or '' if unknown."""
    parts = store_path.split(os.sep)
    prof = parts[-2] if parts[-1] == "Cookies" else parts[-3]
    if prof == "Network":
        prof = parts[-3]
    return profile_accounts().get(prof, "")


def _pinned_profile() -> str | None:
    """The profile directory to use, or None to fall back to browsing them all.

    GEMINI_CHROME_ACCOUNT pins by email, which is the only stable way to say
    which Google account this server speaks as -- Chrome's "Profile N" directory
    names are opaque and the mapping differs per machine. A pinned account that
    does not exist is a hard error: silently falling back would mean running
    under whichever account happens to win, which is how this went wrong before.
    """
    if pin := os.getenv("GEMINI_CHROME_PROFILE"):
        return pin
    account = (os.getenv("GEMINI_CHROME_ACCOUNT") or "").strip().lower()
    if not account:
        return None
    for prof, email in profile_accounts().items():
        if email.strip().lower() == account:
            return prof
    known = sorted(e for e in profile_accounts().values() if e)
    raise RuntimeError(
        f"GEMINI_CHROME_ACCOUNT={account!r} is not signed in to Chrome at "
        f"{CHROME_DIR}. Signed-in accounts: {', '.join(known) or '(none)'}"
    )


def _cookie_stores() -> list[str]:
    pin = _pinned_profile()
    profiles = (
        [os.path.join(CHROME_DIR, pin)] if pin else glob.glob(os.path.join(CHROME_DIR, "*"))
    )
    paths: list[str] = []
    for prof in profiles:
        for name in ("Network/Cookies", "Cookies"):
            p = os.path.join(prof, name)
            if os.path.isfile(p):
                paths.append(p)
    # Most-recently-used profile first. NOTE: unpinned, this makes the account
    # depend on which Chrome profile you last touched -- so with several
    # accounts signed in, ALWAYS pin GEMINI_CHROME_ACCOUNT.
    paths.sort(key=lambda p: (-os.path.getmtime(p), p))
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


_announced = False


def _announce_account(store: str) -> None:
    """Log the account exactly once -- the wrong one is otherwise invisible."""
    global _announced
    if _announced:
        return
    _announced = True
    who = account_of(store) or "unknown account"
    pinned = os.getenv("GEMINI_CHROME_ACCOUNT") or os.getenv("GEMINI_CHROME_PROFILE")
    how = "pinned" if pinned else "AUTO-SELECTED (most recently used Chrome profile)"
    print(f"[gemini] using Google account: {who}  [{how}]", flush=True)


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
            _announce_account(store)
            return jar
    # Local store unreadable — fall back to a logged-in Chrome over CDP.
    return cookies_via_cdp()


# --------------------------------------------------------------------------- #
# Model mapping
# --------------------------------------------------------------------------- #
# Values are library model-NAME strings, never `Model` enum members.
#
# The enum is deprecated in gemini-webapi 2.1 and pending removal, whereas name
# resolution works on every version we support:
#   * 2.0.x registers the models as `gemini-3-pro`, `gemini-3-flash`, ... so a
#     `gemini-3-*` name is an exact match.
#   * 2.1.x renamed them to `gemini-pro`, `gemini-flash`, ... but resolves names
#     through MODEL_PREFIX_RE (`^gemini-(?:\d+(?:\.\d+)?-)?`), which strips the
#     version, so `gemini-3-pro` still matches `gemini-pro`.
# One table therefore serves both, and the public names below stay stable no
# matter what upstream calls them internally.


def _thinking_tier() -> str | None:
    """Name of the thinking tier, or None where the library no longer has one.

    2.1 deleted BASIC/PLUS/ADVANCED_THINKING outright. `*_LITE` is a NEW cheap
    tier with its own model id, not the thinking tier renamed, so there is
    nothing to fall forward to -- callers asking for thinking get flash.
    """
    try:
        from gemini_webapi.constants import Model
    except ImportError:  # pragma: no cover - library always present in practice
        return None
    return "gemini-3-flash-thinking" if hasattr(Model, "BASIC_THINKING") else None


_THINKING = _thinking_tier()


def _thinking(name: str, fallback: str) -> str:
    """`name` where the library still has a thinking tier, else `fallback`."""
    return name if _THINKING else fallback


_CANON: dict[str, str] = {
    "gemini-3-pro": "gemini-3-pro",
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-flash-thinking": _thinking("gemini-3-flash-thinking", "gemini-3-flash"),
    "gemini-3-pro-plus": "gemini-3-pro-plus",
    "gemini-3-flash-plus": "gemini-3-flash-plus",
    "gemini-3-flash-thinking-plus": _thinking(
        "gemini-3-flash-thinking-plus", "gemini-3-flash-plus"
    ),
    "gemini-3-pro-advanced": "gemini-3-pro-advanced",
    "gemini-3-flash-advanced": "gemini-3-flash-advanced",
    "gemini-3-flash-thinking-advanced": _thinking(
        "gemini-3-flash-thinking-advanced", "gemini-3-flash-advanced"
    ),
}

_ALIASES: dict[str, str] = {
    # short forms
    "gemini-pro": "gemini-3-pro",
    "gemini-flash": "gemini-3-flash",
    "gemini-thinking": _CANON["gemini-3-flash-thinking"],
    "pro": "gemini-3-pro",
    "flash": "gemini-3-flash",
    "thinking": _CANON["gemini-3-flash-thinking"],
    # legacy gemini names -> nearest current
    "gemini-2.5-pro": "gemini-3-pro",
    "gemini-2.5-flash": "gemini-3-flash",
    "gemini-1.5-pro": "gemini-3-pro",
    "gemini-1.5-flash": "gemini-3-flash",
    # openai names -> sensible defaults so drop-in clients work
    "gpt-4": "gemini-3-pro",
    "gpt-4o": "gemini-3-pro",
    "gpt-4-turbo": "gemini-3-pro",
    "gpt-4o-mini": "gemini-3-flash",
    "gpt-3.5-turbo": "gemini-3-flash",
}

DEFAULT_MODEL = "gemini-3-flash"


def resolve_model(name: str | None) -> str:
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
    """Names advertised on /v1/models.

    The thinking tier is withheld where the installed library has none, so we
    never advertise a model that would silently be served as flash.
    """
    return [n for n in _CANON if _THINKING or "thinking" not in n]
