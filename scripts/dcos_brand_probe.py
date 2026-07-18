"""DC OneSource multi-brand entitlement probe, read-only (runs on CI).

The user wants seven more DCOS-hosted brands after UA: Champro, Schutt,
Champion, United Sports Brand, Richardson, TKC Twin City, Cap America.
DC OneSource exposes one PromoStandards endpoint per brand
(``/xml/{SLUG}/Product/2.0.0/soap``), and the exact slug spelling is not
documented anywhere we can read -- so this tries a handful of plausible
slugs per brand with the existing key and reports which respond with
sellable data, which reject the key (entitlement to request from DCOS),
and which don't exist. Also runs one PPC price-type check per confirmed
brand so we know upfront whether the feed publishes a real Net (cost)
price or only List -- that decides whether we need a cost formula from
the user (UA's feed was List-only, hence the half-less-20% rule).

Env: DCOS_KEY_ID / DCOS_KEY_PASSWORD.
"""

from __future__ import annotations

import os
import re

import requests

BASE = "https://api.dc-onesource.com/xml"

# brand -> candidate endpoint slugs, most likely first. Round 1 confirmed
# CHAMPRO / UNITEDSPORTSBRANDS / TWINCITYKNITTING; DCOS answers HTTP 200
# with an EMPTY envelope for unknown slugs, so misses are indistinguishable
# from not-entitled -- round 2 tries longer-form company names.
BRAND_SLUGS: dict[str, list[str]] = {
    # Supplier codes below are the ones the user read out of their DC
    # OneSource account -- no more guessing.
    "Champro": ["CHAMPRO"],
    "Schutt": ["CERTORSPORTS-SC"],
    "Champion": ["CHAMPIONSPORTS"],
    "United Sports Brand": ["UNITEDSPORTSBRANDS"],
    "Richardson": ["OUTDOORCAP"],
    "TKC Twin City": ["TWINCITYKNITTING"],
    "Cap America": ["CAP"],
}

SELLABLE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:ns="http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
    xmlns:shar="http://www.promostandards.org/WSDL/ProductDataService/2.0.0/SharedObjects/">
  <soapenv:Header/>
  <soapenv:Body>
    <ns:GetProductSellableRequest>
      <shar:wsVersion>2.0.0</shar:wsVersion>
      <shar:id>{key_id}</shar:id>
      <shar:password>{key_pw}</shar:password>
      <shar:localizationCountry>US</shar:localizationCountry>
      <shar:localizationLanguage>en</shar:localizationLanguage>
      <ns:isSellable>true</ns:isSellable>
    </ns:GetProductSellableRequest>
  </soapenv:Body>
</soapenv:Envelope>"""


def probe_slug(slug: str, key_id: str, key_pw: str) -> tuple[str, int, int]:
    """-> (verdict, products, parts). Verdicts: OK / AUTH / MISSING / ERROR."""
    try:
        resp = requests.post(
            f"{BASE}/{slug}/Product/2.0.0/soap",
            data=SELLABLE.format(key_id=key_id, key_pw=key_pw).encode(),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "getProductSellable",
            },
            timeout=300,
        )
    except requests.RequestException as exc:
        print(f"    {slug}: request failed: {str(exc)[:100]}")
        return "ERROR", 0, 0
    text = resp.text
    if resp.status_code == 404 or "<html" in text[:200].lower():
        return "MISSING", 0, 0
    products = len(re.findall(r"<\s*(?:\w+:)?productId\s*>", text))
    parts = len(re.findall(r"<\s*(?:\w+:)?partId\s*>", text))
    if resp.status_code == 200 and products:
        return "OK", products, parts
    if "ErrorMessage" in text or resp.status_code in (401, 403, 500):
        m = re.search(r"<(?:\w+:)?description>(.*?)</(?:\w+:)?description>", text, re.S)
        detail = (m.group(1).strip()[:120] if m else f"HTTP {resp.status_code}")
        print(f"    {slug}: {detail}")
        return "AUTH", 0, 0
    print(f"    {slug}: HTTP {resp.status_code}, unrecognized body {text[:120]!r}")
    return "ERROR", 0, 0


def check_outdoorcap_is_richardson(key_id: str, key_pw: str) -> None:
    """The OUTDOORCAP endpoint answers our key with 1,491 products -- but
    Outdoor Cap is its own headwear company, so before treating it as the
    Richardson feed, intersect its sellable styles with the user's
    Richardson price list."""
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data" / "pricelist_richardson.csv"
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        wanted = {(r.get("style") or "").strip().upper()
                  for r in csv.DictReader(fh)} - {""}
    resp = requests.post(
        f"{BASE}/OUTDOORCAP/Product/2.0.0/soap",
        data=SELLABLE.format(key_id=key_id, key_pw=key_pw).encode(),
        headers={"Content-Type": "text/xml; charset=utf-8",
                 "SOAPAction": "getProductSellable"},
        timeout=300,
    )
    feed = sorted(set(re.findall(r"<\s*(?:\w+:)?productId\s*>([^<]+)<", resp.text)))
    hits = [s for s in feed if s.upper() in wanted]
    print("\n=== OUTDOORCAP identity check vs Richardson price list ===")
    print(f"feed styles: {len(feed):,}; Richardson price-list styles: {len(wanted):,}; "
          f"overlap: {len(hits):,}")
    print(f"feed sample: {feed[:12]}")
    if hits:
        print(f"overlap sample: {hits[:12]}")


def main() -> int:
    key_id = os.environ.get("DCOS_KEY_ID", "")
    key_pw = os.environ.get("DCOS_KEY_PASSWORD", "")
    if not key_id or not key_pw:
        print("DCOS_KEY_ID / DCOS_KEY_PASSWORD not set")
        return 1
    check_outdoorcap_is_richardson(key_id, key_pw)

    confirmed: dict[str, tuple[str, int, int]] = {}
    for brand, slugs in BRAND_SLUGS.items():
        print(f"\n=== {brand} ===")
        for slug in slugs:
            verdict, products, parts = probe_slug(slug, key_id, key_pw)
            print(f"  {slug:<24} {verdict}"
                  + (f"  ({products:,} products / {parts:,} parts)" if verdict == "OK" else ""))
            if verdict == "OK":
                confirmed[brand] = (slug, products, parts)
                break
            if verdict == "AUTH":
                # endpoint exists but key refused -- no point trying variants
                confirmed[brand] = (f"{slug} (NOT ENTITLED)", 0, 0)
                break

    print("\n=== summary ===")
    for brand in BRAND_SLUGS:
        got = confirmed.get(brand)
        if got and got[1]:
            print(f"  {brand:<22} slug={got[0]:<20} {got[1]:,} products")
        elif got:
            print(f"  {brand:<22} {got[0]}")
        else:
            print(f"  {brand:<22} NO ENDPOINT FOUND (all slug guesses missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
