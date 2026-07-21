"""Unit test for the RESTlet CSV-import job payload (scripts/sanmar_csv_import).

The orchestration needs live NetSuite, but the job payload is pure: an inline
CSV, the saved-map id, and the target File Cabinet folder (as an int).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_csv_import import build_job  # noqa: E402


def test_build_job_shape():
    job = build_job("custimport_bsg_sanmar_child", "771", "A,B\n1,2\n", "sanmar_part01")
    assert job == {
        "mappingId": "custimport_bsg_sanmar_child",
        "folderId": 771,            # coerced to int for N/file
        "fileName": "sanmar_part01.csv",
        "name": "sanmar_part01",
        "csv": "A,B\n1,2\n",
    }
