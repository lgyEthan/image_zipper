from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event


class CompressionError(Exception):
    """A recoverable error that can be displayed to the user."""


class Cancelled(CompressionError):
    pass


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("작업이 취소되었습니다.")


@dataclass(frozen=True)
class Settings:
    mode: str = "dpi"  # dpi, size, original
    target_dpi: float = 300
    max_bytes: int = 2 * 1024 * 1024
    min_dpi: float = 72
    quality: int = 92
    min_quality: int = 82
    output_format: str = "auto"
    content: str = "photo"  # artwork never introduces a lossy image codec
    reference_dpi: float = 300
    print_width_mm: float | None = None
    jpeg_engine: str = "jpegli"

    def validate(self) -> None:
        if self.mode not in {"dpi", "size", "original"}:
            raise CompressionError("올바른 크기 조절 모드를 선택하세요.")
        if self.output_format not in {"auto", "jpeg", "png", "webp", "avif", "tiff"}:
            raise CompressionError("지원하지 않는 출력 형식입니다.")
        if self.content not in {"photo", "artwork"}:
            raise CompressionError("올바른 이미지 유형을 선택하세요.")
        for value in (self.target_dpi, self.min_dpi, self.reference_dpi):
            if not math.isfinite(value) or not 1 <= value <= 9600:
                raise CompressionError("DPI는 1–9600 사이의 값이어야 합니다.")
        if not 1 <= self.min_quality <= self.quality <= 100:
            raise CompressionError("품질은 1–100이고, 최소 품질보다 높아야 합니다.")
        if self.max_bytes < 1024:
            raise CompressionError("목표 용량은 1 KiB 이상이어야 합니다.")
        if self.print_width_mm is not None and (
            not math.isfinite(self.print_width_mm) or self.print_width_mm <= 0
        ):
            raise CompressionError("인쇄 너비는 0보다 커야 합니다.")
        if self.jpeg_engine not in {"jpegli", "pillow"}:
            raise CompressionError("지원하지 않는 JPEG 인코더입니다.")
        if self.content == "artwork" and self.output_format in {"jpeg", "avif"}:
            raise CompressionError("도표 보존 설정에서는 PNG, TIFF, WebP 또는 자동을 선택하세요.")


@dataclass(frozen=True)
class Asset:
    path: Path
    format: str
    size_bytes: int
    width: int = 0
    height: int = 0
    dpi_x: float | None = None
    dpi_y: float | None = None
    frames: int = 1
    has_alpha: bool = False
    color_mode: str = ""
    page_sizes: tuple[tuple[float, float], ...] = ()
    orientation: int = 1

    @property
    def is_pdf(self) -> bool:
        return self.format == "PDF"

    def inches(self, settings: Settings) -> tuple[float, float]:
        if settings.print_width_mm is not None:
            width = settings.print_width_mm / 25.4
            return width, width * self.height / max(1, self.width)
        return (
            self.width / (self.dpi_x or settings.reference_dpi),
            self.height / (self.dpi_y or settings.reference_dpi),
        )


@dataclass
class Encoded:
    data: bytes
    extension: str
    width: int = 0
    height: int = 0
    dpi_x: float | None = None
    dpi_y: float | None = None
    quality: int | None = None
    warnings: list[str] = field(default_factory=list)
    pages: int = 1
    target_met: bool = True
    details: str = ""

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass
class SavedResult:
    source: Path
    output: Path
    before_bytes: int
    after_bytes: int
    dpi: float | None
    warnings: list[str] = field(default_factory=list)


def human_bytes(value: int | float) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value:.0f} B"
