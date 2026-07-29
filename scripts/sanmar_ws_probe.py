"""Read-only probe of SanMar's Web Services (ws.sanmar.com, PromoStandards).

The FTP files this integration consumes have two known blind spots, both
confirmed against the live feed:

* no closeout signal -- PRODUCTSTATUS only ever carries Regular / Active /
  Discontinued, and the CLOSEOUT title-prefix idea matched 0 of 160,404 SKUs;
* inventory capped at 1500/warehouse -- sanmar_dip.txt reports at most 1500,
  and 13k+ SKUs sit exactly at the cap.

SanMar's PromoStandards web services carry both: Product Data 2.0.0 has an
``isCloseout`` boolean per product, and Inventory 2.0.0 returns true on-hand
quantities. This probe calls each once and prints what comes back, so wiring a
real sync starts from evidence instead of another 1h45m guess.

Auth: PromoStandards wants ``id`` (SanMar customer number) + ``password``.
SANMAR_WS_ID / SANMAR_WS_PASSWORD override; otherwise the SFTP credentials are
tried, since SanMar commonly provisions them together. Whatever happens is
reported (which combination was tried, never the secrets themselves).

Writes nothing, touches NetSuite not at all.
"""

from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

import requests

WS_BASE = os.environ.get("SANMAR_WS_BASE", "https://ws.sanmar.com:8080")
PRODUCT_ENDPOINT = f"{WS_BASE}/promostandards/ProductDataServiceV2.svc"
INVENTORY_ENDPOINT = f"{WS_BASE}/promostandards/InventoryServiceBindingV2final.svc"

# Styles to ask about. S500T showed as Discontinued with 0 stock in the last
# sample -- a plausible closeout candidate; 29M is a live evergreen control.
PROBE_STYLES = [s for s in os.environ.get(
    "SANMAR_WS_STYLES", "29M,S500T,PC61"
).split(",") if s.strip()]

PRODUCT_NS = "http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
PRODUCT_SHARED = f"{PRODUCT_NS}SharedObjects/"
INVENTORY_NS = "http://www.promostandards.org/WSDL/Inventory/2.0.0/"
INVENTORY_SHARED = f"{INVENTORY_NS}SharedObjects/"


def _creds() -> tuple[str, str, str, str]:
    """(customer number, username, password, which-source).

    SanMar web services authenticate with a THREE-part webServiceUser
    (customer number + username + password), issued separately from FTP --
    proven live 2026-07-29: a structurally perfect call with the SFTP pair
    answers "ERROR: User authentication failed." Set the three SANMAR_WS_*
    secrets once SanMar issues them; until then the SFTP pair is tried so
    the probe still demonstrates the full round trip.
    """
    custno = os.environ.get("SANMAR_WS_CUSTNO", "").strip()
    user = os.environ.get("SANMAR_WS_USERNAME", "").strip()
    pw = os.environ.get("SANMAR_WS_PASSWORD", "").strip()
    if custno and user and pw:
        return custno, user, pw, "SANMAR_WS_CUSTNO/USERNAME/PASSWORD secrets"
    sftp_user = os.environ.get("SANMAR_SFTP_USERNAME", "").strip()
    sftp_pw = os.environ.get("SANMAR_SFTP_PASSWORD", "").strip()
    return (custno or sftp_user, user or sftp_user, pw or sftp_pw,
            "SFTP fallback (known to fail WS auth -- set the SANMAR_WS_* secrets)")


def _call(endpoint: str, action: str, body: str) -> tuple[int, str]:
    resp = requests.post(
        endpoint,
        data=body.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action},
        timeout=60,
    )
    return resp.status_code, resp.text


def _tag(xml: str, name: str) -> list[str]:
    """Text of every <ns:name>...</ns:name> occurrence, namespace-agnostic."""
    return re.findall(
        rf"<(?:\w+:)?{name}(?:\s[^>]*)?>([^<]*)</(?:\w+:)?{name}>", xml
    )


def probe_product(pid: str, pw: str, style: str) -> None:
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns="{PRODUCT_NS}" xmlns:shar="{PRODUCT_SHARED}">
 <soapenv:Header/>
 <soapenv:Body>
  <ns:GetProductRequest>
   <shar:wsVersion>2.0.0</shar:wsVersion>
   <shar:id>{escape(pid)}</shar:id>
   <shar:password>{escape(pw)}</shar:password>
   <shar:localizationCountry>US</shar:localizationCountry>
   <shar:localizationLanguage>en</shar:localizationLanguage>
   <shar:productId>{escape(style)}</shar:productId>
  </ns:GetProductRequest>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(PRODUCT_ENDPOINT, "getProduct", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    codes = _tag(text, "code")
    descs = _tag(text, "description")
    if codes or "Fault" in text[:2000]:
        print(f"  {style}: HTTP {status}; service message: "
              f"{codes[:1]} {descs[:1] or text[:160]!r}")
        return
    name = _tag(text, "productName")
    closeout = _tag(text, "isCloseout")
    brand = _tag(text, "productBrand")
    print(f"  {style}: HTTP {status}  productName={name[:1]}  "
          f"brand={brand[:1]}  isCloseout={closeout[:1] or 'NOT PRESENT'}")


def probe_inventory(pid: str, pw: str, style: str) -> None:
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns="{INVENTORY_NS}" xmlns:shar="{INVENTORY_SHARED}">
 <soapenv:Header/>
 <soapenv:Body>
  <ns:GetInventoryLevelsRequest>
   <shar:wsVersion>2.0.0</shar:wsVersion>
   <shar:id>{escape(pid)}</shar:id>
   <shar:password>{escape(pw)}</shar:password>
   <shar:productId>{escape(style)}</shar:productId>
  </ns:GetInventoryLevelsRequest>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(INVENTORY_ENDPOINT, "getInventoryLevels", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    codes = _tag(text, "code")
    if codes or "Fault" in text[:2000]:
        descs = _tag(text, "description")
        print(f"  {style}: HTTP {status}; service message: "
              f"{codes[:1]} {descs[:1] or text[:160]!r}")
        return
    qtys = [int(q) for q in _tag(text, "value") if q.strip().isdigit()]
    over_cap = [q for q in qtys if q > 1500]
    print(f"  {style}: HTTP {status}  {len(qtys)} quantity value(s); "
          f"max={max(qtys) if qtys else '-'}; "
          f"{len(over_cap)} value(s) ABOVE the 1500 FTP cap"
          f"{' <-- UNCAPPED CONFIRMED' if over_cap else ''}")


# Candidate service paths for discovery. SanMar's docs have used several
# spellings across revisions and the first probe 404'd on both guesses, so ask
# the server which ones exist instead of guessing again: a GET of ?wsdl needs
# no auth and returns 200 + XML on a real endpoint.
DISCOVERY_PATHS = [
    "/promostandards/ProductDataServiceV2.svc",
    "/promostandards/ProductDataServiceV1.svc",
    "/promostandards/ProductDataService.svc",
    "/promostandards/InventoryServiceBindingV2final.svc",
    "/promostandards/InventoryServiceBindingV2.svc",
    "/promostandards/InventoryServiceBinding.svc",
    "/promostandards/PricingAndConfigurationServiceBinding.svc",
    "/promostandards/MediaContentServiceBinding.svc",
    "/SanMarWebService/SanMarProductInfoServicePort",
    "/SanMarWebService/SanMarStandardServicePort",
    "/SanMarWebService/SanMarPricingServicePort",
    "/SanMarWebService/SanMarInventoryServicePort",
]


def discover() -> None:
    print("=" * 70)
    print("0. Endpoint discovery -- GET <path>?wsdl, no auth needed")
    print("=" * 70)
    for path in DISCOVERY_PATHS:
        url = f"{WS_BASE}{path}?wsdl"
        try:
            resp = requests.get(url, timeout=30)
            body = resp.text[:120].replace("\n", " ")
            head = resp.text[:2000].lower()
            looks_wsdl = "wsdl" in head or "definitions" in head
            mark = "  <-- LIVE ENDPOINT" if resp.status_code == 200 and looks_wsdl else ""
            print(f"  {resp.status_code}  {path}{mark}")
            if resp.status_code == 200 and not looks_wsdl:
                print(f"       (200 but not WSDL: {body!r})")
        except requests.RequestException as exc:
            print(f"  ERR  {path}: {str(exc)[:100]}")
    print()


def introspect_wsdl(path: str) -> None:
    """Print a WSDL's operations and its COMPLETE type vocabulary.

    Chases imported XSDs (JAX-WS splits types into ?xsd=N siblings) and dumps
    every element and complexType name, so the real request/response shapes
    are read off the contract instead of guessed. Small services -- the full
    dump is a few lines.
    """
    url = f"{WS_BASE}{path}?wsdl"
    print(f"\n--- {path} ---")
    try:
        text = requests.get(url, timeout=30).text
    except requests.RequestException as exc:
        print(f"  WSDL fetch failed: {str(exc)[:120]}")
        return
    ops = sorted(set(re.findall(r'<(?:\w+:)?operation\s+name="([^"]+)"', text)))
    print(f"  operations: {ops}")
    # Chase every referenced schema (schemaLocation= and import location=).
    locs = set(re.findall(r'schemaLocation="([^"]+)"', text))
    locs.update(re.findall(r'<(?:\w+:)?import[^>]*location="([^"]+)"', text))
    schema_texts = [text]
    fetched = []
    for loc in sorted(locs)[:8]:
        loc_url = loc if loc.startswith("http") else f"{WS_BASE}{path}?{loc.split('?')[-1]}"
        try:
            schema_texts.append(requests.get(loc_url, timeout=30).text)
            fetched.append(loc_url.rsplit("?", 1)[-1])
        except requests.RequestException as exc:
            print(f"  (schema fetch failed {loc_url}: {str(exc)[:80]})")
    if fetched:
        print(f"  imported schemas fetched: {fetched}")
    elements: set[str] = set()
    ctypes: set[str] = set()
    for st in schema_texts:
        elements.update(re.findall(r'<(?:\w+:)?element[^>]*\sname="([^"]+)"', st))
        ctypes.update(re.findall(r'<(?:\w+:)?complexType[^>]*\sname="([^"]+)"', st))
    print(f"  elements   : {sorted(elements)}")
    print(f"  complexTypes: {sorted(ctypes)}")


def dump_types(path: str, names: list[str]) -> None:
    """Print the named complexType definitions verbatim from a service's XSD.

    The argument ORDER of SanMar's wrapper types (is arg0 the user or the
    item?) can only be read off the contract -- the last guess earned a
    server-side NullPointerException, which is what dispatch-then-null-user
    looks like. So print the actual definitions and stop guessing.
    """
    print(f"\n--- {path} ---")
    try:
        wsdl = requests.get(f"{WS_BASE}{path}?wsdl", timeout=30).text
        texts = [wsdl]
        for loc in sorted(set(re.findall(r'schemaLocation="([^"]+)"', wsdl)))[:4]:
            loc_url = loc if loc.startswith("http") else f"{WS_BASE}{path}?{loc.split('?')[-1]}"
            texts.append(requests.get(loc_url, timeout=30).text)
    except requests.RequestException as exc:
        print(f"  fetch failed: {str(exc)[:120]}")
        return
    blob = "\n".join(texts)
    for name in names:
        m = re.search(
            rf'<(?:\w+:)?complexType\s+name="{name}".*?</(?:\w+:)?complexType>',
            blob, re.S,
        )
        if m:
            compact = re.sub(r"\s+", " ", m.group(0))
            print(f"  {name}: {compact[:600]}")
        else:
            print(f"  {name}: (not found)")


_NS_CACHE: dict[str, str] = {}


def _service_ns(path: str) -> str:
    """The WSDL's own targetNamespace -- read it, don't assume it is shared."""
    if path not in _NS_CACHE:
        try:
            wsdl = requests.get(f"{WS_BASE}{path}?wsdl", timeout=30).text
            m = re.search(r'targetNamespace="([^"]+)"', wsdl)
            _NS_CACHE[path] = m.group(1) if m else ""
        except requests.RequestException:
            _NS_CACHE[path] = ""
    return _NS_CACHE[path]


def probe_std_inventory(
    custno: str, user: str, pw: str, style: str, *,
    color: str = "", size: str = ""
) -> None:
    """Call getInventory with the contract's order (item first, user second).

    The namespace is read from the WSDL itself. The server's answer --
    quantities, an auth message, or a fault -- is printed with credentials
    scrubbed; each outcome names the next step.
    """
    path = "/SanMarWebService/SanMarInventoryServicePort"
    ns = _service_ns(path) or "http://webservice.integration.sanmar.com/"
    auth = (f"<sanMarCustomerNumber>{escape(custno)}</sanMarCustomerNumber>"
            f"<sanMarUserName>{escape(user)}</sanMarUserName>"
            f"<sanMarUserPassword>{escape(pw)}</sanMarUserPassword>")
    item = f"<style>{escape(style)}</style>"
    if color:
        item += f"<color>{escape(color)}</color>"
    if size:
        item += f"<size>{escape(size)}</size>"
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:web="{ns}">
 <soapenv:Header/>
 <soapenv:Body>
  <web:getInventory>
   <arg0>{item}</arg0>
   <arg1>{auth}</arg1>
  </web:getInventory>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(f"{WS_BASE}{path}", "", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    for secret in (pw, custno, user):
        if secret:
            text = text.replace(secret, "***")
    err = _tag(text, "errorOccured") + _tag(text, "errorOccurred")
    msg = _tag(text, "message")
    qtys = [int(q) for q in _tag(text, "quantity")
            if q.strip().lstrip("-").isdigit()]
    whses = _tag(text, "warehouse")
    over = [q for q in qtys if q > 1500]
    spec = "/".join(x for x in (style, color, size) if x)
    print(f"  {spec}: HTTP {status}  error={err[:1]}  "
          f"message={msg[:1]}  {len(qtys)} qty value(s) across "
          f"{len(set(whses))} warehouse(s); max={max(qtys) if qtys else '-'}; "
          f"{len(over)} ABOVE the 1500 FTP cap"
          f"{'  <-- UNCAPPED CONFIRMED' if over else ''}")
    if not qtys:
        print(f"      full response: {text[:1200].strip()!r}")


def probe_std_product(custno: str, user: str, pw: str, style: str) -> None:
    """Call getProductInfoByStyleColorSize; print status + sale fields.

    The schema carries productStatus, pieceSalePrice and saleStartDate --
    if the FTP feed's PRODUCTSTATUS never says closeout but this one does,
    this becomes the closeout source.
    """
    path = "/SanMarWebService/SanMarProductInfoServicePort"
    ns = _service_ns(path) or "http://webservice.integration.sanmar.com/"
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:web="{ns}">
 <soapenv:Header/>
 <soapenv:Body>
  <web:getProductInfoByStyleColorSize>
   <arg0><style>{escape(style)}</style></arg0>
   <arg1>
    <sanMarCustomerNumber>{escape(custno)}</sanMarCustomerNumber>
    <sanMarUserName>{escape(user)}</sanMarUserName>
    <sanMarUserPassword>{escape(pw)}</sanMarUserPassword>
   </arg1>
  </web:getProductInfoByStyleColorSize>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(f"{WS_BASE}{path}", "", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    for secret in (pw, custno, user):
        if secret:
            text = text.replace(secret, "***")
    err = _tag(text, "errorOccured") + _tag(text, "errorOccurred")
    msg = _tag(text, "message")
    statuses = sorted(set(_tag(text, "productStatus")))
    sale = sorted(set(_tag(text, "pieceSalePrice")))[:3]
    print(f"  {style}: HTTP {status}  error={err[:1]}  message={msg[:1]}")
    print(f"      productStatus values: {statuses or '(none)'}  "
          f"pieceSalePrice sample: {sale or '(none)'}")
    if not statuses:
        print(f"      response head: {text[:400].strip()!r}")


def main() -> int:
    custno, user, pw, which = _creds()
    if not custno or not pw:
        print("no credentials available (need SANMAR_WS_ID/SANMAR_WS_PASSWORD "
              "or the SFTP pair) -- cannot probe")
        return 1
    print(f"ws base: {WS_BASE}")
    print(f"auth: trying {which}\n")

    print("=" * 70)
    print("1. Request/response types, verbatim from the contracts")
    print("=" * 70)
    dump_types("/SanMarWebService/SanMarInventoryServicePort",
               ["getInventory", "item", "webServiceUser", "responseBean"])
    dump_types("/SanMarWebService/SanMarProductInfoServicePort",
               ["getProductInfoByStyleColorSize", "productInfo",
                "productBasicInfo"])

    for p in ("/SanMarWebService/SanMarInventoryServicePort",
              "/SanMarWebService/SanMarProductInfoServicePort"):
        print(f"  targetNamespace {p}: {_service_ns(p)!r}")
    print()

    print("=" * 70)
    print("2. getInventory (item,user order per the contract)")
    print("=" * 70)
    # Style-only, and one fully-specified SKU -- if the generic 'Unexpected
    # Error' is about query shape rather than auth, these will differ.
    probe_std_inventory(custno, user, pw, "PC61")
    probe_std_inventory(custno, user, pw, "PC61", color="Black", size="L")

    print()
    print("=" * 70)
    print("3. getProductInfoByStyleColorSize -- what does productStatus say?")
    print("=" * 70)
    for style in PROBE_STYLES:
        probe_std_product(custno, user, pw, style.strip())

    print("\nIf the calls above failed auth, ask SanMar Integration Support "
          "for web-service credentials\n(customer number + username + "
          "password) -- sanmarintegrations@sanmar.com.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
