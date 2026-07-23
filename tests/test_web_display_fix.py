"""Unit tests for the Display in Web Site body builder
(scripts/item_web_display_fix).

isOnline is only ever turned ON (when the item has an Atlas image and isn't
already online) -- items without an image are left alone rather than forced
to No.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_web_display_fix import IMAGE_FIELD, plan_body  # noqa: E402


def test_sets_online_when_image_present_and_offline():
    row = {"isonline": "F", IMAGE_FIELD: "https://cdn.example.com/img.jpg"}
    assert plan_body(row) == {"isOnline": True}


def test_no_change_when_already_online():
    row = {"isonline": "T", IMAGE_FIELD: "https://cdn.example.com/img.jpg"}
    assert plan_body(row) == {}


def test_no_change_when_no_image():
    row = {"isonline": "F", IMAGE_FIELD: ""}
    assert plan_body(row) == {}


def test_no_change_when_no_image_field_missing():
    row = {"isonline": "F"}
    assert plan_body(row) == {}
