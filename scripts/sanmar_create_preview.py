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
from sanmar_netsuite.transform.sizes import normalize_size

ROOT = Path(__file__).resolve().parents[1]

# NetSuite's CSV Import Assistant refuses a file with more than 25,000 lines
# (header + rows). Split well under that so each part uploads cleanly, with
# headroom (24,000 data rows + header = 24,001 lines < 25,000).
MAX_ROWS_PER_PART = 24_000


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


def split_csv(src: Path, out_dir: Path, prefix: str, max_rows: int) -> list[Path]:
    """Split a CSV into <=``max_rows``-data-row parts, header repeated on each.

    NetSuite's Import Assistant caps a file at 25,000 lines, so a big create-only
    export has to arrive as several parts. The children are independent (each just
    references its already-existing parent), so the parts import in any order.
    Returns the part paths ([src] unchanged if it fits in one).
    """
    with src.open(encoding="utf-8", newline="") as fh:
        header = fh.readline()
        rows = fh.readlines()
    if len(rows) <= max_rows:
        return [src]
    parts: list[Path] = []
    for i in range(0, len(rows), max_rows):
        part = out_dir / f"{prefix}_part{len(parts) + 1:02d}.csv"
        with part.open("w", encoding="utf-8", newline="") as out:
            out.write(header)
            out.writelines(rows[i:i + max_rows])
        parts.append(part)
    return parts


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
    # Split into <=25k-line parts (the Import Assistant's hard limit).
    parts = split_csv(child_csv, data, "sanmar_new_children", MAX_ROWS_PER_PART)

    # Pre-flight input: the distinct colours/sizes the NEW children reference.
    # The ensure-values step reads these and creates any the matrix lists don't
    # carry yet -- BEFORE the import, so a child never fails on a missing option.
    used_colors: set[str] = set()
    used_sizes: set[str] = set()
    for style in styles:
        if style.style not in parent_refs:
            continue  # net-new-parent children aren't in the child CSV
        for sku in style.skus:
            if child_external_id(sku.unique_key) in split.child_only_skip:
                continue
            used_colors.add(sku.color_name)
            used_sizes.add(normalize_size(sku.size))
    (data / "sanmar_new_children_colors.txt").write_text(
        "\n".join(sorted(used_colors)) + ("\n" if used_colors else ""), encoding="utf-8"
    )
    (data / "sanmar_new_children_sizes.txt").write_text(
        "\n".join(sorted(used_sizes)) + ("\n" if used_sizes else ""), encoding="utf-8"
    )
    print(f"new children reference {len(used_colors)} distinct colour(s), "
          f"{len(used_sizes)} size(s) -> ensure-values checks/creates these first")

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
    if len(parts) > 1:
        print(f"  split into {len(parts)} part(s) of <= {MAX_ROWS_PER_PART} rows "
              f"(Import Assistant caps a file at 25,000 lines):")
        for p in parts:
            n = sum(1 for _ in p.open(encoding="utf-8")) - 1  # minus header
            print(f"    {p.relative_to(ROOT)}: {n} rows")
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
