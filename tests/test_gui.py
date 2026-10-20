from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from image_zipper.app import MainWindow  # noqa: E402
from image_zipper.imaging import probe  # noqa: E402


def spin(app, condition, timeout=30):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert condition(), "GUI worker timed out"
    app.processEvents()


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.closeAllWindows()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_gui_estimate_latest_settings_and_export(png, tmp_path, app):
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window.format.setCurrentIndex(window.format.findData("jpeg"))
        for value in (250, 200, 180, 150):
            window.dpi.setValue(value)
        spin(app, lambda: "시험 압축:" in window.estimate_detail.text())
        assert "150 DPI" in window.metric_label.text()
        window.destination.setText(str(tmp_path / "exports"))
        window._export()
        spin(app, lambda: window.exporter is None)
        assert list((tmp_path / "exports").glob("*.jpg"))
        assert "1개 저장" in window.status.text()
    finally:
        if window.estimator:
            window.estimator.cancel.set()
        spin(app, lambda: window.estimator is None)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_gui_clearing_selection_ignores_stale_estimate(png, app):
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window._start_estimate()
        window._clear()
        spin(app, lambda: window.estimator is None)
        assert window.assets == []
        assert window.metric_label.text() == "—"
        assert not window.export_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
