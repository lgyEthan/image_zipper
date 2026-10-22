from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from pictumi.app import MainWindow, configure_theme  # noqa: E402
from pictumi.imaging import probe  # noqa: E402


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


def test_editor_drag_rotate_resize_undo_and_cancel(png, app):
    from pictumi.editor import EditorDialog
    from pictumi.edits import ImageEdits
    from pictumi.imaging import preview_source

    asset = probe(png)
    before = png.read_bytes()
    dialog = EditorDialog(asset, preview_data=preview_source(asset)[0])
    try:
        dialog.show()
        app.processEvents()
        assert dialog.crop.isChecked()
        assert dialog.apply_button.isEnabled()
        assert dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint
        assert not dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        # macOS accessibility toggles checkable buttons without a clicked signal.
        dialog.tools[2].setChecked(True)
        assert dialog.panels.currentIndex() == 2
        assert sum(button.isChecked() for button in dialog.tools) == 1
        dialog.tools[0].setChecked(True)
        assert dialog.panels.currentIndex() == 0
        box = dialog.canvas.image_rect()
        start = QPoint(round(box.left() + box.width() * 0.2), round(box.top() + box.height() * 0.2))
        end = QPoint(round(box.left() + box.width() * 0.6), round(box.top() + box.height() * 0.6))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        assert len(dialog.edits.mosaics) == 1
        QTest.mouseClick(dialog.tools[2], Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.right, Qt.MouseButton.LeftButton)
        assert dialog.edits.turns == 3
        assert (dialog.width.value(), dialog.height.value()) == (480, 720)
        dialog.angle.setValue(12.5)
        assert dialog.edits.angle == 12.5
        dialog.undo()
        assert dialog.edits.angle == 0
        dialog.redo()
        assert dialog.edits.angle == 12.5
        QTest.mouseClick(dialog.tools[1], Qt.MouseButton.LeftButton)
        dialog.width.setValue(200)
        assert dialog.edits.width == 200
        assert dialog.edits.height == dialog.height.value()
        dialog._render()
        assert not dialog.canvas.pixmap.isNull() and dialog.apply_button.isEnabled()
        dialog.reject()
        assert dialog.result() == 0
        assert asset.edits == ImageEdits()
        assert png.read_bytes() == before
        assert dialog.source is None
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_editor_settings_ignore_wheel_and_apply_updates_only_selected_file(png, tmp_path, app):
    from pictumi.editor import EditorDialog
    from pictumi.imaging import preview_source

    second = tmp_path / "second.png"
    second.write_bytes(png.read_bytes())
    window = MainWindow()
    dialog = EditorDialog(probe(png), preview_data=preview_source(probe(png))[0])
    try:
        window._asset_loaded(probe(png))
        window._asset_loaded(probe(second))
        window.navigation[1].setChecked(True)
        assert window.operation.currentIndex() == 1
        window.navigation[0].setChecked(True)
        assert window.operation.currentIndex() == 0
        assert sum(button.isChecked() for button in window.navigation) == 1
        window.table.selectRow(0)
        dialog.show()
        for index, widgets in (
            (0, (dialog.strength,)),
            (1, (dialog.width, dialog.height)),
            (2, (dialog.angle, dialog.angle_slider)),
        ):
            dialog._tool(index)
            app.processEvents()
            for widget in widgets:
                value = widget.value()
                position = widget.mapTo(dialog, widget.rect().center())
                QTest.wheelEvent(dialog.windowHandle(), position, QPoint(0, -120))
                assert widget.value() == value
        dialog._tool(1)
        dialog.width.setValue(360)
        dialog.accept()
        assert dialog.result() == 1
        window._apply_edits(window.assets[0], dialog.edits)
        assert window.assets[0].pixel_size == (360, 240)
        assert not window.assets[1].edits.active
        assert window.before_title.text() == "편집한 이미지"
        assert window.table.item(0, 5).text() == "편집됨"
        assert not window.settings().strip_metadata
        window.strip_metadata.setChecked(True)
        assert window.settings().strip_metadata
        assert window.trial_cache is None
    finally:
        dialog.close()
        dialog.deleteLater()
        dispose(window, app)


def test_editor_can_close_while_preview_process_is_starting(png, app):
    from pictumi.editor import EditorDialog

    dialog = EditorDialog(probe(png))
    dialog.show()
    dialog.reject()
    spin(app, lambda: dialog.worker is None)
    assert not dialog.isVisible()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_crop_drag_handles_move_ratio_undo_and_reopen(png, app):
    from dataclasses import replace

    from pictumi.editor import EditorDialog
    from pictumi.editor_canvas import CROP, SIZE
    from pictumi.edits import edited_size
    from pictumi.imaging import preview_source

    asset = probe(png)
    before = png.read_bytes()
    preview = preview_source(asset)[0]
    dialog = EditorDialog(asset, preview_data=preview)

    def drag(start, end):
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)

    try:
        dialog.show()
        app.processEvents()
        assert dialog.canvas.tool == CROP
        assert dialog.ratios["자유"][0].isChecked()
        image = dialog.canvas.image_rect()
        start = QPoint(
            round(image.left() + image.width() * 0.2), round(image.top() + image.height() * 0.2)
        )
        end = QPoint(
            round(image.left() + image.width() * 0.7), round(image.top() + image.height() * 0.8)
        )
        drag(start, end)
        first = dialog.edits
        assert first.crop_box == pytest.approx((0.2, 0.2, 0.7, 0.8), abs=0.003)
        # Selecting a crop never crops away the context needed to expand it again.
        dialog._render()
        assert dialog.canvas.pixmap.width() / dialog.canvas.pixmap.height() == pytest.approx(1.5)
        selection = dialog.canvas.selection_rect()
        drag(
            selection.bottomRight().toPoint(), (selection.bottomRight() + QPoint(35, 20)).toPoint()
        )
        resized = dialog.edits
        assert resized.crop_box[2] > first.crop_box[2]
        assert resized.crop_box[3] > first.crop_box[3]
        selection = dialog.canvas.selection_rect()
        drag(selection.center().toPoint(), (selection.center() + QPoint(25, -15)).toPoint())
        moved = dialog.edits
        assert edited_size((720, 480), moved) == edited_size((720, 480), resized)
        assert moved.crop_box[0] > resized.crop_box[0]
        dialog.undo()
        assert dialog.edits == resized
        dialog.redo()
        assert dialog.edits == moved
        for label, ratio in (("1:1", (1, 1)), ("4:5", (4, 5))):
            # Also covers native accessibility activation without clicked().
            dialog.ratios[label][0].setChecked(True)
            assert dialog.edits.crop_ratio == ratio
            assert sum(button.isChecked() for button, _ in dialog.ratios.values()) == 1
            w, h = edited_size((720, 480), dialog.edits)
            assert w * ratio[1] == h * ratio[0]
            selection = dialog.canvas.selection_rect()
            drag(
                selection.bottomRight().toPoint(),
                (selection.bottomRight() - QPoint(50, 50)).toPoint(),
            )
            w, h = edited_size((720, 480), dialog.edits)
            assert w * ratio[1] == h * ratio[0]
        after = dialog.edits
        dialog._tool(SIZE)
        assert dialog.canvas.pixmap.width() * 5 == dialog.canvas.pixmap.height() * 4
        dialog._tool(CROP)
        assert dialog.canvas.pixmap.width() / dialog.canvas.pixmap.height() == pytest.approx(1.5)
        dialog.accept()
        assert dialog.result() == 1 and dialog.edits == after
        assert png.read_bytes() == before
        assert not asset.edits.active
    finally:
        dialog.close()
        dialog.deleteLater()
    reopened = EditorDialog(replace(asset, edits=after), preview_data=preview)
    try:
        reopened.show()
        app.processEvents()
        assert reopened.edits == after
        assert reopened.canvas.pixmap.width() / reopened.canvas.pixmap.height() == pytest.approx(
            1.5
        )
        QTest.mouseClick(reopened.clear_crop, Qt.MouseButton.LeftButton)
        assert reopened.edits.crop_box is None and reopened.edits.crop_ratio is None
        assert edited_size((720, 480), reopened.edits) == (720, 480)
        reopened.undo()
        assert reopened.edits == after
        reopened.reject()
        assert reopened.result() == 0
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_crop_rotation_preserves_selected_content_and_ratio(png, app):
    from PIL import Image

    from pictumi.editor import EditorDialog
    from pictumi.edits import apply_edits
    from pictumi.imaging import preview_source

    asset = probe(png)
    dialog = EditorDialog(asset, preview_data=preview_source(asset)[0])
    try:
        dialog._set_crop_ratio((4, 5))
        dialog._set_crop_box((0.2, 0.1, 0.6, 0.85))
        before = dialog.edits
        dialog._rotate(1)
        assert dialog.edits.crop_ratio == (5, 4)
        with (
            Image.open(png) as source,
            apply_edits(source, before) as first,
            apply_edits(source, dialog.edits) as rotated,
        ):
            with first.transpose(Image.Transpose.ROTATE_90) as expected:
                assert rotated.size == expected.size
                assert rotated.tobytes() == expected.tobytes()
        dialog._rotate(-1)
        assert dialog.edits.crop_box == pytest.approx(before.crop_box)
        assert dialog.edits.crop_ratio == (4, 5)
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_crop_drag_cancel_keyboard_and_wheel(png, app):
    from pictumi.editor import EditorDialog
    from pictumi.imaging import preview_source

    asset = probe(png)
    dialog = EditorDialog(asset, preview_data=preview_source(asset)[0])
    try:
        dialog.show()
        app.processEvents()
        dialog._set_crop_box((0.2, 0.2, 0.8, 0.8))
        before = dialog.edits
        canvas = dialog.canvas
        start = canvas.selection_rect().center().toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, start + QPoint(40, 0))
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
        assert dialog.isVisible() and dialog.edits == before
        assert canvas.crop_box == before.crop_box
        QTest.keyClick(canvas, Qt.Key.Key_Right)
        assert dialog.edits.crop_box[0] == pytest.approx(before.crop_box[0] + 1 / 720)
        dialog.ratios["4:5"][0].setChecked(True)
        before = dialog.edits
        button = dialog.ratios["1:1"][0]
        QTest.wheelEvent(
            dialog.windowHandle(), button.mapTo(dialog, button.rect().center()), QPoint(0, -120)
        )
        assert dialog.edits == before
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("handle", ["nw", "n", "ne", "e", "se", "s", "sw", "w"])
def test_crop_all_handles_keep_ratio_and_stay_inside_image(png, app, handle):
    from pictumi.editor import EditorDialog
    from pictumi.edits import edited_size
    from pictumi.imaging import preview_source

    asset = probe(png)
    dialog = EditorDialog(asset, preview_data=preview_source(asset)[0])
    try:
        dialog.show()
        app.processEvents()
        dialog._set_crop_ratio((4, 5))
        dialog._set_crop_box((0.3, 0.3, 0.6, 0.8))
        selection = dialog.canvas.selection_rect()
        image = dialog.canvas.image_rect()
        x = (
            selection.left()
            if "w" in handle
            else selection.right()
            if "e" in handle
            else selection.center().x()
        )
        y = (
            selection.top()
            if "n" in handle
            else selection.bottom()
            if "s" in handle
            else selection.center().y()
        )
        start = QPoint(round(x), round(y))
        end = QPoint(
            round(
                image.left() - 80 if "w" in handle else image.right() + 80 if "e" in handle else x
            ),
            round(
                image.top() - 80 if "n" in handle else image.bottom() + 80 if "s" in handle else y
            ),
        )
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        assert dialog.canvas.drag[0] == handle
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        x0, y0, x1, y1 = dialog.edits.crop_box
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        width, height = edited_size((720, 480), dialog.edits)
        assert width * 5 == height * 4
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_original_crop_ratio_survives_straightening_without_progressive_shrinking(png, app):
    from pictumi.editor import EditorDialog
    from pictumi.edits import edited_size
    from pictumi.imaging import preview_source

    asset = probe(png)
    dialog = EditorDialog(asset, preview_data=preview_source(asset)[0])
    try:
        dialog.ratios["원본"][0].setChecked(True)
        assert dialog.edits.crop_box == (0, 0, 1, 1)
        for angle in (0, 5, 10, 20, 10, 5, 0):
            dialog._angle(angle)
            width, height = edited_size((720, 480), dialog.edits)
            assert width * 2 == height * 3
        assert edited_size((720, 480), dialog.edits) == (720, 480)
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_native_window_controls_and_simple_default_settings(app):
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
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
        assert not window.format.isVisible()
        assert window.preset.currentText() == "균형"
        assert window.dpi.isVisible() and window.preset.isVisible()
        settings = window.settings()
        assert (settings.target_dpi, settings.quality, settings.min_quality) == (150, 86, 76)
        assert settings.output_format == "auto" and settings.jpeg_engine == "jpegli"
        QTest.mouseClick(window.advanced_toggle, Qt.MouseButton.LeftButton)
        assert window.format.isVisible() and window.quality.isVisible()
        QTest.mouseClick(window.advanced_toggle, Qt.MouseButton.LeftButton)
        assert not window.format.isVisible()
        assert window.settings() == settings
        assert window.measure_button.text() == "결과 확인"
    finally:
        dispose(window, app)


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("trackpad", [False, True])
def test_settings_wheel_scrolls_sidebar_without_changing_values(app, focused, trackpad):
    window = MainWindow()
    try:
        window.resize(1040, 700)
        window.advanced_toggle.setChecked(True)
        window.show()
        app.processEvents()
        scroll = window.settings_card.findChild(QScrollArea)
        bar = scroll.verticalScrollBar()
        assert bar.maximum() > 0
        for mode in ("dpi", "size"):
            window.mode.setCurrentIndex(window.mode.findData(mode))
            for widget in (
                window.mode,
                window.preset,
                window.dpi if mode == "dpi" else window.max_mib,
                window.format,
                window.print_width,
                window.reference,
                window.quality if mode == "dpi" else window.min_quality,
                window.jpeg_engine,
            ):
                scroll.ensureWidgetVisible(widget)
                (widget if focused else window.add_files).setFocus()
                app.processEvents()
                before, revision, position = window.settings(), window.revision, bar.value()
                direction = 1 if position else -1
                QTest.wheelEvent(
                    window.windowHandle(),
                    widget.mapTo(window, widget.rect().center()),
                    QPoint(0, direction * 120),
                    QPoint(0, direction * 40) if trackpad else QPoint(),
                    phase=Qt.ScrollPhase.ScrollUpdate if trackpad else Qt.ScrollPhase.NoScrollPhase,
                )
                app.processEvents()
                assert window.settings() == before
                assert window.revision == revision
                assert bar.value() != position
    finally:
        dispose(window, app)


def test_settings_still_accept_keyboard_and_explicit_menu_selection(app):
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        window.dpi.setFocus()
        QTest.keyClick(window.dpi, Qt.Key.Key_Up)
        assert window.dpi.value() == 151
        window.preset.setFocus()
        QTest.keyClick(window.preset, Qt.Key.Key_Up)
        assert window.preset.currentText() == "도표 · 인쇄"
        assert window.dpi.value() == 600
        QTest.mouseClick(window.preset, Qt.MouseButton.LeftButton)
        view = window.preset.view()
        assert view.isVisible()
        # Qt briefly suppresses releases after opening a combo popup.
        QTest.qWait(app.doubleClickInterval() + 20)
        index = window.preset.model().index(2, 0)
        QTest.mouseMove(view.viewport(), view.visualRect(index).center())
        QTest.mouseClick(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=view.visualRect(index).center(),
            delay=20,
        )
        app.processEvents()
        assert window.preset.currentText() == "균형"
        assert window.dpi.value() == 150
    finally:
        dispose(window, app)


def test_loading_and_settings_never_automatically_trial_encode(png, tmp_path, app, monkeypatch):
    from pictumi import app as app_module

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
        window.preset.setCurrentIndex(0)
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
    from pictumi import workers

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
    from pictumi.models import Settings
    from pictumi.workers import Exporter

    worker = Exporter([probe(png)], Settings(), tmp_path / "exports", None)
    completed = []
    worker.completed.connect(lambda *values: completed.append(values))
    worker.cancel.set()
    worker.start()
    spin(app, lambda: not worker.isRunning())
    assert completed == [([], [], True)]
    assert not (tmp_path / "exports").exists()


def test_estimated_size_can_exceed_two_gibibytes(app):
    from pictumi.models import Encoded, Settings
    from pictumi.workers import Estimator

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

    from pictumi.file_table import FileTable

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
