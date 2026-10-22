"""Lightweight editor gestures; dragging only repaints the cached preview."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

MOSAIC, SIZE, ROTATE, CROP = range(4)


class EditorCanvas(QWidget):
    region_added = Signal(object)
    crop_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.tool = CROP
        self.start = self.end = None
        self.drag = None
        self.crop_box = (0.0, 0.0, 1.0, 1.0)
        self.crop_ratio = None
        self.crop_size = (1, 1)
        self.message = "미리보기 준비 중…"
        self.setMinimumSize(320, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("이미지 편집 미리보기")

    def image_rect(self):
        if self.pixmap.isNull():
            return QRectF()
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        scale = min(area.width() / self.pixmap.width(), area.height() / self.pixmap.height())
        size = QPointF(self.pixmap.width() * scale, self.pixmap.height() * scale)
        origin = area.center() - size / 2
        return QRectF(origin.x(), origin.y(), size.x(), size.y())

    def selection_rect(self):
        image = self.image_rect()
        x0, y0, x1, y1 = self.crop_box
        return QRectF(
            image.left() + image.width() * x0,
            image.top() + image.height() * y0,
            image.width() * (x1 - x0),
            image.height() * (y1 - y0),
        )

    @staticmethod
    def _grid(painter, box):
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        for fraction in (1 / 3, 2 / 3):
            x, y = box.left() + box.width() * fraction, box.top() + box.height() * fraction
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))

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
        if self.tool == ROTATE:
            self._grid(painter, box)
        if self.tool == CROP:
            selected = self.selection_rect()
            shade = QColor(0, 0, 0, 150)
            for area in (
                QRectF(box.left(), box.top(), box.width(), selected.top() - box.top()),
                QRectF(
                    box.left(), selected.bottom(), box.width(), box.bottom() - selected.bottom()
                ),
                QRectF(box.left(), selected.top(), selected.left() - box.left(), selected.height()),
                QRectF(
                    selected.right(),
                    selected.top(),
                    box.right() - selected.right(),
                    selected.height(),
                ),
            ):
                painter.fillRect(area, shade)
            self._grid(painter, selected)
            painter.setPen(QPen(QColor(0, 0, 0, 160), 3))
            painter.drawRect(selected)
            painter.setPen(QPen(QColor("white"), 1))
            painter.drawRect(selected)
            painter.restore()
            # Handles remain visible even when the crop touches an image edge.
            for color, width in ((QColor(0, 0, 0, 180), 5), (QColor("white"), 3)):
                painter.setPen(QPen(color, width))
                length = min(14, selected.width() / 4, selected.height() / 4)
                for x, dx in ((selected.left(), 1), (selected.right(), -1)):
                    for y, dy in ((selected.top(), 1), (selected.bottom(), -1)):
                        painter.drawLine(QPointF(x, y), QPointF(x + dx * length, y))
                        painter.drawLine(QPointF(x, y), QPointF(x, y + dy * length))
                for y in (selected.top(), selected.bottom()):
                    painter.drawLine(
                        QPointF(selected.center().x() - 5, y), QPointF(selected.center().x() + 5, y)
                    )
                for x in (selected.left(), selected.right()):
                    painter.drawLine(
                        QPointF(x, selected.center().y() - 5), QPointF(x, selected.center().y() + 5)
                    )
            return
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

    def _normalized(self, point):
        box = self.image_rect()
        point = self._clamp(point)
        return ((point.x() - box.left()) / box.width(), (point.y() - box.top()) / box.height())

    def _hit(self, point):
        selected = self.selection_rect()
        near_x = (
            "w"
            if abs(point.x() - selected.left()) <= 9
            else "e"
            if abs(point.x() - selected.right()) <= 9
            else ""
        )
        near_y = (
            "n"
            if abs(point.y() - selected.top()) <= 9
            else "s"
            if abs(point.y() - selected.bottom()) <= 9
            else ""
        )
        if selected.adjusted(-9, -9, 9, 9).contains(point) and (near_x or near_y):
            return near_y + near_x
        if selected.contains(point) and self.crop_box != (0, 0, 1, 1):
            return "move"
        return "new"

    def _cursor(self, point):
        mode = self._hit(point) if self.tool == CROP else "new" if self.tool == MOSAIC else ""
        shape = {
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.OpenHandCursor,
            "new": Qt.CursorShape.CrossCursor,
        }.get(mode, Qt.CursorShape.ArrowCursor)
        self.setCursor(shape)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.pixmap.isNull():
            return super().mousePressEvent(event)
        if self.tool == CROP and self.image_rect().adjusted(-9, -9, 9, 9).contains(
            event.position()
        ):
            self.setFocus()
            self.drag = (
                self._hit(event.position()),
                self._normalized(event.position()),
                self.crop_box,
            )
            if self.drag[0] == "move":
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.tool == MOSAIC and self.image_rect().contains(event.position()):
            self.start = self.end = event.position()
            self.update()
        else:
            super().mousePressEvent(event)

    def _drag_crop(self, point):
        mode, start, original = self.drag
        px, py = self._normalized(point)
        x0, y0, x1, y1 = original
        ratio = None
        if self.crop_ratio:
            ratio = self.crop_ratio[0] / self.crop_ratio[1] * self.crop_size[1] / self.crop_size[0]
        if mode == "move":
            dx = max(-x0, min(1 - x1, px - start[0]))
            dy = max(-y0, min(1 - y1, py - start[1]))
            return x0 + dx, y0 + dy, x1 + dx, y1 + dy
        if mode in {"w", "e"}:
            anchor, center = x1 if mode == "w" else x0, (y0 + y1) / 2
            direction = -1 if px < anchor else 1
            width = abs(px - anchor)
            if ratio:
                width = min(width, 2 * min(center, 1 - center) * ratio)
                y0, y1 = center - width / ratio / 2, center + width / ratio / 2
            return (
                min(anchor, anchor + direction * width),
                y0,
                max(anchor, anchor + direction * width),
                y1,
            )
        if mode in {"n", "s"}:
            anchor, center = y1 if mode == "n" else y0, (x0 + x1) / 2
            direction = -1 if py < anchor else 1
            height = abs(py - anchor)
            if ratio:
                height = min(height, 2 * min(center, 1 - center) / ratio)
                x0, x1 = center - height * ratio / 2, center + height * ratio / 2
            return (
                x0,
                min(anchor, anchor + direction * height),
                x1,
                max(anchor, anchor + direction * height),
            )
        ax, ay = start if mode == "new" else (x1 if "w" in mode else x0, y1 if "n" in mode else y0)
        dx, dy = (-1 if px < ax else 1), (-1 if py < ay else 1)
        width, height = abs(px - ax), abs(py - ay)
        if ratio:
            width = min(
                max(width, height * ratio),
                ax if dx < 0 else 1 - ax,
                (ay if dy < 0 else 1 - ay) * ratio,
            )
            height = width / ratio
        bx, by = ax + dx * width, ay + dy * height
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def mouseMoveEvent(self, event):
        if self.drag:
            self.crop_box = self._drag_crop(event.position())
            self.update()
        elif self.start is not None:
            self.end = self._clamp(event.position())
            self.update()
        elif not self.pixmap.isNull():
            self._cursor(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.drag:
            self.crop_box = self._drag_crop(event.position())
            selection = self.selection_rect()
            old = self.drag[2]
            self.drag = None
            if selection.width() >= 4 and selection.height() >= 4:
                self.crop_changed.emit(self.crop_box)
            else:
                self.crop_box = old
            self._cursor(event.position())
            self.update()
            return
        if self.start is None:
            return
        self.end = self._clamp(event.position())
        selection = QRectF(self.start, self.end).normalized()
        self.start = self.end = None
        if selection.width() >= 4 and selection.height() >= 4:
            self.region_added.emit(
                tuple(
                    self._normalized(p)
                    for p in (
                        selection.topLeft(),
                        selection.topRight(),
                        selection.bottomRight(),
                        selection.bottomLeft(),
                    )
                )
            )
        self.update()

    def keyPressEvent(self, event):
        if self.drag and event.key() == Qt.Key.Key_Escape:
            self.crop_box = self.drag[2]
            self.drag = None
            self.update()
            event.accept()
            return
        directions = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if self.tool == CROP and event.key() in directions and not self.pixmap.isNull():
            dx, dy = directions[event.key()]
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            x0, y0, x1, y1 = self.crop_box
            dx = max(-x0, min(1 - x1, step * dx / self.crop_size[0]))
            dy = max(-y0, min(1 - y1, step * dy / self.crop_size[1]))
            self.crop_changed.emit((x0 + dx, y0 + dy, x1 + dx, y1 + dy))
            event.accept()
            return
        super().keyPressEvent(event)
