"""Immutable, source-coordinate edits shared by the editor, previews and exports."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Mosaic:
    points: tuple[tuple[float, float], ...]
    strength: int = 35


@dataclass(frozen=True)
class ImageEdits:
    mosaics: tuple[Mosaic, ...] = ()
    turns: int = 0
    angle: float = 0.0
    crop: bool = True
    width: int | None = None
    height: int | None = None
    # Normalized bounds within the rotated, automatically trimmed canvas.
    crop_box: tuple[float, float, float, float] | None = None
    crop_ratio: tuple[int, int] | None = None

    @property
    def active(self) -> bool:
        return bool(self.mosaics or self.turns % 4 or self.angle or self.width or self.crop_box)

    @property
    def rotation(self) -> float:
        return (self.turns * 90 + self.angle) % 360

    def validate(self):
        if self.crop_box is not None:
            if (
                len(self.crop_box) != 4
                or any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.crop_box)
                or self.crop_box[0] >= self.crop_box[2]
                or self.crop_box[1] >= self.crop_box[3]
            ):
                raise ValueError("자르기 영역은 이미지 안에 지정하세요.")
        if self.crop_ratio is not None and (
            len(self.crop_ratio) != 2
            or any(not isinstance(v, int) or not 1 <= v <= 1_000_000 for v in self.crop_ratio)
        ):
            raise ValueError("올바르지 않은 자르기 비율입니다.")
        if not math.isfinite(self.angle) or not -45 <= self.angle <= 45:
            raise ValueError("기울기는 -45°부터 45°까지 조절할 수 있습니다.")
        if (self.width is None) != (self.height is None):
            raise ValueError("가로와 세로 크기를 함께 지정하세요.")
        if self.width is not None and (
            not 1 <= self.width <= 20000
            or not 1 <= self.height <= 20000
            or self.width * self.height > 100_000_000
        ):
            raise ValueError("이미지 크기는 각 변 20,000 px, 전체 1억 픽셀 이내로 지정하세요.")
        if len(self.mosaics) > 200:
            raise ValueError("한 이미지에 모자이크 영역은 최대 200개까지 지정할 수 있습니다.")
        for region in self.mosaics:
            if len(region.points) != 4 or not 5 <= region.strength <= 100:
                raise ValueError("올바르지 않은 모자이크 영역입니다.")
            if any(not math.isfinite(v) or not 0 <= v <= 1 for p in region.points for v in p):
                raise ValueError("모자이크 영역은 이미지 안에 지정하세요.")


def rotation_layout(size: tuple[int, int], edits: ImageEdits):
    """Return Pillow's expanded size and a centered, empty-corner-free crop box."""
    w, h = size
    angle = math.radians(edits.rotation)
    c, s = round(math.cos(angle), 15), round(math.sin(angle), 15)
    if edits.rotation % 90 == 0:
        expanded = (h, w) if edits.turns % 2 else (w, h)
    else:
        corners = [
            (c * (x - w / 2) - s * (y - h / 2) + w / 2, s * (x - w / 2) + c * (y - h / 2) + h / 2)
            for x, y in ((0, 0), (w, 0), (w, h), (0, h))
        ]
        expanded = tuple(
            math.ceil(max(p[i] for p in corners)) - math.floor(min(p[i] for p in corners))
            for i in (0, 1)
        )
    width, height = expanded
    if edits.crop and edits.angle:
        c, s = abs(c), abs(s)
        a, b = w / 2, h / 2
        candidates = [(a / (2 * c), a / (2 * s)), (b / (2 * s), b / (2 * c))]
        det = c * c - s * s
        if abs(det) > 1e-10:
            candidates.append(((a * c - b * s) / det, (b * c - a * s) / det))
        valid = [
            (x, y)
            for x, y in candidates
            if x > 0 and y > 0 and c * x + s * y <= a + 1e-6 and s * x + c * y <= b + 1e-6
        ]
        x, y = max(valid, key=lambda point: point[0] * point[1])
        # Leave a small interpolation margin, so empty corners cannot bleed in.
        width, height = max(1, math.floor(2 * x) - 4), max(1, math.floor(2 * y) - 4)
    left, top = (expanded[0] - width) // 2, (expanded[1] - height) // 2
    return expanded, (left, top, left + width, top + height)


def crop_bounds(size, box, ratio=None):
    """Snap a manual selection to pixels; standard ratios retain exact integer multiples."""
    w, h = size
    if box is None:
        return 0, 0, w, h
    left = min(w - 1, max(0, round(box[0] * w)))
    top = min(h - 1, max(0, round(box[1] * h)))
    right = min(w, max(left + 1, round(box[2] * w)))
    bottom = min(h, max(top + 1, round(box[3] * h)))
    if ratio:
        a, b = ratio
        divisor = math.gcd(a, b)
        a, b = a // divisor, b // divisor
        multiple = min((right - left) // a, (bottom - top) // b)
        if multiple:
            width, height = multiple * a, multiple * b
        else:
            # A tiny source or a relatively prime source ratio may not contain
            # even one exact multiple. Keep the closest nonempty pixel size.
            scale = min((right - left) / a, (bottom - top) / b)
            width, height = max(1, round(a * scale)), max(1, round(b * scale))
        left += (right - left - width) // 2
        top += (bottom - top - height) // 2
        right, bottom = left + width, top + height
    return left, top, right, bottom


def crop_layout(size, edits):
    """The final source rectangle, before explicit output-pixel resizing."""
    expanded, base = rotation_layout(size, edits)
    box = crop_bounds((base[2] - base[0], base[3] - base[1]), edits.crop_box, edits.crop_ratio)
    return expanded, (base[0] + box[0], base[1] + box[1], base[0] + box[2], base[1] + box[3])


def edited_size(size: tuple[int, int], edits: ImageEdits) -> tuple[int, int]:
    if edits.width is not None:
        return edits.width, edits.height
    _, (x0, y0, x1, y1) = crop_layout(size, edits)
    return x1 - x0, y1 - y0


def source_point(point, size, edits):
    """Map a normalized edited-canvas point back to the oriented source image."""
    expanded, box = crop_layout(size, edits)
    x = box[0] + point[0] * (box[2] - box[0]) - expanded[0] / 2
    y = box[1] + point[1] * (box[3] - box[1]) - expanded[1] / 2
    angle = math.radians(edits.rotation)
    c, s = math.cos(angle), math.sin(angle)
    return (
        min(1.0, max(0.0, (c * x - s * y + size[0] / 2) / size[0])),
        min(1.0, max(0.0, (s * x + c * y + size[1] / 2) / size[1])),
    )


def apply_edits(
    source: Image.Image,
    edits: ImageEdits,
    *,
    preview: bool = False,
    reference_size: tuple[int, int] | None = None,
) -> Image.Image:
    edits.validate()
    if not edits.active:
        return source
    if source.mode not in {"RGB", "RGBA", "L", "LA", "CMYK", "1"}:
        raise ValueError("이 색상·비트 심도의 이미지는 편집을 지원하지 않습니다.")
    im = source.copy()
    try:
        for region in edits.mosaics:
            points = [(x * im.width, y * im.height) for x, y in region.points]
            left = max(0, math.floor(min(x for x, _ in points)))
            top = max(0, math.floor(min(y for _, y in points)))
            right = min(im.width, math.ceil(max(x for x, _ in points)))
            bottom = min(im.height, math.ceil(max(y for _, y in points)))
            if right <= left or bottom <= top:
                continue
            box = left, top, right, bottom
            block = max(2, round(min(im.size) * region.strength / 1000))
            with im.crop(box) as area:
                cells = max(1, area.width // block), max(1, area.height // block)
                with area.resize(cells, Image.Resampling.BOX) as small:
                    with small.resize(area.size, Image.Resampling.NEAREST) as pixels:
                        with Image.new("L", area.size) as mask:
                            ImageDraw.Draw(mask).polygon(
                                [(x - left, y - top) for x, y in points], fill=255
                            )
                            im.paste(pixels, box, mask)
        if preview and reference_size is not None:
            # Use full-resolution crop coordinates on the thumbnail, so dragged
            # masks line up with the exported pixels even on very large images.
            expanded, box = crop_layout(reference_size, edits)
            target = edited_size(reference_size, edits)
            scale = min(1.0, 1000 / max(target))
            target = tuple(max(1, round(value * scale)) for value in target)
            if not edits.crop and edits.angle and im.mode not in {"RGBA", "LA"}:
                old, im = im, im.convert("RGBA")
                old.close()
            angle = math.radians(edits.rotation)
            c, s = math.cos(angle), math.sin(angle)
            sx, sy = im.width / reference_size[0], im.height / reference_size[1]
            dx, dy = (box[2] - box[0]) / target[0], (box[3] - box[1]) / target[1]
            x, y = box[0] - expanded[0] / 2, box[1] - expanded[1] / 2
            transform = (
                c * dx * sx,
                -s * dy * sx,
                (c * x - s * y + reference_size[0] / 2) * sx,
                s * dx * sy,
                c * dy * sy,
                (s * x + c * y + reference_size[1] / 2) * sy,
            )
            old, im = (
                im,
                im.transform(target, Image.Transform.AFFINE, transform, Image.Resampling.BICUBIC),
            )
            old.close()
            return im
        expanded, box = crop_layout(im.size, edits)
        if edits.rotation:
            if not edits.crop and edits.angle and im.mode not in {"RGBA", "LA"}:
                if im.mode == "CMYK":
                    raise ValueError("CMYK의 기울기 보정은 빈 여백 자동 자르기를 사용하세요.")
                old, im = im, im.convert("LA" if im.mode in {"L", "1"} else "RGBA")
                old.close()
            rotated = im.rotate(edits.rotation, resample=Image.Resampling.BICUBIC, expand=True)
            im.close()
            im = rotated
            if im.size != expanded:
                raise ValueError("회전 결과의 크기를 확인할 수 없습니다.")
        if box != (0, 0, *expanded):
            old, im = im, im.crop(box)
            old.close()
        if edits.width is not None:
            target = edits.width, edits.height
            if preview:
                scale = min(1.0, 1000 / max(target))
                target = tuple(max(1, round(v * scale)) for v in target)
            if im.size != target:
                old, im = im, im.resize(target, Image.Resampling.LANCZOS, reducing_gap=3.0)
                old.close()
        return im
    except BaseException:
        im.close()
        raise
