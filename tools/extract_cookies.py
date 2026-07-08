"""Extract Gemini auth cookies from local Chrome profiles.

Gemini web auth needs two HttpOnly cookies on .google.com:
  - __Secure-1PSID
  - __Secure-1PSIDTS   (rotates; gemini_webapi auto-refreshes it after init)

We scan every Chrome profile's cookie store and return the first profile that
has both. Chrome may be running; browser_cookie3 copies the DB to avoid locks.
"""

from __future__ import annotations

import glob
import json
import os
import sys

import browser_cookie3

CHROME_DIR = os.path.expanduser("~/.config/google-chrome")
WANTED = ("__Secure-1PSID", "__Secure-1PSIDTS")


def cookie_stores() -> list[str]:
    """All candidate cookie DB paths, newest first (most-recently-used profile)."""
    paths = []
    for prof in glob.glob(os.path.join(CHROME_DIR, "*")):
        for name in ("Network/Cookies", "Cookies"):
            p = os.path.join(prof, name)
            if os.path.isfile(p):
                paths.append(p)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


def extract() -> dict | None:
    for store in cookie_stores():
        try:
            cj = browser_cookie3.chrome(cookie_file=store, domain_name=".google.com")
        except Exception as e:  # noqa: BLE001
            print(f"# skip {store}: {e}", file=sys.stderr)
            continue
        found = {c.name: c.value for c in cj if c.name in WANTED}
        if all(k in found for k in WANTED):
            found["__profile__"] = store
            return found
        if "__Secure-1PSID" in found:
            # partial — still record which profile, may be usable
            print(f"# partial in {store}: {list(found)}", file=sys.stderr)
    return None


if __name__ == "__main__":
    result = extract()
    if not result:
        print(json.dumps({"ok": False}))
        sys.exit(1)
    # print lengths only unless --values given (avoid leaking secrets to logs)
    if "--values" in sys.argv:
        print(json.dumps({"ok": True, **result}))
    else:
        safe = {k: (f"<len:{len(v)}>" if k.startswith("__Secure") else v) for k, v in result.items()}
        print(json.dumps({"ok": True, **safe}))
