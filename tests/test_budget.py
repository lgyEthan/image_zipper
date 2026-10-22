from __future__ import annotations

import io
from dataclasses import replace

import pytest
from PIL import Image

from pictumi import imaging, service
from pictumi.imaging import (
    compress_image,
    encode_pixels,
    preview_source,
    probe,
    sample_estimate,
)
from pictumi.models import Settings, TrialCache, source_fingerprints
from pictumi.pdf import compress_pdf


def test_size_mode_spends_budget_above_dpi_preset_quality(photo, png, monkeypatch):
    settings = Settings(
        mode="size", quality=50, min_quality=50, output_format="jpeg", jpeg_engine="pillow"
    )
    cap = len(encode_pixels(photo, "jpeg", 98, (300, 300), settings))
    sizes = []
    resize = imaging.resize_to

    def counted(im, size):
        sizes.append(size)
        return resize(im, size)

    monkeypatch.setattr(imaging, "resize_to", counted)
    result = compress_image(probe(png), replace(settings, max_bytes=cap))
    assert 0.95 * cap <= result.size_bytes <= cap
    assert result.quality >= 97
    assert (result.width, result.height) == photo.size
    assert sizes == [photo.size]  # Quality trials reuse the same pixels.


@pytest.mark.parametrize("limit", [14000, 50000, 140000])
def test_png_final_optimized_bytes_are_close_to_limit(png, limit):
    result = compress_image(probe(png), Settings(mode="size", max_bytes=limit))
    assert result.target_met
    assert 0.90 * limit <= result.size_bytes <= limit
    with Image.open(io.BytesIO(result.data)) as im:
        im.load()
        assert im.size == (result.width, result.height)


def test_pdf_decodes_each_image_once_while_fitting_budget(pdf_file, monkeypatch):
    from pikepdf import PdfImage

    calls = []
    original = PdfImage.as_pil_image

    def counted(self, *args, **kwargs):
        calls.append(self.obj.objgen)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PdfImage, "as_pil_image", counted)
    result = compress_pdf(pdf_file, Settings(mode="size", max_bytes=20000, min_quality=60))
    assert 0.90 * 20000 <= result.size_bytes <= 20000
    assert len(calls) == 1


def test_detailed_444_jpeg_does_not_exhaust_pillow_buffer(tmp_path):
    import random

    pixels = random.Random(28).randbytes(800 * 600 * 3)
    with Image.frombytes("RGB", (800, 600), pixels) as source:
        data = encode_pixels(source, "jpeg", 90, (300, 300), Settings(jpeg_engine="pillow"))
    with Image.open(io.BytesIO(data)) as result:
        result.load()
        assert result.size == (800, 600)
        assert all(component[1:3] == (1, 1) for component in result.layer)


def test_sample_dpi_uses_image_content_instead_of_source_storage_size(tmp_path):
    path = tmp_path / "uncompressed.png"
    Image.new("RGB", (1600, 1000), "white").save(path, compress_level=0, dpi=(300, 300))
    asset = probe(path)
    assert asset.size_bytes > 2_000_000
    _, sample = preview_source(asset)
    dpi, _, note = sample_estimate(asset, Settings(mode="size", max_bytes=100_000), sample)
    assert dpi == pytest.approx(300, abs=1)
    assert "표본" in note


def test_trial_result_is_reused_only_with_unchanged_input_and_settings(png, tmp_path, monkeypatch):
    settings = Settings(mode="size", max_bytes=14000, output_format="jpeg")
    asset = probe(png)
    result = compress_image(asset, settings)
    path = tmp_path / "cached-output"
    path.write_bytes(result.data)
    cache = TrialCache(
        path,
        settings,
        source_fingerprints([asset]),
        False,
        replace(result, data=b""),
        result.size_bytes,
    )

    def no_reencode(*args, **kwargs):
        raise AssertionError("A measured output must not be recompressed when saving")

    monkeypatch.setattr(service, "compress_image", no_reencode)
    saved = service.save_asset(asset, settings, tmp_path / "output", cached=cache)
    assert saved[0].output.read_bytes() == result.data
    assert cache.read([asset], replace(settings, max_bytes=15000), False) is None
    assert cache.read([asset], settings, True) is None
    png.touch()
    assert cache.read([asset], settings, False) is None
    assert [item.suffix for item in (tmp_path / "output").iterdir()] == [".jpg"]


def test_cli_export_does_not_create_sidecar_report(png, tmp_path):
    from pictumi.cli import main

    folder = tmp_path / "output"
    assert main([str(png), "--output", str(folder), "--format", "jpeg"]) == 0
    assert [file.suffix for file in folder.iterdir()] == [".jpg"]


def test_png_budget_limits_expensive_optimization_to_two_encodes(png, monkeypatch):
    calls = []
    optimize = imaging.oxipng.optimize_from_memory

    def counted(data, **options):
        calls.append(len(data))
        return optimize(data, **options)

    monkeypatch.setattr(imaging.oxipng, "optimize_from_memory", counted)
    result = compress_image(probe(png), Settings(mode="size", max_bytes=50000))
    assert result.target_met and result.size_bytes <= 50000
    assert 1 <= len(calls) <= 2


def test_close_enough_jpeg_does_not_reencode_to_fill_last_bytes(photo, tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    photo.save(source, compress_level=0)
    settings = Settings(mode="size", min_quality=90, output_format="jpeg", jpeg_engine="pillow")
    expected = encode_pixels(photo, "jpeg", 90, (300, 300), settings)
    settings = replace(settings, max_bytes=round(len(expected) / 0.94))
    calls = []
    encode = imaging.encode_pixels

    def counted(*args, **kwargs):
        calls.append(args[2])
        return encode(*args, **kwargs)

    monkeypatch.setattr(imaging, "encode_pixels", counted)
    result = compress_image(probe(source), settings)
    assert 0.90 * settings.max_bytes <= result.size_bytes <= settings.max_bytes
    assert calls == [90]
