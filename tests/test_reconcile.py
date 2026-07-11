from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from sanmar_netsuite.netsuite.reconcile import _reconcile_body, reconcile_items


class FakeRepo:
    def __init__(self, ids: dict[str, str]) -> None:
        self._ids = ids
        self.patches: list[tuple[str, dict]] = []

    def find_id_by_external_id(self, external_id: str):
        return self._ids.get(external_id)

    def update_fields(self, internal_id: str, body: dict) -> None:
        self.patches.append((internal_id, body))


def _sku(unique_key: str, msrp):
    return SimpleNamespace(unique_key=unique_key, msrp=msrp)


def _style(*skus):
    return SimpleNamespace(skus=list(skus))


def test_reconcile_sets_income_and_base_price():
    repo = FakeRepo({"SANMAR-1959911": "207331"})
    styles = [_style(_sku("1959911", Decimal("18.00")))]
    report = reconcile_items(repo, styles, income_account_id="218", allow_write=True)
    assert report.updated == ["SANMAR-1959911"]
    internal_id, body = repo.patches[0]
    assert internal_id == "207331"
    assert body["incomeAccount"] == {"id": "218"}
    assert body["price"]["items"][0]["price"] == 18.0
    assert body["price"]["items"][0]["priceLevel"] == {"id": "1"}


def test_reconcile_dry_run_makes_no_writes():
    repo = FakeRepo({"SANMAR-1959911": "207331"})
    styles = [_style(_sku("1959911", Decimal("18.00")))]
    report = reconcile_items(repo, styles, income_account_id="218", allow_write=False)
    assert report.updated == ["SANMAR-1959911"]
    assert repo.patches == []


def test_reconcile_reports_items_not_yet_imported():
    repo = FakeRepo({})
    styles = [_style(_sku("999", None))]
    report = reconcile_items(repo, styles, income_account_id="218", allow_write=True)
    assert report.missing == ["SANMAR-999"]
    assert repo.patches == []


def test_reconcile_body_omits_price_when_no_msrp():
    body = _reconcile_body(SimpleNamespace(msrp=None), "218")
    assert "price" not in body
    assert body["incomeAccount"] == {"id": "218"}
