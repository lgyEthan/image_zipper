from __future__ import annotations

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
    TrialCache,
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
            prefix=".pictumi-", suffix=".tmp", dir=folder, delete=False
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
            f"목표 용량 초과: 최소 픽셀까지 축소한 결과가 {human_bytes(result.size_bytes)}입니다. "
            "파일 구조·색상 프로파일 또는 PDF 보존 요소가 상한보다 큽니다. 용량 상한을 늘려주세요. 파일은 저장하지 않았습니다."
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
    cached: TrialCache | None = None,
) -> list[SavedResult]:
    results = []
    cached_result = cached.read([asset], settings, False) if cached else None
    check_cancel(cancel)
    result = cached_result or measure(asset, settings, cancel)
    require_target(result)
    check_cancel(cancel)
    path = atomic_output(folder, f"{asset.path.stem}_small", result.extension, result.data)
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
    cached: TrialCache | None = None,
) -> SavedResult:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise CompressionError("병합 파일 이름에는 폴더 경로를 넣을 수 없습니다.")
    result = (cached.read(assets, settings, True) if cached else None) or merge_pdf(
        assets, settings, cancel, progress
    )
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
