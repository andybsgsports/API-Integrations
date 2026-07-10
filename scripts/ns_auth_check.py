"""NetSuite auth preflight for CI: pinpoint credential problems safely.

Prints, for each NetSuite auth value: present/empty, its length, and whether
it carries leading/trailing whitespace (a common copy-paste bug in GitHub
secrets). Never prints secret values. The account id itself is printed —
it's not a secret (it appears in every NetSuite URL).

Then attempts one trivial SuiteQL query and reports the outcome.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def describe(name: str, *, show_value: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        print(f"  {name:26} MISSING/EMPTY")
        return False
    ws = " (!) has leading/trailing whitespace" if raw != raw.strip() else ""
    shown = raw if show_value else f"len={len(raw)}"
    print(f"  {name:26} set  {shown}{ws}")
    return True


def main() -> int:
    print("NetSuite auth preflight")
    print("=" * 40)
    ok = True
    ok &= describe("NETSUITE_ACCOUNT_ID", show_value=True)
    for name in (
        "NETSUITE_CONSUMER_KEY",
        "NETSUITE_CONSUMER_SECRET",
        "NETSUITE_TOKEN_ID",
        "NETSUITE_TOKEN_SECRET",
    ):
        ok &= describe(name)

    if not ok:
        print("\nRESULT: one or more NetSuite values are missing — fix the GitHub "
              "secrets above (Settings > Secrets and variables > Actions).")
        return 1

    from sanmar_netsuite.config import get_config
    from sanmar_netsuite.netsuite.client import NetSuiteClient, NetSuiteError

    cfg = get_config()
    print(f"\nrest base: {cfg.netsuite.rest_base}")
    try:
        client = NetSuiteClient(cfg.netsuite)
        rows = client.suiteql("SELECT COUNT(*) AS n FROM item")
        print(f"RESULT: auth OK — item count = {rows[0].get('n') if rows else '?'}")
        return 0
    except NetSuiteError as exc:
        print(f"RESULT: NetSuite rejected the call -> {exc}")
        if exc.status == 401:
            print(
                "  401 means the signature was refused: consumer key/secret or "
                "token id/secret is wrong for this account. Re-copy each value "
                "into the GitHub secret (watch for truncation/whitespace), or "
                "create a fresh Access Token in NetSuite (Setup > Users/Roles > "
                "Access Tokens > New) and update NETSUITE_TOKEN_ID/SECRET."
            )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"RESULT: client error -> {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
