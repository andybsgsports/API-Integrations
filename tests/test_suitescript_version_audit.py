"""The SuiteScript 2.1 audit must not under-report what stops working.

NetSuite retires SuiteScript 1.0/2.0/2.x in 2028.2. The dangerous failure is
a script that WILL break being reported as fine -- so the tests pin the two
classifications the report is built on: what counts as 1.0, and what counts
as ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from suitescript_version_audit import SUPPORTED, api_version, is_ours


def test_a_missing_api_version_is_reported_as_1_0():
    # SuiteScript 1.0 predates the apiversion field, so NetSuite leaves it
    # NULL. Reporting that as "unknown" would hide the scripts most at risk.
    assert api_version({"apiversion": None}).startswith("1.0")
    assert api_version({"apiversion": ""}).startswith("1.0")
    assert api_version({}).startswith("1.0")


def test_versions_below_2_1_are_not_treated_as_supported():
    for ver in ("1.0", "2.0", "2.x", "2"):
        assert api_version({"apiversion": ver}) not in SUPPORTED, ver
    assert api_version({"apiversion": "2.1"}) in SUPPORTED
    assert api_version({"apiversion": " 2.1 "}) in SUPPORTED


def test_bsg_owned_scripts_are_separated_from_bundles():
    # Ours are ours to convert; a bundle's script is the vendor's to ship.
    assert is_ours({"scriptid": "customscript_bsg_sanmar_matrix"})
    assert is_ours({"scriptid": "CUSTOMSCRIPT_BSG_CSV_IMPORT"})
    assert not is_ours({"scriptid": "customscript_celigo_connector"})
    assert not is_ours({"scriptid": ""})
