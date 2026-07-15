"""Shared SuiteTalk SOAP helpers: TBA passport + a thin ``add`` client.

The REST record API doesn't expose every record type (custom field
definitions, File Cabinet folders, and File records all 404), but SOAP's
generic ``add``/``delete`` operations reach them with the same token-based
auth the REST client already uses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import requests

VERSION = "2023_2"


def passport(cfg) -> str:
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


def soap_url(cfg) -> str:
    account = cfg.account_id.replace("_", "-").lower()
    return f"https://{account}.suitetalk.api.netsuite.com/services/NetSuitePort_{VERSION}"


def post(cfg, action: str, body_xml: str, extra_ns: str = "") -> str:
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:platformMsgs="urn:messages_{VERSION}.platform.webservices.netsuite.com"
    xmlns:platformCore="urn:core_{VERSION}.platform.webservices.netsuite.com"
    {extra_ns}>
  <soapenv:Header>{passport(cfg)}
  </soapenv:Header>
  <soapenv:Body>{body_xml}</soapenv:Body>
</soapenv:Envelope>"""
    resp = requests.post(
        soap_url(cfg),
        data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action},
        timeout=120,
    )
    return resp.text


def is_success(text: str) -> bool:
    return 'isSuccess="true"' in text
