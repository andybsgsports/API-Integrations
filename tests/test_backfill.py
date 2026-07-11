from __future__ import annotations

from sanmar_netsuite.netsuite.adopt import MatchRow, ReconcileReport
from sanmar_netsuite.netsuite.backfill import backfill_keys, names_equivalent


class FakeWriter:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_on = fail_on or set()

    def update_record(self, record_type: str, internal_id: str, body: dict) -> None:
        if internal_id in self.fail_on:
            raise RuntimeError("NetSuite 400: nope")
        self.calls.append((record_type, internal_id, body))


def _row(key, ns_id, gtin="00012345678905") -> MatchRow:
    return MatchRow(
        unique_key=key, style="2000", color_name="Ash", mainframe_color="Ash",
        size="Large", gtin=gtin, ns_id=ns_id, method="option:name",
    )


def test_names_equivalent_ignores_whitespace_and_case():
    assert names_equivalent("Steel/Black", "Steel/ Black")
    assert names_equivalent("sport grey", "Sport Grey")
    assert not names_equivalent("Cabl", "Carolina Blue")


def test_writes_upc_per_matched_item():
    report = ReconcileReport(rows=[_row("111", "500"), _row("222", "501")])
    client = FakeWriter()
    result = backfill_keys(client, report, allow_write=True)
    assert result.items_written == 2
    assert ("inventoryItem", "500", {"upcCode": "00012345678905"}) in client.calls
    # external ids NOT written unless explicitly enabled
    assert all("externalId" not in body for _, _, body in client.calls)


def test_external_ids_optional_and_conflicts_deduped():
    report = ReconcileReport(rows=[
        _row("111", "500"),
        _row("999", "500"),  # second feed row claiming the same item -> conflict
    ])
    client = FakeWriter()
    result = backfill_keys(client, report, allow_write=True, set_external_ids=True)
    assert result.items_conflict == 1
    assert result.items_written == 1
    _, _, body = client.calls[0]
    assert body["externalId"] == "SANMAR-111"


def test_no_gtin_skips_upc_but_counts():
    report = ReconcileReport(rows=[_row("111", "500", gtin="")])
    client = FakeWriter()
    result = backfill_keys(client, report, allow_write=True)
    assert result.items_skipped_no_gtin == 1
    assert result.items_written == 0  # nothing to write without external ids
    assert client.calls == []


def test_dry_run_writes_nothing_but_counts():
    report = ReconcileReport(rows=[_row("111", "500")])
    report.color_renames["1016"] = ("Cabl", "Carolina Blue")
    client = FakeWriter()
    result = backfill_keys(client, report, allow_write=False)
    assert client.calls == []
    assert result.items_written == 1
    assert result.renames_written == 1
    assert "WOULD write" in result.summary()


def test_max_items_caps_writes():
    report = ReconcileReport(rows=[_row(str(i), str(500 + i)) for i in range(10)])
    client = FakeWriter()
    result = backfill_keys(client, report, allow_write=True, max_items=3)
    assert result.items_written == 3
    assert len(client.calls) == 3


def test_renames_skip_whitespace_only_and_survive_failures():
    report = ReconcileReport(rows=[_row("111", "500")])
    report.color_renames["1363"] = ("Steel/Black", "Steel/ Black")  # ws only
    report.color_renames["1016"] = ("Cabl", "Carolina Blue")
    client = FakeWriter(fail_on={"1016"})
    result = backfill_keys(client, report, allow_write=True)
    assert result.renames_skipped_ws == 1
    assert result.renames_written == 0
    assert any(t == "color 1016" for t, _ in result.failures)
