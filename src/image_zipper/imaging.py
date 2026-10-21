from __future__ import annotations

import io
import math
import os
import struct
import warnings
from collections.abc import Iterator
from pathlib import Path
from threading import Event

import oxipng
import pillow_heif
import pyjpegli
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .models import Asset, CompressionError, Encoded, Settings, check_cancel

pillow_heif.register_heif_opener(thumbnails=False)

INPUT_EXTENSIONS = frozenset(
    ".png .jpg .jpeg .jpe .heic .heif .tif .tiff .pdf .bmp .dib .webp .avif "
    ".gif .apng .ico .ppm .pgm .pbm .pnm .tga .jp2 .j2k .jpf .pcx .dds".split()
)
EXTENSIONS = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "tiff": ".tif"}


def metadata_exif(im: Image.Image) -> Image.Exif:
    """Read PNG EXIF without PngImageFile.getexif(), which decodes every pixel.

    eXIf may follow IDAT. Seek over compressed pixels instead of decoding them,
    preserving orientation and physical size even for those legal PNGs.
    """
    if im.format == "PNG" and "exif" not in im.info and im.fp is not None:
        position = im.fp.tell()
        try:
            im.fp.seek(8)
            while header := im.fp.read(8):
                if len(header) != 8:
                    break
                length, kind = struct.unpack(">I4s", header)
                if kind == b"eXIf" and length <= 1024 * 1024:
                    im.info["exif"] = im.fp.read(length)
                    break
                if kind == b"IEND":
                    break
                im.fp.seek(length + 4, 1)
        finally:
            im.fp.seek(position)
    return Image.Image.getexif(im)


def read_dpi(im: Image.Image, exif: Image.Exif | None = None) -> tuple[float | None, float | None]:
    if im.format == "TIFF" and (
        im.tag_v2.get(296) not in (2, 3) or 282 not in im.tag_v2 or 283 not in im.tag_v2
    ):
        return None, None
    dpi = im.info.get("dpi")
    if not dpi:
        exif = metadata_exif(im) if exif is None else exif
        if 282 in exif and 283 in exif:
            factor = 2.54 if exif.get(296) == 3 else 1
            if exif.get(296, 2) in (2, 3):
                dpi = (float(exif[282]) * factor, float(exif[283]) * factor)
    try:
        x, y = map(float, dpi)
        if all(math.isfinite(v) and 1 <= v <= 9600 for v in (x, y)):
            return x, y
    except (ValueError, TypeError, OverflowError):
        pass
    return None, None


def image_asset(path: Path, frame: int = 0) -> Asset:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as im:
            im.seek(frame)
            width, height = im.size
            exif = metadata_exif(im)
            dx, dy = read_dpi(im, exif)
            if exif.get(274) in (5, 6, 7, 8):
                width, height, dx, dy = height, width, dy, dx
            return Asset(
                path=path,
                format=im.format or path.suffix.upper().lstrip("."),
                size_bytes=path.stat().st_size,
                width=width,
                height=height,
                dpi_x=dx,
                dpi_y=dy,
                frames=getattr(im, "n_frames", 1),
                has_alpha="A" in im.getbands() or "transparency" in im.info,
                color_mode=im.mode,
                orientation=int(exif.get(274, 1)),
            )


def probe(path: str | Path) -> Asset:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise CompressionError(f"파일을 찾을 수 없습니다: {path.name}")
    with path.open("rb") as source:
        is_pdf = b"%PDF-" in source.read(1024)
    if is_pdf:
        from .pdf import probe_pdf

        return probe_pdf(path)
    try:
        return image_asset(path)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise CompressionError(f"이미지를 읽을 수 없습니다: {path.name} ({exc})") from exc


def discover(paths: list[Path]) -> Iterator[Path]:
    """Stream files as each directory is read, without following directory symlinks."""
    seen = set()

    def walk(root: Path):
        # Sort each directory, not the entire tree: the first files arrive immediately.
        with os.scandir(root) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            if entry.is_file() and Path(entry.name).suffix.lower() in INPUT_EXTENSIONS:
                yield Path(entry.path)
        for entry in children:
            if entry.is_dir(follow_symlinks=False):
                yield from walk(Path(entry.path))

    for item in paths:
        candidates = walk(item) if item.is_dir() else [item]
        for path in candidates:
            resolved = path.resolve()
            if path.is_file() and resolved not in seen:
                seen.add(resolved)
                yield resolved


def load_image(asset: Asset, frame: int = 0) -> tuple[Image.Image, list[str]]:
    notes: list[str] = []
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(asset.path) as source:
            source.seek(frame)
            source.load()
            im = ImageOps.exif_transpose(source)
    if im.mode == "P":
        im = im.convert("RGBA" if asset.has_alpha else "RGB")
    if asset.format in {"HEIF", "HEIC", "AVIF"}:
        # Pillow's public pipeline is 8-bit; never imply HDR preservation.
        notes.append(
            "HEIC/AVIF는 8비트 SDR 경로로 처리합니다. HDR·보조 깊이 정보는 보존하지 않습니다."
        )
    if im.mode in {"I", "F"} or im.mode.startswith("I;16"):
        notes.append(
            "고비트 심도 이미지입니다. PNG/TIFF 원본 해상도 출력만 비트 심도를 보존합니다."
        )
    return im, notes


def select_format(asset: Asset, settings: Settings) -> str:
    if settings.output_format != "auto":
        return settings.output_format
    if settings.content == "artwork" or asset.has_alpha:
        return "png"
    if asset.color_mode.startswith("I") or asset.color_mode in {"F", "CMYK"}:
        return "tiff"
    if asset.format == "PNG":
        return "png"
    return "jpeg"


def resize_to(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    if im.size == size:
        return im
    if im.mode == "1":
        # Bilevel scans remain bilevel: antialiasing would change their semantics.
        return im.resize(size, Image.Resampling.NEAREST)
    if im.mode in {"I", "F"} or im.mode.startswith("I;16"):
        raise CompressionError(
            "고비트 심도 이미지의 해상도 변경은 지원하지 않습니다. 원본 해상도와 TIFF/PNG를 선택하세요."
        )
    return im.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)


def _rgb(im: Image.Image) -> Image.Image:
    """Convert unsupported color models using ICC, keeping the resulting profile consistent."""
    if im.mode in {"RGB", "RGBA", "L", "LA", "1"}:
        return im
    if im.mode == "CMYK":
        icc = im.info.get("icc_profile")
        if icc:
            try:
                srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                converted = ImageCms.profileToProfile(
                    im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB"
                )
                converted.info["icc_profile"] = srgb.tobytes()
                return converted
            except (ImageCms.PyCMSError, OSError) as exc:
                raise CompressionError(
                    "CMYK 색상 프로파일을 변환할 수 없습니다. TIFF 출력을 사용하세요."
                ) from exc
        raise CompressionError(
            "ICC 프로파일 없는 CMYK입니다. 색 보존을 위해 TIFF 출력을 사용하세요."
        )
    raise CompressionError(
        "이 비트 심도는 선택한 형식으로 보존할 수 없습니다. TIFF 또는 PNG를 선택하세요."
    )


def _exif(dpi: tuple[float, float]) -> bytes:
    # Only physical size is copied; stale orientation, thumbnails and dimensions are removed.
    exif = Image.Exif()
    exif[282], exif[283], exif[296] = dpi[0], dpi[1], 2
    return exif.tobytes()


def jpeg_metadata(data: bytes, dpi: tuple[float, float], icc: bytes | None) -> bytes:
    """Insert metadata without a second JPEG encode (APP markers do not touch pixels)."""

    def marker(number: int, payload: bytes) -> bytes:
        return b"\xff" + bytes([number]) + struct.pack(">H", len(payload) + 2) + payload

    metadata = marker(0xE1, _exif(dpi))
    if icc:
        chunks = [icc[i : i + 65519] for i in range(0, len(icc), 65519)]
        if len(chunks) > 255:
            raise CompressionError("ICC 프로파일이 JPEG에 저장하기에 너무 큽니다.")
        metadata += b"".join(
            marker(0xE2, b"ICC_PROFILE\0" + bytes([i + 1, len(chunks)]) + chunk)
            for i, chunk in enumerate(chunks)
        )
    # Patch the encoder's JFIF density, which some readers prefer over EXIF.
    result = bytearray(data)
    position = 2
    while position + 4 <= len(result) and result[position] == 0xFF:
        kind = result[position + 1]
        if kind in {0xDA, 0xD9}:
            break
        length = int.from_bytes(result[position + 2 : position + 4], "big")
        if length < 2:
            break
        if kind == 0xE0 and result[position + 4 : position + 9] == b"JFIF\0":
            result[position + 11] = 1
            result[position + 12 : position + 16] = struct.pack(
                ">HH", *(max(1, min(65535, round(v))) for v in dpi)
            )
            break
        position += length + 2
    return bytes(result[:2]) + metadata + bytes(result[2:])


def encode_pixels(
    im: Image.Image,
    fmt: str,
    quality: int,
    dpi: tuple[float, float],
    settings: Settings,
    *,
    optimize_png: bool = True,
) -> bytes:
    if fmt != "tiff" and not (fmt == "png" and im.mode.startswith(("I", "L"))):
        im = _rgb(im)
    if fmt == "png" and im.mode == "F":
        raise CompressionError("부동소수점 이미지는 TIFF로 저장하세요.")
    if fmt == "png" and im.mode == "I":
        low, high = im.getextrema()
        if low < 0 or high > 65535:
            raise CompressionError("16비트 범위를 넘는 정수 이미지는 TIFF로 저장하세요.")
    if fmt in {"jpeg", "avif", "webp"} and im.mode not in {"RGB", "RGBA", "L", "LA", "1"}:
        raise CompressionError("고비트 심도 이미지는 TIFF/PNG로 저장하세요.")
    icc = im.info.get("icc_profile")
    if im.mode in {"L", "LA"} and icc and fmt in {"webp", "avif"}:
        try:
            srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
            alpha = im.getchannel("A") if im.mode == "LA" else None
            im = ImageCms.profileToProfile(
                im.convert("L"), ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB"
            )
            if alpha is not None:
                im.putalpha(alpha)
            icc = srgb.tobytes()
        except (ImageCms.PyCMSError, OSError) as exc:
            raise CompressionError(
                "회색조 프로파일을 변환할 수 없습니다. PNG/TIFF를 사용하세요."
            ) from exc
    if fmt == "jpeg":
        if "A" in im.getbands():
            rgba = im.convert("RGBA")
            background = Image.new("RGB", im.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            im = background
        if settings.jpeg_engine == "jpegli" and im.mode not in {"CMYK", "L", "1"}:
            rgb = im.convert("RGB")
            data = pyjpegli.encode(rgb.tobytes(), rgb.width, rgb.height, quality=quality)
            return jpeg_metadata(data, dpi, icc)
    stream = io.BytesIO()
    kwargs = {"icc_profile": icc} if icc else {}
    if fmt in {"jpeg", "webp", "avif"}:
        kwargs["exif"] = _exif(dpi)
    if fmt == "jpeg":
        im = im.convert("L" if im.mode in {"1", "L"} else "RGB")
        kwargs.update(quality=quality, optimize=True, progressive=True, subsampling=0, dpi=dpi)
    elif fmt == "png":
        kwargs.update(optimize=True, compress_level=9, dpi=dpi)
    elif fmt == "webp":
        kwargs.update(quality=quality, method=6, lossless=settings.content == "artwork", exact=True)
    elif fmt == "avif":
        kwargs.update(quality=quality, speed=6, subsampling="4:4:4", max_threads=2)
    elif fmt == "tiff":
        kwargs.update(compression="group4" if im.mode == "1" else "tiff_adobe_deflate", dpi=dpi)
    im.save(stream, format={"tiff": "TIFF", "jpeg": "JPEG"}.get(fmt, fmt.upper()), **kwargs)
    data = stream.getvalue()
    if fmt == "png" and optimize_png:
        optimized = oxipng.optimize_from_memory(data, level=2, optimize_alpha=False, timeout=5)
        if len(optimized) < len(data):
            data = optimized
    return data


def compress_image(
    asset: Asset, settings: Settings, cancel: Event | None = None, frame: int = 0
) -> Encoded:
    settings.validate()
    check_cancel(cancel)
    if frame:
        asset = image_asset(asset.path, frame)
    fmt = select_format(asset, settings)
    original_format = {
        "JPEG": "jpeg",
        "PNG": "png",
        "WEBP": "webp",
        "AVIF": "avif",
        "TIFF": "tiff",
    }.get(asset.format)
    if (
        settings.mode == "size"
        and asset.size_bytes <= settings.max_bytes
        and asset.frames == 1
        and asset.orientation == 1
        and original_format == fmt
        and settings.print_width_mm is None
    ):
        inches = asset.inches(settings)
        return Encoded(
            asset.path.read_bytes(),
            EXTENSIONS[fmt],
            asset.width,
            asset.height,
            asset.width / inches[0],
            asset.height / inches[1],
            details=f"{fmt.upper()} · 이미 용량 상한 이내여서 원본 데이터 유지",
        )
    im, notes = load_image(asset, frame)
    try:
        if asset.has_alpha and fmt == "jpeg":
            notes.append("JPEG는 투명도를 지원하지 않아 흰 배경으로 저장합니다.")
        if asset.dpi_x is None and settings.print_width_mm is None:
            notes.append(
                f"원본 DPI 정보 없음: {settings.reference_dpi:g} DPI를 인쇄 크기의 기준으로 가정했습니다."
            )
        inches = asset.inches(settings)
        max_scale = 1.0
        if settings.mode == "dpi":
            max_scale = min(
                1.0,
                settings.target_dpi * inches[0] / im.width,
                settings.target_dpi * inches[1] / im.height,
            )
        lossy = fmt in {"jpeg", "avif"} or (fmt == "webp" and settings.content == "photo")
        # A byte budget is independent of target DPI and presets. Search down to
        # the smallest representable image; effective DPI follows pixel count.
        min_scale = 1 / max(im.size)

        def candidate(scale: float, quality: int) -> Encoded:
            check_cancel(cancel)
            size = (
                max(1, math.floor(im.width * scale + 1e-7)),
                max(1, math.floor(im.height * scale + 1e-7)),
            )
            resized = resize_to(im, size)
            dpi = size[0] / inches[0], size[1] / inches[1]
            try:
                data = encode_pixels(
                    resized, fmt, quality, dpi, settings, optimize_png=settings.mode != "size"
                )
            finally:
                if resized is not im:
                    resized.close()
            return Encoded(
                data,
                EXTENSIONS[fmt],
                *size,
                *dpi,
                quality if lossy else None,
                list(notes),
                details=f"{fmt.upper()} · {settings.jpeg_engine if fmt == 'jpeg' else 'lossless' if not lossy else 'lossy'}",
            )

        result = candidate(max_scale, settings.quality)
        if (
            asset.frames == 1
            and asset.orientation == 1
            and original_format == fmt
            and (result.width, result.height) == (asset.width, asset.height)
            and settings.print_width_mm is None
            and settings.mode in {"original", "size"}
            and asset.size_bytes <= result.size_bytes
            and (settings.mode != "size" or asset.size_bytes <= settings.max_bytes)
        ):
            result.data = asset.path.read_bytes()
            result.quality = None
            result.details = f"{fmt.upper()} · 기존 파일이 더 효율적이어서 원본 데이터 유지"
        if settings.mode == "size" and result.size_bytes > settings.max_bytes:
            # Resolution first: retain the largest image that fits at the quality floor,
            # then spend any remaining bytes on quality. Each trial uses original pixels.
            low_quality = settings.min_quality if lossy else settings.quality
            at_floor = candidate(1.0, low_quality) if lossy else result
            if at_floor.size_bytes <= settings.max_bytes:
                best, scale = at_floor, 1.0
            else:
                at_min = candidate(min_scale, low_quality)
                if at_min.size_bytes > settings.max_bytes:
                    at_min.target_met = False
                    at_min.warnings.append(
                        "1픽셀까지 축소해도 파일 헤더·색상 프로파일이 용량 상한보다 큽니다. 용량 상한을 늘려주세요."
                    )
                    return at_min
                best, scale, lo, hi = at_min, min_scale, min_scale, 1.0
                for _ in range(8):
                    mid = (lo + hi) / 2
                    trial = candidate(mid, low_quality)
                    if trial.size_bytes <= settings.max_bytes:
                        best, scale, lo = trial, mid, mid
                    else:
                        hi = mid
            if lossy:
                lo_q, hi_q = low_quality + 1, settings.quality
                while lo_q <= hi_q:
                    q = (lo_q + hi_q) // 2
                    trial = candidate(scale, q)
                    if trial.size_bytes <= settings.max_bytes:
                        best, lo_q = trial, q + 1
                    else:
                        hi_q = q - 1
            result = best
        if settings.mode == "size" and fmt == "png":
            check_cancel(cancel)
            optimized = oxipng.optimize_from_memory(
                result.data, level=2, optimize_alpha=False, timeout=5
            )
            if len(optimized) < result.size_bytes:
                result.data = optimized
        if min(result.dpi_x, result.dpi_y) < 299.5:
            result.warnings.append(
                "출력 해상도가 300 DPI 미만입니다. 인쇄 크기와 제출 기준을 확인하세요."
            )
        if settings.mode == "dpi" and min(result.dpi_x, result.dpi_y) + 1 < settings.target_dpi:
            result.warnings.append(
                "원본 해상도가 낮아 목표 DPI에 미달합니다. 확대 보간은 하지 않았습니다."
            )
        if asset.frames == 1 and result.size_bytes > asset.size_bytes:
            result.warnings.append(
                "지정한 형식·품질의 결과가 원본보다 큽니다. 다른 형식이나 용량 상한을 선택할 수 있습니다."
            )
        check_cancel(cancel)
        return result
    finally:
        im.close()


def quick_estimate(asset: Asset, settings: Settings) -> tuple[float | None, int, str]:
    """Immediate heuristic; exact encoding is only performed on explicit request."""
    if asset.is_pdf:
        return (
            None,
            settings.max_bytes if settings.mode == "size" else asset.size_bytes,
            "PDF 내부 이미지 분석 필요",
        )
    width_in, height_in = asset.inches(settings)
    dpi = min(asset.width / width_in, asset.height / height_in)
    if settings.mode == "dpi":
        ratio = min(1.0, settings.target_dpi / dpi)
    elif settings.mode == "size":
        ratio = min(1.0, math.sqrt(settings.max_bytes / max(1, asset.size_bytes)))
    else:
        ratio = 1.0
    return (
        dpi * ratio,
        int(asset.size_bytes * ratio * ratio),
        "빠른 추정 · 실제 결과와 다를 수 있음",
    )


def thumbnail(asset: Asset, edge: int = 1000) -> bytes:
    if asset.is_pdf:
        from .pdf import render_preview

        return render_preview(asset.path, edge=edge)
    with Image.open(asset.path) as source:
        # Pillow uses JPEG draft/shrink-on-load before decoding. Transpose only
        # the thumbnail, avoiding two full-resolution copies of the original.
        source.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=2.0)
        im = ImageOps.exif_transpose(source)
        if im.mode not in {"RGB", "RGBA", "L"}:
            converted = im.convert("RGBA" if asset.has_alpha else "RGB")
            im.close()
            im = converted
        stream = io.BytesIO()
        im.save(stream, "PNG", compress_level=1)
        im.close()
        return stream.getvalue()


def preview_encoded(result: Encoded, edge: int = 1000) -> bytes:
    if result.extension == ".pdf":
        from .pdf import render_preview

        return render_preview(result.data, edge=edge)
    with Image.open(io.BytesIO(result.data)) as im:
        im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        im.convert("RGBA").save(stream, "PNG")
        return stream.getvalue()
