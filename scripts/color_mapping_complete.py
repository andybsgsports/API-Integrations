"""Complete the color-token mapping: user-confirmed + inferred + artifacts.

Takes the auto-proposal (color_token_mapping_proposal.csv) and layers on:
1. the user's confirmed mappings,
2. inferred mappings for the remaining legacy tokens (validated against the
   production color list -- a guess that isn't a real list value is flagged
   NOT_IN_LIST instead of silently invented),
3. size-artifact detection: tokens like "Black-Large/X" come from sizes the
   name parser didn't know ("Large/X-Large", "Youth X-Small"...) -- the real
   color is the prefix; these need parser fixes, not color renames.

Output: data/color_token_mapping_final.csv -- the single input the fix
writer consumes. Statuses: auto | user | inferred | inferred_low |
artifact | NOT_IN_LIST | unresolved.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_CONFIRMED = {
    "kg": "Kelly Green",
    "roy": "Royal",
    "si": "Silver",
    # user said "light heather grey"; the list's canonical form is this:
    "lthtgr": "Light Grey Heather",
    "sc": "Scarlet",
    "crd": "Cardinal",
    "rofr": "Royal Frost",
    "grfr": "Grey Frost",
    "pond blhe": "Pond Blue Heather",
    "-navy": "Navy",
    "true royalx": "True Royal",
}

# Inferred from the user's confirmed pattern language (Gr=Grey, initial
# pairs/consonant skeletons) + production's own rename history (Jtbk -> Jet
# Black, Brrd -> Bright Red, Sg -> Sport Grey per the drift audit).
INFERRED = {
    "lm": "Lime",
    "whi": "White",
    "gp": "Graphite",
    "sg": "Sport Grey",
    "bkfr": "Black Frost",
    "pi": "Pink",
    "nahea": "Navy Heather",
    "naht": "Navy Heather",
    "saph": "Sapphire",
    "carobl": "Carolina Blue",
    "sfpi": "Safety Pink",
    "grhtr": "Grey Heather",
    "grhe": "Grey Heather",
    "txor": "Texas Orange",
    "rowh": "Royal/White",
    "neye": "Neon Yellow",
    "jtbk": "Jet Black",
    "ltbl": "Light Blue",
    "hgr": "Heather Grey",
    "brrd": "Bright Red",
    "cstlrk": "Castlerock",
    "ox": "Oxford",
    "unrd": "University Red",
    "crdrd": "Cardinal Red",
    "ma/wh": "Maroon/White",
    "mahtr": "Maroon Heather",
    "bor": "Burnt Orange",
    "rdfr": "Red Frost",
    "ne or": "Neon Orange",
    "rd/w": "Red/White",
    "vg/bk": "Vegas Gold/Black",
    "mnt": "Mint",
    "vihe/bk": "Vintage Heather/Black",
    "pu/wh": "Purple/White",
    "crnslk": "Cornsilk",
    "bk/chhe": "Black/Charcoal Heather",
    "charcoal healther": "Charcoal Heather",  # typo in the item name
    "nat": "Natural",
    "gr3": "Grey Three",
    "irgr": "Iron Grey",
    "wh/urd": "White/University Red",
    "dprd": "Deep Red",
    "bk/or": "Black/Orange",
}

# Plausible but genuinely uncertain -- kept separate so they read as
# "needs a second look" even though a best guess is filled in.
INFERRED_LOW = {
    "st": "Stealth",
    "ca/ro": "Cardinal/Royal",
    "ca/bk": "Cardinal/Black",
    "soblack": "Solid Black",
}

# Size tails the name parser didn't know; the leading part is the color.
ARTIFACT_TAILS = (
    "-Large/X", "-Youth X", "-Youth 2X", "-2X", "-2x", "-6X",
    "Youth X", "Youth 2X", "Large/X", "2X", "-Navy",
)
ARTIFACT_RE = re.compile(
    r"^(?P<color>.*?)-?(Large/X|Youth X|Youth 2X|2X|2x|6X|1\.5\" X 42\")$"
)


def main() -> int:
    names: dict[str, str] = {}
    with (ROOT / "data" / "color_list_production.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            nm = r["Name"].strip()
            if nm:
                names[nm.lower()] = nm

    rows = list(csv.DictReader(
        (ROOT / "data" / "color_token_mapping_proposal.csv").open(encoding="utf-8")
    ))
    out_path = ROOT / "data" / "color_token_mapping_final.csv"
    counts: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["token", "item_count", "final_name", "status", "notes"])
        for r in rows:
            token = r["token"]
            n = r["item_count"]
            low = token.lower()
            if r["method"] != "UNRESOLVED":
                w.writerow([token, n, r["proposed_name"], "auto", r["method"]])
                counts["auto"] = counts.get("auto", 0) + 1
                continue
            for source, status in (
                (USER_CONFIRMED, "user"),
                (INFERRED, "inferred"),
                (INFERRED_LOW, "inferred_low"),
            ):
                if low in source:
                    proposed = source[low]
                    if proposed.lower() in names:
                        w.writerow([token, n, names[proposed.lower()], status, ""])
                        counts[status] = counts.get(status, 0) + 1
                    else:
                        w.writerow([token, n, proposed, "NOT_IN_LIST",
                                    "proposed name missing from production color list"])
                        counts["NOT_IN_LIST"] = counts.get("NOT_IN_LIST", 0) + 1
                    break
            else:
                m = ARTIFACT_RE.match(token)
                if m is not None:
                    color = m.group("color").strip("-").strip()
                    resolved = (
                        names.get(color.lower())
                        or names.get(USER_CONFIRMED.get(color.lower(), "").lower())
                        or names.get(INFERRED.get(color.lower(), "").lower())
                    )
                    note = f"size tail; real color: {resolved or color or '(none)'}"
                    w.writerow([token, n, resolved or "", "artifact", note])
                    counts["artifact"] = counts.get("artifact", 0) + 1
                else:
                    w.writerow([token, n, "", "unresolved", r["alternatives"]])
                    counts["unresolved"] = counts.get("unresolved", 0) + 1
    print(f"wrote {out_path}")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
