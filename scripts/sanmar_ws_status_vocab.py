"""What productStatus values does SanMar's web service ACTUALLY publish?

The whole point of reaching for web services was a closeout signal the FTP
feed lacks. Three sample styles returned only 'Active' and 'Discontinued' --
the same vocabulary the feed already carries -- so "the web service has
closeout" is unproven, not established. This settles it by sampling widely
enough to see the real vocabulary.

Samples styles from the live SanMar feed (so they are real, current styles),
prioritising ones the FTP feed already calls Discontinued -- the likeliest
place a distinct closeout value would show up -- plus a spread of ordinary
ones as a control. Calls getProductInfoByStyleColorSize per style with
bounded concurrency and tallies every distinct productStatus seen.

Read-only: SanMar only, no NetSuite interaction at all.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.sax.saxutils import escape

import requests
from sanmar_field_update import _dl

from sanmar_netsuite.config import get_config
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles

WS_BASE = os.environ.get("SANMAR_WS_BASE", "https://ws.sanmar.com:8080")
PRODUCT_PATH = "/SanMarWebService/SanMarProductInfoServicePort"
PRODUCT_NS = "http://impl.webservice.integration.sanmar.com/"
SAMPLE_SIZE = int(os.environ.get("VOCAB_SAMPLE", "60") or "60")
WORKERS = int(os.environ.get("VOCAB_WORKERS", "4") or "4")


def _tag(xml: str, name: str) -> list[str]:
    return re.findall(
        rf"<(?:\w+:)?{name}(?:\s[^>]*)?>([^<]*)</(?:\w+:)?{name}>", xml
    )


def _creds() -> tuple[str, str, str]:
    return (
        os.environ.get("SANMAR_WS_CUSTNO", "").strip(),
        os.environ.get("SANMAR_WS_USERNAME", "").strip(),
        os.environ.get("SANMAR_WS_PASSWORD", "").strip(),
    )


def fetch_statuses(
    custno: str, user: str, pw: str, style: str
) -> tuple[str, list[str], str]:
    """(style, productStatus values, error message) for one style."""
    body = (
        f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        f' xmlns:web="{PRODUCT_NS}"><soapenv:Header/><soapenv:Body>'
        f"<web:getProductInfoByStyleColorSize>"
        f"<arg0><style>{escape(style)}</style></arg0>"
        f"<arg1><sanMarCustomerNumber>{escape(custno)}</sanMarCustomerNumber>"
        f"<sanMarUserName>{escape(user)}</sanMarUserName>"
        f"<sanMarUserPassword>{escape(pw)}</sanMarUserPassword></arg1>"
        f"</web:getProductInfoByStyleColorSize></soapenv:Body></soapenv:Envelope>"
    )
    try:
        resp = requests.post(
            f"{WS_BASE}{PRODUCT_PATH}",
            data=body.encode(),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=60,
        )
        text = resp.text
    except requests.RequestException as exc:
        return style, [], str(exc)[:80]
    err = _tag(text, "errorOccured") + _tag(text, "errorOccurred")
    if err[:1] == ["true"]:
        return style, [], (_tag(text, "message") or ["?"])[0][:80]
    return style, _tag(text, "productStatus"), ""


def main() -> int:
    custno, user, pw = _creds()
    if not (custno and user and pw):
        print("SANMAR_WS_CUSTNO / SANMAR_WS_USERNAME / SANMAR_WS_PASSWORD "
              "must all be set")
        return 1

    cfg = get_config()
    styles = parse_styles(_dl(cfg, C.FILE_SDL_N))
    # The FTP feed's own status is the best prior for where a distinct
    # closeout value would appear, so lead with those it calls Discontinued.
    by_status: dict[str, list[str]] = {}
    for s in styles:
        status = str(getattr(s, "product_status", "") or "").strip()
        by_status.setdefault(status or "(blank)", []).append(s.style)
    print("FTP feed status distribution (styles):")
    for status, group in sorted(by_status.items(), key=lambda kv: -len(kv[1])):
        print(f"  {status:<16} {len(group):>5,} style(s)")

    discontinued = by_status.get("Discontinued", [])
    others = [s for k, g in by_status.items() if k != "Discontinued" for s in g]
    half = max(1, SAMPLE_SIZE // 2)
    # Dedupe while keeping order: discontinued first, then a spread of others.
    picked: list[str] = []
    for candidate in discontinued[:half] + others[::max(1, len(others) // half)]:
        if candidate not in picked:
            picked.append(candidate)
        if len(picked) >= SAMPLE_SIZE:
            break
    print(f"\nprobing {len(picked)} style(s) "
          f"({min(len(discontinued), half)} feed-Discontinued + control spread)\n")

    vocab: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    per_style: list[tuple[str, list[str]]] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fetch_statuses, custno, user, pw, s) for s in picked]
        for fut in as_completed(futs):
            style, statuses, err = fut.result()
            if err:
                errors[err] += 1
                continue
            vocab.update(statuses)
            per_style.append((style, sorted(set(statuses))))

    print("=" * 70)
    print("DISTINCT productStatus VALUES ACROSS THE SAMPLE")
    print("=" * 70)
    for value, n in vocab.most_common():
        print(f"  {value!r:<20} {n:>7,} SKU(s)")
    if not vocab:
        print("  (none returned)")
    if errors:
        print("\nerrors:")
        for msg, n in errors.most_common(5):
            print(f"  {msg}: {n}")

    closeoutish = [v for v in vocab if re.search(r"close|clear|discont", v, re.I)]
    print(f"\nvalues implying closeout/clearance: {closeoutish or 'NONE'}")
    mixed = [(s, v) for s, v in per_style if len(v) > 1]
    print(f"styles whose SKUs carry MIXED statuses: {len(mixed)} "
          f"(e.g. {mixed[:3]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
