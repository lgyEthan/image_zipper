from __future__ import annotations

import io
from dataclasses import replace

import pikepdf
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from pictumi.edits import (
    ImageEdits,
    Mosaic,
    apply_edits,
    crop_bounds,
    edited_size,
    source_point,
)
from pictumi.imaging import compress_image, load_image, probe
from pictumi.models import Settings, TrialCache, source_fingerprints
from pictumi.pdf import merge_pdf
from pictumi.service import save_asset


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "CMYK"])
def test_free_crop_keeps_exact_selected_pixels_and_color_mode(photo, mode):
    with photo.convert(mode) as source:
        original = source.tobytes()
        edits = ImageEdits(crop_box=(0.25, 0.125, 0.75, 0.875))
        with apply_edits(source, edits) as output, source.crop((180, 60, 540, 420)) as expected:
            assert output.size == (360, 360)
            assert output.mode == source.mode
            assert output.tobytes() == expected.tobytes()
            assert source.tobytes() == original


@pytest.mark.parametrize("ratio", [(1, 1), (4, 5), (3, 2), (16, 9)])
def test_preset_ratios_remain_exact_on_odd_pixel_dimensions(ratio):
    edits = ImageEdits(crop_box=(0.13, 0.07, 0.89, 0.91), crop_ratio=ratio)
    with Image.new("RGB", (1601, 1003), "red") as source, apply_edits(source, edits) as output:
        assert output.width * ratio[1] == output.height * ratio[0]
        assert output.size == edited_size(source.size, edits)
        assert output.getextrema() == ((255, 255), (0, 0), (0, 0))


@pytest.mark.parametrize(
    "box", [(0, 0, 0, 1), (-0.1, 0, 1, 1), (0, 0, 1.1, 1), (0, float("nan"), 1, 1)]
)
def test_invalid_crop_is_rejected(box):
    with pytest.raises(ValueError, match="자르기"):
        ImageEdits(crop_box=box).validate()


def test_tiny_crop_never_becomes_empty_or_adds_pixels():
    assert crop_bounds((1, 1), (0.99, 0.99, 1, 1), (4, 5)) == (0, 0, 1, 1)
    with Image.new("RGBA", (7, 5), (50, 60, 70, 80)) as source:
        with apply_edits(source, ImageEdits(crop_box=(0.999, 0.999, 1, 1))) as output:
            assert output.size == (1, 1)
            assert output.getpixel((0, 0)) == (50, 60, 70, 80)


@pytest.mark.parametrize("turns,angle", [(0, 0), (0, 13), (1, -17), (2, 45), (3, 0)])
def test_cropped_preview_matches_full_resolution_export(turns, angle):
    source = Image.new("RGB", (2400, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 1199, 799), fill="red")
    draw.rectangle((1200, 0, 2399, 799), fill="green")
    draw.rectangle((0, 800, 1199, 1599), fill="blue")
    thumb = source.copy()
    thumb.thumbnail((900, 900))
    edits = ImageEdits(
        turns=turns, angle=angle, crop_box=(0.12, 0.09, 0.87, 0.91), crop_ratio=(4, 5)
    )
    expected = apply_edits(source, edits)
    preview = apply_edits(thumb, edits, preview=True, reference_size=source.size)
    resized = expected.resize(preview.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(resized, preview)
    assert max(ImageStat.Stat(diff).mean) < 1.5
    for image in (source, thumb, expected, preview, resized, diff):
        image.close()


def test_mosaic_added_after_crop_covers_the_selected_output_region(photo):
    edits = ImageEdits(turns=1, crop_box=(0.25, 0.25, 0.75, 0.75))
    selection = ((0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5))
    mosaic = Mosaic(tuple(source_point(p, photo.size, edits) for p in selection), 100)
    with (
        apply_edits(photo, edits) as baseline,
        apply_edits(photo, replace(edits, mosaics=(mosaic,))) as result,
    ):
        with ImageChops.difference(result, baseline) as diff:
            x0, y0, x1, y1 = diff.getbbox()
            assert x0 == y0 == 0
            assert x1 <= result.width // 2 + 1
            assert y1 <= result.height // 2 + 1


def test_crop_exports_new_file_without_sidecars_or_stale_trial(png, tmp_path):
    original = png.read_bytes()
    asset = probe(png)
    settings = Settings(mode="size", max_bytes=2_000_000, output_format="png", content="artwork")
    initial = compress_image(asset, settings)
    trial = tmp_path / "trial"
    trial.write_bytes(initial.data)
    cache = TrialCache(
        trial, settings, source_fingerprints([asset]), False, initial, len(initial.data)
    )
    asset = replace(asset, edits=ImageEdits(crop_box=(0.25, 0.125, 0.75, 0.875)))
    assert cache.read([asset], settings, False) is None
    results = save_asset(asset, settings, tmp_path / "output")
    with Image.open(results[0].output) as output, Image.open(png) as source:
        with source.crop((180, 60, 540, 420)) as expected:
            assert output.size == (360, 360)
            assert output.tobytes() == expected.tobytes()
    assert png.read_bytes() == original
    assert len(list((tmp_path / "output").iterdir())) == 1


def test_pdf_merge_uses_cropped_pixels_and_physical_page_size(png):
    asset = replace(probe(png), edits=ImageEdits(crop_box=(0.25, 0.125, 0.75, 0.875)))
    result = merge_pdf([asset], Settings(mode="original", content="artwork"))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        page = pdf.pages[0]
        assert float(page.mediabox[2]) == pytest.approx(360 / 300 * 72, abs=0.01)
        assert float(page.mediabox[3]) == pytest.approx(360 / 300 * 72, abs=0.01)
        actual = pikepdf.PdfImage(next(iter(page.Resources.XObject.values()))).as_pil_image()
        expected, _ = load_image(asset)
        assert actual.size == expected.size == (360, 360)
        assert actual.tobytes() == expected.tobytes()
        actual.close()
        expected.close()
