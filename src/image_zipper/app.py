from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .imaging import INPUT_EXTENSIONS, quick_estimate
from .models import Asset, CompressionError, Settings, human_bytes
from .workers import Estimator, Exporter, Loader

STYLESHEET = """
QWidget { color: #243b48; font-size: 13px; }
QMainWindow, #workspace { background: #f4f6f8; }
QLabel { background: transparent; }
QLabel#brand { font-size: 13px; font-weight: 700; color: #087f78; letter-spacing: 2px; }
QLabel#heading { font-size: 28px; font-weight: 700; color: #172f3c; }
QLabel#subtitle, QLabel#muted { color: #6e818e; }
QLabel#section { font-size: 17px; font-weight: 700; }
QLabel#metric { font-size: 23px; font-weight: 700; color: #087f78; }
QLabel#badge { background: #e5f3ee; color: #217060; border-radius: 12px; padding: 8px 14px; font-size: 11px; font-weight: 700; }
QFrame#card { background: white; border: 1px solid #e0e6eb; border-radius: 14px; }
QFrame#estimate { background: #eff8f5; border: 1px solid #d8ebe4; border-radius: 12px; }
QLabel#dropTitle { font-size: 21px; font-weight: 600; color: #45616f; }
QPushButton { background: white; border: 1px solid #d7e0e5; padding: 9px 14px; border-radius: 8px; font-weight: 600; }
QPushButton:hover { background: #edf6f4; border-color: #80b7ab; }
QPushButton:pressed { background: #dcefe9; }
QPushButton:disabled { color: #aab6bf; background: #f5f7f8; border-color: #e8edef; }
QPushButton#primary { background: #087f78; border: 1px solid #087f78; color: white; padding: 13px 25px; }
QPushButton#primary:hover { background: #066e68; }
QPushButton#primary:disabled { background: #9ebdb8; border-color: #9ebdb8; color: white; }
QPushButton#subtle { border: none; background: transparent; color: #647b89; padding: 6px 10px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #fbfcfd; border: 1px solid #d7e0e5; border-radius: 7px; padding: 7px; min-height: 20px; selection-background-color: #c4e8dd; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #087f78; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("ASSET_ROOT/chevron.svg"); width: 12px; height: 8px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #b9c9cf; border-radius: 3px; background: white; }
QCheckBox::indicator:checked { border-color: #087f78; background: #087f78; image: url("ASSET_ROOT/check.svg"); }
QComboBox QAbstractItemView { background: white; selection-background-color: #dcefe9; selection-color: #173c36; }
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #a4b1ba; background: #f3f5f7; }
QTableWidget { border: none; background: white; gridline-color: #eff2f5; selection-background-color: #e4f3ef; selection-color: #1b5146; outline: 0; }
QTableWidget::item { padding: 10px 8px; border-bottom: 1px solid #eff2f5; }
QHeaderView::section { background: #f7f9fa; color: #6e818e; border: none; padding: 11px 8px; font-size: 11px; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #f1f4f6; width: 8px; border: 0; }
QScrollBar::handle:vertical { background: #c4d1d7; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { border: none; border-radius: 3px; background: #e4eaee; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #087f78; border-radius: 3px; }
QPlainTextEdit { border: 1px solid #e0e6eb; background: #fbfcfd; border-radius: 8px; padding: 6px; }
QToolTip { background: #213b48; color: white; border: 0; padding: 6px; }
""".replace("ASSET_ROOT", (Path(__file__).parent / "assets").as_posix())


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def app_icon() -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#087f78"))
    painter.drawRoundedRect(4, 4, 120, 120, 28, 28)
    painter.setBrush(QColor("#f0fffa"))
    painter.drawRoundedRect(28, 29, 72, 70, 9, 9)
    painter.setBrush(QColor("#087f78"))
    for y in range(36, 93, 11):
        painter.drawRect(58 if (y // 11) % 2 else 65, y, 10, 7)
    painter.end()
    return QIcon(pixmap)


class Preview(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 130)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #f3f5f7; color: #8a9ba6; border-radius: 9px;")

    def set_image(self, data: bytes):
        self.source.loadFromData(data)
        self._fit()

    def clear_image(self, text: str = "미리보기"):
        self.source = QPixmap()
        self.clear()
        self.setText(text)

    def _fit(self):
        if not self.source.isNull():
            self.setPixmap(
                self.source.scaled(
                    self.size() - QSize(12, 12),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Image Zipper · {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1320, 940)
        self.setMinimumSize(1050, 740)
        self.setAcceptDrops(True)
        self.assets: list[Asset] = []
        self.loaders: list[Loader] = []
        self.estimator: Estimator | None = None
        self.exporter: Exporter | None = None
        self.revision = 0
        self.pending_estimate = False
        self.closing = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(550)
        self.debounce.timeout.connect(self._start_estimate)
        self._build()
        self._connect()
        self._preset_changed()

    def _build(self):
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 20)
        outer.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label("IMAGE ZIPPER", "brand"))
        titles.addWidget(label("필요한 품질로, 더 가볍게.", "heading"))
        titles.addWidget(label("이미지 일괄 축소부터 출판용 PDF까지, 내 컴퓨터에서.", "subtitle"))
        header.addLayout(titles)
        header.addStretch()
        badge = label("●  LOCAL & OFFLINE", "badge")
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(20)
        self.settings_card = QFrame()
        self.settings_card.setObjectName("card")
        self.settings_card.setFixedWidth(330)
        side_outer = QVBoxLayout(self.settings_card)
        side_outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side = QWidget()
        side.setStyleSheet("background: transparent;")
        controls = QVBoxLayout(side)
        controls.setContentsMargins(22, 22, 22, 22)
        controls.setSpacing(14)
        controls.addWidget(label("01  출력 설정", "section"))
        self.operation = QComboBox()
        self.operation.addItems(["각 파일을 개별 저장", "하나의 PDF로 병합"])
        controls.addWidget(self.operation)
        self.preset = QComboBox()
        self.preset.addItems(
            [
                "출판 · 사진  /  300 DPI",
                "출판 · 도표  /  손실 코덱 사용 안 함",
                "균형  /  150 DPI",
                "작은 파일  /  96 DPI",
            ]
        )
        controls.addWidget(label("품질 프리셋", "muted"))
        controls.addWidget(self.preset)
        self.mode = QComboBox()
        for text, value in [
            ("DPI 지정", "dpi"),
            ("최대 파일 용량 지정", "size"),
            ("원본 픽셀 수 유지", "original"),
        ]:
            self.mode.addItem(text, value)
        controls.addWidget(label("크기 조절 기준", "muted"))
        controls.addWidget(self.mode)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.dpi = QSpinBox()
        self.dpi.setRange(1, 9600)
        self.dpi.setSuffix(" DPI")
        self.dpi.setValue(300)
        self.max_mib = QDoubleSpinBox()
        self.max_mib.setRange(0.001, 100000)
        self.max_mib.setDecimals(3)
        self.max_mib.setSuffix(" MiB")
        self.max_mib.setValue(2)
        self.max_mib.setToolTip(
            "1 MiB = 1,048,576 bytes. 개별 저장은 출력 파일마다, 병합은 최종 PDF 전체에 적용합니다."
        )
        form.addRow("목표 해상도", self.dpi)
        form.addRow("용량 상한", self.max_mib)
        controls.addLayout(form)
        self.format = QComboBox()
        for text, value in [
            ("자동 · 입력 특성에 맞게", "auto"),
            ("JPEG · 높은 호환성", "jpeg"),
            ("PNG · 무손실 압축", "png"),
            ("WebP · 작은 파일", "webp"),
            ("AVIF · 고효율 압축", "avif"),
            ("TIFF · 출판·보관", "tiff"),
        ]:
            self.format.addItem(text, value)
        controls.addWidget(label("이미지 출력 형식 · PDF는 PDF로 유지", "muted"))
        controls.addWidget(self.format)
        self.artwork = QCheckBox("도표 보호 · 손실 코덱 사용 안 함")
        self.artwork.setToolTip(
            "픽셀 수를 줄이면 도표도 세부 정보가 줄어듭니다. 픽셀까지 보존하려면 ‘원본 픽셀 수 유지’를 선택하세요."
        )
        controls.addWidget(self.artwork)
        controls.addWidget(label("02  인쇄 크기와 품질", "section"))
        self.print_width = QDoubleSpinBox()
        self.print_width.setRange(0, 10000)
        self.print_width.setSpecialValueText("원본 정보 사용")
        self.print_width.setSuffix(" mm")
        self.print_width.setToolTip(
            "0이면 원본 DPI를 사용합니다. 너비를 지정하면 비율을 유지해 인쇄 크기를 계산합니다. 기존 PDF 페이지 크기는 바꾸지 않습니다."
        )
        self.reference = QSpinBox()
        self.reference.setRange(1, 9600)
        self.reference.setValue(300)
        self.reference.setSuffix(" DPI")
        self.min_dpi = QSpinBox()
        self.min_dpi.setRange(1, 9600)
        self.min_dpi.setValue(150)
        self.min_dpi.setSuffix(" DPI")
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.min_quality = QSpinBox()
        self.min_quality.setRange(1, 100)
        advanced = QFormLayout()
        advanced.setVerticalSpacing(10)
        for text, widget in [
            ("인쇄 너비", self.print_width),
            ("DPI 정보 없을 때", self.reference),
            ("최소 DPI", self.min_dpi),
            ("인코더 품질", self.quality),
            ("최소 품질", self.min_quality),
        ]:
            advanced.addRow(text, widget)
        controls.addLayout(advanced)
        self.jpeg_engine = QComboBox()
        self.jpeg_engine.addItem("Jpegli · 효율 우선 (4:2:0)", "jpegli")
        self.jpeg_engine.addItem("JPEG · 색 디테일 보존 (4:4:4)", "pillow")
        controls.addWidget(self.jpeg_engine)
        controls.addWidget(
            label(
                "DPI = 픽셀 수 ÷ 인쇄 크기(inch)\n원본보다 확대하지 않습니다. 최소 DPI는 용량 모드에서 추가 축소를 제한합니다.",
                "muted",
                True,
            )
        )
        controls.addStretch()
        scroll.setWidget(side)
        side_outer.addWidget(scroll)
        body.addWidget(self.settings_card)

        right = QVBoxLayout()
        right.setSpacing(16)
        self.queue_card = QFrame()
        self.queue_card.setObjectName("card")
        queue_layout = QVBoxLayout(self.queue_card)
        queue_layout.setContentsMargins(18, 16, 18, 12)
        toolbar = QHBoxLayout()
        self.queue_title = label("작업 목록  0", "section")
        toolbar.addWidget(self.queue_title)
        toolbar.addStretch()
        self.add_files = QPushButton("＋ 파일 추가")
        self.add_folder = QPushButton("폴더 추가")
        toolbar.addWidget(self.add_files)
        toolbar.addWidget(self.add_folder)
        queue_layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        icon = label("↘  ▧  ↙", "heading")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(icon)
        empty_title = label("파일을 여기에 놓아주세요", "dropTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        formats = label("PNG · JPG · HEIC · TIFF · PDF · BMP · WebP · AVIF 외", "muted")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(formats)
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["파일", "원본 크기", "픽셀 / 페이지", "원본 DPI", "예상 출력", "상태"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(49)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.stack.addWidget(self.table)
        self.stack.setMinimumHeight(200)
        queue_layout.addWidget(self.stack, 1)
        queue_actions = QHBoxLayout()
        self.up = QPushButton("↑")
        self.down = QPushButton("↓")
        self.remove = QPushButton("선택 제거")
        self.clear_all = QPushButton("비우기")
        for button in (self.up, self.down, self.remove, self.clear_all):
            button.setObjectName("subtle")
            queue_actions.addWidget(button)
        self.up.setToolTip("선택 파일을 위로 이동 · PDF 병합 순서")
        self.down.setToolTip("선택 파일을 아래로 이동 · PDF 병합 순서")
        queue_actions.addStretch()
        self.total = label("0 B", "muted")
        queue_actions.addWidget(self.total)
        queue_layout.addLayout(queue_actions)
        right.addWidget(self.queue_card, 5)

        estimate_card = QFrame()
        estimate_card.setObjectName("estimate")
        estimate_layout = QVBoxLayout(estimate_card)
        estimate_layout.setContentsMargins(18, 14, 18, 14)
        metrics = QHBoxLayout()
        self.estimate_title = label("선택 파일 미리보기", "section")
        metrics.addWidget(self.estimate_title)
        metrics.addStretch()
        self.metric_label = label("—", "metric")
        metrics.addWidget(self.metric_label)
        estimate_layout.addLayout(metrics)
        self.estimate_detail = label(
            "파일을 추가하면 용량과 실제 해상도를 계산합니다.", "muted", True
        )
        estimate_layout.addWidget(self.estimate_detail)
        self.estimate_note = label("", "muted", True)
        estimate_layout.addWidget(self.estimate_note)
        right.addWidget(estimate_card)

        previews = QSplitter(Qt.Orientation.Horizontal)
        self.before, self.after = Preview("원본"), Preview("압축 후")
        for title, view in (
            ("원본 · 첫 페이지", self.before),
            ("시험 압축 · 첫 페이지", self.after),
        ):
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            layout.addWidget(label(title, "muted"))
            layout.addWidget(view)
            previews.addWidget(container)
        previews.setHandleWidth(14)
        previews.setMinimumHeight(165)
        right.addWidget(previews, 3)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        output = QHBoxLayout()
        output.addWidget(label("저장 폴더", "muted"))
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("출력 폴더를 선택하세요")
        self.choose_folder = QPushButton("찾아보기")
        self.open_folder = QPushButton("폴더 열기")
        output.addWidget(self.destination, 1)
        output.addWidget(self.choose_folder)
        output.addWidget(self.open_folder)
        self.merge_name = QLineEdit("merged.pdf")
        self.merge_name.setMaximumWidth(200)
        self.merge_name.setToolTip("병합한 PDF의 파일 이름")
        self.merge_name.hide()
        output.addWidget(self.merge_name)
        outer.addLayout(output)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        outer.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.status = label("준비됨 · 원본 파일은 유지하고, 결과를 새 파일로 저장합니다.", "muted")
        bottom.addWidget(self.status, 1)
        self.show_log = QPushButton("작업 기록")
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton("압축해서 저장  →")
        self.export_button.setObjectName("primary")
        self.export_button.setEnabled(False)
        bottom.addWidget(self.show_log)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.export_button)
        outer.addLayout(bottom)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.hide()
        outer.addWidget(self.log)

    def _connect(self):
        self.add_files.clicked.connect(self._pick_files)
        self.add_folder.clicked.connect(self._pick_input_folder)
        self.choose_folder.clicked.connect(self._pick_output)
        self.open_folder.clicked.connect(self._open_output)
        self.remove.clicked.connect(self._remove)
        self.clear_all.clicked.connect(self._clear)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.show_log.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible()))
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.table.itemSelectionChanged.connect(self._settings_changed)
        for widget in (self.operation, self.mode, self.format, self.jpeg_engine):
            widget.currentIndexChanged.connect(self._settings_changed)
        for widget in (
            self.dpi,
            self.max_mib,
            self.reference,
            self.min_dpi,
            self.print_width,
            self.quality,
            self.min_quality,
        ):
            widget.valueChanged.connect(self._settings_changed)
        self.artwork.toggled.connect(self._settings_changed)
        self.export_button.clicked.connect(self._export)
        self.cancel_button.clicked.connect(self._cancel_export)

    def settings(self) -> Settings:
        settings = Settings(
            mode=self.mode.currentData(),
            target_dpi=self.dpi.value(),
            max_bytes=round(self.max_mib.value() * 1024 * 1024),
            min_dpi=self.min_dpi.value(),
            quality=self.quality.value(),
            min_quality=self.min_quality.value(),
            output_format="auto"
            if self.operation.currentIndex() == 1
            else self.format.currentData(),
            content="artwork" if self.artwork.isChecked() else "photo",
            reference_dpi=self.reference.value(),
            print_width_mm=self.print_width.value() or None,
            jpeg_engine=self.jpeg_engine.currentData(),
        )
        settings.validate()
        return settings

    def _preset_changed(self):
        presets = [
            (300, 95, 90, 300, False, "pillow"),
            (600, 95, 90, 300, True, "pillow"),
            (150, 86, 76, 96, False, "jpegli"),
            (96, 78, 60, 72, False, "jpegli"),
        ]
        dpi, quality, minimum, floor, artwork, engine = presets[self.preset.currentIndex()]
        for widget, value in (
            (self.dpi, dpi),
            (self.quality, quality),
            (self.min_quality, minimum),
            (self.min_dpi, floor),
        ):
            widget.setValue(value)
        self.artwork.setChecked(artwork)
        self.jpeg_engine.setCurrentIndex(self.jpeg_engine.findData(engine))
        if artwork and self.format.currentData() in {"jpeg", "avif"}:
            self.format.setCurrentIndex(0)
        self._settings_changed()

    def _settings_changed(self):
        self.revision += 1
        self.pending_estimate = True
        if self.estimator:
            self.estimator.cancel.set()
        merge = self.operation.currentIndex() == 1
        self.merge_name.setVisible(merge)
        self.format.setEnabled(not merge)
        self.dpi.setEnabled(self.mode.currentData() == "dpi")
        is_size = self.mode.currentData() == "size"
        self.max_mib.setEnabled(is_size)
        self.min_dpi.setEnabled(is_size)
        self.min_quality.setEnabled(is_size and not self.artwork.isChecked())
        self.quality.setEnabled(not self.artwork.isChecked())
        self.export_button.setText("PDF로 병합해서 저장  →" if merge else "압축해서 저장  →")
        self.before.clear_image("원본 미리보기 준비 중")
        self.after.clear_image("시험 압축 대기 중")
        # Invalidate all previous measurements when settings or selection change.
        for row in range(self.table.rowCount()):
            self.table.item(row, 4).setText("—")
        asset = self.selected_asset()
        if asset is None:
            self.metric_label.setText("—")
            self.estimate_detail.setText("파일을 추가하면 용량과 실제 해상도를 계산합니다.")
            self.estimate_note.setText("")
            return
        try:
            settings = self.settings()
            dpi, size, note = quick_estimate(asset, settings)
            if merge:
                self.metric_label.setText(
                    f"상한 {human_bytes(settings.max_bytes)}" if is_size else "병합 계산 중"
                )
                self.estimate_title.setText("최종 PDF 전체 예상")
                note = "선택 순서의 모든 페이지를 시험 병합합니다."
            else:
                self.metric_label.setText(f"약 {dpi:.0f} DPI" if dpi else "분석 중")
                self.estimate_title.setText("선택 파일 예상")
                note += f" · {human_bytes(size)}"
            self.estimate_detail.setText(note)
            if asset.is_pdf:
                reference = "PDF 페이지에 실제 배치된 이미지의 DPI를 측정합니다."
            elif settings.print_width_mm:
                reference = f"인쇄 너비 {settings.print_width_mm:g} mm 기준"
            elif asset.dpi_x:
                reference = f"원본 {asset.dpi_x:.0f} × {asset.dpi_y:.0f} DPI 기준"
            else:
                reference = f"DPI 정보 없음 → {settings.reference_dpi:g} DPI를 기준으로 가정"
            self.estimate_note.setText(
                reference
                + (
                    " · 여러 프레임 중 첫 프레임 예상"
                    if not merge and asset.frames > 1 and not asset.is_pdf
                    else ""
                )
            )
            self.debounce.start()
        except CompressionError as exc:
            self.estimate_detail.setText(str(exc))

    def selected_asset(self) -> Asset | None:
        row = self.table.currentRow()
        return (
            self.assets[row]
            if 0 <= row < len(self.assets)
            else (self.assets[0] if self.assets else None)
        )

    def _start_estimate(self):
        if self.exporter and self.exporter.isRunning():
            return
        if self.estimator is not None:
            self.pending_estimate = True
            return
        asset = self.selected_asset()
        if asset is None:
            return
        try:
            settings = self.settings()
        except CompressionError:
            return
        self.pending_estimate = False
        merge = self.operation.currentIndex() == 1
        self.estimator = Estimator(
            self.revision, list(self.assets) if merge else [asset], settings, merge, self
        )
        self.estimator.ready.connect(self._estimate_ready)
        self.estimator.failed.connect(self._estimate_failed)
        self.estimator.finished.connect(self._estimate_finished)
        self.estimator.start()

    def _estimate_finished(self):
        if self.estimator:
            self.estimator.deleteLater()
            self.estimator = None
        if self.pending_estimate and not self.closing:
            self.debounce.start()

    def _estimate_ready(self, revision, result, original, compressed):
        if revision != self.revision or self.closing:
            return
        self.before.set_image(original)
        self.after.set_image(compressed)
        dpi = f"{result.dpi_x:.0f} DPI" if result.dpi_x is not None else "벡터 / DPI 없음"
        self.metric_label.setText(dpi)
        cap = " · 목표 용량 충족" if self.mode.currentData() == "size" and result.target_met else ""
        if not result.target_met:
            cap = " · 목표 용량 미달 — 저장 안 함"
        self.estimate_detail.setText(
            f"시험 압축: {human_bytes(result.size_bytes)}{cap}\n{result.details}"
        )
        scope = (
            " · 첫 프레임 예상"
            if self.selected_asset()
            and not self.selected_asset().is_pdf
            and self.selected_asset().frames > 1
            and self.operation.currentIndex() == 0
            else ""
        )
        self.estimate_note.setText("\n".join(result.warnings[:3]) + scope)
        self.estimate_note.setToolTip("\n".join(result.warnings))
        if self.operation.currentIndex() == 0:
            row = self.table.currentRow()
            if row >= 0:
                self.table.item(row, 4).setText(human_bytes(result.size_bytes))
                self.table.item(row, 5).setText("계산 완료" if result.target_met else "용량 미달")

    def _estimate_failed(self, revision: int, message: str):
        if revision == self.revision:
            self.estimate_detail.setText(message)
            self.after.clear_image("미리보기 불가")

    def _pick_files(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(INPUT_EXTENSIONS))
        names, _ = QFileDialog.getOpenFileNames(
            self, "이미지·PDF 추가", "", f"이미지와 PDF ({patterns});;모든 파일 (*)"
        )
        if names:
            self.add_paths([Path(name) for name in names])

    def _pick_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "이미지가 있는 폴더")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]):
        if self.exporter and self.exporter.isRunning():
            return
        loader = Loader(paths, self)
        self.loaders.append(loader)
        loader.loaded.connect(self._asset_loaded)
        loader.failed.connect(self._log_error)
        loader.finished.connect(lambda: self._loader_finished(loader))
        self.status.setText("파일 정보를 불러오는 중…")
        self.export_button.setEnabled(False)
        loader.start()

    def _loader_finished(self, loader):
        self.loaders.remove(loader)
        loader.deleteLater()
        if not self.loaders:
            self.status.setText(
                f"{len(self.assets)}개 파일 준비됨 · 선택한 파일의 예상 결과를 표시합니다."
            )
            self.export_button.setEnabled(bool(self.assets))
            self._settings_changed()

    def _asset_loaded(self, asset: Asset):
        if self.closing or any(item.path == asset.path for item in self.assets):
            return
        self.assets.append(asset)
        row = self.table.rowCount()
        self.table.insertRow(row)
        size = (
            f"{asset.frames}쪽"
            if asset.is_pdf
            else f"{asset.width} × {asset.height}"
            + (f" · {asset.frames}장" if asset.frames > 1 else "")
        )
        dpi = "페이지별" if asset.is_pdf else (f"{asset.dpi_x:.0f}" if asset.dpi_x else "정보 없음")
        for column, value in enumerate(
            (asset.path.name, human_bytes(asset.size_bytes), size, dpi, "—", "대기")
        ):
            item = QTableWidgetItem(value)
            item.setToolTip(str(asset.path) if column == 0 else value)
            self.table.setItem(row, column, item)
        if not self.destination.text():
            self.destination.setText(str(asset.path.parent / "compressed"))
        self._queue_changed()
        if len(self.assets) == 1:
            self.table.selectRow(0)

    def _queue_changed(self):
        self.stack.setCurrentIndex(1 if self.assets else 0)
        self.queue_title.setText(f"작업 목록  {len(self.assets)}")
        self.total.setText(human_bytes(sum(asset.size_bytes for asset in self.assets)))
        self.export_button.setEnabled(bool(self.assets) and not self.loaders)

    def _remove(self):
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        self.table.blockSignals(True)
        for row in rows:
            del self.assets[row]
            self.table.removeRow(row)
        self.table.blockSignals(False)
        self._queue_changed()
        self._settings_changed()

    def _clear(self):
        self.table.blockSignals(True)
        self.assets.clear()
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self._queue_changed()
        self._settings_changed()

    def _move(self, delta: int):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(self.assets):
            return
        self.table.blockSignals(True)
        self.assets[row], self.assets[target] = self.assets[target], self.assets[row]
        for column in range(self.table.columnCount()):
            old = self.table.takeItem(row, column)
            new = self.table.takeItem(target, column)
            self.table.setItem(row, column, new)
            self.table.setItem(target, column, old)
        self.table.selectRow(target)
        self.table.blockSignals(False)
        self._settings_changed()

    def _pick_output(self):
        folder = QFileDialog.getExistingDirectory(self, "출력 폴더", self.destination.text())
        if folder:
            self.destination.setText(folder)

    def _open_output(self):
        if self.destination.text() and Path(self.destination.text()).is_dir():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(self.destination.text()).resolve()))
            )

    def _export(self):
        if self.exporter or not self.assets or self.loaders:
            return
        try:
            settings = self.settings()
            if not self.destination.text().strip():
                raise CompressionError("저장 폴더를 지정하세요.")
            folder = Path(self.destination.text()).expanduser().resolve()
        except (CompressionError, OSError) as exc:
            QMessageBox.warning(self, "설정 확인", str(exc))
            return
        self.revision += 1
        self.pending_estimate = False
        self.debounce.stop()
        if self.estimator:
            self.estimator.cancel.set()
        name = self.merge_name.text().strip() if self.operation.currentIndex() == 1 else None
        self.exporter = Exporter(list(self.assets), settings, folder, name, self)
        self.exporter.progress.connect(self._progress)
        self.exporter.saved.connect(self._saved)
        self.exporter.failed.connect(self._log_error)
        self.exporter.completed.connect(self._export_completed)
        self.exporter.finished.connect(self._export_finished)
        self._busy(True)
        self.exporter.start()

    def _busy(self, active: bool):
        for widget in (
            self.settings_card,
            self.queue_card,
            self.destination,
            self.choose_folder,
            self.merge_name,
        ):
            widget.setEnabled(not active)
        self.export_button.setEnabled(not active and bool(self.assets))
        self.cancel_button.setEnabled(active)

    def _progress(self, index, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(index)
        self.status.setText(f"처리 중 {index + 1}/{total} · {name}")

    def _saved(self, result):
        self.log.appendPlainText(f"저장: {result.output.name} · {human_bytes(result.after_bytes)}")
        for warning in result.warnings:
            self.log.appendPlainText(f"  {warning}")
        for row, asset in enumerate(self.assets):
            if asset.path == result.source:
                self.table.item(row, 4).setText(human_bytes(result.after_bytes))
                self.table.item(row, 5).setText("저장 완료")

    def _log_error(self, message):
        self.log.appendPlainText(f"오류: {message}")
        self.log.show()

    def _export_completed(self, results, errors, report, cancelled):
        self.progress.setRange(0, 100)
        self.progress.setValue(0 if cancelled else 100)
        status = "취소됨" if cancelled else "완료"
        self.status.setText(f"{status} · {len(results)}개 저장 · {len(errors)}개 오류")
        if report:
            self.log.appendPlainText(f"결과 보고서: {report}")

    def _export_finished(self):
        self.exporter.deleteLater()
        self.exporter = None
        self._busy(False)

    def _cancel_export(self):
        if self.exporter:
            self.exporter.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("현재 인코딩이 끝나면 취소합니다. 저장된 결과는 유지됩니다.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.exporter:
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event):
        self.closing = True
        self.debounce.stop()
        running = [
            worker
            for worker in [self.estimator, self.exporter, *self.loaders]
            if worker and worker.isRunning()
        ]
        if running:
            for worker in running:
                worker.cancel.set()
            self.status.setText("작업을 안전하게 종료하는 중…")
            event.ignore()
            QTimer.singleShot(150, self.close)
        else:
            event.accept()


def main() -> int:
    if "--self-test" in sys.argv:
        from .selftest import run

        index = sys.argv.index("--self-test")
        path = Path(sys.argv[index + 1]) if len(sys.argv) > index + 1 else Path("self-test.json")
        return run(path)
    app = QApplication(sys.argv)
    app.setApplicationName("Image Zipper")
    app.setOrganizationName("ImageZipper")
    app.setStyle("Fusion")
    app.setFont(QFont("Apple SD Gothic Neo" if sys.platform == "darwin" else "Malgun Gothic", 11))
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    paths = [Path(arg) for arg in sys.argv[1:] if not arg.startswith("-")]
    if paths:
        window.add_paths(paths)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
