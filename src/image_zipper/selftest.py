"""Smoke check for frozen applications; verifies that native codecs were bundled."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PIL import Image

from .imaging import compress_image, probe
from .models import Settings
from .pdf import merge_pdf, render_preview


def run(report_path: Path) -> int:
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix="image-zipper-check-") as directory:
            root = Path(directory)
            source = Image.new("RGB", (120, 80), (36, 118, 100))
            for fmt, extension in (
                ("PNG", "png"),
                ("JPEG", "jpg"),
                ("HEIF", "heic"),
                ("TIFF", "tif"),
                ("BMP", "bmp"),
                ("AVIF", "avif"),
                ("WEBP", "webp"),
            ):
                path = root / f"input.{extension}"
                source.save(path, format=fmt)
                result = compress_image(
                    probe(path), Settings(mode="original", output_format="jpeg")
                )
                if not result.data.startswith(b"\xff\xd8"):
                    raise RuntimeError(f"{fmt}: invalid JPEG output")
                results[fmt] = "ok"
            asset = probe(root / "input.png")
            for fmt in ("png", "tiff", "avif", "webp"):
                compress_image(asset, Settings(mode="original", output_format=fmt))
                results[f"encode_{fmt}"] = "ok"
            pdf = merge_pdf([asset, asset], Settings())
            if pdf.pages != 2 or not render_preview(pdf.data).startswith(b"\x89PNG"):
                raise RuntimeError("PDF merge/render failed")
            results["PDF"] = "ok"
        from PySide6.QtWidgets import QApplication

        from .app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        window.close()
        results["GUI"] = "ok"
        results["status"] = "passed"
        status = 0
    except Exception as exc:
        results["status"], results["error"] = "failed", str(exc)
        status = 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return status
