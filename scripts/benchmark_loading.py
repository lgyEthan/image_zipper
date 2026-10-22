"""Reproducible GUI loading benchmark; input pixels are never copied into the repo."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    from pictumi.app import MainWindow, configure_theme
    from pictumi.imaging import probe

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--count", type=int, default=1000)
    args = parser.parse_args()
    source = args.source.resolve()
    start = time.perf_counter()
    asset = probe(source)
    metadata_seconds = time.perf_counter() - start
    app = QApplication([])
    configure_theme(app)
    first = []
    ticks = []

    class Window(MainWindow):
        def _asset_loaded(self, asset):
            super()._asset_loaded(asset)
            if not first:
                first.append(time.perf_counter())

    window = Window()
    window.show()
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))

    def wait_for(condition, timeout=120):
        deadline = time.perf_counter() + timeout
        while not condition() and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)
        if not condition():
            raise RuntimeError("Benchmark timed out")
        app.processEvents()

    try:
        with tempfile.TemporaryDirectory(
            prefix="zipper-benchmark-", dir=source.parent
        ) as directory:
            paths = []
            for index in range(args.count):
                path = Path(directory) / f"image-{index:05d}{source.suffix}"
                os.link(source, path)
                paths.append(path)
            app.processEvents()
            timer.start()
            start = time.perf_counter()
            window.add_paths(paths)
            wait_for(lambda: not window.loaders)
            loaded = time.perf_counter()
            wait_for(lambda: not window.before.source.isNull())
            previewed = time.perf_counter()
            assert window.estimator is None
            window.mode.setCurrentIndex(window.mode.findData("size"))
            start_setting = time.perf_counter()
            window.max_mib.setValue(0.5)
            updated = time.perf_counter() - start_setting
            assert window.estimator is None
            result = {
                "source_pixels": [asset.width, asset.height],
                "source_bytes": asset.size_bytes,
                "count": len(window.assets),
                "storage": "hardlinks to one local file; warm filesystem cache",
                "metadata_seconds": metadata_seconds,
                "first_row_seconds": first[0] - start,
                "all_rows_seconds": loaded - start,
                "preview_seconds": previewed - start,
                "size_setting_seconds": updated,
                "longest_ui_tick_seconds": max(
                    (b - a for a, b in zip(ticks, ticks[1:], strict=False)), default=0
                ),
                "automatic_trial_encodes": 0,
            }
            print(json.dumps(result, indent=2))
            window.close()
            wait_for(lambda: window.previewer is None and window.estimator is None)
    finally:
        timer.stop()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    main()
