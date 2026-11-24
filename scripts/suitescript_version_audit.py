"""Which scripts in the account are not yet SuiteScript 2.1? -- read-only.

NetSuite is warning that SuiteScript 1.0 / 2.0 / 2.x scripts stop working in
**2028.2**. Every SuiteScript module in this repo already declares
``@NApiVersion 2.1``, so the exposure is scripts the account carries that we
did not write -- legacy customizations, partner/bundle SuiteApps, or a stale
deployed copy that predates the repo.

Guessing which those are is worthless; the account knows. This queries the
``script`` record and reports every script whose API version is not 2.1,
split into what BSG owns (scriptid contains ``bsg`` -- ours to fix) and what
arrived in a bundle (the vendor's to fix, or ours to retire).

Deployment status is joined in where it projects: a 1.0 script with no active
deployment is dead weight to delete, not work to schedule -- a very different
answer than the same script running nightly.

Same discipline as the swatch / category probes: every candidate column is
probed before it is SELECTed (one bad column name 400s the whole query), and
nothing is written.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# Everything we might want about a script; only the ones that project are used.
SCRIPT_COLS = [
    "id", "scriptid", "name", "scripttype", "apiversion", "isinactive",
    "owner", "description",
]
DEPLOY_COLS = ["script", "status", "isdeployed"]

# NetSuite's 2028.2 cutoff: these keep working, everything else does not.
SUPPORTED = {"2.1"}


def valid_cols(client: NetSuiteClient, table: str, candidates: list[str]) -> list[str]:
    """Keep only columns that actually project (a bad one 400s the query)."""
    ok: list[str] = []
    for col in candidates:
        try:
            client.suiteql(f"SELECT {col} FROM {table} WHERE rownum <= 1")
            ok.append(col)
        except Exception:  # noqa: BLE001 - column absent/unqueryable; skip it
            print(f"  (column {table}.{col!r} does not project; skipping)")
    return ok


def api_version(row: dict) -> str:
    """The row's API version, normalised. Blank means SuiteScript 1.0.

    A 1.0 script predates the apiversion field, so NetSuite leaves it NULL --
    reporting that as 'unknown' would hide the very scripts most at risk.
    """
    raw = str(row.get("apiversion") or "").strip()
    return raw or "1.0 (no version set)"


def is_ours(row: dict) -> bool:
    """BSG-owned scripts carry 'bsg' in the scriptid (our naming convention)."""
    return "bsg" in str(row.get("scriptid") or "").lower()


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    cols = valid_cols(client, "script", SCRIPT_COLS)
    if not cols:
        print("FAILED: the 'script' record does not project any column -- "
              "SuiteQL may not expose it to this role. Grant the token's role "
              "'SuiteScript' permission, or read the list in the UI under "
              "Customization > Scripting > Scripts.")
        return 1

    try:
        scripts = client.suiteql(f"SELECT {', '.join(cols)} FROM script")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to read the script list: {str(exc)[:300]}")
        return 1
    print(f"\nscripts in the account: {len(scripts):,}")

    # Deployment status, so a dead script isn't reported as live work.
    deployed: dict[str, list[str]] = defaultdict(list)
    dcols = valid_cols(client, "scriptdeployment", DEPLOY_COLS)
    if "script" in dcols:
        try:
            for dep in client.suiteql(f"SELECT {', '.join(dcols)} FROM scriptdeployment"):
                status = str(dep.get("status") or "").strip() or "?"
                if str(dep.get("isdeployed") or "").upper() in ("F", "FALSE", "NO"):
                    status = f"{status} (not deployed)"
                deployed[str(dep.get("script") or "")].append(status)
        except Exception as exc:  # noqa: BLE001
            print(f"  (deployment status unavailable: {str(exc)[:160]})")

    tally: Counter[str] = Counter(api_version(s) for s in scripts)
    print("\n=== API version across the account")
    for ver, n in sorted(tally.items()):
        flag = "OK" if ver in SUPPORTED else "STOPS WORKING IN 2028.2"
        print(f"  {ver:<22} {n:>5}  {flag}")

    stale = [s for s in scripts if api_version(s) not in SUPPORTED]
    if not stale:
        print("\nNothing to do: every script in the account is already 2.1.")
        return 0

    ours = [s for s in stale if is_ours(s)]
    theirs = [s for s in stale if not is_ours(s)]

    for label, rows in (("BSG-OWNED (ours to convert)", ours),
                        ("BUNDLE / THIRD-PARTY (vendor's to convert, or retire)", theirs)):
        print(f"\n=== {label}: {len(rows)}")
        if not rows:
            continue
        print(f"  {'api':<10} {'type':<16} {'scriptid':<44} {'deployments'}")
        for s in sorted(rows, key=lambda r: (api_version(r), str(r.get("scriptid") or ""))):
            flag_raw = str(s.get("isinactive") or "").upper()
            inactive = " [INACTIVE]" if flag_raw in ("T", "TRUE") else ""
            deps = deployed.get(str(s.get("id") or ""), [])
            dep_txt = ", ".join(sorted(set(deps))) if deps else "none"
            print(f"  {api_version(s)[:10]:<10} {str(s.get('scripttype') or '?')[:16]:<16} "
                  f"{str(s.get('scriptid') or '?')[:44]:<44} {dep_txt}{inactive}")
            print(f"    {str(s.get('name') or '')[:100]}")

    live = [s for s in stale
            if any("released" in d.lower() for d in deployed.get(str(s.get("id") or ""), []))]
    print(f"\n{len(stale)} script(s) below 2.1; {len(ours)} BSG-owned, "
          f"{len(live)} with a RELEASED deployment (actually running today).")
    print("read-only probe: nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
