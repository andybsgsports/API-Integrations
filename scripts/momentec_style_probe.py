"""Probe Momentec's /v2/Style endpoint to discover the negotiated cart_price.

Our static-CSV feed only carries STANDARD wholesale Cost (~50% of MSRP; the ASG
feed spec calls MSRP "not the same as the Standard Price, Contract Price, or
Bracket Price"). Badger is on NEGOTIATED pricing, which Momentec exposes only
via the /v2/Style "credentials flow" as a ``cart_price`` field.

This read-only probe POSTs to /v2/Style for a sample style with credentials and
prints, for each product: MSRP, cart_price, and the cart_price/MSRP ratio (we
expect ~0.425 == "half less 15%"). It tries both plausible credential shapes
(nested ``credentials`` object vs. top-level) and reports which one works, so we
can wire the real integration against the exact schema.

Requires MOMENTEC_LOGON_ID / MOMENTEC_PASSWORD. Env: MOMENTEC_PROBE_STYLE
(default 790), MOMENTEC_PROBE_STYLES (comma list, overrides single).
"""

from __future__ import annotations

import json
import os

import requests

from momentec_netsuite.config import get_config


def _walk_find(obj, key: str):
    """Yield every value stored under ``key`` anywhere in a nested JSON blob."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            else:
                yield from _walk_find(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_find(item, key)


def _first_number(val):
    """MSRP/price may be a bare string, or [{'currency','value'}], or {'value'}."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            return None
    if isinstance(val, dict):
        return _first_number(val.get("value"))
    if isinstance(val, list) and val:
        return _first_number(val[0])
    return None


def call_style(cfg, style: str) -> tuple[str, dict | None]:
    url = f"{cfg.api_base_url.rstrip('/')}/v2/Style"
    shapes = {
        "nested-credentials": {
            "productOrDesignNumber": style,
            "credentials": {"logonId": cfg.logon_id, "password": cfg.password},
        },
        "top-level-credentials": {
            "productOrDesignNumber": style,
            "logonId": cfg.logon_id,
            "password": cfg.password,
        },
    }
    for name, body in shapes.items():
        try:
            r = requests.post(url, json=body, timeout=60,
                              headers={"Content-Type": "application/json"})
        except Exception as exc:  # noqa: BLE001 - probe, surface any error
            print(f"  [{name}] POST failed: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        preview = r.text[:200].replace("\n", " ")
        print(f"  [{name}] HTTP {r.status_code} ({len(r.text)} bytes): {preview}")
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                print("    (200 but body was not JSON)")
                continue
            has_cart = any(True for _ in _walk_find(data, "cart_price"))
            print(f"    -> parsed JSON; cart_price present: {has_cart}")
            return name, data
    return "", None


def main() -> int:
    cfg = get_config()
    if not cfg.logon_id or not cfg.password:
        print("MISSING MOMENTEC_LOGON_ID / MOMENTEC_PASSWORD -- add them as repo "
              "secrets so the credentials flow can authenticate. Cannot probe "
              "cart_price without them.")
        return 1
    styles_env = os.environ.get("MOMENTEC_PROBE_STYLES", "").strip()
    styles = [s.strip() for s in styles_env.split(",") if s.strip()] or \
        [os.environ.get("MOMENTEC_PROBE_STYLE", "790")]

    print(f"api_base_url = {cfg.api_base_url}")
    for style in styles:
        print(f"\n=== /v2/Style productOrDesignNumber={style} ===")
        shape, data = call_style(cfg, style)
        if not data:
            print("  no usable response")
            continue
        print(f"  working credential shape: {shape}")
        prods = list(_walk_find(data, "productInfo"))
        products = prods[0] if prods and isinstance(prods[0], list) else \
            (data.get("productInfo") if isinstance(data, dict) else []) or []
        shown = 0
        for p in products:
            sku = p.get("productOrDesignNumber") or p.get("SKU")
            msrp = _first_number(p.get("MSRP"))
            cart = _first_number(next(iter(_walk_find(p, "cart_price")), None))
            ratio = f"{cart / msrp:.3f}" if (cart and msrp) else "n/a"
            print(f"    {sku!s:<16} MSRP={msrp} cart_price={cart} ratio={ratio}")
            shown += 1
            if shown >= 8:
                break
        if not products:
            print("  ---- raw response (first 1500 chars) ----")
            print(json.dumps(data, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
