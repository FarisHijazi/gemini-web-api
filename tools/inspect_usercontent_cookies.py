"""Dump every cookie whose domain mentions 'usercontent' or is an OSID/auth
cookie — to see whether the per-host download auth cookie is in the store and
whether get_full_jar's `.google.com + path=/` filter drops it."""

import browser_cookie3

import gemini_openai.config as config

AUTH_NAMES = {"OSID", "__Secure-OSID", "SID", "SAPISID", "__Secure-1PSID",
              "HSID", "SSID", "APISID", "__Secure-3PSID"}

for store in config._cookie_stores():
    print(f"\n=== store: {store} ===")
    try:
        cj = browser_cookie3.chrome(cookie_file=store, domain_name="google.com")
    except Exception as e:  # noqa: BLE001
        print("  err:", e)
        continue
    seen_domains = {}
    for c in cj:
        seen_domains.setdefault(c.domain, set()).add(c.name)
    # Show any domain containing 'usercontent'
    for dom, names in sorted(seen_domains.items()):
        if "usercontent" in dom:
            print(f"  usercontent domain {dom!r}: {sorted(names)}")
    # Show where OSID lives
    for c in cj:
        if c.name in ("OSID", "__Secure-OSID"):
            print(f"  {c.name}: domain={c.domain!r} path={c.path!r}")
    break  # newest store only
