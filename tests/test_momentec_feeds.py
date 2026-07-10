from __future__ import annotations

from pathlib import Path

from momentec_netsuite.feeds import parse_product_data

SAMPLE = Path(
    "/root/.claude/uploads/bc8c9923-f9d7-5294-8fec-b39eba96f23b"
    "/3e8b1c81-sublimationproductdatastdall.csv"
)


def test_parse_real_sample_feed():
    if not SAMPLE.exists():  # sample only exists in the dev session
        return
    styles = parse_product_data([SAMPLE])
    assert len(styles) > 100
    total = sum(len(s.skus) for s in styles)
    assert total > 40_000
    sku = styles[0].skus[0]
    assert sku.item_sku.count(".") == 2
    assert sku.color_code and sku.size_code
    # barcodes present on the overwhelming majority of rows
    with_gtin = sum(1 for s in styles for k in s.skus if k.gtin)
    assert with_gtin / total > 0.9
