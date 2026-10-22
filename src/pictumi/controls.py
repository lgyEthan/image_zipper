"""Settings change only through an explicit click or keyboard action."""

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSlider, QSpinBox


class SettingComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class SettingSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class SettingDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class SettingSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()
