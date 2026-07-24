"""Read-only probe: what does NetSuite actually have for the bsg_csv_import
RESTlet? (diagnosing SSS_INVALID_SCRIPTLET_ID from the autocreate import step)

Lists script records + deployments whose scriptid/name look like the CSV-import
runner, so the operator can compare against the NETSUITE_CSVIMPORT_SCRIPT_ID /
_DEPLOY_ID secrets without anyone having to read a secret. Never writes;
always exits 0 (a "not found" answer is the diagnosis, not a CI failure).
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def _q(client, label: str, query: str) -> None:
    print(f"\n== {label} ==")
    try:
        rows = client.suiteql(query)
    except Exception as exc:  # noqa: BLE001 - table may not be exposed
        print(f"  (query failed: {str(exc)[:200]})")
        return
    if not rows:
        print("  (no rows)")
    for r in rows:
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    _q(
        client,
        "script records matching csv_import / CSV Import",
        "SELECT id, scriptid, name, scripttype, isinactive FROM script "
        "WHERE LOWER(scriptid) LIKE '%csv_import%' OR LOWER(name) LIKE '%csv import%'",
    )
    _q(
        client,
        "deployments of those scripts",
        "SELECT id, scriptid, script, status, isdeployed, title "
        "FROM scriptdeployment WHERE LOWER(scriptid) LIKE '%csv_import%'",
    )
    _q(
        client,
        "all RESTlet script records (fallback if the LIKE misses)",
        "SELECT id, scriptid, name, isinactive FROM script "
        "WHERE scripttype = 'RESTLET'",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
