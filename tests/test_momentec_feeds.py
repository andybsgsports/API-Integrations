from __future__ import annotations

import os
from pathlib import Path

import pytest

from momentec_netsuite.feeds import parse_product_data

_DEFAULT_SAMPLE = (
    "/root/.claude/uploads/bc8c9923-f9d7-5294-8fec-b39eba96f23b"
    "/3e8b1c81-sublimationproductdatastdall.csv"
)


def _sample() -> Path | None:
    """The real feed sample, when readable (dev session only; CI can't see it)."""
    path = Path(os.environ.get("MOMENTEC_SAMPLE_FEED", _DEFAULT_SAMPLE))
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def test_parse_real_sample_feed():
    sample = _sample()
    if sample is None:
        pytest.skip("real feed sample not available")
    styles = parse_product_data([sample])
    assert len(styles) > 100
    total = sum(len(s.skus) for s in styles)
    assert total > 40_000
    sku = styles[0].skus[0]
    assert sku.item_sku.count(".") == 2
    assert sku.color_code and sku.size_code
    with_gtin = sum(1 for s in styles for k in s.skus if k.gtin)
    assert with_gtin / total > 0.9


def test_parse_synthetic_feed(tmp_path):
    csv_text = (
        "Parent_SKU,Item_SKU,UPC_Code,Item_Name,Brand,Division,Item_Description,"
        "Category,MSRP,Cost,Currency,Launch_Date,Features,Main_Image_URL,"
        "Other_Image_URL,Swatch_Image_URL,Size_Chart_Image_URL,Variation_Theme,"
        "Color,Size,Weight,Weight_Unit,Volume,Volume_Unit,Case_Pack_Qty,"
        "Color_Hex_Value,Status,ProductVideoUrl,Ribbon,Country_Of_Origin,GTIN\n"
        '"029HBM","029HBM.BLK.2XL","711293000000","Sweatpant","60","ASI","d",'
        '"Adult | FLEECE","31.8","15.9","USD",,,"https://x/img.jpg",,,,"Size,Color",'
        '"BLACK","2XL","1.2","lb","344","cu in","12","#101820","20",,,"HONDURAS",'
        '"00711293000000"\n'
    )
    f = tmp_path / "feed.csv"
    f.write_text(csv_text, encoding="utf-8")
    styles = parse_product_data([f])
    assert len(styles) == 1
    sku = styles[0].skus[0]
    assert sku.parent_sku == "029HBM"
    assert sku.color_code == "BLK" and sku.size_code == "2XL"
    assert sku.gtin == "00711293000000" and sku.cost == "15.9"
