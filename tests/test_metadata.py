from __future__ import annotations

import io
import struct
from dataclasses import replace

import pikepdf
import pytest
from PIL import Image, PngImagePlugin

from image_zipper.imaging import compress_image, probe
from image_zipper.models import Settings
from image_zipper.pdf import compress_pdf


@pytest.mark.parametrize("output_format", ["jpeg", "png", "webp", "avif", "tiff"])
@pytest.mark.parametrize("strip", [False, True])
def test_capture_metadata_is_optional_and_color_orientation_survive(
    photo, tmp_path, output_format, strip
):
    path = tmp_path / "camera.jpg"
    exif = Image.Exif()
    exif[274], exif[315] = 6, "Test Photographer"
    exif[34853] = {1: "N", 2: (10, 20, 30)}
    photo.save(
        path,
        exif=exif,
        xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/">private</x:xmpmeta>',
        icc_profile=photo.info["icc_profile"],
        dpi=(300, 300),
    )
    original = path.read_bytes()
    result = compress_image(
        probe(path), Settings(mode="original", output_format=output_format, strip_metadata=strip)
    )
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.size == (480, 720)
        assert out.getexif().get(274, 1) == 1
        assert out.info["icc_profile"] == photo.info["icc_profile"]
        if strip:
            assert 315 not in out.getexif()
            assert 34853 not in out.getexif()
            assert not out.info.get("xmp")
        elif output_format != "tiff":
            assert out.getexif()[315] == "Test Photographer"
    assert path.read_bytes() == original


def test_png_text_is_removed_without_changing_pixels_or_gamma(photo, tmp_path):
    path = tmp_path / "text.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("Description", "private caption")
    metadata.add(b"gAMA", struct.pack(">I", 45455))
    photo.save(path, pnginfo=metadata, dpi=(300, 300), icc_profile=photo.info["icc_profile"])
    settings = Settings(mode="size", max_bytes=2_000_000, strip_metadata=True)
    result = compress_image(probe(path), settings)
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.tobytes() == photo.tobytes()
        assert not out.text
        assert out.info["gamma"] == pytest.approx(0.45455)
        assert out.info["dpi"][0] == pytest.approx(300, abs=0.1)
        assert out.info["icc_profile"] == photo.info["icc_profile"]
    preserved = compress_image(probe(path), replace(settings, strip_metadata=False))
    assert preserved.data == path.read_bytes()


def test_pillow_jpeg_comment_removed(tmp_path):
    path = tmp_path / "comment.jpg"
    Image.new("RGB", (80, 60), "blue").save(path, comment=b"private comment")
    result = compress_image(
        probe(path), Settings(mode="original", jpeg_engine="pillow", strip_metadata=True)
    )
    with Image.open(io.BytesIO(result.data)) as out:
        assert not out.info.get("comment")


def test_multipage_tiff_strip_preserves_frames_and_pixels(tmp_path):
    path = tmp_path / "pages.tif"
    frames = [Image.new("RGB", (50, 30), color) for color in ("red", "blue")]
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        tiffinfo={315: "Test Photographer", 270: "Private caption"},
        dpi=(300, 300),
    )
    result = compress_image(
        probe(path), Settings(mode="size", max_bytes=1_000_000, strip_metadata=True)
    )
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.n_frames == 2
        for index, expected in enumerate(frames):
            out.seek(index)
            assert out.tobytes() == expected.tobytes()
            assert 315 not in out.tag_v2 and 270 not in out.tag_v2
            assert out.info["dpi"] == (300, 300)


@pytest.mark.parametrize("strip", [False, True])
def test_pdf_document_metadata_option_preserves_page_contents_and_annotations(tmp_path, strip):
    path = tmp_path / "document.pdf"
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(200, 300))
        page.Contents = pdf.make_stream(b"0 0 1 rg 10 10 100 200 re f")
        annotation = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Text,
            Rect=[10, 10, 20, 20],
            Contents="Keep annotation",
        )
        page.Annots = [pdf.make_indirect(annotation)]
        pdf.docinfo["/Author"] = "Test Photographer"
        with pdf.open_metadata() as meta:
            meta["dc:title"] = "Private title"
        pdf.docinfo["/Author"] = "Test Photographer"
        page.Metadata = pdf.Root.Metadata
        pdf.save(path)
    original = path.read_bytes()
    result = compress_pdf(path, Settings(mode="size", max_bytes=1_000_000, strip_metadata=strip))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        assert pdf.pages[0].Contents.read_bytes() == b"0 0 1 rg 10 10 100 200 re f"
        assert str(pdf.pages[0].Annots[0].Contents) == "Keep annotation"
        assert tuple(pdf.pages[0].mediabox) == (0, 0, 200, 300)
        if strip:
            assert not pdf.docinfo
            assert "/Metadata" not in pdf.Root
            assert "/Metadata" not in pdf.pages[0].obj
        else:
            assert str(pdf.docinfo.Author) == "Test Photographer"
            assert "/Metadata" in pdf.Root
    assert path.read_bytes() == original
