from __future__ import annotations

import io
import math
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, RLock

import img2pdf
import pikepdf
import pypdfium2
from pikepdf import Array, Dictionary, Name, Pdf, PdfImage
from PIL import Image

from .models import Asset, CompressionError, Encoded, Settings, check_cancel

# PDFium permits only one simultaneous caller, including preview workers.
PDFIUM_LOCK = RLock()
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1, 0, 0, 1, 0, 0)


def multiply(parent: Matrix, local: Matrix) -> Matrix:
    a, b, c, d, e, f = parent
    g, h, i, j, k, last = local
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * last + e,
        b * k + d * last + f,
    )


def open_pdf(source: Path | bytes) -> Pdf:
    try:
        pdf = Pdf.open(io.BytesIO(source) if isinstance(source, bytes) else source)
    except pikepdf.PasswordError as exc:
        raise CompressionError("암호가 설정된 PDF입니다. 암호를 해제한 사본을 불러오세요.") from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise CompressionError(f"PDF를 읽을 수 없습니다: {exc}") from exc
    if pdf.is_encrypted:
        pdf.close()
        raise CompressionError("암호화된 PDF는 지원하지 않습니다. 암호를 해제한 사본을 사용하세요.")
    return pdf


def probe_pdf(path: Path) -> Asset:
    with open_pdf(path) as pdf:
        sizes = []
        for page in pdf.pages:
            box = page.mediabox
            unit = float(page.obj.get("/UserUnit", 1))
            sizes.append(
                ((float(box[2]) - float(box[0])) * unit, (float(box[3]) - float(box[1])) * unit)
            )
        if not sizes:
            raise CompressionError("페이지가 없는 PDF입니다.")
        return Asset(path, "PDF", path.stat().st_size, frames=len(sizes), page_sizes=tuple(sizes))


def _check_signatures(pdf: Pdf) -> None:
    for obj in pdf.objects:
        if isinstance(obj, Dictionary) and (obj.get("/Type") == Name.Sig or "/ByteRange" in obj):
            raise CompressionError("전자서명이 있는 PDF입니다. 서명 전 원본을 사용하세요.")


@dataclass
class Placement:
    obj: pikepdf.Object
    width_in: float
    height_in: float


def _protect_images(obj, protected: set, visited: set, depth: int = 0) -> None:
    if depth > 50 or not isinstance(obj, (Dictionary, pikepdf.Stream, Array)):
        return
    key = obj.objgen
    if key != (0, 0):
        if key in visited:
            return
        visited.add(key)
    if isinstance(obj, (Dictionary, pikepdf.Stream)):
        if obj.get("/Subtype") == Name.Image:
            protected.add(key)
        for name, value in obj.items():
            # Do not wander from an appearance stream back into the page tree.
            if name not in {"/Parent", "/P", "/Pages"}:
                _protect_images(value, protected, visited, depth + 1)
    else:
        for value in obj:
            _protect_images(value, protected, visited, depth + 1)


def placements(pdf: Pdf, cancel: Event | None = None) -> tuple[dict, list[str], set]:
    found: dict[tuple[int, int], Placement] = {}
    notes: list[str] = []
    protected: set[tuple[int, int]] = set()

    def walk(stream, resources, initial: Matrix, ancestors: frozenset, depth: int = 0):
        check_cancel(cancel)
        if depth > 30:
            raise CompressionError("PDF Form 구조가 너무 깊어 이미지 변경을 생략합니다.")
        ctm, stack = initial, []
        for instruction in pikepdf.parse_content_stream(stream):
            check_cancel(cancel)
            if isinstance(instruction, pikepdf.ContentStreamInlineImage):
                notes.append("인라인 이미지는 원본을 보존했습니다.")
                continue
            operands, operator = instruction
            op = str(operator)
            if op == "q":
                stack.append(ctm)
            elif op == "Q":
                if not stack:
                    raise CompressionError("PDF 그래픽 상태가 불완전해 이미지 변경을 생략합니다.")
                ctm = stack.pop()
            elif op == "cm":
                ctm = multiply(ctm, tuple(map(float, operands)))
            elif op == "Do":
                xobjects = resources.get("/XObject", Dictionary())
                obj = xobjects.get(str(operands[0]))
                if obj is None:
                    raise CompressionError("PDF 이미지 리소스 참조를 해석할 수 없습니다.")
                key = obj.objgen
                if obj.get("/Subtype") == Name.Image:
                    w, h = math.hypot(ctm[0], ctm[1]) / 72, math.hypot(ctm[2], ctm[3]) / 72
                    if not all(math.isfinite(v) and v > 0 for v in (w, h)):
                        protected.add(key)
                        continue
                    if key in found:
                        found[key].width_in = max(found[key].width_in, w)
                        found[key].height_in = max(found[key].height_in, h)
                    else:
                        found[key] = Placement(obj, w, h)
                elif obj.get("/Subtype") == Name.Form:
                    if key in ancestors:
                        raise CompressionError(
                            "순환 PDF Form 참조가 있어 이미지 변경을 생략합니다."
                        )
                    form_resources = obj.get("/Resources", resources)
                    # Patterns, Type3 glyphs and appearance streams are deliberately unmodified.
                    for scope in (form_resources.get("/Pattern"), form_resources.get("/Font")):
                        _protect_images(scope, protected, set())
                    matrix = tuple(map(float, obj.get("/Matrix", IDENTITY)))
                    walk(obj, form_resources, multiply(ctm, matrix), ancestors | {key}, depth + 1)

    for page in pdf.pages:
        resources = page.obj.get("/Resources", Dictionary())
        unit = float(page.obj.get("/UserUnit", 1))
        for scope in (resources.get("/Pattern"), resources.get("/Font"), page.obj.get("/Annots")):
            _protect_images(scope, protected, set())
        walk(page, resources, (unit, 0, 0, unit, 0, 0), frozenset())
    return found, list(dict.fromkeys(notes)), protected


def _save(pdf: Pdf) -> bytes:
    stream = io.BytesIO()
    pdf.save(
        stream,
        compress_streams=True,
        recompress_flate=True,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
    )
    return stream.getvalue()


def _eligible(obj: pikepdf.Object) -> bool:
    if any(key in obj for key in ("/SMask", "/Mask", "/Decode", "/Alternates", "/OPI")):
        return False
    if obj.get("/ImageMask", False) or obj.get("/SMaskInData", 0):
        return False
    if int(obj.get("/BitsPerComponent", 0)) != 8:
        return False
    colorspace = obj.get("/ColorSpace")
    if colorspace not in (Name.DeviceRGB, Name.DeviceGray):
        if not (
            isinstance(colorspace, Array)
            and colorspace[0] == Name.ICCBased
            and int(colorspace[1].get("/N", 0)) in (1, 3)
        ):
            return False
    filters = obj.get("/Filter")
    if filters is not None:
        items = filters if isinstance(filters, Array) else [filters]
        if any(item not in {Name.FlateDecode, Name.DCTDecode} for item in items):
            return False
    return True


def _pdf_candidate(
    source: bytes,
    settings: Settings,
    dpi_limit: float | None,
    quality: int,
    cancel: Event | None,
    *,
    lossless_only: bool = False,
) -> Encoded:
    from .imaging import encode_pixels, resize_to

    check_cancel(cancel)
    with open_pdf(source) as pdf:
        _check_signatures(pdf)
        try:
            images, notes, protected = placements(pdf, cancel)
        except (pikepdf.PdfError, ValueError, TypeError, CompressionError) as exc:
            check_cancel(cancel)
            images, notes, protected = (
                {},
                [f"이미지 구조 분석을 생략하고 무손실 최적화만 수행: {exc}"],
                set(),
            )
        changed, skipped, dpi_values = 0, 0, []
        for key, entry in images.items():
            check_cancel(cancel)
            obj = entry.obj
            width, height = int(obj.Width), int(obj.Height)
            if not lossless_only:
                if key in protected or not _eligible(obj):
                    skipped += 1
                else:
                    # The PDF maps the image to a unit square, so axes can be
                    # sampled independently without changing its rendered shape.
                    size = (
                        (
                            max(1, min(width, math.ceil(dpi_limit * entry.width_in - 1e-7))),
                            max(1, min(height, math.ceil(dpi_limit * entry.height_in - 1e-7))),
                        )
                        if dpi_limit
                        else (width, height)
                    )
                    im = None
                    resized = None
                    try:
                        # Enforce Pillow's memory guard even on raw PDF image streams.
                        if width * height > Image.MAX_IMAGE_PIXELS:
                            raise ValueError("PDF 이미지가 안전한 디코딩 크기를 초과합니다.")
                        im = PdfImage(obj).as_pil_image()
                        im.load()
                        if im.mode not in {"RGB", "L"}:
                            raise ValueError("지원하지 않는 PDF 색상 모델")
                        resized = resize_to(im, size)
                        # Low-color graphics keep a lossless codec, including in photo mode.
                        artwork = settings.content == "artwork" or im.getcolors(256) is not None
                        raw = zlib.compress(resized.tobytes(), 9)
                        encoded, filter_name = raw, Name.FlateDecode
                        if not artwork:
                            local_settings = (
                                replace(settings, jpeg_engine="pillow")
                                if im.mode == "L"
                                else settings
                            )
                            jpeg = encode_pixels(resized, "jpeg", quality, (72, 72), local_settings)
                            if len(jpeg) < len(raw):
                                encoded, filter_name = jpeg, Name.DCTDecode
                        if len(encoded) < len(obj.read_raw_bytes()):
                            obj.write(encoded, filter=filter_name)
                            if "/DecodeParms" in obj:
                                del obj["/DecodeParms"]
                            obj.Width, obj.Height = size
                            obj.BitsPerComponent = 8
                            width, height = size
                            changed += 1
                    except (pikepdf.PdfError, NotImplementedError, ValueError, OSError) as exc:
                        skipped += 1
                        notes.append(f"일부 PDF 이미지를 보존했습니다: {exc}")
                    finally:
                        if resized is not None and resized is not im:
                            resized.close()
                        if im is not None:
                            im.close()
            dpi_values.extend((width / entry.width_in, height / entry.height_in))
        check_cancel(cancel)
        data = _save(pdf)
        if not changed and len(data) > len(source):
            data = source
        if skipped:
            notes.append(
                f"마스크·특수 색상·고비트 심도 등 이미지 {skipped}개는 원본을 보존했습니다."
            )
        if dpi_limit and any(value > dpi_limit + 2 for value in dpi_values):
            notes.append(
                "일부 이미지는 보존 조건 또는 기존 압축 효율 때문에 지정 DPI보다 높게 유지됩니다."
            )
        low, high = (min(dpi_values), max(dpi_values)) if dpi_values else (None, None)
        if low is not None and low < 299.5:
            notes.append(
                "일부 이미지가 300 DPI 미만입니다. 원본에 이미 저해상도 이미지가 포함됐을 수 있습니다."
            )
        detail = f"{len(pdf.pages)}쪽 · 이미지 {changed}개 최적화 · 텍스트/벡터 유지"
        if low is not None:
            detail += f" · 이미지 {low:.0f}–{high:.0f} DPI"
        else:
            detail += " · 측정 가능한 이미지 DPI 없음"
        return Encoded(
            data,
            ".pdf",
            dpi_x=low,
            dpi_y=high,
            quality=quality,
            warnings=list(dict.fromkeys(notes)),
            pages=len(pdf.pages),
            details=detail,
        )


def compress_pdf(source: Path | bytes, settings: Settings, cancel: Event | None = None) -> Encoded:
    settings.validate()
    data = source if isinstance(source, bytes) else source.read_bytes()
    if settings.mode != "size":
        dpi = settings.target_dpi if settings.mode == "dpi" else None
        return _pdf_candidate(data, settings, dpi, settings.quality, cancel)
    # Avoid generation loss when structural optimization alone already meets the cap.
    original = _pdf_candidate(data, settings, None, settings.quality, cancel, lossless_only=True)
    if original.size_bytes <= settings.max_bytes:
        return original
    max_dpi = max(settings.min_dpi, min(original.dpi_y or settings.target_dpi, 9600))
    full = _pdf_candidate(data, settings, max_dpi, settings.min_quality, cancel)
    if full.size_bytes <= settings.max_bytes:
        best, best_dpi = full, max_dpi
    else:
        minimum = _pdf_candidate(data, settings, settings.min_dpi, settings.min_quality, cancel)
        if minimum.size_bytes > settings.max_bytes:
            minimum.target_met = False
            minimum.warnings.append(
                "텍스트·벡터·보존 이미지와 최소 품질 때문에 목표 용량에 도달할 수 없습니다. 저장하지 않습니다."
            )
            return minimum
        best, best_dpi = minimum, settings.min_dpi
        lo, hi = settings.min_dpi, max_dpi
        for _ in range(7):
            dpi = (lo + hi) / 2
            trial = _pdf_candidate(data, settings, dpi, settings.min_quality, cancel)
            if trial.size_bytes <= settings.max_bytes:
                best, best_dpi, lo = trial, dpi, dpi
            else:
                hi = dpi
    lo, hi = settings.min_quality + 1, settings.quality
    while lo <= hi:
        quality = (lo + hi) // 2
        trial = _pdf_candidate(data, settings, best_dpi, quality, cancel)
        if trial.size_bytes <= settings.max_bytes:
            best, lo = trial, quality + 1
        else:
            hi = quality - 1
    return best


def merge_pdf(
    assets: list[Asset],
    settings: Settings,
    cancel: Event | None = None,
    progress=None,
) -> Encoded:
    from .imaging import encode_pixels, image_asset, load_image

    settings.validate()
    if not assets:
        raise CompressionError("병합할 파일을 추가하세요.")
    notes = []
    # First embed losslessly; the final image optimization is the only lossy pass.
    with Pdf.new() as merged:
        for index, asset in enumerate(assets):
            check_cancel(cancel)
            if progress:
                progress(index, len(assets), asset.path.name)
            if asset.is_pdf:
                with open_pdf(asset.path) as source:
                    _check_signatures(source)
                    merged.pages.extend(source.pages)
                notes.append(
                    "기존 PDF에서는 페이지를 가져옵니다. 문서 책갈피·양식 필드는 병합하지 않습니다."
                )
                continue
            for frame in range(asset.frames):
                check_cancel(cancel)
                current = image_asset(asset.path, frame) if frame else asset
                im, image_notes = load_image(current, frame)
                notes.extend(image_notes)
                try:
                    inches = current.inches(settings)
                    fmt = "tiff" if im.mode == "CMYK" or im.mode in {"F", "I"} else "png"
                    pixels = encode_pixels(im, fmt, 100, (72, 72), settings, optimize_png=False)
                    layout = img2pdf.get_layout_fun(pagesize=(inches[0] * 72, inches[1] * 72))
                    encoded = img2pdf.convert(
                        pixels, layout_fun=layout, rotation=img2pdf.Rotation.ifvalid
                    )
                    with Pdf.open(io.BytesIO(encoded)) as page_pdf:
                        merged.pages.extend(page_pdf.pages)
                finally:
                    im.close()
        source_bytes = _save(merged)
    result = compress_pdf(source_bytes, settings, cancel)
    result.warnings = list(dict.fromkeys(notes + result.warnings))
    return result


def render_preview(source: Path | bytes, edge: int = 1000, page_number: int = 0) -> bytes:
    with PDFIUM_LOCK:
        with pypdfium2.PdfDocument(source) as doc:
            page = doc[page_number]
            width, height = page.get_size()
            bitmap = page.render(scale=min(edge / width, edge / height), rev_byteorder=True)
            try:
                im = bitmap.to_pil()
                stream = io.BytesIO()
                im.save(stream, "PNG")
                return stream.getvalue()
            finally:
                bitmap.close()
                page.close()
