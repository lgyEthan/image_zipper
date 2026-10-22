from __future__ import annotations

import io
import math
import zlib
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, RLock

import img2pdf
import pikepdf
import pypdfium2
from pikepdf import Array, Dictionary, Name, Pdf, PdfImage
from PIL import Image

from .budget import fit_quality, fit_scale
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


def _save(pdf: Pdf, *, recompress: bool = True) -> bytes:
    stream = io.BytesIO()
    pdf.save(
        stream,
        compress_streams=True,
        recompress_flate=recompress,
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


class PDFWorkspace:
    """Analyze once; cache original/resized pixels with a bounded memory budget."""

    def __init__(self, source: bytes, cancel):
        self.pdf = open_pdf(source)
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.artwork = {}
        try:
            _check_signatures(self.pdf)
            try:
                self.images, self.notes, self.protected = placements(self.pdf, cancel)
            except (pikepdf.PdfError, ValueError, TypeError, CompressionError) as exc:
                check_cancel(cancel)
                self.images, self.protected = {}, set()
                self.notes = [f"이미지 구조 분석을 생략하고 무손실 최적화만 수행: {exc}"]
        except BaseException:
            self.pdf.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for im in self.cache.values():
            im.close()
        self.pdf.close()

    def _remember(self, key, im):
        size = im.width * im.height * len(im.getbands())
        if size > 128 * 1024**2:
            return False
        while self.cache and self.cache_bytes + size > 128 * 1024**2:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= old.width * old.height * len(old.getbands())
            old.close()
        self.cache[key] = im
        self.cache_bytes += size
        return True

    def pixels(self, key, size):
        from .imaging import resize_to

        cache_key = key, size
        if cache_key in self.cache:
            self.cache.move_to_end(cache_key)
            return self.cache[cache_key], True
        obj = self.images[key].obj
        full_size = int(obj.Width), int(obj.Height)
        original_key = key, full_size
        im = self.cache.get(original_key)
        owned = im is not None
        if im is None:
            if full_size[0] * full_size[1] > Image.MAX_IMAGE_PIXELS:
                raise ValueError("PDF 이미지가 안전한 디코딩 크기를 초과합니다.")
            im = PdfImage(obj).as_pil_image()
            im.load()
            if im.mode not in {"RGB", "L"}:
                im.close()
                raise ValueError("지원하지 않는 PDF 색상 모델")
            self.artwork[key] = im.getcolors(256) is not None
        if size == full_size:
            return im, owned or self._remember(cache_key, im)
        resized = resize_to(im, size)
        if not owned:
            self._remember(original_key, im) or im.close()
        return resized, self._remember(cache_key, resized)


def _pdf_candidate(
    source: bytes,
    settings: Settings,
    dpi_limit: float | None,
    quality: int,
    cancel: Event | None,
    *,
    lossless_only: bool = False,
    workspace: PDFWorkspace | None = None,
) -> Encoded:
    from .imaging import encode_pixels

    check_cancel(cancel)
    if workspace is None:
        with PDFWorkspace(source, cancel) as work:
            return _pdf_candidate(
                source,
                settings,
                dpi_limit,
                quality,
                cancel,
                lossless_only=lossless_only,
                workspace=work,
            )
    with open_pdf(source) as pdf:
        images, notes, protected = workspace.images, list(workspace.notes), workspace.protected
        changed, skipped, dpi_values = 0, 0, []
        for key, entry in images.items():
            check_cancel(cancel)
            obj = pdf.get_object(key)
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
                    resized = None
                    cached = False
                    try:
                        resized, cached = workspace.pixels(key, size)
                        # Low-color graphics keep a lossless codec, including in photo mode.
                        artwork = settings.content == "artwork" or workspace.artwork[key]
                        if artwork:
                            encoded, filter_name = (
                                zlib.compress(resized.tobytes(), 6),
                                Name.FlateDecode,
                            )
                        else:
                            local_settings = (
                                replace(settings, jpeg_engine="pillow")
                                if resized.mode == "L"
                                else settings
                            )
                            encoded = encode_pixels(
                                resized, "jpeg", quality, (72, 72), local_settings
                            )
                            filter_name = Name.DCTDecode
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
                        if resized is not None and not cached:
                            resized.close()
            dpi_values.extend((width / entry.width_in, height / entry.height_in))
        check_cancel(cancel)
        # Re-deflating every existing stream on each trial buys little and can
        # dominate image fitting. Newly written raw streams are still compressed.
        data = _save(pdf, recompress=False)
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
    with PDFWorkspace(data, cancel) as workspace:

        def candidate(dpi, quality, lossless=False):
            return _pdf_candidate(
                data,
                settings,
                dpi,
                quality,
                cancel,
                lossless_only=lossless,
                workspace=workspace,
            )

        # A lossless result already retains all available detail; do not pad it.
        original = candidate(None, 100, True)
        if original.size_bytes <= settings.max_bytes:
            original.details += " · 무손실 최적화로 목표 충족"
            return original
        max_dpi = min(original.dpi_y or 9600, 9600)
        full = candidate(max_dpi, settings.min_quality)
        maximum_pixels = max(
            (
                max(int(entry.obj.Width), int(entry.obj.Height))
                for entry in workspace.images.values()
            ),
            default=1,
        )
        best, scale = fit_scale(
            lambda value: candidate(max_dpi * value, settings.min_quality),
            full,
            settings.max_bytes,
            min(1e-6, 1 / maximum_pixels),
        )
        if not best.target_met:
            best.warnings.append(
                "이미지를 최소 픽셀까지 줄여도 텍스트·벡터·보존 이미지·문서 구조가 용량 상한보다 큽니다. 용량 상한을 늘려주세요."
            )
            return best
        if settings.content != "artwork":
            best = fit_quality(
                lambda quality: candidate(max_dpi * scale, quality),
                best,
                settings.min_quality,
                settings.max_bytes,
            )
        best.details += f" · 상한의 {best.size_bytes / settings.max_bytes:.1%} 사용"
        return best


def _append_raster_page(pdf: Pdf, im: Image.Image, inches: tuple[float, float]) -> bool:
    """Embed common pixel modes once, without a PNG encode/decode round trip."""
    if im.mode not in {"RGB", "RGBA", "L", "LA", "CMYK"}:
        return False
    has_alpha = "A" in im.getbands()
    mode = "RGB" if im.mode == "RGBA" else "L" if im.mode == "LA" else im.mode
    pixels = im.convert(mode) if has_alpha else im
    try:
        channels = len(pixels.getbands())
        space = {1: Name.DeviceGray, 3: Name.DeviceRGB, 4: Name.DeviceCMYK}[channels]
        image = pdf.make_stream(zlib.compress(pixels.tobytes(), 1))
        image.Type, image.Subtype = Name.XObject, Name.Image
        image.Width, image.Height, image.BitsPerComponent = im.width, im.height, 8
        image.Filter, image.ColorSpace = Name.FlateDecode, space
        if profile := im.info.get("icc_profile"):
            icc = pdf.make_stream(profile)
            icc.N, icc.Alternate = channels, space
            image.ColorSpace = Array([Name.ICCBased, icc])
        if has_alpha:
            with im.getchannel("A") as alpha:
                mask = pdf.make_stream(zlib.compress(alpha.tobytes(), 1))
            mask.Type, mask.Subtype = Name.XObject, Name.Image
            mask.Width, mask.Height, mask.BitsPerComponent = im.width, im.height, 8
            mask.Filter, mask.ColorSpace = Name.FlateDecode, Name.DeviceGray
            image.SMask = mask
        width, height = inches[0] * 72, inches[1] * 72
        page = pdf.add_blank_page(page_size=(width, height))
        page.Resources = Dictionary(XObject=Dictionary(Im0=image))
        page.Contents = pdf.make_stream(
            f"q {width:.10g} 0 0 {height:.10g} 0 0 cm /Im0 Do Q".encode()
        )
        return True
    finally:
        if pixels is not im:
            pixels.close()


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
                    if any(
                        key in source.Root
                        for key in (
                            "/AcroForm",
                            "/Outlines",
                            "/Names",
                            "/Dests",
                            "/OCProperties",
                            "/StructTreeRoot",
                            "/Collection",
                        )
                    ):
                        raise CompressionError(
                            "책갈피·양식·첨부 자료 등 문서 구조가 있는 PDF는 병합 시 정보가 사라질 수 있습니다. "
                            "이 문서는 개별 압축을 사용하세요."
                        )
                    merged.pages.extend(source.pages)
                continue
            if asset.frames > 1 and asset.format != "TIFF":
                raise CompressionError(
                    "움직임이나 보조 이미지를 잃지 않도록 이 다중 프레임 파일의 PDF 병합을 중단했습니다."
                )
            for frame in range(asset.frames):
                check_cancel(cancel)
                current = image_asset(asset.path, frame) if frame else asset
                im, image_notes = load_image(current, frame)
                notes.extend(image_notes)
                try:
                    inches = current.inches(settings)
                    if _append_raster_page(merged, im, inches):
                        continue
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
        source_bytes = _save(merged, recompress=False)
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
