"""Find the real CDN base for SanMar's bare-filename colour swatches -- read-only.

The SDL feed carries 26,090 ``color_swatch_url`` values that are BARE filenames
('29Msw.jpg') with no path, unlike the flat/model images which arrive as
relative paths. Guessing a base and writing it onto 26k items is exactly the
2026-08-05 zero-write failure mode, so this probe tests each candidate base
against real filenames sampled from today's feed and reports which one serves
actual images. Andy's browser test (2026-08-10) showed
``cdnm.sanmar.com/swatch/gifs/`` redirecting to the ImageNotAvailable
placeholder on marketing.sanmar.com -- which is itself the hint for the
marketing.sanmar.com candidates below.

Success = HTTP 200, an image/* content type, a real body (>500 bytes), and a
final URL that is NOT the placeholder (SanMar redirects misses to
``.../ImageNotAvailable.jpg`` and answers 200 for it).
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sanmar_netsuite.config import get_config
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp

CANDIDATE_BASES = [
    "https://marketing.sanmar.com/catalog/images/",
    "https://cdnm.sanmar.com/catalog/images/",
    "https://cdnm.sanmar.com/swatch/gifs/",
    "https://cdnm.sanmar.com/swatch/",
    "https://cdnm.sanmar.com/imglib/swatch/",
    "https://www.sanmar.com/cs/images/products/swatch/",
]

SAMPLES = 8


def _resolve_feed(config) -> Path:
    local = Path(config.sftp.download_dir) / C.FILE_SDL_N
    if local.exists():
        return local
    return SanMarSftp(config.sftp).download(C.FILE_SDL_N)


def main() -> int:
    cfg = get_config()
    styles = parse_styles(str(_resolve_feed(cfg)))

    # Distinct bare-filename swatch values, sampled across the alphabet rather
    # than the first N (one style's colours would all hit the same directory).
    bare = sorted({
        (sku.color_swatch_url or "").strip()
        for s in styles for sku in s.skus
        if (sku.color_swatch_url or "").strip()
        and "/" not in (sku.color_swatch_url or "")
        and not (sku.color_swatch_url or "").lower().startswith("http")
    })
    if not bare:
        print("feed carries no bare-filename swatch values -- nothing to probe")
        return 0
    step = max(1, len(bare) // SAMPLES)
    sample = bare[::step][:SAMPLES]
    print(f"{len(bare)} distinct bare swatch filename(s); probing {len(sample)}: {sample}\n")

    best: tuple[int, str] | None = None
    for base in CANDIDATE_BASES:
        hits = 0
        detail: list[str] = []
        for name in sample:
            url = base + name
            try:
                r = requests.get(url, timeout=20, allow_redirects=True)
                ctype = r.headers.get("content-type", "")
                placeholder = "imagenotavailable" in r.url.lower()
                ok = (r.status_code == 200 and ctype.startswith("image/")
                      and len(r.content) > 500 and not placeholder)
            except Exception as exc:  # noqa: BLE001 - a dead host is a result
                detail.append(f"    {name}: ERROR {str(exc)[:80]}")
                continue
            if ok:
                hits += 1
            else:
                why = "placeholder" if placeholder else f"{r.status_code} {ctype} {len(r.content)}B"
                detail.append(f"    {name}: {why}")
        print(f"{base}  ->  {hits}/{len(sample)} real image(s)")
        for line in detail[:3]:
            print(line)
        if best is None or hits > best[0]:
            best = (hits, base)

    print("")
    if best and best[0] == len(sample):
        print(f"WINNER: {best[1]} served every sample -- safe to wire as the swatch base")
    elif best and best[0] > 0:
        print(f"PARTIAL: {best[1]} served {best[0]}/{len(sample)} -- "
              f"some swatches may simply not exist; review before wiring")
    else:
        print("NO candidate served a single real image -- do not wire any base; "
              "the filenames may need a transform (e.g. style-scoped paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
