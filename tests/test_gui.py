from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from image_zipper.app import MainWindow, configure_theme  # noqa: E402
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
    configure_theme(application)
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
        window._start_estimate()
        spin(app, lambda: window.estimator is None)
        assert "시험 압축:" in window.estimate_detail.text(), window.estimate_detail.text()
        assert "150 DPI" in window.metric_label.text()
        cached_bytes = window.trial_cache.path.read_bytes()
        window.destination.setText(str(tmp_path / "exports"))
        window._export()
        spin(app, lambda: window.exporter is None)
        assert list((tmp_path / "exports").glob("*.jpg"))[0].read_bytes() == cached_bytes
        assert not list((tmp_path / "exports").glob("*.json"))
        assert "1개 저장" in window.status.text()
    finally:
        if window.estimator:
            window.estimator.cancel.set()
        if window.previewer:
            window.previewer.cancel.set()
        window.debounce.stop()
        spin(app, lambda: window.estimator is None and window.previewer is None)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_gui_clearing_selection_ignores_stale_estimate(png, app):
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window._start_estimate()
        window._clear()
        spin(app, lambda: window.estimator is None and window.previewer is None)
        assert window.assets == []
        assert window.metric_label.text() == "—"
        assert not window.export_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def dispose(window, app):
    window.close()
    spin(
        app,
        lambda: (
            all(
                w is None
                for w in (window.estimator, window.previewer, window.sampler, window.exporter)
            )
            and not window.loaders
        ),
    )
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_native_window_controls_and_simple_default_settings(app):
    window = MainWindow()
    try:
        flags = window.windowFlags()
        for flag in (
            Qt.WindowType.WindowTitleHint,
            Qt.WindowType.WindowCloseButtonHint,
            Qt.WindowType.WindowMinimizeButtonHint,
            Qt.WindowType.WindowMaximizeButtonHint,
        ):
            assert flags & flag
        assert not flags & Qt.WindowType.FramelessWindowHint
        assert window.advanced_panel.isHidden()
        assert window.artwork.parent() == window.advanced_panel
        assert window.measure_button.text() == "결과 확인"
    finally:
        dispose(window, app)


def test_loading_and_settings_never_automatically_trial_encode(png, tmp_path, app, monkeypatch):
    from image_zipper import app as app_module

    def no_estimate(*args, **kwargs):
        raise AssertionError("Adding/selecting files must not perform full trial compression")

    monkeypatch.setattr(app_module, "Estimator", no_estimate)
    paths = []
    for index in range(100):
        path = tmp_path / f"batch-{index:03d}.png"
        os.link(png, path)
        paths.append(path)
    window = MainWindow()
    try:
        window.add_paths(paths)
        spin(app, lambda: not window.loaders)
        assert len(window.assets) == 100
        assert window.total_bytes == 100 * png.stat().st_size
        window.mode.setCurrentIndex(window.mode.findData("size"))
        window.max_mib.setValue(0.02)
        window.preset.setCurrentIndex(2)
        spin(app, lambda: window.previewer is None and not window.debounce.isActive())
        assert not window.before.source.isNull(), window.before.toolTip() or window.before.text()
        assert window.estimator is None
        assert "약" in window.metric_label.text()
        assert "빠른 추정" in window.estimate_detail.text()
    finally:
        dispose(window, app)


def test_size_mode_hides_dpi_and_exports_under_cap_with_publication_preset(png, tmp_path, app):
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window.mode.setCurrentIndex(window.mode.findData("size"))
        window.format.setCurrentIndex(window.format.findData("jpeg"))
        window.max_mib.setValue(0.005)
        assert window.dpi.isHidden()
        assert not window.max_mib.isHidden()
        assert window.settings().target_dpi == 300
        window.destination.setText(str(tmp_path / "size-output"))
        window._export()
        spin(app, lambda: window.exporter is None)
        outputs = list((tmp_path / "size-output").glob("*.jpg"))
        assert len(outputs) == 1
        assert outputs[0].stat().st_size <= round(0.005 * 1024 * 1024)
        assert probe(outputs[0]).dpi_x < 300
        assert "0개 오류" in window.status.text()
        window.mode.setCurrentIndex(window.mode.findData("dpi"))
        assert not window.dpi.isHidden()
        assert window.max_mib.isHidden()
    finally:
        dispose(window, app)


def test_native_compression_keeps_gui_event_loop_responsive(png, app):
    window = MainWindow()
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    try:
        window._asset_loaded(probe(png))
        window.format.setCurrentIndex(window.format.findData("avif"))
        timer.start()
        window._start_estimate()
        spin(app, lambda: window.estimator is None)
        assert len(ticks) >= 3
        assert max(b - a for a, b in zip(ticks, ticks[1:], strict=False)) < 1.0
    finally:
        timer.stop()
        dispose(window, app)


def test_cancelled_preview_restarts_for_same_selected_file(png, app):
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window._start_preview()
        window.previewer.cancel.set()
        spin(app, lambda: not window.before.source.isNull())
        assert window.preview_asset == window.assets[0]
    finally:
        dispose(window, app)


def crash_export(connection, kind, assets, settings, cancel, **options):
    connection.send(("progress", 0, len(assets), assets[0].path.name))
    os._exit(73)


def crash_after_completion(connection, kind, assets, settings, cancel, **options):
    connection.send(("completed", [], [], False))
    connection.send(("done",))
    os._exit(73)


@pytest.mark.parametrize("job", [crash_export, crash_after_completion])
def test_crashed_export_reports_failure_and_restores_controls(png, tmp_path, app, monkeypatch, job):
    from image_zipper import workers

    monkeypatch.setattr(workers, "run_job", job)
    window = MainWindow()
    try:
        window._asset_loaded(probe(png))
        window.destination.setText(str(tmp_path / "exports"))
        window._export()
        spin(app, lambda: window.exporter is None)
        assert "저장 실패" in window.status.text()
        assert "0개 저장" in window.status.text()
        assert "1개 오류" in window.status.text()
        assert "처리 중" not in window.status.text()
        assert window.export_button.isEnabled()
        assert not window.cancel_button.isEnabled()
        assert "오류:" in window.log.toPlainText()
    finally:
        dispose(window, app)


def test_export_cancelled_before_start_emits_completion(png, tmp_path, app):
    from image_zipper.models import Settings
    from image_zipper.workers import Exporter

    worker = Exporter([probe(png)], Settings(), tmp_path / "exports", None)
    completed = []
    worker.completed.connect(lambda *values: completed.append(values))
    worker.cancel.set()
    worker.start()
    spin(app, lambda: not worker.isRunning())
    assert completed == [([], [], True)]
    assert not (tmp_path / "exports").exists()


def test_estimated_size_can_exceed_two_gibibytes(app):
    from image_zipper.models import Encoded, Settings
    from image_zipper.workers import Estimator

    worker = Estimator(1, [], Settings(), False)
    received = []
    worker.ready.connect(lambda *values: received.append(values))
    result = Encoded(b"", ".pdf")
    worker.message(("estimate", result, 3 * 1024**3, b""))
    assert received == [(1, result, 3 * 1024**3, b"")]


@pytest.mark.parametrize("dark_mode", [False, True])
def test_selection_contrast_on_dark_host_palette(app, dark_mode):
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Base, QColor("#111111"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#111111"))
    app.setPalette(dark)
    configure_theme(app, dark=dark_mode)

    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(values, (0.2126, 0.7152, 0.0722), strict=True))

    window = MainWindow()
    try:
        for widget in (
            window.table,
            window.mode.view(),
            window.destination,
            window.dpi.lineEdit(),
            window.log,
        ):
            widget.ensurePolished()
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                palette = widget.palette()
                fg = luminance(palette.color(group, QPalette.ColorRole.HighlightedText))
                bg = luminance(palette.color(group, QPalette.ColorRole.Highlight))
                assert (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05) >= 4.5
    finally:
        dispose(window, app)


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="Cocoa accessibility adapter")
def test_file_accessibility_rows_survive_updates_and_removal(app):
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface
    from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem

    from image_zipper.file_table import FileTable

    table = FileTable(2, 2)
    table.setHorizontalHeaderLabels(["File", "Size"])
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setItem(0, 0, QTableWidgetItem("first.png"))
    table.setItem(1, 0, QTableWidgetItem("second.jpg"))
    table.setItem(1, 1, QTableWidgetItem("1.98 MB"))
    table.show()
    app.processEvents()
    interface = QAccessible.queryAccessibleInterface(table)
    assert interface.role() == QAccessible.Role.List
    assert interface.interface_cast(QAccessible.InterfaceType.TableInterface) is None
    assert interface.childCount() == 2
    child = interface.child(1)
    assert child.role() == QAccessible.Role.ListItem
    assert child.parent() == interface
    assert "second.jpg" in child.text(QAccessible.Text.Name)
    assert "1.98 MB" in child.text(QAccessible.Text.Name)
    child.actionInterface().doAction(QAccessibleActionInterface.pressAction())
    assert table.currentRow() == 1
    assert child.state().selected
    assert child.rect().width() > 0
    table.removeRow(0)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert interface.childCount() == 1
    assert interface.indexOfChild(child) == 0
    assert interface.child(0) == child
    table.item(0, 1).setText("2.00 MB")
    assert "2.00 MB" in child.text(QAccessible.Text.Name)
    table.setRowCount(0)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert interface.childCount() == 0
    assert interface.child(0) is None
    table.insertRow(0)
    table.setItem(0, 0, QTableWidgetItem("new.pdf"))
    assert "new.pdf" in interface.child(0).text(QAccessible.Text.Name)
    table.close()
    table.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
