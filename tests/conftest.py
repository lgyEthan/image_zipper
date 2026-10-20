from __future__ import annotations

import random
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name
from PIL import Image, ImageCms


@pytest.fixture
def photo() -> Image.Image:
    rng = random.Random(42)
    width, height = 720, 480
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            n = rng.randrange(-35, 36)
            pixels[offset : offset + 3] = bytes(
                (
                    max(0, min(255, int(255 * x / width) + n)),
                    max(0, min(255, int(255 * y / height) + n)),
                    max(0, min(255, 120 + n)),
                )
            )
    im = Image.frombytes("RGB", (width, height), bytes(pixels))
    im.info["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return im


@pytest.fixture
def png(photo: Image.Image, tmp_path: Path) -> Path:
    path = tmp_path / "sample.png"
    photo.save(path, dpi=(300, 300), icc_profile=photo.info["icc_profile"])
    return path


def make_pdf(
    path: Path, image: Image.Image, *, nested=False, mask=False, shared=False, inherited=False
):
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(360, 360))
        stream = pdf.make_stream(image.convert("RGB").tobytes())
        stream.Type, stream.Subtype = Name.XObject, Name.Image
        stream.Width, stream.Height = image.size
        stream.ColorSpace, stream.BitsPerComponent = Name.DeviceRGB, 8
        if mask:
            alpha = pdf.make_stream(bytes([200]) * (image.width * image.height))
            alpha.Type, alpha.Subtype = Name.XObject, Name.Image
            alpha.Width, alpha.Height = image.size
            alpha.ColorSpace, alpha.BitsPerComponent = Name.DeviceGray, 8
            stream.SMask = alpha
        font = pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )
        resources = Dictionary(XObject=Dictionary(Im0=stream), Font=Dictionary(F1=font))
        if nested:
            form = pdf.make_stream(b"q 216 0 0 144 0 0 cm /Im0 Do Q")
            form.Type, form.Subtype = Name.XObject, Name.Form
            form.BBox = [0, 0, 216, 144]
            form.Matrix = [1, 0, 0, 1, 20, 20]
            form.Resources = resources
            page.Resources = Dictionary(XObject=Dictionary(Fm0=form), Font=Dictionary(F1=font))
            commands = b"q 1 0 0 1 0 0 cm /Fm0 Do Q"
        else:
            page.Resources = resources
            commands = b"q 216 0 0 144 20 20 cm /Im0 Do Q"
            if shared:
                commands += b" q 0 288 -192 0 340 40 cm /Im0 Do Q"
        if inherited:
            page.obj.Parent.Resources = page.Resources
            del page.obj["/Resources"]
        page.Contents = pdf.make_stream(
            b"BT /F1 16 Tf 24 330 Td (Publication text 123) Tj ET\n" + commands
        )
        pdf.save(path, compress_streams=False)
    return path


@pytest.fixture
def pdf_file(photo, tmp_path):
    return make_pdf(tmp_path / "publication.pdf", photo)
