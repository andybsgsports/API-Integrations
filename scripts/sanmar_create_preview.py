"""Preview (and stage) the SanMar items NetSuite is missing -- read-only.

This is the "what would CSV auto-create add?" report. It downloads today's
SanMar SDL feed, reads the live NetSuite catalog once (SuiteQL, no writes), and
diffs them the same way ``sanmar-sync export-csv --merge`` does -- but instead of
one blended file it separates the two kinds of "missing":

* **new children under an EXISTING parent** -- a new colour/size for a style
  NetSuite already carries. These are directly importable with a *child* CSV
  import map (the parent already lists the grid), so they are written to
  ``data/sanmar_new_children.csv`` -- the pilot-ready create-only file.

* **children of a NET-NEW parent style** -- a style NetSuite doesn't have at
  all. Their parent matrix item must be created first (with its full grid), so
  these can't go in the child import. The distinct styles are listed in
  ``data/sanmar_new_parents.txt`` for the parent-creation phase.

Nothing here writes to NetSuite; it only reads and emits files + a summary, so
it's safe to run on every push. The child CSV it produces is what the operator
builds/validates the saved import map against (see docs/CSV_AUTOCREATE.md).
"""

from __future__ import annotations

from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.merge import prepare_merge
from sanmar_netsuite.netsuite.repository import child_external_id
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from sanmar_netsuite.transform.csv_export import write_matrix_csv

ROOT = Path(__file__).resolve().parents[1]


class MissingSplit:
    """The result of diffing the feed against the live catalog."""

    def __init__(self) -> None:
        self.new_children = 0          # new colour/size under an existing parent
        self.new_parent_rows = 0       # rows belonging to a net-new parent style
        self.new_parent_styles: set[str] = set()
        self.child_only_skip: set[str] = set()  # skip ∪ all net-new-parent rows


def split_missing(styles, parent_refs: dict[str, str], skip: set[str]) -> MissingSplit:
    """Bucket the feed's missing SKUs into existing-parent vs net-new-parent.

    ``skip`` (from ``prepare_merge``) already covers combos present in NetSuite.
    A style absent from ``parent_refs`` has no parent record yet, so *all* its
    children are held out of the child CSV (``child_only_skip``) and counted as
    net-new-parent rows -- their parent must be created before they can import.
    """
    out = MissingSplit()
    out.child_only_skip = set(skip)
    for s in styles:
        has_parent = s.style in parent_refs
        for sku in s.skus:
            eid = child_external_id(sku.unique_key)
            if eid in skip:
                continue  # combo already in NetSuite
            if has_parent:
                out.new_children += 1
            else:
                out.new_parent_rows += 1
                out.new_parent_styles.add(s.style)
                out.child_only_skip.add(eid)  # keep net-new-parent rows out
    return out


def _resolve_feed(config) -> Path:
    """Local SDL feed if already downloaded, else fetch it from SanMar SFTP."""
    local = Path(config.sftp.download_dir) / C.FILE_SDL_N
    if local.exists():
        return local
    return SanMarSftp(config.sftp).download(C.FILE_SDL_N)


def main() -> int:
    config = get_config()
    feed = _resolve_feed(config)
    styles = parse_styles(str(feed))
    total_skus = sum(len(s.skus) for s in styles)
    print(f"SanMar feed: {len(styles)} styles, {total_skus} SKUs")

    client = NetSuiteClient(config.netsuite)
    parent_refs, skip = prepare_merge(client, styles)
    print(
        f"NetSuite catalog: {len(parent_refs)} of {len(styles)} styles already "
        f"exist as parents; {len(skip)} colour/size combo(s) already present"
    )

    # Split the "missing" rows into the two buckets; child_only_skip keeps the
    # child CSV to ONLY existing-parent adds.
    split = split_missing(styles, parent_refs, skip)

    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)

    # Pilot-ready create-only CSV: new colours/sizes under existing parents only.
    child_csv = data / "sanmar_new_children.csv"
    write_matrix_csv(
        styles,
        child_csv,
        tax_schedule=config.sync.tax_schedule,
        income_account=config.sync.income_account,
        parent_refs=parent_refs,
        skip_external_ids=split.child_only_skip,
    )

    # Net-new parent styles need a parent record created first -- list them for
    # the (later) parent-creation phase; they are NOT in the child CSV.
    styles_sorted = sorted(split.new_parent_styles)
    parents_txt = data / "sanmar_new_parents.txt"
    parents_txt.write_text(
        "\n".join(styles_sorted) + ("\n" if styles_sorted else ""),
        encoding="utf-8",
    )

    print("")
    print(f"NEW children under existing parents : {split.new_children:>6}  "
          f"-> {child_csv.relative_to(ROOT)} (import-ready)")
    print(f"NEW rows under net-new parent styles: {split.new_parent_rows:>6}  "
          f"across {len(styles_sorted)} style(s) -> "
          f"{parents_txt.relative_to(ROOT)} (need parent first)")
    if styles_sorted:
        sample = ", ".join(styles_sorted[:20])
        more = "" if len(styles_sorted) <= 20 else f" (+{len(styles_sorted) - 20} more)"
        print(f"  net-new styles: {sample}{more}")
    if not split.new_children and not split.new_parent_rows:
        print("Catalog is in sync -- nothing new to create.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
