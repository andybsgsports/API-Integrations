"""Create custom item fields via SuiteTalk SOAP (REST has no such endpoint).

SOAP's ``add`` operation supports customization records (``ItemCustomField``),
authenticated with the same TBA consumer/token credentials the REST client
uses. Tries one probe field first and dumps the full SOAP fault if the schema
needs adjusting; on success, creates every missing field from the shared list.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from xml.sax.saxutils import escape

import requests
from ns_field_setup import FIELDS  # shared field list (scripts/ dir on sys.path)

from sanmar_netsuite.config import get_config

VERSION = "2023_2"
TYPE_MAP = {
    "TEXT": "_freeFormText",
    "CURRENCY": "_currency",
    "INTEGER": "_integerNumber",
    "TEXTAREA": "_textArea",
    "FLOAT": "_decimalNumber",
    "CHECKBOX": "_checkBox",
    "URL": "_hyperlink",
}


def _passport(cfg) -> str:
    account = cfg.account_id.replace("-", "_").upper()
    nonce = secrets.token_hex(16)
    ts = str(int(time.time()))
    base = "&".join([account, cfg.consumer_key, cfg.token_id, nonce, ts])
    key = f"{cfg.consumer_secret}&{cfg.token_secret}".encode()
    sig = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha256).digest()).decode()
    return f"""
    <platformMsgs:tokenPassport>
      <platformCore:account>{account}</platformCore:account>
      <platformCore:consumerKey>{cfg.consumer_key}</platformCore:consumerKey>
      <platformCore:token>{cfg.token_id}</platformCore:token>
      <platformCore:nonce>{nonce}</platformCore:nonce>
      <platformCore:timestamp>{ts}</platformCore:timestamp>
      <platformCore:signature algorithm="HMAC-SHA256">{sig}</platformCore:signature>
    </platformMsgs:tokenPassport>"""


def add_field(cfg, scriptid: str, label: str, ftype: str) -> tuple[bool, str]:
    url_account = cfg.account_id.replace("_", "-").lower()
    url = f"https://{url_account}.suitetalk.api.netsuite.com/services/NetSuitePort_{VERSION}"
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:platformMsgs="urn:messages_{VERSION}.platform.webservices.netsuite.com"
    xmlns:platformCore="urn:core_{VERSION}.platform.webservices.netsuite.com"
    xmlns:setupCustom="urn:customization_{VERSION}.setup.webservices.netsuite.com">
  <soapenv:Header>{_passport(cfg)}
  </soapenv:Header>
  <soapenv:Body>
    <platformMsgs:add>
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" scriptId="{scriptid}">
        <setupCustom:label>{escape(label)}</setupCustom:label>
        <setupCustom:storeValue>true</setupCustom:storeValue>
        <setupCustom:appliesToInventory>true</setupCustom:appliesToInventory>
        <setupCustom:fieldType>{TYPE_MAP[ftype]}</setupCustom:fieldType>
      </platformMsgs:record>
    </platformMsgs:add>
  </soapenv:Body>
</soapenv:Envelope>"""
    resp = requests.post(
        url,
        data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "add"},
        timeout=60,
    )
    text = resp.text
    ok = "<platformCore:isSuccess>true</platformCore:isSuccess>" in text or (
        "isSuccess" in text and ">true<" in text and resp.status_code == 200
    )
    return ok, text


def main() -> int:
    cfg = get_config().netsuite
    todo = [(sid, label, ftype) for sid, label, ftype, _ in FIELDS]

    # Probe with the first field; print the raw fault if the schema is off.
    sid, label, ftype = todo[0]
    ok, text = add_field(cfg, sid, label, ftype)
    print(f"probe {sid}: {'OK' if ok else 'FAILED'}")
    if not ok:
        print("---- raw SOAP response (first 3000 chars) ----")
        print(text[:3000])
        return 1

    results = {sid: "created"}
    for sid, label, ftype in todo[1:]:
        ok, text = add_field(cfg, sid, label, ftype)
        if ok:
            results[sid] = "created"
        elif "UNIQUE_CUST_ID_REQD" in text or "already" in text.lower():
            results[sid] = "exists"
        else:
            snippet = text[text.find("message") : text.find("message") + 160]
            results[sid] = f"failed: {snippet}"
        print(f"  {results[sid][:9]:<9} {sid}")

    created = sum(1 for v in results.values() if v == "created")
    failed = {k: v for k, v in results.items() if v.startswith("failed")}
    print(f"\nsummary: created={created} exists={sum(1 for v in results.values() if v=='exists')} "
          f"failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
