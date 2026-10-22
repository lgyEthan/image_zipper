"""Smoke check for frozen applications; verifies that native codecs were bundled."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from PIL import Image

from . import __version__
from .imaging import compress_image, probe
from .models import Settings
from .pdf import merge_pdf, render_preview


def run(report_path: Path) -> int:
    results = {"version": __version__}
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def record(stage: str, status: str = "ok"):
        results[stage] = status
        report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    record("status", "running")
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
                record(fmt)
            asset = probe(root / "input.png")
            for fmt in ("png", "tiff", "avif", "webp"):
                compress_image(asset, Settings(mode="original", output_format=fmt))
                record(f"encode_{fmt}")
            pdf = merge_pdf([asset, asset], Settings())
            if pdf.pages != 2 or not render_preview(pdf.data).startswith(b"\x89PNG"):
                raise RuntimeError("PDF merge/render failed")
            record("PDF")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication

        from .app import MainWindow, configure_theme

        app = QApplication.instance() or QApplication([])
        configure_theme(app)
        window = MainWindow()
        window.show()
        app.processEvents()
        with tempfile.TemporaryDirectory(prefix="image-zipper-gui-check-") as directory:
            root = Path(directory)
            path = root / "input.png"
            Image.effect_noise((600, 400), 30).convert("RGB").save(path, dpi=(300, 300))

            def wait_for(condition):
                deadline = time.monotonic() + 60
                while not condition() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                if not condition():
                    raise RuntimeError("GUI worker did not complete")
                app.processEvents()

            window.add_paths([path])
            wait_for(lambda: not window.loaders)
            if window.estimator is not None:
                raise RuntimeError("Loading unexpectedly started trial compression")
            wait_for(lambda: not window.before.source.isNull())
            record("GUI_preview_process")
            window.mode.setCurrentIndex(window.mode.findData("size"))
            window.format.setCurrentIndex(window.format.findData("jpeg"))
            window.max_mib.setValue(0.008)
            window._start_estimate()
            wait_for(lambda: window.estimator is None)
            if "목표 용량 충족" not in window.estimate_detail.text():
                raise RuntimeError(f"Byte budget trial failed: {window.estimate_detail.text()}")
            record("GUI_size_process")
            window.destination.setText(str(root / "output"))
            window._export()
            wait_for(lambda: window.exporter is None)
            outputs = list((root / "output").glob("*.jpg"))
            if len(outputs) != 1 or outputs[0].stat().st_size > 8000:
                raise RuntimeError("GUI export failed its byte budget")
            record("GUI_export_process")
            wait_for(lambda: window.previewer is None and window.sampler is None)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        record("GUI")
        results["status"] = "passed"
        status = 0
    except Exception as exc:
        results["status"], results["error"] = "failed", str(exc)
        status = 1
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return status
