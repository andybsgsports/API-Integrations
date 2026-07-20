"""Read-only probe: does DC OneSource expose the PromoStandards Media Content
service (product images) for our suppliers, and how are the images keyed?

We currently pull only Product + Inventory from DC OneSource, so Champro (and
every other DCOS supplier) has no images. PromoStandards has a separate Media
Content service (``getMediaContent``) that returns image URLs -- if DCOS has it
enabled, we can feed those into the item image backfill.

This probe calls getMediaContent for a few sample sellable styles per supplier,
trying the likely endpoint path + wsVersion variants, and prints the raw
response plus a parsed summary (URLs and the fields they hang off of, so we can
see whether images key by productId, partId, or color). No writes anywhere.

Env: DCOS_KEY_ID / DCOS_KEY_PASSWORD (same as the other DCOS workflows).
Optional: DCOS_MEDIA_SUPPLIERS (comma list; default all), DCOS_MEDIA_STYLES
(comma list to probe instead of auto-picking), DCOS_MEDIA_SAMPLE (styles per
supplier to auto-pick, default 2).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from dcos_backfill import SUPPLIERS, _soap, _strip, get_sellable_styles

MEDIA_NS_TMPL = "http://www.promostandards.org/WSDL/MediaService/{v}/"

# (endpoint path segment, wsVersion) combos to try, most-likely first.
ENDPOINT_VARIANTS = [
    ("MediaContent", "1.0.0"),
    ("Media", "1.0.0"),
    ("MediaContent", "1.1.0"),
    ("Media", "1.1.0"),
]


def media_request(key_id: str, key_pw: str, version: str, product_id: str,
                  media_type: str = "Image") -> str:
    ns = MEDIA_NS_TMPL.format(v=version)
    mt = f"<shar:mediaType>{media_type}</shar:mediaType>" if media_type else ""
    return (
        f'<ns:GetMediaContentRequest xmlns:ns="{ns}" xmlns:shar="{ns}SharedObjects/">'
        f"<shar:wsVersion>{version}</shar:wsVersion>"
        f"<shar:id>{key_id}</shar:id><shar:password>{key_pw}</shar:password>"
        f"{mt}"
        f"<shar:productId>{product_id}</shar:productId>"
        "</ns:GetMediaContentRequest>"
    )


def is_fault(text: str) -> bool:
    low = text.lower()
    return "soap:fault" in low or "<fault" in low or "faultstring" in low


def summarize(text: str) -> tuple[int, list[dict]]:
    """Return (url count, sample of media entries with url + nearby context)."""
    root = ET.fromstring(text)
    entries: list[dict] = []
    for el in root.iter():
        if _strip(el.tag) != "MediaContent":
            continue
        entry: dict = {}
        for sub in el.iter():
            t, v = _strip(sub.tag), (sub.text or "").strip()
            if not v:
                continue
            if t in ("url", "mediaType", "productId", "partId", "color",
                     "colorName", "classTypeName", "classTypeId"):
                entry.setdefault(t, v)
        if entry:
            entries.append(entry)
    # fallback: any <url> if MediaContent nodes weren't found
    if not entries:
        for el in root.iter():
            if _strip(el.tag) == "url" and (el.text or "").strip():
                entries.append({"url": el.text.strip()})
    return len(entries), entries


def probe_style(base: str, key_id: str, key_pw: str, style: str) -> bool:
    dumped = False
    for seg, version in ENDPOINT_VARIANTS:
        url = f"{base}/{seg}/{version}/soap"
        endpoint_ok = False
        for mtype in ("Image", ""):  # "" = omit the mediaType filter entirely
            tag = f"{seg} {version} mt={mtype or 'ALL'}"
            try:
                text = _soap(url, "getMediaContent",
                             media_request(key_id, key_pw, version, style, mtype))
            except Exception as exc:  # noqa: BLE001
                print(f"    [{tag}] HTTP error: {str(exc)[:120]}")
                continue
            if is_fault(text):
                snippet = " ".join(text.split())
                idx = snippet.lower().find("faultstring")
                shown = snippet[idx:idx + 160] if idx >= 0 else snippet[:160]
                print(f"    [{tag}] fault: {shown}")
                continue
            endpoint_ok = True
            try:
                count, entries = summarize(text)
            except ET.ParseError as exc:
                print(f"    [{tag}] unparseable: {str(exc)[:100]}")
                continue
            print(f"    [{tag}] OK -- {count} media entr(y/ies)")
            for e in entries[:6]:
                print(f"        {e}")
            # Dump the raw response once per style so a 0-count result can be
            # told apart from a response our parser didn't recognize.
            if not dumped:
                print("    ---- raw (first 2500 chars) ----")
                print("    " + " ".join(text.split())[:2500])
                dumped = True
            if count:
                return True
        if endpoint_ok:
            # This endpoint answered; no need to try the other path variants.
            return dumped
    return dumped


def main() -> int:
    key_id = os.environ.get("DCOS_KEY_ID", "")
    key_pw = os.environ.get("DCOS_KEY_PASSWORD", "")
    if not key_id or not key_pw:
        print("DCOS_KEY_ID / DCOS_KEY_PASSWORD not set")
        return 1

    def _csv(name: str) -> list[str]:
        return [s.strip() for s in (os.environ.get(name) or "").split(",") if s.strip()]

    suppliers = _csv("DCOS_MEDIA_SUPPLIERS") or list(SUPPLIERS)
    forced_styles = _csv("DCOS_MEDIA_STYLES")
    sample = int(os.environ.get("DCOS_MEDIA_SAMPLE", "5") or "5")

    any_ok = False
    for key in suppliers:
        if key not in SUPPLIERS:
            print(f"unknown supplier {key!r}; skipping")
            continue
        sup = SUPPLIERS[key]
        base = f"https://api.dc-onesource.com/xml/{sup['slug']}"
        print(f"\n=== {sup['label']} ({key}) — {base} ===")
        styles = forced_styles
        if not styles:
            try:
                styles = get_sellable_styles(base, key_id, key_pw)[:sample]
            except Exception as exc:  # noqa: BLE001
                print(f"  could not list sellable styles: {str(exc)[:120]}")
                continue
        if not styles:
            print("  no styles to probe")
            continue
        for style in styles:
            print(f"  style {style}:")
            if probe_style(base, key_id, key_pw, style):
                any_ok = True

    verdict = "at least one endpoint responded" if any_ok else "no working media endpoint found"
    print(f"\nmedia probe: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
