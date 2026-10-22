"""Measure actual compression time/size without creating output or report files."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--format", choices=["jpeg", "png", "webp", "avif", "pdf"], default="jpeg")
    parser.add_argument("--max-mb", type=float, default=2)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[1] / "src"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    sys.path.insert(0, str(args.source_root.resolve()))
    from image_zipper.imaging import compress_image, probe
    from image_zipper.models import Settings
    from image_zipper.pdf import compress_pdf, merge_pdf

    asset = probe(args.input.resolve())
    settings = Settings(
        mode="size",
        max_bytes=round(args.max_mb * 1_000_000),
        quality=95,
        min_quality=90,
        output_format="auto" if args.format == "pdf" else args.format,
        jpeg_engine="pillow",
    )
    times = []
    for _ in range(args.repeat):
        start = time.perf_counter()
        if asset.is_pdf:
            result = compress_pdf(asset.path, settings)
        elif args.format == "pdf":
            result = merge_pdf([asset], settings)
        else:
            result = compress_image(asset, settings)
        times.append(time.perf_counter() - start)
        print(
            f"{times[-1]:.3f}s, {result.size_bytes:,} bytes, "
            f"{result.size_bytes / settings.max_bytes:.1%} of cap, DPI={result.dpi_x}, "
            f"quality={result.quality}, target_met={result.target_met}"
        )
    print(f"Median: {statistics.median(times):.3f}s ({len(times)} runs)")


if __name__ == "__main__":
    main()
