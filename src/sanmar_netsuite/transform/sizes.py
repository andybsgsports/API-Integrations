"""Spell out SanMar's abbreviated apparel sizes for NetSuite matrix options.

SanMar's feed uses short size codes (``S``, ``XL``, ``2XL``, ``LT``, ``YM`` …).
Badger's NetSuite matrix size dimension is meant to read in full words
(``Small``, ``X-Large``, ``2X-Large``, ``Large Tall``, ``Youth Medium`` …), so
this module translates one to the other.

Design rules:

* The mapping is *additive* — anything it doesn't recognise (numeric waist
  sizes, hat sizes, one-off codes) is returned **unchanged** so no data is lost.
* It composes: a ``Y`` prefix means *Youth* and a trailing ``T`` means *Tall*,
  layered on top of the base size (so ``2XLT`` → ``2X-Large Tall``).

The single entry point is :func:`normalize_size`.
"""

from __future__ import annotations

import re

# Base alpha sizes → spelled-out form.
_BASE: dict[str, str] = {
    "XS": "X-Small",
    "S": "Small",
    "M": "Medium",
    "L": "Large",
    "XL": "X-Large",
}

# Codes that map directly, ignoring internal spaces/slashes.
_EXACT: dict[str, str] = {
    "OSFA": "One Size",
    "OS": "One Size",
    "OSFM": "One Size",
    "ONESIZE": "One Size",
}


def _expand_x(key: str) -> str | None:
    """Expand the ``nX`` family, e.g. ``2XL``/``XXL`` → ``2X-Large``.

    Handles both the numeric form (``2XL``, ``3XL`` …) and the repeated-letter
    form (``XXL``, ``XXXL`` … / ``XXS`` …). Returns ``None`` when ``key`` isn't
    one of these.
    """
    m = re.fullmatch(r"(\d+)X(L|S)", key)
    if m:
        n, side = m.group(1), m.group(2)
        return f"{n}X-{'Large' if side == 'L' else 'Small'}"
    m = re.fullmatch(r"(X+)(L|S)", key)
    if m:
        n, side = len(m.group(1)), m.group(2)
        word = "Large" if side == "L" else "Small"
        return f"X-{word}" if n == 1 else f"{n}X-{word}"
    return None


def _lookup(key: str) -> str | None:
    """Resolve an uppercased, space-stripped size code, or ``None`` if unknown."""
    if key in _EXACT:
        return _EXACT[key]
    if key in _BASE:
        return _BASE[key]
    expanded = _expand_x(key)
    if expanded:
        return expanded
    # Youth prefix: YS, YM, YXL, …
    if key.startswith("Y") and len(key) > 1:
        inner = _lookup(key[1:])
        if inner:
            return f"Youth {inner}"
    # Tall suffix: LT, XLT, 2XLT, …
    if key.endswith("T") and len(key) > 1:
        inner = _lookup(key[:-1])
        if inner:
            return f"{inner} Tall"
    return None


def normalize_size(raw: str | None) -> str:
    """Return the spelled-out size for a SanMar size code.

    Unknown values (numeric sizes, anything not in the apparel vocabulary) are
    returned trimmed but otherwise unchanged, so the function is always safe to
    apply.

    >>> normalize_size("S")
    'Small'
    >>> normalize_size("2XL")
    '2X-Large'
    >>> normalize_size("LT")
    'Large Tall'
    >>> normalize_size("YM")
    'Youth Medium'
    >>> normalize_size("32")
    '32'
    """
    s = (raw or "").strip()
    if not s:
        return s
    key = s.upper().replace(" ", "").replace("/", "")
    return _lookup(key) or s
