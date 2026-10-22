from __future__ import annotations

import io
from dataclasses import replace

import pikepdf
import pytest
from PIL import Image, ImageChops

from pictumi.edits import ImageEdits, Mosaic, apply_edits, edited_size, source_point
from pictumi.imaging import compress_image, load_image, preview_source, probe
from pictumi.models import Settings, TrialCache, source_fingerprints
from pictumi.pdf import merge_pdf
from pictumi.service import save_asset


@pytest.mark.parametrize("turns", range(4))
@pytest.mark.parametrize("angle", [-45, -15, 0, 15, 45])
def test_straighten_autocrops_without_empty_corners(turns, angle):
    with Image.new("RGB", (401, 299), "white") as original:
        edits = ImageEdits(turns=turns, angle=angle)
        output = apply_edits(original, edits)
        try:
            assert output.size == edited_size(original.size, edits)
            assert output.getextrema() == ((255, 255),) * 3
            assert original.size == (401, 299)
            if angle:
                assert output.width * output.height < original.width * original.height
        finally:
            if output is not original:
                output.close()


def test_mosaic_changes_only_selected_region_and_follows_rotation(photo):
    before = photo.tobytes()
    region = Mosaic(((0.25, 0.25), (0.5, 0.25), (0.5, 0.5), (0.25, 0.5)), 100)
    edited = apply_edits(photo, ImageEdits(mosaics=(region,)))
    assert photo.tobytes() == before
    diff = ImageChops.difference(photo, edited)
    assert diff.getbbox() == (180, 120, 360, 240)
    rotated = apply_edits(photo, ImageEdits(mosaics=(region,), turns=1))
    assert rotated.tobytes() == edited.transpose(Image.Transpose.ROTATE_90).tobytes()
    assert rotated.size == (480, 720)
    for image in (edited, rotated, diff):
        image.close()


def test_mosaic_selection_on_rotated_canvas_maps_to_correct_source_quadrant(photo):
    rotation = ImageEdits(turns=1)
    corners = ((0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5))
    region = Mosaic(tuple(source_point(p, photo.size, rotation) for p in corners), 100)
    edited = apply_edits(photo, replace(rotation, mosaics=(region,)))
    baseline = photo.transpose(Image.Transpose.ROTATE_90)
    diff = ImageChops.difference(edited, baseline)
    x0, y0, x1, y1 = diff.getbbox()
    assert x0 == 0 and y0 == 0 and x1 <= 241 and y1 <= 361
    for image in (edited, baseline, diff):
        image.close()


def test_straighten_can_keep_transparent_corners_and_source_alpha():
    with Image.new("RGB", (300, 200), "red") as original:
        with apply_edits(original, ImageEdits(angle=15, crop=False)) as output:
            assert output.mode == "RGBA"
            assert output.getpixel((0, 0))[3] == 0
    with Image.new("RGBA", (300, 200), (100, 120, 140, 50)) as original:
        with apply_edits(original, ImageEdits(turns=1)) as output:
            assert output.getextrema()[3] == (50, 50)


def test_resize_preview_export_and_source_protection(png, tmp_path):
    original = png.read_bytes()
    asset = replace(probe(png), edits=ImageEdits(width=360, height=240))
    settings = Settings(target_dpi=72, output_format="png", strip_metadata=True)
    # An explicit edit size is authoritative for an individual image.
    result = compress_image(asset, settings)
    assert (result.width, result.height) == (360, 240)
    assert result.dpi_x == pytest.approx(150, abs=1)
    preview, sample = preview_source(asset)
    with Image.open(io.BytesIO(preview)) as image:
        assert image.size == (360, 240)
    assert sample == b""
    saved = save_asset(asset, settings, tmp_path / "output")
    with Image.open(saved[0].output) as output:
        with Image.open(io.BytesIO(result.data)) as expected:
            assert output.tobytes() == expected.tobytes()
    assert png.read_bytes() == original
    assert len(list((tmp_path / "output").iterdir())) == 1


def test_edits_cannot_be_bypassed_by_small_original_or_trial_cache(png, tmp_path):
    asset = probe(png)
    settings = Settings(mode="size", max_bytes=2_000_000, output_format="png")
    initial = compress_image(asset, settings)
    cache_path = tmp_path / "trial"
    cache_path.write_bytes(initial.data)
    cache = TrialCache(
        cache_path, settings, source_fingerprints([asset]), False, initial, len(initial.data)
    )
    edited = replace(asset, edits=ImageEdits(turns=1))
    assert cache.read([asset], settings, False) is not None
    assert cache.read([edited], settings, False) is None
    result = compress_image(edited, settings)
    assert (result.width, result.height) == (480, 720)
    assert result.data != asset.path.read_bytes()
    assert result.target_met


def test_edited_size_can_still_fit_hard_byte_cap(png):
    asset = replace(probe(png), edits=ImageEdits(width=360, height=240, angle=10))
    result = compress_image(
        asset, Settings(mode="size", max_bytes=5000, output_format="jpeg", min_quality=60)
    )
    assert result.target_met and result.size_bytes <= 5000
    assert result.width <= 360 and result.height <= 240


def test_pdf_merge_uses_edited_pixels_and_rotated_page_dimensions(png):
    region = Mosaic(((0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)), 100)
    asset = replace(probe(png), edits=ImageEdits(turns=1, mosaics=(region,)))
    result = merge_pdf([asset], Settings(mode="original", content="artwork"))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        page = pdf.pages[0]
        assert float(page.mediabox[2]) == pytest.approx(480 / 300 * 72, abs=0.01)
        assert float(page.mediabox[3]) == pytest.approx(720 / 300 * 72, abs=0.01)
        image = next(iter(page.Resources.XObject.values()))
        actual = pikepdf.PdfImage(image).as_pil_image()
        expected, _ = load_image(asset)
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
        actual.close()
        expected.close()


def test_mosaic_never_leaves_original_thumbnail_or_capture_metadata(tmp_path):
    path = tmp_path / "camera.jpg"
    exif = Image.Exif()
    exif[315] = "Private photographer"
    exif[34853] = {1: "N", 2: (10, 20, 30)}
    Image.effect_noise((300, 200), 70).convert("RGB").save(
        path, exif=exif, comment=b"Private preview", dpi=(300, 300)
    )
    asset = replace(
        probe(path), edits=ImageEdits(mosaics=(Mosaic(((0, 0), (1, 0), (1, 1), (0, 1))),))
    )
    result = compress_image(asset, Settings(mode="original", strip_metadata=False))
    with Image.open(io.BytesIO(result.data)) as out:
        assert 315 not in out.getexif() and 34853 not in out.getexif()
        assert not out.info.get("comment")
    assert any("메타데이터" in note for note in result.warnings)


@pytest.mark.parametrize("turns,angle", [(0, 15), (1, -15), (2, 45), (3, 0)])
def test_preview_matches_export_geometry(turns, angle):
    from PIL import ImageDraw, ImageStat

    source = Image.new("RGB", (2400, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 1199, 799), fill="red")
    draw.rectangle((1200, 0, 2399, 799), fill="green")
    draw.rectangle((0, 800, 1199, 1599), fill="blue")
    thumb = source.copy()
    thumb.thumbnail((900, 900))
    edits = ImageEdits(turns=turns, angle=angle)
    expected = apply_edits(source, edits)
    preview = apply_edits(thumb, edits, preview=True, reference_size=source.size)
    resized = expected.resize(preview.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(resized, preview)
    assert max(ImageStat.Stat(diff).mean) < 1.5
    for image in (source, thumb, expected, preview, resized, diff):
        image.close()
