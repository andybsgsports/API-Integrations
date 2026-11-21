"""Hold a vendor's price and cost writes when too many of them move too far.

Andy's safeguard for the automatic route (2026-11-12): hold any price list
where more than a quarter of the prices move by more than 25%, and put it on
the skipped list for him instead of applying it. A feed that suddenly reprices
a big share of a vendor's items is far more likely to be a bad file (wrong
column, wrong unit, a truncated export) than a real price change.

How it fits together:

* Every writer that sets Base Price or Purchase Price calls :func:`record`
  with the current and the new value (``native_pricing.add_native_diffs``
  does it for SanMar, S&S and Momentec; ``ua_backfill`` calls it itself).
* With ``PRICE_GUARD_REPORT=<path>`` set, the counts are written to that file
  when the script exits.
* ``vendor_pipeline`` runs the update script once as a read-only dry run to
  collect the counts, decides with :func:`tripped`, and then runs the real
  pass -- with ``PRICE_GUARD_HOLD=1`` when the guard tripped, which makes the
  writers leave Base Price and Purchase Price alone. Every other field
  (inventory, flags, images, heartbeat) is still written.

Only a price that already exists and changes counts as a "move". Filling a
blank price is not a move, and neither is an unchanged one, but both count
toward how many prices were looked at.
"""

from __future__ import annotations

import atexit
import csv
import json
import os
from pathlib import Path

#: A single price counts as a big move past this fraction of its old value.
MOVE_PCT = float(os.environ.get("PRICE_GUARD_MOVE_PCT", "25") or "25") / 100.0
#: The guard trips when more than this share of compared prices moved big.
MAX_SHARE = float(os.environ.get("PRICE_GUARD_MAX_SHARE", "25") or "25") / 100.0
#: Below this many compared prices a share is noise (a 3-item vendor where one
#: price doubles is not a broken file), so the guard does not trip.
MIN_COMPARED = int(os.environ.get("PRICE_GUARD_MIN_COMPARED", "20") or "20")

KINDS = ("price", "cost")

_stats: dict[str, dict[str, int]] = {
    k: {"compared": 0, "moved_big": 0, "filled_blank": 0} for k in KINDS
}


#: Every price or cost that would change this run: (kind, item id, item name,
#: current, new). Written next to the report so a person can review a hold.
_moves: list[tuple[str, str, str, float | None, float]] = []
MOVES_FILE = Path("data/price_guard_moves.csv")
SAMPLES = 10
_LABEL = {"price": "Base Price", "cost": "Purchase Price"}


def _num(value: object) -> float | None:
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n


def holding() -> bool:
    """True when this run must leave Base Price and Purchase Price alone."""
    return os.environ.get("PRICE_GUARD_HOLD", "").strip() == "1"


def record(kind: str, current: object, new: object, *, ref: object = "",
           name: object = "") -> None:
    """Count one item's current -> new value for ``kind`` (price or cost).

    ``ref`` (item id) and ``name`` only label the review list of moves."""
    new_n = _num(new)
    if new_n is None:
        return
    s = _stats[kind]
    cur_n = _num(current)
    if cur_n is None or cur_n <= 0:
        s["filled_blank"] += 1
        _moves.append((kind, str(ref or ""), str(name or ""), None, new_n))
        return
    s["compared"] += 1
    if abs(new_n - cur_n) / cur_n > MOVE_PCT:
        s["moved_big"] += 1
    if abs(new_n - cur_n) >= 0.005:
        _moves.append((kind, str(ref or ""), str(name or ""), cur_n, new_n))


def _pct(cur: float | None, new: float) -> float | None:
    return None if not cur else (new - cur) / cur


def sample_lines(moves=None, n: int = SAMPLES) -> list[str]:
    """The largest moves up and down for each kind, for the run log."""
    moves = _moves if moves is None else moves
    out = []
    for k in KINDS:
        rows = [m for m in moves if m[0] == k and m[3]]
        rows.sort(key=lambda m: _pct(m[3], m[4]))
        picks = [("down", rows[:n]), ("up", rows[::-1][:n])]
        for direction, chosen in picks:
            for _k, ref, name, cur, new in chosen:
                pct = _pct(cur, new)
                if (direction == "down") != (pct < 0):
                    continue
                out.append(
                    f"PRICE GUARD sample ({_LABEL[k]}, largest {direction}): "
                    f"item {ref} {name}: {cur:,.2f} -> {new:,.2f} ({pct:+.0%})")
    return out


def _write_moves() -> None:
    if not _moves:
        return
    try:
        MOVES_FILE.parent.mkdir(parents=True, exist_ok=True)
        fresh = not MOVES_FILE.exists()
        with MOVES_FILE.open("a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if fresh:
                w.writerow(["Field", "NetSuite Item Id", "NetSuite Item",
                            "Current", "New", "Change", "Percent Change"])
            for k, ref, name, cur, new in _moves:
                pct = _pct(cur, new)
                w.writerow([
                    _LABEL[k], ref, name,
                    "" if cur is None else f"{cur:.2f}", f"{new:.2f}",
                    "" if cur is None else f"{new - cur:.2f}",
                    "" if pct is None else f"{pct:.4f}"])
    except OSError as exc:
        print(f"price guard: could not write {MOVES_FILE}: {exc}")


def snapshot() -> dict[str, dict[str, int]]:
    return {k: dict(v) for k, v in _stats.items()}


def reset() -> None:
    for v in _stats.values():
        for key in v:
            v[key] = 0
    _moves.clear()


def _dump() -> None:
    path = os.environ.get("PRICE_GUARD_REPORT")
    if not path:
        return
    _write_moves()
    for line in sample_lines():
        print(line)
    merged = snapshot()
    p = Path(path)
    # One vendor step can run several scripts or suppliers into the same
    # report; add to what is already there.
    if p.exists():
        try:
            old = json.loads(p.read_text())
            for k in KINDS:
                for key in merged[k]:
                    merged[k][key] += int(old.get(k, {}).get(key, 0))
        except (ValueError, OSError):
            pass
    try:
        p.write_text(json.dumps(merged))
    except OSError as exc:
        print(f"price guard: could not write report {path}: {exc}")


def share(stats: dict[str, int]) -> float:
    compared = int(stats.get("compared", 0))
    return (int(stats.get("moved_big", 0)) / compared) if compared else 0.0


def tripped(report: dict[str, dict[str, int]]) -> list[str]:
    """The kinds (price / cost) whose share of big moves is over the limit."""
    out = []
    for k in KINDS:
        s = report.get(k, {})
        if int(s.get("compared", 0)) >= MIN_COMPARED and share(s) > MAX_SHARE:
            out.append(k)
    return out


def describe(report: dict[str, dict[str, int]]) -> list[str]:
    """One plain line per kind, for the log and the daily summary."""
    label = {"price": "Base Price", "cost": "Purchase Price"}
    lines = []
    for k in KINDS:
        s = report.get(k, {})
        compared = int(s.get("compared", 0))
        lines.append(
            f"{label[k]}: {int(s.get('moved_big', 0)):,} of {compared:,} "
            f"existing price(s) would move more than {MOVE_PCT:.0%} "
            f"({share(s):.1%}); {int(s.get('filled_blank', 0)):,} blank "
            f"price(s) would be filled"
        )
    return lines


# Registered at import so a script that compared no prices still writes an
# (all-zero) report: "no report" has to mean the check itself broke.
if os.environ.get("PRICE_GUARD_REPORT"):
    atexit.register(_dump)
