from __future__ import annotations

import io
import math
import os
import struct
import warnings
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from threading import Event, RLock

import oxipng
import pillow_heif
import pyjpegli
from PIL import Image, ImageFile, ImageOps, PngImagePlugin, TiffImagePlugin, UnidentifiedImageError

from .budget import fit_quality, fit_scale
from .edits import apply_edits
from .models import Asset, CompressionError, Encoded, Settings, check_cancel

pillow_heif.register_heif_opener(thumbnails=False)
JPEG_BUFFER_LOCK = RLock()

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
            if source.format == "PNG":
                im.info["_png_text"] = dict(source.text)
            if source.format == "JPEG":
                im.info["_jpeg_extra"] = [
                    (name, data) for name, data in source.applist if name in {"APP13", "COM"}
                ]
            if source.format == "TIFF":
                # Descriptive tags must not disappear when recompressing a TIFF.
                im.info["_tiff_tags"] = {
                    tag: source.tag_v2[tag]
                    for tag in (269, 270, 271, 272, 285, 305, 306, 315, 316, 33432, 33723, 700)
                    if tag in source.tag_v2
                }
    if im.mode == "P":
        im = im.convert("RGBA" if asset.has_alpha else "RGB")
    if asset.edits.active:
        edited = apply_edits(im, asset.edits)
        if edited is not im:
            im.close()
            im = edited
        if asset.edits.mosaics:
            notes.append(
                "모자이크 원본 미리보기가 남지 않도록 촬영·위치 등 부가 메타데이터를 삭제했습니다."
            )
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
    if asset.format == "TIFF":
        return "tiff"
    if asset.edits.angle and not asset.edits.crop:
        return "png"
    if asset.format == "WEBP":
        return "webp"
    if settings.content == "artwork" or asset.has_alpha:
        return "png"
    if asset.color_mode.startswith("I") or asset.color_mode in {"F", "CMYK"}:
        return "tiff"
    if asset.format == "PNG":
        return "png"
    if asset.format == "AVIF":
        return "avif"
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
        raise CompressionError("CMYK 색 공간을 그대로 보존하려면 TIFF 출력을 사용하세요.")
    raise CompressionError(
        "이 비트 심도는 선택한 형식으로 보존할 수 없습니다. TIFF 또는 PNG를 선택하세요."
    )


def _exif(dpi: tuple[float, float], im: Image.Image | None = None) -> bytes:
    exif = Image.Exif()
    if im is not None:
        original = im.info.get("exif")
        if original:
            exif.load(original)
        # These fields describe the newly sampled pixels; capture metadata stays.
        if 274 in exif:
            exif[274] = 1
        for tag, value in ((256, im.width), (257, im.height)):
            if tag in exif:
                exif[tag] = value
        if 34665 in exif:
            details = dict(exif.get_ifd(34665))
            for tag, value in ((40962, im.width), (40963, im.height)):
                if tag in details:
                    details[tag] = value
            exif[34665] = details
    exif[282], exif[283], exif[296] = dpi[0], dpi[1], 2
    return exif.tobytes()


def jpeg_metadata(
    data: bytes, dpi: tuple[float, float], icc: bytes | None, im: Image.Image | None = None
) -> bytes:
    """Insert metadata without a second JPEG encode (APP markers do not touch pixels)."""

    def marker(number: int, payload: bytes) -> bytes:
        if len(payload) > 65533:
            raise CompressionError("메타데이터가 JPEG 한 구간에 저장하기에 너무 큽니다.")
        return b"\xff" + bytes([number]) + struct.pack(">H", len(payload) + 2) + payload

    metadata = marker(0xE1, _exif(dpi, im))
    if im is not None:
        xmp = im.info.get("xmp")
        if xmp:
            metadata += marker(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + xmp)
        for name, payload in im.info.get("_jpeg_extra", ()):
            metadata += marker(0xED if name == "APP13" else 0xFE, payload)
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
        raise CompressionError("회색조 색상 프로파일을 그대로 보존하려면 PNG/TIFF를 사용하세요.")
    if fmt == "jpeg":
        if "A" in im.getbands():
            raise CompressionError(
                "JPEG는 투명도를 보존할 수 없습니다. PNG, WebP 또는 TIFF를 선택하세요."
            )
        if settings.jpeg_engine == "jpegli" and im.mode not in {"CMYK", "L", "1"}:
            rgb = im.convert("RGB")
            data = pyjpegli.encode(rgb.tobytes(), rgb.width, rgb.height, quality=quality)
            return jpeg_metadata(data, dpi, icc, None if settings.strip_metadata else im)
    stream = io.BytesIO()
    kwargs = {"icc_profile": icc} if icc else {}
    if fmt in {"jpeg", "webp", "avif", "png"}:
        kwargs["exif"] = _exif(dpi, None if settings.strip_metadata else im)
    if fmt in {"jpeg", "webp", "avif"}:
        kwargs["xmp"] = b"" if settings.strip_metadata else im.info.get("xmp", b"")
    if fmt == "jpeg":
        im = im.convert("L" if im.mode in {"1", "L"} else "RGB")
        kwargs.update(quality=quality, optimize=True, progressive=False, subsampling=0, dpi=dpi)
        extra = () if settings.strip_metadata else im.info.get("_jpeg_extra", ())
        if settings.strip_metadata:
            kwargs["comment"] = b""
        kwargs["extra"] = b"".join(
            b"\xff"
            + (b"\xed" if name == "APP13" else b"\xfe")
            + struct.pack(">H", len(payload) + 2)
            + payload
            for name, payload in extra
        )
    elif fmt == "png":
        metadata = PngImagePlugin.PngInfo()
        for name, value in (
            {} if settings.strip_metadata else im.info.get("_png_text", {})
        ).items():
            if isinstance(value, PngImagePlugin.iTXt):
                metadata.add_itxt(name, value, value.lang, value.tkey)
            else:
                metadata.add_text(name, value)
        if im.info.get("xmp") and not settings.strip_metadata:
            metadata.add_itxt("XML:com.adobe.xmp", im.info["xmp"].decode("utf-8"))
        for name, value in (("gamma", "gAMA"), ("srgb", "sRGB"), ("chromaticity", "cHRM")):
            if name not in im.info or (icc and name == "srgb"):
                continue
            number = im.info[name]
            payload = (
                bytes([number])
                if name == "srgb"
                else struct.pack(">I", round(number * 100000))
                if name == "gamma"
                else struct.pack(">8I", *(round(n * 100000) for n in number))
            )
            metadata.add(value.encode(), payload)
        kwargs["pnginfo"] = metadata
        kwargs.update(optimize=False, compress_level=3, dpi=dpi)
    elif fmt == "webp":
        kwargs.update(quality=quality, method=4, lossless=settings.content == "artwork", exact=True)
    elif fmt == "avif":
        # Pillow exposes one quality setting for color and alpha. Keep alpha
        # lossless rather than silently changing transparency at low quality.
        if "A" in im.getbands():
            quality = 100
        kwargs.update(quality=quality, speed=6, subsampling="4:4:4", max_threads=2)
    elif fmt == "tiff":
        kwargs["tiffinfo"] = {} if settings.strip_metadata else im.info.get("_tiff_tags", {})
        kwargs.update(compression="group4" if im.mode == "1" else "tiff_adobe_deflate", dpi=dpi)
    if fmt == "jpeg":
        # Pillow underestimates the optimized 4:4:4 buffer for detailed photos.
        # Scope the larger buffer to this encode, including concurrent API callers.
        with JPEG_BUFFER_LOCK:
            previous = ImageFile.MAXBLOCK
            try:
                ImageFile.MAXBLOCK = max(previous, im.width * im.height * 4)
                im.save(stream, "JPEG", **kwargs)
            finally:
                ImageFile.MAXBLOCK = previous
    else:
        im.save(stream, format={"tiff": "TIFF"}.get(fmt, fmt.upper()), **kwargs)
    data = stream.getvalue()
    if fmt == "png" and optimize_png:
        optimized = oxipng.optimize_from_memory(data, level=1, optimize_alpha=False, timeout=3)
        if len(optimized) < len(data):
            data = optimized
    return data


def compress_image(asset: Asset, settings: Settings, cancel: Event | None = None) -> Encoded:
    settings.validate()
    check_cancel(cancel)
    if asset.edits.active and asset.frames > 1:
        raise CompressionError("편집은 단일 이미지에만 적용할 수 있습니다.")
    if asset.edits.mosaics:
        # Camera maker notes / XMP can embed an unredacted original thumbnail.
        settings = replace(settings, strip_metadata=True)
    if asset.frames > 1:
        if asset.format != "TIFF":
            raise CompressionError(
                "움직임이나 여러 이미지를 잃지 않도록 이 다중 프레임 파일의 변환을 중단했습니다."
            )
        return _compress_multipage_tiff(asset, settings, cancel)
    return _compress_image_frame(asset, settings, cancel)


def _compress_multipage_tiff(asset: Asset, settings: Settings, cancel) -> Encoded:
    if settings.output_format not in {"auto", "tiff"}:
        raise CompressionError(
            "여러 페이지를 한 파일에 유지하려면 TIFF를 선택하세요. PDF로 합치기도 가능합니다."
        )
    if (
        settings.mode == "size"
        and not settings.strip_metadata
        and not asset.edits.active
        and asset.size_bytes <= settings.max_bytes
        and settings.print_width_mm is None
    ):
        return Encoded(
            asset.path.read_bytes(),
            ".tif",
            asset.width,
            asset.height,
            asset.dpi_x,
            asset.dpi_y,
            pages=asset.frames,
            details="TIFF · 원본 데이터 유지",
        )
    frames = []
    notes = []
    try:
        for index in range(asset.frames):
            check_cancel(cancel)
            current = image_asset(asset.path, index)
            im, warnings = load_image(current, index)
            frames.append((im, current.inches(settings)))
            notes.extend(warnings)

        def candidate(scale):
            buffer = io.BytesIO()
            dimensions, dpis = [], []
            with TiffImagePlugin.AppendingTiffWriter(buffer) as writer:
                for im, inches in frames:
                    check_cancel(cancel)
                    factor = (
                        min(
                            1.0,
                            settings.target_dpi * inches[0] / im.width,
                            settings.target_dpi * inches[1] / im.height,
                        )
                        if settings.mode == "dpi"
                        else scale
                    )
                    size = tuple(max(1, math.floor(v * factor + 1e-7)) for v in im.size)
                    resized = resize_to(im, size)
                    dpi = size[0] / inches[0], size[1] / inches[1]
                    try:
                        writer.write(encode_pixels(resized, "tiff", 100, dpi, settings))
                        writer.newFrame()
                    finally:
                        if resized is not im:
                            resized.close()
                    dimensions.append(size)
                    dpis.extend(dpi)
            return Encoded(
                buffer.getvalue(),
                ".tif",
                *dimensions[0],
                min(dpis),
                max(dpis),
                warnings=list(dict.fromkeys(notes)),
                pages=len(frames),
                details=f"TIFF · {len(frames)}쪽 유지 · 무손실 코덱",
            )

        result = candidate(1.0)
        if settings.mode == "size":
            result, _ = fit_scale(
                candidate, result, settings.max_bytes, 1 / max(max(im.size) for im, _ in frames)
            )
        return result
    finally:
        for im, _ in frames:
            im.close()


def _compress_image_frame(asset: Asset, settings: Settings, cancel: Event | None = None) -> Encoded:
    settings.validate()
    check_cancel(cancel)
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
        and not settings.strip_metadata
        and not asset.edits.active
        and asset.size_bytes <= settings.max_bytes
        and asset.frames == 1
        and original_format == fmt
        and settings.print_width_mm is None
    ):
        inches = asset.inches(settings)
        notes = []
        if asset.dpi_x is None:
            notes.append(
                f"원본 DPI 정보 없음: {settings.reference_dpi:g} DPI를 인쇄 크기의 기준으로 가정했습니다."
            )
        return Encoded(
            asset.path.read_bytes(),
            EXTENSIONS[fmt],
            asset.width,
            asset.height,
            asset.width / inches[0],
            asset.height / inches[1],
            warnings=notes,
            details=f"{fmt.upper()} · 이미 용량 상한 이내여서 원본 데이터 유지",
        )
    im, notes = load_image(asset)
    resized_cache = None
    try:
        if asset.dpi_x is None and settings.print_width_mm is None:
            notes.append(
                f"원본 DPI 정보 없음: {settings.reference_dpi:g} DPI를 인쇄 크기의 기준으로 가정했습니다."
            )
        inches = asset.inches(settings)
        max_scale = 1.0
        if settings.mode == "dpi" and asset.edits.width is None:
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
            nonlocal resized_cache
            check_cancel(cancel)
            size = (
                max(1, math.floor(im.width * scale + 1e-7)),
                max(1, math.floor(im.height * scale + 1e-7)),
            )
            if resized_cache is None or resized_cache.size != size:
                if resized_cache is not None and resized_cache is not im:
                    resized_cache.close()
                resized_cache = resize_to(im, size)
            dpi = size[0] / inches[0], size[1] / inches[1]
            # Fit fast PNG bytes first. Run the expensive lossless optimizer only
            # once, after choosing resolution; it can only make the result smaller.
            data = encode_pixels(
                resized_cache, fmt, quality, dpi, settings, optimize_png=settings.mode != "size"
            )
            return Encoded(
                data,
                EXTENSIONS[fmt],
                *size,
                *dpi,
                quality if lossy else None,
                list(notes),
                details=f"{fmt.upper()} · {'사진 압축' if lossy else '무손실'}",
            )

        alpha_avif = fmt == "avif" and "A" in im.getbands()
        floor = 100 if alpha_avif else settings.min_quality if lossy else settings.quality
        result = candidate(
            max_scale, floor if settings.mode == "size" else 100 if alpha_avif else settings.quality
        )
        if (
            asset.frames == 1
            and not settings.strip_metadata
            and not asset.edits.active
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
        if settings.mode == "size":
            result, scale = fit_scale(
                lambda value: candidate(value, floor), result, settings.max_bytes, min_scale
            )
            if not result.target_met:
                result.warnings.append(
                    "1픽셀까지 축소해도 파일 헤더·색상 프로파일이 용량 상한보다 큽니다. 용량 상한을 늘려주세요."
                )
                return result
            if lossy:
                result = fit_quality(
                    lambda q: candidate(scale, q), result, floor, settings.max_bytes
                )
            elif fmt == "png":
                optimized = oxipng.optimize_from_memory(
                    result.data, level=1, optimize_alpha=False, timeout=3
                )
                if len(optimized) < len(result.data):
                    result.data = optimized
                if result.size_bytes < settings.max_bytes * 0.90 and scale < 1:
                    # Optimization sometimes saves >15%. Spend that gain on
                    # resolution with one bounded correction, never another loop.
                    proposed = min(
                        1.0, scale * math.sqrt(settings.max_bytes * 0.94 / result.size_bytes)
                    )
                    correction = candidate(proposed, floor)
                    optimized = oxipng.optimize_from_memory(
                        correction.data, level=1, optimize_alpha=False, timeout=3
                    )
                    if len(optimized) < correction.size_bytes:
                        correction.data = optimized
                    if correction.size_bytes <= settings.max_bytes:
                        result = correction
            result.details += f" · 상한의 {result.size_bytes / settings.max_bytes:.1%} 사용"
            if (
                result.width,
                result.height,
            ) == im.size and result.size_bytes < settings.max_bytes * 0.985:
                result.details += " · 원본 해상도 유지" if lossy else " · 원본 해상도, 무손실"
        if min(result.dpi_x, result.dpi_y) < 299.5:
            result.warnings.append(
                "출력 해상도가 300 DPI 미만입니다. 인쇄 크기와 제출 기준을 확인하세요."
            )
        if (
            settings.mode == "dpi"
            and asset.edits.width is None
            and min(result.dpi_x, result.dpi_y) + 1 < settings.target_dpi
        ):
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
        if resized_cache is not None and resized_cache is not im:
            resized_cache.close()
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
    width, height = asset.pixel_size
    dpi = min(width / width_in, height / height_in)
    estimated_bytes = asset.size_bytes * width * height / max(1, asset.width * asset.height)
    if settings.mode == "dpi" and asset.edits.width is None:
        ratio = min(1.0, settings.target_dpi / dpi)
    elif settings.mode == "size":
        ratio = min(1.0, math.sqrt(settings.max_bytes / max(1, estimated_bytes)))
    else:
        ratio = 1.0
    return (
        dpi * ratio,
        int(estimated_bytes * ratio * ratio),
        "빠른 추정 · 원본 용량 기준",
    )


def preview_source(asset: Asset, edge: int = 900) -> tuple[bytes, bytes]:
    """One deferred decode supplies a thumbnail and small full-detail sample tiles."""
    if asset.is_pdf:
        return thumbnail(asset, edge), b""
    if asset.edits.active:
        im, _ = load_image(asset)
        try:
            im.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=2.0)
            stream = io.BytesIO()
            im.convert("RGBA" if "A" in im.getbands() else "RGB").save(stream, "PNG")
            # A scaled preview is not a full-detail compression sample.
            return stream.getvalue(), b""
        finally:
            im.close()
    with Image.open(asset.path) as source:
        source.load()
        sample_data = b""
        if source.mode in {"RGB", "RGBA", "L", "LA", "P"}:
            mode = "RGBA" if asset.has_alpha else "RGB"
            tile = min(160, source.width // 2, source.height // 2)
            if tile >= 16:
                sample = Image.new(mode, (tile * 2, tile * 2))
                for index, (fx, fy) in enumerate(((0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8))):
                    x = int((source.width - tile) * fx)
                    y = int((source.height - tile) * fy)
                    with source.crop((x, y, x + tile, y + tile)) as crop:
                        sample.paste(crop.convert(mode), ((index % 2) * tile, (index // 2) * tile))
                stream = io.BytesIO()
                sample.save(stream, "PNG", compress_level=1)
                sample_data = stream.getvalue()
                sample.close()
        source.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=2.0)
        with ImageOps.exif_transpose(source) as oriented:
            with oriented.convert("RGBA" if asset.has_alpha else "RGB") as im:
                stream = io.BytesIO()
                im.save(stream, "PNG", compress_level=1)
                return stream.getvalue(), sample_data


def sample_estimate(asset: Asset, settings: Settings, sample: bytes, cancel=None):
    """Estimate from unscaled image content, not the input file's compression ratio.

    Sampling is deliberately approximate: it never writes an output or replaces
    the actual-byte check in compress_image. Full calculation stays user initiated.
    """
    if not sample or asset.is_pdf or settings.mode != "size":
        return quick_estimate(asset, settings)
    fmt = select_format(asset, settings)
    if (
        fmt == asset.format.lower().replace("jpg", "jpeg")
        and not settings.strip_metadata
        and not asset.edits.active
        and asset.size_bytes <= settings.max_bytes
    ):
        dpi = min(asset.width / asset.inches(settings)[0], asset.height / asset.inches(settings)[1])
        return dpi, asset.size_bytes, "빠른 추정 · 원본이 이미 상한 이내"
    with Image.open(io.BytesIO(sample)) as im:
        im.load()
        # Carry the source ICC length separately when actual output is calculated.
        ratio = asset.width * asset.height / (im.width * im.height)
        floor = settings.min_quality

        def candidate(scale):
            check_cancel(cancel)
            size = max(1, round(im.width * scale)), max(1, round(im.height * scale))
            resized = resize_to(im, size)
            try:
                data = encode_pixels(resized, fmt, floor, (72, 72), settings, optimize_png=False)
                return Encoded(data, EXTENSIONS[fmt], *size)
            finally:
                if resized is not im:
                    resized.close()

        minimum = candidate(1 / max(im.size)).size_bytes
        budget = max(minimum + 1, round((settings.max_bytes - minimum) / ratio + minimum))
        best, scale = fit_scale(candidate, candidate(1), budget, 1 / max(im.size))
        width_in, height_in = asset.inches(settings)
        dpi = min(asset.width / width_in, asset.height / height_in) * scale
        predicted = round(max(0, best.size_bytes - minimum) * ratio + minimum)
        return (
            dpi,
            min(settings.max_bytes, predicted),
            "빠른 추정 · 이미지 표본 기준, 실제 결과와 차이 가능",
        )


def thumbnail(asset: Asset, edge: int = 1000) -> bytes:
    if asset.is_pdf:
        from .pdf import render_preview

        return render_preview(asset.path, edge=edge)
    if asset.edits.active:
        return preview_source(asset, edge)[0]
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
