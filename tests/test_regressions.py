from __future__ import annotations

import io
import struct
import zlib

import pikepdf
from conftest import make_pdf
from pikepdf import Name
from PIL import Image, PngImagePlugin

from image_zipper.imaging import compress_image, probe
from image_zipper.models import Settings
from image_zipper.pdf import compress_pdf


def test_inactive_mode_controls_do_not_constrain_validation():
    Settings(mode="dpi", target_dpi=150, max_bytes=0, quality=70, min_quality=90).validate()
    Settings(mode="size", target_dpi=0, max_bytes=5000).validate()


def test_already_small_jpeg_keeps_original_without_false_failure(photo, tmp_path):
    path = tmp_path / "small.jpg"
    photo.save(path, quality=25, dpi=(300, 300))
    source = path.read_bytes()
    result = compress_image(
        probe(path),
        Settings(
            mode="size",
            output_format="jpeg",
            max_bytes=len(source) + 10,
            min_quality=95,
            quality=95,
        ),
    )
    assert result.target_met
    assert result.data == source
    assert result.width == 720


def test_gray_jpeg_stays_one_channel(tmp_path):
    path = tmp_path / "gray.png"
    Image.linear_gradient("L").save(path)
    result = compress_image(
        probe(path), Settings(mode="original", output_format="jpeg", jpeg_engine="jpegli")
    )
    with Image.open(io.BytesIO(result.data)) as im:
        assert im.mode == "L"


def test_preserving_small_file_still_discloses_assumed_dpi(tmp_path):
    path = tmp_path / "no-resolution.png"
    Image.new("RGB", (30, 20)).save(path)
    result = compress_image(probe(path), Settings(mode="size", reference_dpi=150))
    assert result.data == path.read_bytes()
    assert any("150 DPI" in note and "가정" in note for note in result.warnings)


def test_size_mode_automatically_goes_below_publication_dpi(png):
    results = [
        compress_image(
            probe(png),
            Settings(
                mode="size",
                target_dpi=dpi,
                output_format="jpeg",
                max_bytes=5000,
                quality=95,
                min_quality=90,
                jpeg_engine="pillow",
            ),
        )
        for dpi in (72, 300, 1200)
    ]
    assert all(result.target_met and result.size_bytes <= 5000 for result in results)
    assert 0 < results[0].dpi_x < 72
    assert len({(r.width, r.height, r.data) for r in results}) == 1


def test_pdf_size_mode_ignores_target_dpi(pdf_file):
    outputs = [
        compress_pdf(
            pdf_file,
            Settings(
                mode="size",
                target_dpi=dpi,
                max_bytes=5000,
                quality=95,
                min_quality=90,
            ),
        )
        for dpi in (300, 1200)
    ]
    assert all(r.target_met and r.size_bytes <= 5000 and r.dpi_x < 72 for r in outputs)
    assert outputs[0].dpi_x == outputs[1].dpi_x


def test_png_probe_never_decodes_pixels_including_trailing_exif(tmp_path, monkeypatch):
    path = tmp_path / "trailing-exif.png"
    Image.new("RGB", (1200, 800)).save(path)
    exif = Image.Exif()
    exif[274], exif[282], exif[283], exif[296] = 6, 600, 300, 2
    payload = exif.tobytes()[6:]
    chunk = struct.pack(">I", len(payload)) + b"eXIf" + payload
    chunk += struct.pack(">I", zlib.crc32(b"eXIf" + payload))
    original = path.read_bytes()
    path.write_bytes(original[:-12] + chunk + original[-12:])

    def no_decode(*args, **kwargs):
        raise AssertionError("Metadata loading must not decode any pixels")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no_decode)
    asset = probe(path)
    assert (asset.width, asset.height) == (800, 1200)
    assert (asset.dpi_x, asset.dpi_y) == (300, 600)
    assert asset.orientation == 6


def test_png_without_exif_probes_without_decode(png, monkeypatch):
    def no_decode(*args, **kwargs):
        raise AssertionError("PNG without EXIF must not trigger Pillow's full decode")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no_decode)
    assert probe(png).width == 720


def test_pdf_userunit_and_stretched_axes(photo, tmp_path):
    path = make_pdf(tmp_path / "units.pdf", photo)
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.UserUnit = 2
        pdf.pages[0].Contents = pdf.make_stream(b"q 216 0 0 216 20 20 cm /Im0 Do Q")
        pdf.save(path)
    result = compress_pdf(path, Settings(mode="dpi", target_dpi=70))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        im = pdf.pages[0].Resources.XObject.Im0
        assert im.Width == 420
        assert im.Height == 420
    assert result.dpi_x >= 70


def test_pdf_low_colour_graphics_use_lossless_filter(tmp_path):
    source = Image.new("RGB", (800, 600), "white")
    source.paste((10, 100, 200), (40, 40, 600, 70))
    path = make_pdf(tmp_path / "chart.pdf", source)
    result = compress_pdf(path, Settings(target_dpi=100))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        assert pdf.pages[0].Resources.XObject.Im0.Filter == Name.FlateDecode
