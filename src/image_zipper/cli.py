from __future__ import annotations

import argparse
import json
from pathlib import Path

from .imaging import discover, probe
from .models import CompressionError, Settings
from .service import measure, save_asset, save_merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image Zipper — 오프라인 이미지·PDF 압축")
    parser.add_argument("files", type=Path, nargs="+", help="이미지, PDF 또는 폴더")
    parser.add_argument("--output", "-o", type=Path, default=Path("compressed"))
    parser.add_argument("--mode", choices=["dpi", "size", "original"], default="dpi")
    parser.add_argument("--dpi", type=float, default=300)
    parser.add_argument("--max-mib", type=float, default=2)
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--min-quality", type=int, default=82)
    parser.add_argument(
        "--format", choices=["auto", "jpeg", "png", "webp", "avif", "tiff"], default="auto"
    )
    parser.add_argument("--artwork", action="store_true", help="도표를 손실 코덱으로 변환하지 않음")
    parser.add_argument("--reference-dpi", type=float, default=300)
    parser.add_argument("--print-width-mm", type=float)
    parser.add_argument("--jpeg-engine", choices=["jpegli", "pillow"], default="jpegli")
    parser.add_argument(
        "--strip-metadata",
        action="store_true",
        help="촬영·문서 부가 정보 삭제 (색상·방향·DPI 유지)",
    )
    parser.add_argument("--merge", metavar="NAME.pdf", help="선택한 순서로 PDF 병합")
    parser.add_argument(
        "--estimate", action="store_true", help="실제 시험 압축으로 용량과 DPI만 출력"
    )
    args = parser.parse_args(argv)
    settings = Settings(
        mode=args.mode,
        target_dpi=args.dpi,
        max_bytes=int(args.max_mib * 1024 * 1024),
        quality=args.quality,
        min_quality=args.min_quality,
        output_format=args.format,
        content="artwork" if args.artwork else "photo",
        reference_dpi=args.reference_dpi,
        print_width_mm=args.print_width_mm,
        jpeg_engine=args.jpeg_engine,
        strip_metadata=args.strip_metadata,
    )
    results, errors, assets = [], [], []
    try:
        settings.validate()
        for path in discover(args.files):
            try:
                assets.append(probe(path))
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
        if not assets:
            raise CompressionError("처리 가능한 파일이 없습니다.")
        if args.merge:
            if errors:
                raise CompressionError("병합 입력을 읽지 못했습니다: " + "; ".join(errors))
            if args.estimate:
                from .pdf import merge_pdf

                estimate = merge_pdf(assets, settings)
                print(
                    json.dumps(
                        {
                            "bytes": estimate.size_bytes,
                            "dpi_min": estimate.dpi_x,
                            "dpi_max": estimate.dpi_y,
                            "target_met": estimate.target_met,
                            "warnings": estimate.warnings,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0 if estimate.target_met else 1
            results.append(save_merged(assets, settings, args.output, args.merge))
        else:
            for asset in assets:
                try:
                    if args.estimate:
                        result = measure(asset, settings)
                        print(
                            json.dumps(
                                {
                                    "file": str(asset.path),
                                    "bytes": result.size_bytes,
                                    "dpi": result.dpi_x,
                                    "target_met": result.target_met,
                                    "scope": "whole file",
                                    "warnings": result.warnings,
                                },
                                ensure_ascii=False,
                            )
                        )
                        if not result.target_met:
                            errors.append(f"{asset.path.name}: 목표 용량 미달")
                    else:
                        save_asset(asset, settings, args.output, on_saved=results.append)
                except Exception as exc:
                    errors.append(f"{asset.path.name}: {exc}")
        if not args.estimate:
            for result in results:
                print(f"저장: {result.output}")
                for warning in result.warnings:
                    print(f"  {warning}")
            print(f"{len(results)}개 저장 · {len(errors)}개 오류")
    except (CompressionError, OSError, ValueError) as exc:
        errors.append(str(exc))
    for error in errors:
        print(f"오류: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
