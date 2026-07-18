"""Find the matrix RESTlet's script id + deployment id from NetSuite (read-only).

The item-create pilot needs NETSUITE_MATRIX_SCRIPT_ID / NETSUITE_MATRIX_DEPLOY_ID
as repo secrets. Rather than have the user hunt through the UI, query the
``script`` and ``scriptdeployment`` tables over SuiteQL and print the exact
values (both the string scriptid and the numeric internal id work).
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    print("=== RESTlet scripts (type RESTLET) ===")
    scripts = []
    try:
        scripts = client.suiteql(
            "SELECT id, scriptid, name, scripttype FROM script "
            "WHERE scripttype = 'RESTLET' ORDER BY id"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  script table query failed ({str(exc)[:120]})")
    matrix_ids = []
    for r in scripts:
        name = str(r.get("name") or "")
        sid = str(r.get("scriptid") or "")
        internal = str(r.get("id") or "")
        flag = ""
        if "matrix" in name.lower() or "matrix" in sid.lower():
            flag = "   <<< MATRIX RESTLET"
            matrix_ids.append(internal)
        print(f"  internalId={internal:<8} scriptId={sid:<40} name={name!r}{flag}")

    print("\n=== Deployments for the matrix RESTlet(s) ===")
    if not matrix_ids:
        print("  no script whose name/scriptid contains 'matrix' -- listing ALL "
              "RESTlet deployments so you can spot it:")
        matrix_ids = [str(r.get("id")) for r in scripts]
    for sid in matrix_ids:
        try:
            deps = client.suiteql(
                "SELECT id, scriptid, status, script FROM scriptdeployment "
                f"WHERE script = {int(sid)} ORDER BY id"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  deployment query failed for script {sid} ({str(exc)[:100]})")
            continue
        for d in deps:
            print(f"  script(internal {sid}) -> deployment internalId={d.get('id')} "
                  f"deployScriptId={str(d.get('scriptid'))!r} status={d.get('status')}")

    print("\n=== what to put in the two repo secrets ===")
    print("  NETSUITE_MATRIX_SCRIPT_ID = the matrix script's scriptId (customscript_...)")
    print("  NETSUITE_MATRIX_DEPLOY_ID = its deployment's deployScriptId (customdeploy...)")
    print("  (the numeric internalIds also work)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
