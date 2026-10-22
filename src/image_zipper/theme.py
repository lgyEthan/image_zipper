"""System appearance, neutral surfaces and explicit readable selection colors."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def configure_theme(app: QApplication, dark: bool | None = None) -> None:
    if dark is None:
        dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    app.setStyle("Fusion")
    colors = dict(
        window="#242424" if dark else "#ffffff",
        sidebar="#202020" if dark else "#f3f3f5",
        base="#242424" if dark else "#ffffff",
        group="#2c2c2e" if dark else "#f5f5f7",
        control="#39393b" if dark else "#ffffff",
        line="#3c3c3e" if dark else "#dedee3",
        text="#f2f2f7" if dark else "#202024",
        muted="#aaaab0" if dark else "#626269",
        disabled="#77777e" if dark else "#898990",
        accent="#73b5ff" if dark else "#005fbf",
        blue="#0865d3",
        selection="#285589" if dark else "#d9eaff",
        selected="#ffffff" if dark else "#15395f",
        hover="#343436" if dark else "#e8e8ed",
    )
    palette = QPalette()
    roles = {
        "Window": "window",
        "WindowText": "text",
        "Base": "base",
        "AlternateBase": "group",
        "Text": "text",
        "Button": "control",
        "ButtonText": "text",
        "Highlight": "selection",
        "HighlightedText": "selected",
        "PlaceholderText": "muted",
        "ToolTipBase": "group",
        "ToolTipText": "text",
        "Link": "accent",
        "Light": "line",
        "Midlight": "control",
        "Mid": "line",
        "Dark": "sidebar",
    }
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, value in roles.items():
            color = (
                colors["disabled"]
                if group == QPalette.ColorGroup.Disabled
                and role in {"Text", "WindowText", "ButtonText"}
                else colors[value]
            )
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(color))
    app.setPalette(palette)
    css = """
    QWidget { color: @text; font-size: 13px; }
    QMainWindow, #workspace { background: @window; }
    QLabel { background: transparent; }
    QLabel#muted, QLabel#caption { color: @muted; }
    QLabel#caption { font-size: 11px; }
    QLabel#section { font-size: 12px; font-weight: 600; color: @muted; }
    QLabel#heading { font-size: 18px; font-weight: 600; }
    QLabel#metric { font-size: 16px; font-weight: 600; }
    QLabel#dropTitle { font-size: 17px; font-weight: 600; color: @muted; }
    QLabel#preview { background: @group; border-radius: 8px; color: @muted; padding: 6px; }
    QFrame#sidebar { background: @sidebar; border: 1px solid @line; border-radius: 13px; }
    QFrame#inspector, QFrame#output { background: @group; border: none; border-radius: 10px; }
    QFrame#separator { background: @line; max-height: 1px; border: none; }
    QScrollArea, QScrollArea > QWidget > QWidget { border: none; background: transparent; }
    QPushButton { background: @control; border: 1px solid @line; border-radius: 6px; padding: 5px 12px; min-height: 20px; }
    QPushButton:hover { background: @hover; }
    QPushButton:pressed { background: @selection; }
    QPushButton:disabled { color: @disabled; background: @group; border-color: @line; }
    QPushButton#primary { background: @blue; color: white; border: 1px solid @blue; padding: 6px 22px; font-weight: 600; }
    QPushButton#primary:hover { background: #0759bc; }
    QPushButton#primary:disabled { color: @disabled; background: @control; border-color: @line; }
    QPushButton#subtle { background: transparent; border: none; padding: 4px 8px; color: @muted; }
    QPushButton#subtle:hover { background: @hover; color: @text; }
    QPushButton#navigation { background: transparent; border: none; border-radius: 7px; padding: 8px 12px; text-align: left; }
    QPushButton#navigation:hover { background: @hover; }
    QPushButton#navigation:checked { background: @hover; color: @accent; font-weight: 600; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { color: @text; background: @control; border: 1px solid @line; border-radius: 5px; padding: 4px 6px; min-height: 20px; selection-background-color: @selection; selection-color: @selected; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: @blue; }
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: @disabled; background: @group; }
    QComboBox { padding-right: 22px; }
    QComboBox::drop-down { border: none; width: 22px; }
    QComboBox::down-arrow { image: url(ASSET_ROOT/chevron.svg); width: 10px; height: 7px; }
    QComboBox QAbstractItemView { background: @control; color: @text; border: 1px solid @line; padding: 4px; selection-background-color: @selection; selection-color: @selected; outline: 0; }
    QComboBox QAbstractItemView::item { min-height: 26px; padding: 3px 8px; }
    QComboBox QAbstractItemView::item:selected { background: @selection; color: @selected; }
    QCheckBox { spacing: 7px; }
    QCheckBox::indicator { width: 14px; height: 14px; background: @control; border: 1px solid @muted; border-radius: 3px; }
    QCheckBox::indicator:checked { background: @blue; border-color: @blue; image: url(ASSET_ROOT/check.svg); }
    QTableWidget { background: @base; border: none; gridline-color: @line; selection-background-color: @selection; selection-color: @selected; outline: 0; }
    QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid @line; }
    QTableWidget::item:selected, QTableWidget::item:selected:!active { background: @selection; color: @selected; }
    QHeaderView::section { background: @base; color: @muted; border: none; border-bottom: 1px solid @line; padding: 9px 8px; font-size: 11px; }
    QScrollBar:vertical { background: transparent; width: 8px; border: none; }
    QScrollBar:horizontal { background: transparent; height: 8px; border: none; }
    QScrollBar::handle { background: @line; border-radius: 4px; min-height: 28px; min-width: 28px; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QSplitter::handle { background: @window; }
    QProgressBar { border: none; background: @line; min-height: 3px; max-height: 3px; }
    QProgressBar::chunk { background: @blue; }
    QPlainTextEdit { background: @group; color: @text; border: 1px solid @line; border-radius: 7px; padding: 6px; selection-background-color: @selection; selection-color: @selected; }
    QToolTip { background: @group; color: @text; border: 1px solid @line; padding: 6px; }
    """
    for name, value in colors.items():
        css = css.replace("@" + name, value)
    app.setStyleSheet(css.replace("ASSET_ROOT", (Path(__file__).parent / "assets").as_posix()))
