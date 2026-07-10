from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sdl_n_path() -> Path:
    return FIXTURES / "sample_sdl_n.csv"


@pytest.fixture
def dip_path() -> Path:
    return FIXTURES / "sample_dip.txt"


@pytest.fixture
def ss_products_path() -> Path:
    return FIXTURES / "ss" / "sample_products.json"
