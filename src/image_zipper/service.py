from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Event

from .imaging import compress_image
from .models import (
    Asset,
    CompressionError,
    Encoded,
    SavedResult,
    Settings,
    check_cancel,
    human_bytes,
)
from .pdf import compress_pdf, merge_pdf


def atomic_output(folder: Path, stem: str, extension: str, data: bytes) -> Path:
    """Reserve a unique name before publishing a fully written file, never overwrite input."""
    folder.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = f"_{index}" if index else ""
        destination = folder / f"{stem}{suffix}{extension}"
        try:
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            break
        except FileExistsError:
            index += 1
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".image-zipper-", suffix=".tmp", dir=folder, delete=False
        ) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if temp:
            temp.unlink(missing_ok=True)
    return destination


def require_target(result: Encoded) -> None:
    if not result.target_met:
        raise CompressionError(
            f"목표 용량 미달: 최소 설정에서 {human_bytes(result.size_bytes)}. "
            "용량 상한을 늘리거나 최소 DPI·품질을 낮추세요. 파일은 저장하지 않았습니다."
        )


def measure(asset: Asset, settings: Settings, cancel: Event | None = None) -> Encoded:
    return (
        compress_pdf(asset.path, settings, cancel)
        if asset.is_pdf
        else compress_image(asset, settings, cancel)
    )


def save_asset(
    asset: Asset,
    settings: Settings,
    folder: Path,
    cancel: Event | None = None,
    on_saved=None,
) -> list[SavedResult]:
    results = []
    for frame in range(1 if asset.is_pdf else asset.frames):
        check_cancel(cancel)
        result = (
            compress_pdf(asset.path, settings, cancel)
            if asset.is_pdf
            else compress_image(asset, settings, cancel, frame)
        )
        require_target(result)
        check_cancel(cancel)
        label = f"_page_{frame + 1:03d}" if asset.frames > 1 and not asset.is_pdf else ""
        path = atomic_output(
            folder, f"{asset.path.stem}{label}_small", result.extension, result.data
        )
        saved = SavedResult(
            asset.path, path, asset.size_bytes, result.size_bytes, result.dpi_x, result.warnings
        )
        results.append(saved)
        if on_saved:
            on_saved(saved)
    return results


def save_merged(
    assets: list[Asset],
    settings: Settings,
    folder: Path,
    name: str,
    cancel: Event | None = None,
    progress=None,
) -> SavedResult:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise CompressionError("병합 파일 이름에는 폴더 경로를 넣을 수 없습니다.")
    result = merge_pdf(assets, settings, cancel, progress)
    require_target(result)
    check_cancel(cancel)
    stem = Path(name).stem if name.lower().endswith(".pdf") else name
    path = atomic_output(folder, stem, ".pdf", result.data)
    return SavedResult(
        assets[0].path,
        path,
        sum(a.size_bytes for a in assets),
        result.size_bytes,
        result.dpi_x,
        result.warnings,
    )


def save_report(folder: Path, results: list[SavedResult], errors: list[str]) -> Path:
    payload = {
        "outputs": [
            {
                "source": str(r.source),
                "output": str(r.output),
                "input_bytes": r.before_bytes,
                "output_bytes": r.after_bytes,
                "effective_dpi": r.dpi,
                "warnings": r.warnings,
            }
            for r in results
        ],
        "errors": errors,
    }
    return atomic_output(
        folder,
        "image_zipper_report",
        ".json",
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )
