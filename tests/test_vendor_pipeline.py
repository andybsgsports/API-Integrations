"""The per-vendor pipeline (scripts/vendor_pipeline.py).

Andy's sequence (2026-07-31): per vendor, create the colours the new items
need, then create the items themselves fully populated, then update inventory
/ pricing / flags -- and only then move to the next vendor.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vendor_pipeline as vp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_order_is_andys_order():
    assert vp.VENDOR_ORDER[:4] == ["sanmar", "momentec", "ua", "ss"]


def test_phases_are_in_dependency_order():
    # Options before create (an item can't reference a colour that doesn't
    # exist) and create before update (can't price an item that doesn't exist).
    assert vp.PHASES == ("discover", "options", "create", "update", "images")


def test_every_vendor_has_an_update_phase():
    for vendor in vp.VENDOR_ORDER:
        assert vp.PIPELINES[vendor].get("update"), vendor


def test_every_configured_script_exists():
    for vendor, phases in vp.PIPELINES.items():
        for phase, scripts in phases.items():
            for script in scripts:
                assert (ROOT / script).exists(), f"{vendor}/{phase}: {script}"


def test_sanmar_creates_options_before_items():
    sanmar = vp.PIPELINES["sanmar"]
    assert sanmar["options"] and sanmar["create"]
    assert vp.PHASES.index("options") < vp.PHASES.index("create")


def test_vendors_without_a_create_path_are_documented():
    # A missing create phase must be an acknowledged gap, not a silent hole.
    for vendor in vp.VENDOR_ORDER:
        if not vp.PIPELINES[vendor].get("create"):
            assert vendor in vp.PIPELINE_GAPS or vendor == "champro-csv", vendor


def test_ramp_cap_defaults_to_a_bounded_number():
    # Andy chose "ramped in over nights" -- an uncapped default would create
    # ~119k SanMar items on the first live run.
    assert vp.DEFAULT_CREATE_MAX_STYLES.isdigit()
    assert 0 < int(vp.DEFAULT_CREATE_MAX_STYLES) <= 1000


def test_run_vendor_runs_phases_in_order(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(vp, "_run", lambda script, env: calls.append(script) or 0)
    vp.run_vendor("sanmar")
    assert calls == [
        "scripts/sanmar_create_preview.py",
        "scripts/sanmar_ensure_matrix_values.py",
        "scripts/sanmar_child_create.py",
        "scripts/sanmar_parent_create.py",
        "scripts/sanmar_child_finalize.py",
        "scripts/sanmar_field_update.py",
        "scripts/atlas_image_backfill.py",
    ]


def test_run_vendor_honours_a_phase_subset(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(vp, "_run", lambda script, env: calls.append(script) or 0)
    vp.run_vendor("sanmar", phases=("update",))
    assert calls == ["scripts/sanmar_field_update.py"]
    calls.clear()
    vp.run_vendor("sanmar", phases=("update", "images"))
    assert calls == ["scripts/sanmar_field_update.py",
                     "scripts/atlas_image_backfill.py"]


def test_run_vendor_sets_the_ramp_cap(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(vp, "_run", lambda script, env: seen.append(env) or 0)
    monkeypatch.delenv("CREATE_MAX_STYLES", raising=False)
    vp.run_vendor("sanmar", phases=("create",))
    assert seen and seen[0]["CREATE_MAX_STYLES"] == vp.DEFAULT_CREATE_MAX_STYLES


def test_a_failing_phase_does_not_abort_the_vendor(monkeypatch):
    # Diff-aware phases: finishing the rest still moves the catalogue forward.
    calls: list[str] = []

    def fake(script, env):
        calls.append(script)
        return 1 if "ensure_matrix_values" in script else 0

    monkeypatch.setattr(vp, "_run", fake)
    rc = vp.run_vendor("sanmar")
    assert rc == 1                     # still reported as a failure
    assert "scripts/sanmar_field_update.py" in calls   # ...but update still ran


def test_unknown_vendor_is_rejected():
    assert vp.run_vendor("nope") == 1


@pytest.mark.parametrize("vendor", ["sanmar", "momentec", "ua", "ss", "dcos"])
def test_known_vendors_resolve(vendor):
    assert vendor in vp.PIPELINES


# --- shared exit-code policy (scripts/run_status.py)

def test_clean_run_is_success():
    from run_status import exit_code
    assert exit_code("x", 0, 0, 45000) == 0


def test_small_all_429_residue_is_forgiven():
    # 18 throttled items out of 45k filed a failure issue (run 31073107629);
    # 2 out of 17k filed another (run 31233431089). Both are self-healing.
    from run_status import exit_code
    assert exit_code("x", 18, 0, 45000) == 0
    assert exit_code("x", 2, 0, 17252) == 0


def test_any_non_429_failure_stays_fatal():
    # One bad field value silently rejected ~45k records twice; never forgive.
    from run_status import exit_code
    assert exit_code("x", 1, 1, 45000) == 1
    assert exit_code("x", 500, 3, 45000) == 1


def test_large_429_set_is_still_fatal():
    from run_status import exit_code
    assert exit_code("x", 5000, 0, 45000) == 1


def test_many_skipped_chunks_stay_fatal():
    # A skipped chunk is a whole slice never considered, not a lost record.
    from run_status import exit_code
    assert exit_code("x", 0, 0, 45000, 5) == 1
    assert exit_code("x", 10, 0, 45000, 1) == 0


def test_the_image_pass_is_its_own_phase():
    # update+images in one leg ran 295+ of the 350-minute job cap the night
    # swatch URLs first went out to ~62k items (run 31424046955). A leg killed
    # by the cap never reaches the chain's dispatch step, so every vendor
    # after SanMar would silently lose the night -- hence its own step, with
    # its own budget. It still runs AFTER update: it joins on the key the
    # field update stamps.
    assert vp.PIPELINES["sanmar"]["images"] == ["scripts/atlas_image_backfill.py"]
    assert "scripts/atlas_image_backfill.py" not in vp.PIPELINES["sanmar"]["update"]
    assert vp.PHASES.index("images") > vp.PHASES.index("update")


def test_dcos_runs_every_supplier(monkeypatch):
    # The chain's dcos leg used to run only DCOS_SUPPLIER=champro, so TCK /
    # Cap America / Mizuno never restamped their feed heartbeats -- lifecycle
    # saw 100% stale and only the circuit breaker kept their items alive.
    runs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        vp, "_run", lambda script, env: runs.append((script, env["DCOS_SUPPLIER"])) or 0)
    vp.run_vendor("dcos", phases=("update",))
    assert [s for _, s in runs] == list(vp.DCOS_SUPPLIERS)
    assert set(s for _, s in runs) >= {"champro", "tck", "capamerica", "mizuno"}


def test_sanmar_child_create_replaced_the_dead_csv_import():
    # The CSV-import Suitelet came back SSS_INVALID_SCRIPTLET_ID (issue #116);
    # new children under existing parents now post through the matrix RESTlet.
    assert "scripts/sanmar_child_create.py" in vp.PIPELINES["sanmar"]["create"]
    assert "scripts/sanmar_csv_import.py" not in vp.PIPELINES["sanmar"]["create"]
