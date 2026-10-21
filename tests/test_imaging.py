from __future__ import annotations

import io
from threading import Event

import pytest
from PIL import Image, JpegImagePlugin

from image_zipper.imaging import compress_image, probe
from image_zipper.models import Cancelled, CompressionError, Settings
from image_zipper.service import save_asset


@pytest.mark.parametrize(
    "fmt,extension",
    [
        ("PNG", ".png"),
        ("JPEG", ".jpg"),
        ("BMP", ".bmp"),
        ("TIFF", ".tif"),
        ("WEBP", ".webp"),
        ("AVIF", ".avif"),
        ("HEIF", ".heic"),
    ],
)
def test_supported_formats(photo, tmp_path, fmt, extension):
    path = tmp_path / f"input{extension}"
    photo.save(path, format=fmt)
    asset = probe(path)
    result = compress_image(
        asset, Settings(target_dpi=150, output_format="jpeg", print_width_mm=60.96)
    )
    with Image.open(io.BytesIO(result.data)) as im:
        assert im.size == (360, 240)
        assert im.format == "JPEG"
    assert result.dpi_x == pytest.approx(150)


def test_actual_dpi_resamples_using_physical_size(png):
    result = compress_image(probe(png), Settings(target_dpi=150))
    assert result.width in (359, 360)
    assert result.height in (239, 240)
    assert result.dpi_x == pytest.approx(150, abs=0.7)
    with Image.open(io.BytesIO(result.data)) as im:
        assert im.info["dpi"][0] == pytest.approx(result.dpi_x, abs=0.05)


def test_target_size_is_measured_and_no_generation_cascade(png):
    result = compress_image(
        probe(png),
        Settings(mode="size", max_bytes=14000, output_format="jpeg", min_quality=70),
    )
    assert result.target_met
    assert len(result.data) <= 14000
    assert result.width < 720
    assert 30 <= result.dpi_x <= 300
    with Image.open(io.BytesIO(result.data)) as im:
        im.load()
        assert im.width == result.width


def test_impossible_target_does_not_write(png, tmp_path):
    asset = probe(png)
    # JPEG headers plus this image's real ICC profile exceed this tiny budget,
    # even with a 1x1 image. Failure must never be due to a DPI floor.
    settings = Settings(mode="size", max_bytes=1024, output_format="jpeg", jpeg_engine="pillow")
    assert not compress_image(asset, settings).target_met
    with pytest.raises(CompressionError, match="목표 용량"):
        save_asset(asset, settings, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_alpha_and_icc_survive_png_lossless(tmp_path):
    im = Image.new("RGBA", (70, 50), (22, 45, 100, 115))
    im.putpixel((0, 0), (244, 155, 66, 0))
    path = tmp_path / "alpha.png"
    im.save(path, dpi=(300, 300))
    result = compress_image(probe(path), Settings(mode="original", content="artwork"))
    with Image.open(io.BytesIO(result.data)) as output:
        assert output.convert("RGBA").tobytes() == im.tobytes()


def test_jpegli_metadata_roundtrip(png):
    result = compress_image(probe(png), Settings(mode="original", output_format="jpeg"))
    with Image.open(io.BytesIO(result.data)) as im:
        assert im.info["icc_profile"]
        assert float(im.getexif()[282]) == pytest.approx(result.dpi_x, abs=0.1)
        assert im.getexif()[296] == 2
        assert im.getexif().get(274, 1) == 1


def test_print_width_controls_effective_dpi(png):
    result = compress_image(probe(png), Settings(mode="dpi", target_dpi=150, print_width_mm=50.8))
    assert result.width == 300
    assert result.dpi_x == 150


def test_no_upscale(png):
    result = compress_image(probe(png), Settings(target_dpi=600))
    assert result.width == 720
    assert any("확대" in warning for warning in result.warnings)


def test_orientation_rotates_dpi_axes(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (800, 400)).save(path, exif=exif, dpi=(400, 200))
    asset = probe(path)
    assert (asset.width, asset.height) == (400, 800)
    assert (asset.dpi_x, asset.dpi_y) == (200, 400)
    result = compress_image(asset, Settings(mode="original", output_format="jpeg"))
    with Image.open(io.BytesIO(result.data)) as im:
        assert im.size == (400, 800)
        assert im.getexif().get(274, 1) == 1


def test_multiframe_tiff_exports_every_frame(tmp_path):
    path = tmp_path / "multipage.tif"
    frames = [Image.new("RGB", (60, 40), "red"), Image.new("RGB", (80, 50), "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], dpi=(300, 300))
    results = save_asset(
        probe(path), Settings(mode="original", output_format="png"), tmp_path / "out"
    )
    assert len(results) == 2
    with Image.open(results[1].output) as im:
        assert im.size == (80, 50)
        assert im.convert("RGB").getpixel((0, 0)) == (0, 0, 255)


def test_16bit_samples_preserved_or_resize_refused(tmp_path):
    path = tmp_path / "science.tif"
    im = Image.frombytes("I;16", (32, 32), bytes(range(256)) * 8)
    im.save(path, dpi=(300, 300))
    asset = probe(path)
    result = compress_image(asset, Settings(mode="original", output_format="tiff"))
    with Image.open(io.BytesIO(result.data)) as output:
        assert output.tobytes() == im.tobytes()
    with pytest.raises(CompressionError, match="고비트"):
        compress_image(asset, Settings(target_dpi=150))


def test_cmyk_without_profile_is_kept_in_tiff(tmp_path):
    path = tmp_path / "cmyk.tif"
    original = Image.new("CMYK", (40, 30), (10, 40, 90, 30))
    original.save(path)
    result = compress_image(probe(path), Settings(mode="original", output_format="tiff"))
    with Image.open(io.BytesIO(result.data)) as output:
        assert output.mode == "CMYK"
        assert output.tobytes() == original.tobytes()
    with pytest.raises(CompressionError, match="CMYK"):
        compress_image(probe(path), Settings(output_format="jpeg"))


def test_artwork_rejects_lossy_choice(png):
    with pytest.raises(CompressionError):
        compress_image(probe(png), Settings(content="artwork", output_format="jpeg"))


def test_colour_detail_encoder_uses_444(png):
    result = compress_image(probe(png), Settings(output_format="jpeg", jpeg_engine="pillow"))
    with Image.open(io.BytesIO(result.data)) as im:
        assert JpegImagePlugin.get_sampling(im) == 0


def test_cancel_has_no_output(png, tmp_path):
    event = Event()
    event.set()
    with pytest.raises(Cancelled):
        save_asset(probe(png), Settings(), tmp_path / "out", event)
    assert not (tmp_path / "out").exists()


def test_names_never_overwrite_source_or_previous_output(png, tmp_path):
    data = png.read_bytes()
    one = save_asset(probe(png), Settings(), tmp_path)
    two = save_asset(probe(png), Settings(), tmp_path)
    assert one[0].output != two[0].output
    assert png.read_bytes() == data
    assert one[0].output.exists() and two[0].output.exists()


@pytest.mark.parametrize("fmt", ["webp", "avif", "png", "tiff"])
def test_output_formats_reopen_with_icc(png, fmt):
    result = compress_image(probe(png), Settings(output_format=fmt, target_dpi=150))
    with Image.open(io.BytesIO(result.data)) as im:
        im.load()
        assert im.size == (result.width, result.height)
        assert im.info["icc_profile"]
