from __future__ import annotations

import io
from dataclasses import replace

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .controls import SettingDoubleSpinBox, SettingSlider, SettingSpinBox
from .edits import ImageEdits, Mosaic, apply_edits, edited_size, source_point
from .workers import Previewer


def caption(text=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName("caption")
    return widget


class EditorCanvas(QWidget):
    region_added = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.tool = 0
        self.start = self.end = None
        self.message = "미리보기 준비 중…"
        self.setMinimumSize(320, 260)
        self.setAccessibleName("이미지 편집 미리보기")
        self.setToolTip("모자이크할 부분을 드래그하세요. 여러 영역을 추가할 수 있습니다.")

    def image_rect(self):
        if self.pixmap.isNull():
            return QRectF()
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        scale = min(area.width() / self.pixmap.width(), area.height() / self.pixmap.height())
        size = QPointF(self.pixmap.width() * scale, self.pixmap.height() * scale)
        origin = area.center() - size / 2
        return QRectF(origin.x(), origin.y(), size.x(), size.y())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        box = self.image_rect()
        if box.isEmpty():
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        painter.save()
        painter.setClipRect(box)
        painter.fillRect(box, QColor("#dddddd"))
        for y in range(int(box.top()), int(box.bottom()) + 12, 12):
            for x in range(int(box.left()), int(box.right()) + 12, 12):
                if ((x - int(box.left())) // 12 + (y - int(box.top())) // 12) % 2:
                    painter.fillRect(x, y, 12, 12, QColor("#eeeeee"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(box, self.pixmap, QRectF(self.pixmap.rect()))
        if self.tool == 2:
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
            for fraction in (1 / 3, 2 / 3):
                x, y = box.left() + box.width() * fraction, box.top() + box.height() * fraction
                painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
                painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        if self.start is not None and self.end is not None:
            selection = QRectF(self.start, self.end).normalized()
            painter.fillRect(selection, QColor(0, 122, 255, 40))
            painter.setPen(QPen(QColor("#007aff"), 2))
            painter.drawRect(selection)
        painter.restore()

    def _clamp(self, point):
        box = self.image_rect()
        return QPointF(
            max(box.left(), min(box.right(), point.x())),
            max(box.top(), min(box.bottom(), point.y())),
        )

    def mousePressEvent(self, event):
        if (
            self.tool == 0
            and event.button() == Qt.MouseButton.LeftButton
            and self.image_rect().contains(event.position())
        ):
            self.start = self.end = event.position()
            self.update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = self._clamp(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.end = self._clamp(event.position())
        selection, box = QRectF(self.start, self.end).normalized(), self.image_rect()
        self.start = self.end = None
        if selection.width() >= 4 and selection.height() >= 4:
            self.region_added.emit(
                tuple(
                    ((p.x() - box.left()) / box.width(), (p.y() - box.top()) / box.height())
                    for p in (
                        selection.topLeft(),
                        selection.topRight(),
                        selection.bottomRight(),
                        selection.bottomLeft(),
                    )
                )
            )
        self.update()


class EditorDialog(QDialog):
    """A non-destructive per-image edit recipe. Only exports encode full-resolution pixels."""

    def __init__(self, asset, parent=None, *, preview_data: bytes | None = None):
        super().__init__(parent)
        self.setObjectName("imageEditor")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setWindowTitle(f"이미지 편집 — {asset.path.name}")
        self.resize(1020, 780)
        self.setMinimumSize(800, 660)
        self.asset = asset
        self.edits = asset.edits
        self.history = [self.edits]
        self.history_index = 0
        self.source = None
        self.worker = None
        self.pending_result = None
        self.updating = False
        self.commit_timer = QTimer(self)
        self.commit_timer.setSingleShot(True)
        self.commit_timer.setInterval(350)
        self.commit_timer.timeout.connect(self._remember)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(25)
        self.render_timer.timeout.connect(self._render)
        self._build()
        self._sync()
        if preview_data:
            self._loaded(asset, preview_data, b"")
        else:
            self.worker = Previewer(replace(asset, edits=ImageEdits()), self)
            self.worker.ready.connect(self._loaded)
            self.worker.failed.connect(self._failed)
            self.worker.finished.connect(self._load_finished)
            self.worker.start()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        tools = QHBoxLayout()
        tools.addStretch()
        self.tools = []
        self.tool_group = QButtonGroup(self)
        for index, text in enumerate(("모자이크", "크기", "기울기")):
            button = QPushButton(text)
            button.setCheckable(True)
            self.tool_group.addButton(button)
            button.setMinimumWidth(100)
            button.toggled.connect(lambda checked, i=index: self._tool(i) if checked else None)
            self.tools.append(button)
            tools.addWidget(button)
        tools.addStretch()
        root.addLayout(tools)
        self.canvas = EditorCanvas()
        self.canvas.region_added.connect(self._mosaic)
        root.addWidget(self.canvas, 1)
        self.dimensions = caption()
        self.dimensions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.dimensions)
        self.panels = QStackedWidget()
        self.panels.setFixedHeight(108)
        mosaic = QWidget()
        layout = QVBoxLayout(mosaic)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mosaic_hint = caption(
            "가릴 부분을 드래그하세요. 강도는 마지막으로 추가한 영역에 적용됩니다."
        )
        layout.addWidget(self.mosaic_hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("모자이크 강도"))
        self.strength = SettingSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(5, 100)
        self.strength.setValue(35)
        self.strength.setAccessibleName("모자이크 강도")
        self.strength.valueChanged.connect(self._strength)
        row.addWidget(self.strength, 1)
        self.clear_mosaics = QPushButton("모자이크 지우기")
        self.clear_mosaics.clicked.connect(lambda: self._change(replace(self.edits, mosaics=())))
        row.addWidget(self.clear_mosaics)
        layout.addLayout(row)
        layout.addWidget(
            caption("모자이크가 있는 사진은 원본 미리보기·촬영·위치 정보도 함께 삭제합니다.")
        )
        self.panels.addWidget(mosaic)

        size = QWidget()
        layout = QVBoxLayout(size)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.width, self.height = SettingSpinBox(), SettingSpinBox()
        for name, widget in (("가로", self.width), ("세로", self.height)):
            widget.setRange(1, 20000)
            widget.setSuffix(" px")
            widget.setAccessibleName(f"{name} 픽셀 크기")
            row.addWidget(QLabel(name))
            row.addWidget(widget)
        self.lock_ratio = QCheckBox("비율 유지")
        self.lock_ratio.setChecked(True)
        row.addWidget(self.lock_ratio)
        self.original_size = QPushButton("원래 크기")
        self.original_size.clicked.connect(
            lambda: self._change(replace(self.edits, width=None, height=None))
        )
        row.addWidget(self.original_size)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(
            caption(
                "파일 압축에서는 지정한 픽셀 크기로 저장합니다. 최대 용량에 맞출 때는 더 작아질 수 있습니다.\nPDF로 합칠 때는 PDF의 해상도 설정이 적용됩니다."
            )
        )
        self.width.valueChanged.connect(lambda value: self._resize("width", value))
        self.height.valueChanged.connect(lambda value: self._resize("height", value))
        self.panels.addWidget(size)

        rotation = QWidget()
        layout = QVBoxLayout(rotation)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.left = QPushButton("↶ 90°")
        self.right = QPushButton("↷ 90°")
        self.left.setAccessibleName("왼쪽으로 90도 회전")
        self.right.setAccessibleName("오른쪽으로 90도 회전")
        self.left.clicked.connect(lambda: self._rotate(1))
        self.right.clicked.connect(lambda: self._rotate(-1))
        row.addWidget(self.left)
        row.addWidget(self.right)
        self.angle_slider = SettingSlider(Qt.Orientation.Horizontal)
        self.angle_slider.setRange(-450, 450)
        self.angle_slider.setAccessibleName("기울기")
        row.addWidget(self.angle_slider, 1)
        self.angle = SettingDoubleSpinBox()
        self.angle.setRange(-45, 45)
        self.angle.setDecimals(1)
        self.angle.setSingleStep(0.1)
        self.angle.setSuffix("°")
        self.angle.setAccessibleName("기울기 각도")
        self.angle.valueChanged.connect(self._angle)
        self.angle_slider.valueChanged.connect(lambda value: self._angle(value / 10))
        row.addWidget(self.angle)
        level = QPushButton("수평으로")
        level.clicked.connect(lambda: self._geometry(replace(self.edits, angle=0.0)))
        row.addWidget(level)
        layout.addLayout(row)
        self.crop = QCheckBox("빈 여백 자동 자르기")
        self.crop.setChecked(True)
        self.crop.setEnabled(self.asset.color_mode != "CMYK")
        self.crop.toggled.connect(lambda checked: self._geometry(replace(self.edits, crop=checked)))
        layout.addWidget(self.crop)
        layout.addWidget(
            caption(
                "사진의 테두리를 잘라 빈 모서리를 없앱니다. 끄면 투명한 여백을 포함해 저장합니다."
            )
        )
        self.panels.addWidget(rotation)
        root.addWidget(self.panels)
        self.error = caption()
        root.addWidget(self.error)
        line = QFrame()
        line.setObjectName("separator")
        line.setFixedHeight(1)
        root.addWidget(line)
        footer = QHBoxLayout()
        self.undo_button, self.redo_button = QPushButton("실행 취소"), QPushButton("다시 실행")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        footer.addWidget(self.undo_button)
        footer.addWidget(self.redo_button)
        reset = QPushButton("초기화")
        reset.clicked.connect(lambda: self._change(ImageEdits()))
        footer.addWidget(reset)
        footer.addStretch()
        source_note = caption("원본 파일은 그대로 유지됩니다")
        source_note.setWordWrap(False)
        footer.addWidget(source_note)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        self.apply_button = QPushButton("적용")
        self.apply_button.setObjectName("primary")
        self.apply_button.setDefault(True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        for sequence, callback in (
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
        self._tool(0)

    def _tool(self, index):
        self.canvas.start = self.canvas.end = None
        self.canvas.tool = index
        self.canvas.setCursor(
            Qt.CursorShape.CrossCursor if index == 0 else Qt.CursorShape.ArrowCursor
        )
        self.canvas.update()
        self.panels.setCurrentIndex(index)
        for i, button in enumerate(self.tools):
            button.setChecked(i == index)

    def _sync(self):
        self.updating = True
        try:
            self.width.setValue(edited_size((self.asset.width, self.asset.height), self.edits)[0])
            self.height.setValue(edited_size((self.asset.width, self.asset.height), self.edits)[1])
            self.angle.setValue(self.edits.angle)
            self.angle_slider.setValue(round(self.edits.angle * 10))
            self.crop.setChecked(self.edits.crop)
            self.clear_mosaics.setEnabled(bool(self.edits.mosaics))
            if self.edits.mosaics:
                self.strength.setValue(self.edits.mosaics[-1].strength)
            self.original_size.setEnabled(self.edits.width is not None)
            self.undo_button.setEnabled(
                self.history_index > 0 or self.edits != self.history[self.history_index]
            )
            self.redo_button.setEnabled(self.history_index < len(self.history) - 1)
            width, height = edited_size((self.asset.width, self.asset.height), self.edits)
            self.dimensions.setText(
                f"{width:,} × {height:,} px"
                + (f" · 모자이크 {len(self.edits.mosaics)}개" if self.edits.mosaics else "")
            )
        finally:
            self.updating = False

    def _change(self, edits, *, commit=True):
        if self.updating or edits == self.edits:
            return
        try:
            edits.validate()
        except ValueError as exc:
            self.error.setText(str(exc))
            self._sync()
            return
        if commit:
            self._remember()
        self.edits = edits
        self.history = self.history[: self.history_index + 1]
        self.error.clear()
        if commit:
            self._remember()
        else:
            self.commit_timer.start()
        self._sync()
        self.render_timer.start()

    def _remember(self):
        self.commit_timer.stop()
        if self.edits != self.history[self.history_index]:
            self.history = self.history[: self.history_index + 1] + [self.edits]
            self.history = self.history[-100:]
            self.history_index = len(self.history) - 1
        self._sync()

    def undo(self):
        self._remember()
        if self.history_index:
            self.history_index -= 1
            self.edits = self.history[self.history_index]
            self._sync()
            self._render()

    def redo(self):
        self.commit_timer.stop()
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.edits = self.history[self.history_index]
            self._sync()
            self._render()

    def _mosaic(self, points):
        if self.source is None:
            return
        mapped = tuple(
            source_point(point, (self.asset.width, self.asset.height), self.edits)
            for point in points
        )

        self._change(
            replace(
                self.edits, mosaics=self.edits.mosaics + (Mosaic(mapped, self.strength.value()),)
            )
        )

    def _strength(self, value):
        if not self.updating and self.edits.mosaics:
            regions = self.edits.mosaics[:-1] + (replace(self.edits.mosaics[-1], strength=value),)
            self._change(replace(self.edits, mosaics=regions), commit=False)

    def _resize(self, axis, value):
        if self.updating:
            return
        width, height = (
            self.edits.width or self.width.value(),
            self.edits.height or self.height.value(),
        )
        natural = edited_size(
            (self.asset.width, self.asset.height), replace(self.edits, width=None, height=None)
        )
        if axis == "width":
            width = value
            if self.lock_ratio.isChecked():
                height = max(1, round(value * natural[1] / natural[0]))
        else:
            height = value
            if self.lock_ratio.isChecked():
                width = max(1, round(value * natural[0] / natural[1]))
        self._change(replace(self.edits, width=width, height=height), commit=False)

    def _geometry(self, edits, *, commit=True):
        if self.updating:
            return
        if edits.width is not None and self.lock_ratio.isChecked():
            natural = edited_size(
                (self.asset.width, self.asset.height), replace(edits, width=None, height=None)
            )
            edits = replace(edits, height=max(1, round(edits.width * natural[1] / natural[0])))
        self._change(edits, commit=commit)

    def _rotate(self, turns):
        edits = replace(self.edits, turns=(self.edits.turns + turns) % 4)
        if edits.width is not None:
            edits = replace(edits, width=edits.height, height=edits.width)
        self._geometry(edits)

    def _angle(self, value):
        self._geometry(replace(self.edits, angle=value), commit=False)

    def _loaded(self, asset, data, sample):
        if self.pending_result is not None:
            return
        try:
            with Image.open(io.BytesIO(data)) as image:
                self.source = image.copy()
            self._render()
        except Exception as exc:
            self._failed(str(exc))

    def _render(self):
        self.render_timer.stop()
        if self.source is None:
            return
        result = None
        try:
            result = apply_edits(
                self.source,
                self.edits,
                preview=True,
                reference_size=(self.asset.width, self.asset.height),
            )
            with result.convert("RGBA") as rgba:
                self.canvas.pixmap = QPixmap.fromImage(ImageQt(rgba))
            self.canvas.update()
            self.apply_button.setEnabled(True)
            self.error.clear()
        except Exception as exc:
            self._failed(str(exc))
        finally:
            if result is not None and result is not self.source:
                result.close()

    def _failed(self, message):
        self.error.setText(message)
        self.apply_button.setEnabled(False)

    def _load_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        if self.pending_result is not None:
            self.done(self.pending_result)

    def done(self, result):
        self.commit_timer.stop()
        self.render_timer.stop()
        if self.worker is not None:
            self.pending_result = result
            self.worker.cancel.set()
            self.setEnabled(False)
            return
        if self.source is not None:
            self.source.close()
            self.source = None
        super().done(result)

    def accept(self):
        self._render()
        if self.apply_button.isEnabled():
            super().accept()
