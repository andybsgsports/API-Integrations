"""Find the matrix RESTlet's script id + deployment id from NetSuite (read-only).

The item-create pilot needs NETSUITE_MATRIX_SCRIPT_ID / NETSUITE_MATRIX_DEPLOY_ID
as repo secrets. Rather than have the user hunt through the UI, query the
``script`` and ``scriptdeployment`` tables over SuiteQL and print the exact
values (both the string scriptid and the numeric internal id work).
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


KEYWORDS = ("matrix", "sanmar", "bsg", "child", "grid", "item")


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    scripts = []
    try:
        scripts = client.suiteql(
            "SELECT id, scriptid, name, scripttype FROM script "
            "WHERE scripttype = 'RESTLET' ORDER BY id"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  script table query failed ({str(exc)[:120]})")

    # full listing (may scroll off the top -- the candidate block below is the
    # important part and prints last so it lands in the log tail)
    print("=== all RESTlet scripts ===")
    for r in scripts:
        print(f"  internalId={str(r.get('id')):<8} scriptId={str(r.get('scriptid')):<44} "
              f"name={str(r.get('name'))!r}")

    def deployments(script_internal: str) -> list[dict]:
        try:
            return client.suiteql(
                "SELECT id, scriptid, status FROM scriptdeployment "
                f"WHERE script = {int(script_internal)} ORDER BY id"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  deployment query failed for script {script_internal} "
                  f"({str(exc)[:100]})")
            return []

    # candidates: keyword match on name/scriptid, else scripts with a bare
    # customdeployN (hand-uploaded scripts like our matrix RESTlet)
    candidates = []
    for r in scripts:
        name = str(r.get("name") or "").lower()
        sid = str(r.get("scriptid") or "").lower()
        if any(k in name or k in sid for k in KEYWORDS):
            candidates.append(r)
    if not candidates:
        candidates = scripts

    print("\n=== CANDIDATE matrix RESTlet(s) + their deployments ===")
    for r in candidates:
        internal = str(r.get("id"))
        print(f"\n  SCRIPT internalId={internal} scriptId={str(r.get('scriptid'))!r} "
              f"name={str(r.get('name'))!r}")
        for d in deployments(internal):
            print(f"    deployment internalId={d.get('id')} "
                  f"deployScriptId={str(d.get('scriptid'))!r} status={d.get('status')}")

    print("\n=== what to put in the two repo secrets ===")
    print("  NETSUITE_MATRIX_SCRIPT_ID = the matrix script's scriptId (customscript_...)")
    print("  NETSUITE_MATRIX_DEPLOY_ID = its deployment's deployScriptId (customdeploy...)")
    print("  (numeric internalIds also work). The matrix RESTlet's post() creates")
    print("  matrix children -- pick the script whose name/scriptid names SanMar/matrix,")
    print("  or the hand-uploaded one with a bare 'customdeploy1'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
