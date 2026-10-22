from __future__ import annotations

import io
import random

import pikepdf
import pypdfium2
import pytest
from conftest import make_pdf
from pikepdf import Array, Dictionary, Name
from PIL import Image

from image_zipper.imaging import probe
from image_zipper.models import CompressionError, Settings
from image_zipper.pdf import PDFIUM_LOCK, compress_pdf, merge_pdf, render_preview


def text_content(data: bytes) -> str:
    with PDFIUM_LOCK, pypdfium2.PdfDocument(data) as pdf:
        return pdf[0].get_textpage().get_text_range()


def test_pdf_resizes_images_but_retains_text_and_page_size(pdf_file):
    original = pdf_file.read_bytes()
    result = compress_pdf(pdf_file, Settings(target_dpi=150))
    assert len(result.data) < len(original) * 0.3
    assert "Publication text 123" in text_content(result.data)
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        assert tuple(pdf.pages[0].mediabox) == (0, 0, 360, 360)
        image = pdf.pages[0].Resources.XObject.Im0
        assert (int(image.Width), int(image.Height)) == (450, 300)
    assert result.dpi_x == pytest.approx(150)


@pytest.mark.parametrize("nested,inherited", [(True, False), (False, True)])
def test_form_and_inherited_resources(photo, tmp_path, nested, inherited):
    path = make_pdf(tmp_path / "form.pdf", photo, nested=nested, inherited=inherited)
    result = compress_pdf(path, Settings(target_dpi=100))
    assert result.dpi_x == pytest.approx(100, abs=1)
    assert "Publication text 123" in text_content(result.data)


def test_shared_image_uses_largest_rotated_placement(photo, tmp_path):
    path = make_pdf(tmp_path / "shared.pdf", photo, shared=True)
    result = compress_pdf(path, Settings(target_dpi=100))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        image = pdf.pages[0].Resources.XObject.Im0
        assert image.Width == 400  # 288 pt placement = 4 inches
        assert image.Height in (266, 267)
    assert result.dpi_x == pytest.approx(100, abs=1)


def test_transparency_mask_untouched(photo, tmp_path):
    path = make_pdf(tmp_path / "masked.pdf", photo, mask=True)
    result = compress_pdf(path, Settings(target_dpi=72))
    with pikepdf.Pdf.open(path) as original, pikepdf.Pdf.open(io.BytesIO(result.data)) as output:
        old = original.pages[0].Resources.XObject.Im0
        new = output.pages[0].Resources.XObject.Im0
        assert (new.Width, new.Height) == (old.Width, old.Height)
        assert new.read_bytes() == old.read_bytes()
        assert new.SMask.read_bytes() == old.SMask.read_bytes()
    assert any("보존" in warning for warning in result.warnings)


def test_pdf_size_target_and_failure(pdf_file):
    result = compress_pdf(pdf_file, Settings(mode="size", max_bytes=20000, min_quality=60))
    assert result.target_met
    assert result.size_bytes <= 20000
    assert "Publication text 123" in text_content(result.data)
    # An incompressible, valid PDF comment is document content, not image DPI.
    # Preserving it makes a 1 KiB cap truly impossible even at one-pixel images.
    with pikepdf.Pdf.open(pdf_file, allow_overwriting_input=True) as pdf:
        content = pdf.pages[0].Contents.read_bytes()
        content += b"\n%" + random.Random(7).randbytes(8192).hex().encode() + b"\n"
        pdf.pages[0].Contents = pdf.make_stream(content)
        pdf.save(pdf_file)
    impossible = compress_pdf(pdf_file, Settings(mode="size", max_bytes=1024))
    assert not impossible.target_met


def test_vector_only_pdf_has_no_invented_dpi(tmp_path):
    path = tmp_path / "vector.pdf"
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page()
        page.Contents = pdf.make_stream(b"0 0 1 rg 10 10 300 300 re f")
        pdf.save(path)
    result = compress_pdf(path, Settings())
    assert result.dpi_x is None
    assert result.dpi_y is None
    assert render_preview(result.data).startswith(b"\x89PNG")


def test_merge_all_frames_in_order_and_keep_print_size(tmp_path):
    path = tmp_path / "frames.tiff"
    images = [Image.new("RGB", (300, 600), "red"), Image.new("RGB", (300, 600), "blue")]
    images[0].save(path, save_all=True, append_images=images[1:], dpi=(300, 300))
    result = merge_pdf([probe(path)], Settings(target_dpi=150))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        assert len(pdf.pages) == 2
        assert tuple(pdf.pages[0].mediabox) == (0, 0, 72, 144)
    with Image.open(io.BytesIO(render_preview(result.data, edge=100, page_number=0))) as im:
        assert im.getpixel((25, 50))[0] > 240
    with Image.open(io.BytesIO(render_preview(result.data, edge=100, page_number=1))) as im:
        assert im.getpixel((25, 50))[2] > 240


def test_merged_pdf_cap_applies_to_entire_document(png):
    result = merge_pdf(
        [probe(png), probe(png)], Settings(mode="size", max_bytes=30000, min_quality=60)
    )
    assert result.target_met
    assert result.pages == 2
    assert result.size_bytes <= 30000


def test_encrypted_pdf_not_silently_decrypted(tmp_path):
    path = tmp_path / "encrypted.pdf"
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user=""))
    with pytest.raises(CompressionError, match="암호"):
        probe(path)


def test_signed_pdf_refused(tmp_path):
    path = tmp_path / "signed.pdf"
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        pdf.Root.TestSignature = pdf.make_indirect(
            Dictionary(Type=Name.Sig, ByteRange=[0, 1, 2, 3])
        )
        pdf.save(path)
    with pytest.raises(CompressionError, match="서명"):
        compress_pdf(path, Settings())


def test_pdf_nonimage_content_and_document_features_survive(pdf_file, tmp_path):
    path = tmp_path / "annotated.pdf"
    with pikepdf.Pdf.open(pdf_file) as pdf:
        pdf.docinfo["/Title"] = "Research figure"
        pdf.Root.AcroForm = Dictionary(Fields=Array([]))
        with pdf.open_outline() as outline:
            outline.root.append(pikepdf.OutlineItem("Chapter", 0))
        page = pdf.pages[0]
        page.Rotate = 90
        page.CropBox = [10, 10, 350, 350]
        page.Annots = [
            pdf.make_indirect(
                Dictionary(
                    Type=Name.Annot,
                    Subtype=Name.Text,
                    Rect=[20, 20, 40, 40],
                    Contents="Keep this note",
                )
            )
        ]
        content = page.Contents.read_bytes()
        pdf.save(path)
    original = path.read_bytes()
    result = compress_pdf(path, Settings(target_dpi=120))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as output:
        page = output.pages[0]
        assert page.Contents.read_bytes() == content
        assert page.Rotate == 90
        assert list(page.CropBox) == [10, 10, 350, 350]
        assert str(page.Annots[0].Contents) == "Keep this note"
        assert str(output.docinfo["/Title"]) == "Research figure"
        assert "/AcroForm" in output.Root
        with output.open_outline() as outline:
            assert outline.root[0].title == "Chapter"
    assert path.read_bytes() == original
    with pytest.raises(CompressionError, match="문서 구조"):
        merge_pdf([probe(path)], Settings())


def test_direct_pdf_embedding_preserves_alpha_icc_and_page_geometry(tmp_path):
    from PIL import ImageCms

    path = tmp_path / "alpha.png"
    source = Image.new("RGBA", (100, 200), (30, 90, 180, 80))
    source.putpixel((0, 0), (90, 30, 10, 0))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source.save(path, icc_profile=profile, dpi=(100, 100))
    result = merge_pdf([probe(path)], Settings(mode="original"))
    with pikepdf.Pdf.open(io.BytesIO(result.data)) as pdf:
        page = pdf.pages[0]
        assert tuple(float(v) for v in page.MediaBox) == pytest.approx((0, 0, 72, 144), abs=0.01)
        image = page.Resources.XObject.Im0
        assert image.read_bytes() == source.convert("RGB").tobytes()
        assert image.SMask.read_bytes() == source.getchannel("A").tobytes()
        assert image.ColorSpace[1].read_bytes() == profile
