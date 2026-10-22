from __future__ import annotations

import io
from pathlib import Path
from threading import Event

import pytest
from PIL import Image, JpegImagePlugin

from pictumi.imaging import compress_image, probe
from pictumi.models import Cancelled, CompressionError, Settings
from pictumi.service import save_asset


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
    if fmt == "HEIF":
        fixture = Path(__file__).resolve().parents[1] / "src/pictumi/assets/selftest.heic"
        path.write_bytes(fixture.read_bytes())
    else:
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
    # A valid ICC profile can include trailing alignment/padding. Make its
    # declared size alone exceed the cap, regardless of JPEG header efficiency.
    with Image.open(png) as source:
        profile = source.info["icc_profile"] + bytes(2048)
        profile = len(profile).to_bytes(4, "big") + profile[4:]
        source.save(tmp_path / "large-profile.png", icc_profile=profile)
    asset = probe(tmp_path / "large-profile.png")
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


def test_multiframe_tiff_keeps_every_page_in_one_file(tmp_path):
    path = tmp_path / "multipage.tif"
    frames = [Image.new("RGB", (60, 40), "red"), Image.new("RGB", (80, 50), "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], dpi=(300, 300))
    results = save_asset(probe(path), Settings(mode="original"), tmp_path / "out")
    assert len(results) == 1
    with Image.open(results[0].output) as im:
        assert im.n_frames == 2
        assert im.size == (60, 40)
        assert im.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        assert im.info["dpi"] == (300, 300)
        im.seek(1)
        assert im.size == (80, 50)
        assert im.convert("RGB").getpixel((0, 0)) == (0, 0, 255)
        assert im.info["dpi"] == (300, 300)
    with pytest.raises(CompressionError, match="여러 페이지"):
        save_asset(probe(path), Settings(output_format="jpeg"), tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_animation_is_not_silently_flattened(tmp_path):
    path = tmp_path / "animation.gif"
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100, 400], loop=2)
    original = path.read_bytes()
    with pytest.raises(CompressionError, match="다중 프레임"):
        save_asset(probe(path), Settings(), tmp_path / "out")
    assert path.read_bytes() == original
    assert not (tmp_path / "out").exists()


def test_transparent_image_is_not_flattened_to_white(tmp_path):
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (80, 60), (30, 80, 120, 40)).save(path)
    with pytest.raises(CompressionError, match="투명도"):
        save_asset(probe(path), Settings(output_format="jpeg"), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("fmt", ["png", "tiff", "webp", "avif"])
def test_alpha_values_are_preserved_at_original_resolution(tmp_path, fmt):
    path = tmp_path / "alpha.png"
    source = Image.new("RGBA", (128, 128), (50, 130, 190, 255))
    source.putalpha(Image.effect_noise(source.size, 80))
    source.save(path)
    result = compress_image(probe(path), Settings(mode="original", output_format=fmt, quality=70))
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.convert("RGBA").getchannel("A").tobytes() == source.getchannel("A").tobytes()


@pytest.mark.parametrize("engine", ["pillow", "jpegli"])
def test_capture_exif_xmp_and_comment_survive_recompression(tmp_path, engine):
    path = tmp_path / "capture.jpg"
    exif = Image.Exif()
    exif[271], exif[315], exif[306] = "Test Camera", "Test Author", "2020:01:02 03:04:05"
    exif[34665] = {36867: "2020:01:02 03:04:05", 40962: 200, 40963: 100}
    exif[34853] = {1: "N", 2: (10, 20, 30), 3: "E", 4: (40, 50, 0)}
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">test metadata</x:xmpmeta>'
    Image.new("RGB", (200, 100), (20, 60, 100)).save(
        path, exif=exif, xmp=xmp, comment=b"test comment", dpi=(300, 300)
    )
    original = path.read_bytes()
    result = compress_image(probe(path), Settings(target_dpi=150, jpeg_engine=engine))
    with Image.open(io.BytesIO(result.data)) as out:
        metadata = out.getexif()
        assert metadata[271] == "Test Camera"
        assert metadata[315] == "Test Author"
        assert metadata[306] == "2020:01:02 03:04:05"
        assert metadata.get_ifd(34665)[36867] == "2020:01:02 03:04:05"
        assert metadata.get_ifd(34665)[40962] == 100
        assert tuple(metadata.get_ifd(34853)[2]) == (10, 20, 30)
        assert out.info["xmp"] == xmp
        assert out.info["comment"] == b"test comment"
        assert out.size == (100, 50)
    assert path.read_bytes() == original


def test_png_text_and_gamma_survive_resize(tmp_path):
    import struct

    from PIL.PngImagePlugin import PngInfo

    metadata = PngInfo()
    metadata.add_itxt("Description", "테스트 문서", lang="ko")
    metadata.add(b"gAMA", struct.pack(">I", 45455))
    path = tmp_path / "metadata.png"
    Image.new("RGB", (200, 100), "red").save(path, pnginfo=metadata, dpi=(300, 300))
    result = compress_image(probe(path), Settings(target_dpi=150))
    with Image.open(io.BytesIO(result.data)) as output:
        output.load()
        assert output.text["Description"] == "테스트 문서"
        assert output.text["Description"].lang == "ko"
        assert output.info["gamma"] == pytest.approx(0.45455)


def test_multipage_tiff_budget_applies_to_entire_container(photo, tmp_path):
    path = tmp_path / "two-pages.tif"
    photo.save(path, save_all=True, append_images=[photo], dpi=(300, 300))
    result = compress_image(probe(path), Settings(mode="size", max_bytes=50000))
    assert result.target_met and result.size_bytes <= 50000
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.n_frames == 2
        first = out.size
        out.seek(1)
        assert out.size == first


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
