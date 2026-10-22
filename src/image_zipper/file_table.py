"""File table with stable, keyboard-operable macOS accessibility rows.

Qt's Cocoa synthesized table cells can delete their parent's interface while
AX reads the selected children (QTBUG-149612). Expose each file as one real
QObject-backed list item instead; keep the normal visual table and keyboard UI.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QAccessibleEvent
from PySide6.QtWidgets import QAccessibleWidget, QTableWidget, QWidget


class RowAnchor(QWidget):
    def __init__(self, table, row):
        super().__init__(table)
        self.table, self.row = table, row
        self.hide()


class AccessibleRow(QAccessibleWidget):
    def __init__(self, anchor):
        super().__init__(anchor, QAccessible.Role.ListItem)

    def parent(self):
        return QAccessible.queryAccessibleInterface(self.widget().table)

    def childCount(self):
        return 0

    def child(self, index):
        return None

    def text(self, kind):
        if kind == QAccessible.Text.Name:
            anchor = self.widget()
            table, row = anchor.table, anchor.row
            return "; ".join(
                f"{header.text() if (header := table.horizontalHeaderItem(col)) else col + 1}: {item.text()}"
                for col in range(table.columnCount())
                if (item := table.item(row, col)) is not None
            )
        return ""

    def rect(self):
        anchor = self.widget()
        table = anchor.table
        rect = table.visualRect(table.model().index(anchor.row, 0))
        return QRect(
            table.viewport().mapToGlobal(QPoint(0, rect.y())),
            QSize(table.viewport().width(), rect.height()),
        )

    def state(self):
        anchor = self.widget()
        table, row = anchor.table, anchor.row
        state = QAccessible.State()
        state.selectable = state.focusable = True
        state.selected = table.selectionModel().isRowSelected(row, QModelIndex())
        state.focused = table.hasFocus() and table.currentRow() == row
        state.disabled = not table.isEnabled()
        state.invisible = not table.isVisible()
        state.offscreen = (
            not table.viewport().rect().intersects(table.visualRect(table.model().index(row, 0)))
        )
        return state

    def actionNames(self):
        return [
            QAccessibleActionInterface.pressAction(),
            QAccessibleActionInterface.setFocusAction(),
        ]

    def doAction(self, action):
        if action in self.actionNames() and self.widget().table.isEnabled():
            anchor = self.widget()
            anchor.table.setFocus()
            anchor.table.selectRow(anchor.row)
            anchor.table.scrollToItem(anchor.table.item(anchor.row, 0))

    def keyBindingsForAction(self, action):
        return []


class AccessibleFileList(QAccessibleWidget):
    def __init__(self, table):
        super().__init__(table, QAccessible.Role.List)

    def childCount(self):
        return len(self.widget().anchors)

    def child(self, index):
        anchors = self.widget().anchors
        return (
            QAccessible.queryAccessibleInterface(anchors[index])
            if 0 <= index < len(anchors)
            else None
        )

    def indexOfChild(self, child):
        obj = child.object()
        return obj.row if isinstance(obj, RowAnchor) and obj.table is self.widget() else -1

    def childAt(self, x, y):
        table = self.widget()
        index = table.indexAt(table.viewport().mapFromGlobal(QPoint(x, y)))
        return self.child(index.row()) if index.isValid() else None

    def text(self, kind):
        return "파일 목록" if kind == QAccessible.Text.Name else super().text(kind)


def accessibility_factory(name, obj):
    if isinstance(obj, FileTable):
        return AccessibleFileList(obj)
    if isinstance(obj, RowAnchor):
        return AccessibleRow(obj)
    return None


_installed = False


class FileTable(QTableWidget):
    def __init__(self, rows, columns, parent=None):
        global _installed
        if sys.platform == "darwin" and not _installed:
            QAccessible.installFactory(accessibility_factory)
            _installed = True
        self.anchors = []
        super().__init__(rows, columns, parent)
        if sys.platform == "darwin":
            self.anchors = [RowAnchor(self, row) for row in range(rows)]
        self.itemChanged.connect(self._announce_item)

    def insertRow(self, row):
        super().insertRow(row)
        if sys.platform == "darwin":
            self.anchors.insert(row, RowAnchor(self, row))
            self._update_rows()

    def removeRow(self, row):
        if 0 <= row < len(self.anchors):
            self.anchors.pop(row).deleteLater()
        super().removeRow(row)
        self._update_rows()

    def setRowCount(self, count):
        while len(self.anchors) > count:
            self.anchors.pop().deleteLater()
        super().setRowCount(count)
        if sys.platform == "darwin":
            while len(self.anchors) < count:
                self.anchors.append(RowAnchor(self, len(self.anchors)))
            self._update_rows()

    def _update_rows(self):
        for row, anchor in enumerate(self.anchors):
            anchor.row = row
        if QAccessible.isActive():
            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.ObjectReorder))

    def _announce_item(self, item):
        if QAccessible.isActive() and 0 <= item.row() < len(self.anchors):
            QAccessible.updateAccessibility(
                QAccessibleEvent(self.anchors[item.row()], QAccessible.Event.NameChanged)
            )
