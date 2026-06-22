"""Low-level NetSuite SuiteTalk REST transport.

Handles Token-Based Authentication (OAuth 1.0a, HMAC-SHA256) signing and the
two REST surfaces we use:

* **record API** — ``/services/rest/record/v1/...`` for CRUD on item records.
* **SuiteQL** — ``/services/rest/query/v1/suiteql`` for lookups by custom field.

Network calls retry with exponential backoff on transient errors.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests
from requests_oauthlib import OAuth1
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import NetSuiteConfig

log = logging.getLogger(__name__)


def _guard_production(config: NetSuiteConfig) -> None:
    """Refuse to talk to a production NetSuite account unless explicitly allowed.

    Sandbox-first safety: constructing a client against a non-sandbox realm
    raises unless ``NETSUITE_ALLOW_PRODUCTION_WRITES=true`` is set. This makes it
    impossible to accidentally point a run at production during testing.
    """
    if config.is_sandbox or config.allow_production_writes:
        return
    raise RuntimeError(
        f"Refusing to connect to a non-sandbox NetSuite account "
        f"(NETSUITE_ACCOUNT_ID={config.account_id!r}). This integration is "
        "locked to sandbox during testing. Use a sandbox realm (e.g. "
        "'1234567_SB1'), or set NETSUITE_ALLOW_PRODUCTION_WRITES=true to "
        "deliberately enable production access."
    )


def _internal_id_from_location(location: str) -> str:
    """Extract the trailing internal id from a REST ``Location`` header URL."""
    if not location:
        return ""
    return location.rstrip("/").rsplit("/", 1)[-1]


class NetSuiteError(RuntimeError):
    """Raised when NetSuite returns a non-success response."""

    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__(f"NetSuite {status}: {message}")
        self.status = status
        self.payload = payload


class _RetryableHTTP(RuntimeError):
    """Internal marker for 429/5xx responses worth retrying."""


class NetSuiteClient:
    RECORD_PATH = "/services/rest/record/v1"
    QUERY_PATH = "/services/rest/query/v1"
    RESTLET_PATH = "/app/site/hosting/restlet.nl"

    def __init__(self, config: NetSuiteConfig, *, timeout: int = 60) -> None:
        if not config.rest_base:
            raise RuntimeError(
                "NETSUITE_ACCOUNT_ID (or NETSUITE_REST_BASE) is not configured."
            )
        _guard_production(config)
        self._config = config
        self._timeout = timeout
        self._session = requests.Session()
        # NetSuite TBA: realm is the account id (uppercase), HMAC-SHA256.
        self._auth = OAuth1(
            client_key=config.consumer_key,
            client_secret=config.consumer_secret,
            resource_owner_key=config.token_id,
            resource_owner_secret=config.token_secret,
            realm=config.account_id.upper(),
            signature_method="HMAC-SHA256",
        )

    # ── core request with retry/backoff ──────────────────────────────────────
    @retry(
        retry=retry_if_exception_type(
            (_RetryableHTTP, requests.ConnectionError, requests.Timeout)
        ),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        merged_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            merged_headers.update(headers)
        resp = self._session.request(
            method,
            url,
            auth=self._auth,
            json=json_body,
            headers=merged_headers,
            timeout=self._timeout,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("Retryable NetSuite response %s for %s %s", resp.status_code, method, url)
            raise _RetryableHTTP(f"{resp.status_code} {resp.reason}")
        return resp

    def _url(self, path: str) -> str:
        return f"{self._config.rest_base}{path}"

    @staticmethod
    def _json_or_error(resp: requests.Response) -> dict[str, Any]:
        if resp.status_code in (200, 201):
            return resp.json() if resp.content else {}
        if resp.status_code == 204:
            return {}
        try:
            detail = resp.json()
            message = detail.get("title") or detail.get("o:errorDetails") or resp.text
        except ValueError:
            detail = resp.text
            message = resp.text
        raise NetSuiteError(resp.status_code, str(message), detail)

    # ── record API ───────────────────────────────────────────────────────────
    def get_record(self, record_type: str, internal_id: str) -> dict[str, Any]:
        url = self._url(f"{self.RECORD_PATH}/{record_type}/{internal_id}")
        return self._json_or_error(self._request("GET", url))

    def create_record(self, record_type: str, body: dict[str, Any]) -> str:
        """Create a record; return the new internal id (from the Location header)."""
        url = self._url(f"{self.RECORD_PATH}/{record_type}")
        resp = self._request("POST", url, json_body=body)
        if resp.status_code not in (200, 201, 204):
            self._json_or_error(resp)  # raises
        return _internal_id_from_location(resp.headers.get("Location", ""))

    def update_record(
        self, record_type: str, internal_id: str, body: dict[str, Any], *, replace: bool = False
    ) -> None:
        """PATCH (default) or PUT a record by internal id."""
        method = "PUT" if replace else "PATCH"
        url = self._url(f"{self.RECORD_PATH}/{record_type}/{internal_id}")
        resp = self._request(method, url, json_body=body)
        if resp.status_code not in (200, 204):
            self._json_or_error(resp)  # raises

    def upsert_by_external_id(
        self, record_type: str, external_id: str, body: dict[str, Any]
    ) -> str:
        """Upsert via the external-id endpoint (``eid:``). Returns internal id."""
        url = self._url(f"{self.RECORD_PATH}/{record_type}/eid:{external_id}")
        resp = self._request("PUT", url, json_body=body)
        if resp.status_code not in (200, 201, 204):
            self._json_or_error(resp)  # raises
        return _internal_id_from_location(resp.headers.get("Location", "")) or external_id

    # ── RESTlet ──────────────────────────────────────────────────────────────
    def call_restlet(
        self, script_id: str, deploy_id: str, body: Any, *, method: str = "POST"
    ) -> dict[str, Any]:
        """POST a JSON body to a deployed RESTlet, returning its JSON response.

        The RESTlet lives on the ``restlets`` host (not the SuiteTalk REST host)
        and is addressed by ``script``/``deploy`` query params, which TBA signs
        as part of the request. Used to create matrix children — the one item
        operation the record API and CSV importer can't do reliably.
        """
        if not self._config.restlet_base:
            raise RuntimeError("NETSUITE_RESTLET_BASE (or account id) is not configured.")
        if not script_id or not deploy_id:
            raise RuntimeError(
                "RESTlet script/deploy ids are not configured. Set "
                "NETSUITE_MATRIX_SCRIPT_ID and NETSUITE_MATRIX_DEPLOY_ID (see "
                "docs/RESTLET_DEPLOY.md)."
            )
        params = urlencode({"script": script_id, "deploy": deploy_id})
        url = f"{self._config.restlet_base}{self.RESTLET_PATH}?{params}"
        return self._json_or_error(self._request(method, url, json_body=body))

    # ── SuiteQL ──────────────────────────────────────────────────────────────
    def suiteql(self, query: str, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        """Run a SuiteQL query, returning all item rows (paging transparently)."""
        items: list[dict[str, Any]] = []
        while True:
            params = urlencode({"limit": limit, "offset": offset})
            url = self._url(f"{self.QUERY_PATH}/suiteql?{params}")
            resp = self._request(
                "POST",
                url,
                json_body={"q": query},
                headers={"Prefer": "transient"},
            )
            data = self._json_or_error(resp)
            items.extend(data.get("items", []))
            if not data.get("hasMore"):
                break
            offset += limit
        return items
