from __future__ import annotations

import io

import pikepdf
from conftest import make_pdf
from pikepdf import Name
from PIL import Image

from image_zipper.imaging import compress_image, probe
from image_zipper.models import Settings
from image_zipper.pdf import compress_pdf


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
            min_dpi=300,
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


def test_size_floor_survives_rounding(png):
    result = compress_image(
        probe(png),
        Settings(mode="size", output_format="jpeg", max_bytes=5000, min_dpi=73.3, min_quality=60),
    )
    assert result.dpi_x >= 73.3
    assert result.dpi_y >= 73.3


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
