from __future__ import annotations

import multiprocessing
import sys
import tempfile
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
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
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .controls import SettingComboBox, SettingDoubleSpinBox, SettingSpinBox
from .file_table import FileTable
from .imaging import INPUT_EXTENSIONS, quick_estimate
from .models import Asset, CompressionError, Settings, human_bytes
from .theme import configure_theme
from .workers import Estimator, Exporter, Loader, Previewer, SampleEstimator


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def app_icon() -> QIcon:
    return QIcon(str(Path(__file__).with_name("assets") / "app-icon.svg"))


def document_icon(size=64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#8e8e93"), 2.2))
    painter.drawRoundedRect(6, 10, size - 12, size - 20, 7, 7)
    painter.drawEllipse(16, 20, 7, 7)
    painter.drawLine(10, size - 17, 27, 32)
    painter.drawLine(27, 32, 38, 43)
    painter.drawLine(38, 43, 46, 35)
    painter.drawLine(46, 35, size - 8, size - 22)
    painter.end()
    return QIcon(pixmap)


class Preview(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 110)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setObjectName("preview")

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
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowFullscreenButtonHint
        )
        self.setWindowTitle("Pictumi")
        self.setWindowIcon(app_icon())
        self.resize(1200, 820)
        self.setMinimumSize(1040, 700)
        self.setAcceptDrops(True)
        self.assets: list[Asset] = []
        self.asset_paths: set[Path] = set()
        self.total_bytes = 0
        self.loaders: list[Loader] = []
        self.previewer: Previewer | None = None
        self.preview_cache: OrderedDict[Asset, bytes] = OrderedDict()
        self.preview_asset: Asset | None = None
        self.estimator: Estimator | None = None
        self.exporter: Exporter | None = None
        self.sampler = None
        self.sample_cache = {}
        self.measured_revision = None
        self.trial_cache = None
        self.trial_directory = tempfile.TemporaryDirectory(prefix="pictumi-preview-")
        self.revision = 0
        self.closing = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(120)
        self.debounce.timeout.connect(self._start_preview)
        self._build()
        self._connect()
        self._preset_changed()

    def _build(self):
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        self.settings_card = QFrame()
        self.settings_card.setObjectName("sidebar")
        self.settings_card.setFixedWidth(266)
        side_outer = QVBoxLayout(self.settings_card)
        side_outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side = QWidget()
        controls = QVBoxLayout(side)
        controls.setContentsMargins(16, 20, 16, 16)
        controls.setSpacing(10)
        controls.addWidget(label("작업", "section"))
        self.operation = SettingComboBox(self.settings_card)
        self.operation.addItems(["파일 압축", "PDF로 합치기"])
        self.operation.hide()
        self.navigation = []
        self.navigation_group = QButtonGroup(self)
        for index, text in enumerate(("파일 압축", "PDF로 합치기")):
            button = QPushButton(text)
            button.setObjectName("navigation")
            button.setCheckable(True)
            self.navigation_group.addButton(button)
            button.toggled.connect(
                lambda checked, value=index: (
                    self.operation.setCurrentIndex(value) if checked else None
                )
            )
            self.navigation.append(button)
            controls.addWidget(button)
        controls.addSpacing(14)
        controls.addWidget(label("크기 조절", "section"))
        self.mode = SettingComboBox()
        self.mode.addItem("DPI 지정", "dpi")
        self.mode.addItem("최대 파일 크기", "size")
        controls.addWidget(self.mode)
        self.target_form = QFormLayout()
        self.target_form.setVerticalSpacing(10)
        self.dpi = SettingSpinBox()
        self.dpi.setRange(1, 9600)
        self.dpi.setSuffix(" DPI")
        self.dpi.setValue(300)
        self.max_mib = SettingDoubleSpinBox()
        self.max_mib.setRange(0.002, 100000)
        self.max_mib.setDecimals(3)
        self.max_mib.setSuffix(" MB")
        self.max_mib.setValue(2)
        self.max_mib.setToolTip(
            "1 MB = 1,000,000바이트. 개별 저장은 파일마다, PDF로 합치기는 전체에 적용합니다."
        )
        self.target_form.addRow("해상도", self.dpi)
        self.target_form.addRow("최대 용량", self.max_mib)
        controls.addLayout(self.target_form)
        self.mode_help = label("", "caption", True)
        controls.addWidget(self.mode_help)
        controls.addSpacing(14)
        controls.addWidget(label("품질", "section"))
        self.format = SettingComboBox()
        for text, value in (
            ("자동", "auto"),
            ("JPEG", "jpeg"),
            ("PNG", "png"),
            ("WebP", "webp"),
            ("AVIF", "avif"),
            ("TIFF", "tiff"),
        ):
            self.format.addItem(text, value)
        self.format.setToolTip("기존 PDF는 PDF로 저장합니다.")
        self.preset = SettingComboBox()
        self.preset.addItems(["사진 · 인쇄", "도표 · 인쇄", "균형", "작은 파일"])
        self.preset.setCurrentIndex(2)
        controls.addWidget(self.preset)
        self.artwork = QCheckBox("도표의 선과 색상 보존")
        self.artwork.setToolTip(
            "손실 코덱을 사용하지 않습니다. 해상도를 낮추면 세부 정보는 줄어듭니다."
        )
        controls.addSpacing(14)
        self.advanced_toggle = QPushButton("›  고급 설정")
        self.advanced_toggle.setObjectName("subtle")
        self.advanced_toggle.setCheckable(True)
        controls.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(10)
        advanced_layout.addWidget(self.artwork)
        self.strip_metadata = QCheckBox("메타데이터 삭제")
        self.strip_metadata.setToolTip(
            "촬영일·위치·작성자 등의 부가 정보를 삭제합니다. 색상·방향·DPI는 유지합니다. "
            "PDF는 문서 속성과 XMP를 삭제하며 본문·주석·첨부파일은 유지합니다."
        )
        advanced_layout.addWidget(self.strip_metadata)
        self.print_width = SettingDoubleSpinBox()
        self.print_width.setRange(0, 10000)
        self.print_width.setSpecialValueText("원본 기준")
        self.print_width.setSuffix(" mm")
        self.print_width.setToolTip("인쇄할 너비를 지정하면 이 크기를 기준으로 DPI를 계산합니다.")
        self.reference = SettingSpinBox()
        self.reference.setRange(1, 9600)
        self.reference.setValue(300)
        self.reference.setSuffix(" DPI")
        self.quality = SettingSpinBox()
        self.quality.setRange(1, 100)
        self.min_quality = SettingSpinBox()
        self.min_quality.setRange(1, 100)
        self.min_quality.setToolTip("용량 모드는 이 품질 이상에서 해상도와 품질을 자동 조절합니다.")
        self.advanced_form = QFormLayout()
        self.advanced_form.setVerticalSpacing(10)
        for text, widget in (
            ("파일 형식", self.format),
            ("인쇄 너비", self.print_width),
            ("기본 DPI", self.reference),
            ("압축 품질", self.quality),
            ("최소 품질", self.min_quality),
        ):
            self.advanced_form.addRow(text, widget)
        advanced_layout.addLayout(self.advanced_form)
        self.jpeg_engine = SettingComboBox()
        self.jpeg_engine.addItem("Jpegli · 압축 효율", "jpegli")
        self.jpeg_engine.addItem("JPEG · 색상 디테일", "pillow")
        advanced_layout.addWidget(self.jpeg_engine)
        advanced_layout.addWidget(
            label("기본 DPI는 원본에 해상도 정보가 없을 때 사용합니다.", "caption", True)
        )
        self.advanced_panel.hide()
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda opened: self.advanced_toggle.setText(
                "⌄  고급 설정" if opened else "›  고급 설정"
            )
        )
        controls.addWidget(self.advanced_panel)
        controls.addStretch()
        controls.addWidget(label("원본 파일은 그대로 유지됩니다.", "caption"))
        scroll.setWidget(side)
        side_outer.addWidget(scroll)
        outer.addWidget(self.settings_card)

        main = QVBoxLayout()
        main.setContentsMargins(20, 4, 4, 0)
        main.setSpacing(12)
        outer.addLayout(main, 1)
        body = QSplitter(Qt.Orientation.Vertical)
        body.setHandleWidth(10)
        body.setChildrenCollapsible(False)
        self.queue_card = QFrame()
        queue_layout = QVBoxLayout(self.queue_card)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 2, 0, 8)
        self.queue_title = label("파일 압축", "heading")
        toolbar.addWidget(self.queue_title)
        toolbar.addStretch()
        self.add_folder = QPushButton("폴더 추가")
        self.add_files = QPushButton("＋ 파일 추가")
        toolbar.addWidget(self.add_folder)
        toolbar.addWidget(self.add_files)
        queue_layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setSpacing(12)
        empty_layout.addStretch()
        empty_icon = label("")
        empty_icon.setPixmap(document_icon(64).pixmap(64, 64))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        for text, name in (
            ("이미지 또는 PDF 추가", "dropTitle"),
            ("파일을 여기로 드래그하거나 ‘파일 추가’를 누르세요.", "muted"),
            ("PNG · JPG · HEIC · TIFF · PDF · BMP · WebP · AVIF", "caption"),
        ):
            item = label(text, name)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(item)
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        self.table = FileTable(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["파일", "원본 크기", "픽셀 / 페이지", "DPI", "출력 크기", "상태"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((90, 116, 75, 94, 88), start=1):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive
            )
            self.table.setColumnWidth(column, width)
        self.stack.addWidget(self.table)
        self.stack.setMinimumHeight(160)
        queue_layout.addWidget(self.stack, 1)
        actions = QHBoxLayout()
        self.up, self.down = QPushButton("↑"), QPushButton("↓")
        self.remove, self.clear_all = QPushButton("선택 제거"), QPushButton("모두 지우기")
        for button in (self.up, self.down, self.remove, self.clear_all):
            button.setObjectName("subtle")
            actions.addWidget(button)
        self.up.setToolTip("PDF로 합치기 순서를 위로 이동")
        self.down.setToolTip("PDF로 합치기 순서를 아래로 이동")
        actions.addStretch()
        self.total = label("항목 없음", "caption")
        actions.addWidget(self.total)
        queue_layout.addLayout(actions)
        body.addWidget(self.queue_card)

        self.inspector = QFrame()
        self.inspector.setObjectName("inspector")
        inspection = QVBoxLayout(self.inspector)
        inspection.setContentsMargins(14, 12, 14, 14)
        inspection.setSpacing(10)
        metrics = QHBoxLayout()
        self.estimate_title = label("선택 파일", "section")
        self.metric_label = label("—", "metric")
        self.measure_button = QPushButton("결과 확인")
        self.measure_button.setEnabled(False)
        self.measure_button.setToolTip("시험 압축으로 최종 파일 크기와 DPI를 확인합니다.")
        self.edit_button = QPushButton("편집")
        self.edit_button.setEnabled(False)
        metrics.addWidget(self.estimate_title)
        metrics.addStretch()
        metrics.addWidget(self.metric_label)
        metrics.addSpacing(8)
        metrics.addWidget(self.edit_button)
        metrics.addWidget(self.measure_button)
        inspection.addLayout(metrics)
        self.estimate_detail = label("", "muted", True)
        self.estimate_note = label("", "caption", True)
        inspection.addWidget(self.estimate_detail)
        inspection.addWidget(self.estimate_note)
        previews = QHBoxLayout()
        previews.setSpacing(12)
        self.before, self.after = Preview("원본"), Preview("압축 후")
        self.before_title = label("원본", "caption")
        for title, view in (
            (self.before_title, self.before),
            (label("압축 결과", "caption"), self.after),
        ):
            layout = QVBoxLayout()
            layout.setSpacing(4)
            layout.addWidget(title)
            layout.addWidget(view, 1)
            previews.addLayout(layout, 1)
        inspection.addLayout(previews, 1)
        self.inspector.hide()
        body.addWidget(self.inspector)
        body.setSizes([420, 280])
        main.addWidget(body, 1)

        output = QFrame()
        output.setObjectName("output")
        output_layout = QHBoxLayout(output)
        output_layout.setContentsMargins(10, 8, 10, 8)
        output_layout.addWidget(label("저장 위치", "caption"))
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("파일을 추가하면 저장 폴더가 표시됩니다")
        self.choose_folder, self.open_folder = QPushButton("변경…"), QPushButton("열기")
        output_layout.addWidget(self.destination, 1)
        output_layout.addWidget(self.choose_folder)
        output_layout.addWidget(self.open_folder)
        self.merge_name = QLineEdit("merged.pdf")
        self.merge_name.setMaximumWidth(150)
        self.merge_name.setToolTip("병합할 PDF 파일 이름")
        self.merge_name.hide()
        output_layout.addWidget(self.merge_name)
        main.addWidget(output)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.progress.hide()
        main.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.status = label("파일을 추가하세요", "caption")
        self.status.setMinimumWidth(50)
        bottom.addWidget(self.status, 1)
        self.show_log = QPushButton("작업 기록")
        self.show_log.setObjectName("subtle")
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.export_button = QPushButton("저장")
        self.export_button.setObjectName("primary")
        self.export_button.setEnabled(False)
        for widget in (self.show_log, self.cancel_button, self.export_button):
            bottom.addWidget(widget)
        main.addLayout(bottom)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.hide()
        main.addWidget(self.log)

    def _connect(self):
        file_menu = self.menuBar().addMenu("파일")
        add_action = QAction("파일 추가…", self)
        add_action.setShortcut(QKeySequence.StandardKey.Open)
        add_action.triggered.connect(self._pick_files)
        file_menu.addAction(add_action)
        close_action = QAction("창 닫기", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)
        help_menu = self.menuBar().addMenu("도움말")
        about = QAction("Pictumi 정보", self)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(
            lambda: QMessageBox.about(
                self,
                "Pictumi 정보",
                f"Pictumi · 픽투미\n버전 {__version__}\n\n이미지 편집과 이미지·PDF 압축\n모든 작업은 이 기기에서 처리됩니다.",
            )
        )
        help_menu.addAction(about)
        licenses = QAction("오픈소스 라이선스…", self)
        licenses.triggered.connect(self._show_licenses)
        help_menu.addAction(licenses)
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
            self.print_width,
            self.quality,
            self.min_quality,
        ):
            widget.valueChanged.connect(self._settings_changed)
        self.artwork.toggled.connect(self._settings_changed)
        self.strip_metadata.toggled.connect(self._settings_changed)
        self.export_button.clicked.connect(self._export)
        self.cancel_button.clicked.connect(self._cancel_export)
        self.measure_button.clicked.connect(self._start_estimate)
        self.edit_button.clicked.connect(self._open_editor)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_editor())

    def _show_licenses(self):
        root = (
            Path(sys._MEIPASS) / "third-party-licenses"
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[2] / "build/third-party-licenses"
        )
        notice = root / "THIRD_PARTY_NOTICES.txt"
        if not notice.exists():
            root = Path(__file__).resolve().parents[2] / "packaging"
            notice = root / "THIRD_PARTY_NOTICES.txt"
        dialog = QDialog(self)
        dialog.setWindowTitle("오픈소스 라이선스")
        dialog.resize(680, 560)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(
            notice.read_text(encoding="utf-8")
            if notice.exists()
            else "https://github.com/lgyEthan/pictumi"
        )
        layout.addWidget(text)
        buttons = QHBoxLayout()
        folder = QPushButton("라이선스 폴더 열기")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))))
        sources = QPushButton("해당 버전 소스")
        sources.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(f"https://github.com/lgyEthan/pictumi/releases/tag/v{__version__}")
            )
        )
        close = QPushButton("닫기")
        close.setDefault(True)
        close.clicked.connect(dialog.accept)
        buttons.addWidget(folder)
        buttons.addWidget(sources)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def settings(self) -> Settings:
        settings = Settings(
            mode=self.mode.currentData(),
            target_dpi=self.dpi.value(),
            max_bytes=round(self.max_mib.value() * 1_000_000),
            quality=self.quality.value(),
            min_quality=self.min_quality.value(),
            output_format="auto"
            if self.operation.currentIndex() == 1
            else self.format.currentData(),
            content="artwork" if self.artwork.isChecked() else "photo",
            reference_dpi=self.reference.value(),
            print_width_mm=self.print_width.value() or None,
            jpeg_engine=self.jpeg_engine.currentData(),
            strip_metadata=self.strip_metadata.isChecked(),
        )
        settings.validate()
        return settings

    def _preset_changed(self):
        presets = [
            (300, 95, 90, False, "pillow"),
            (600, 95, 90, True, "pillow"),
            (150, 86, 76, False, "jpegli"),
            (96, 78, 60, False, "jpegli"),
        ]
        dpi, quality, minimum, artwork, engine = presets[self.preset.currentIndex()]
        for widget, value in (
            (self.dpi, dpi),
            (self.quality, quality),
            (self.min_quality, minimum),
        ):
            widget.setValue(value)
        self.artwork.setChecked(artwork)
        self.jpeg_engine.setCurrentIndex(self.jpeg_engine.findData(engine))
        if artwork and self.format.currentData() in {"jpeg", "avif"}:
            self.format.setCurrentIndex(0)
        self._settings_changed()

    def _settings_changed(self):
        self.revision += 1
        if self.sampler:
            self.sampler.cancel.set()
        if self.estimator:
            self.estimator.cancel.set()
        merge = self.operation.currentIndex() == 1
        for index, button in enumerate(self.navigation):
            button.setChecked(index == int(merge))
        self.queue_title.setText("PDF로 합치기" if merge else "파일 압축")
        self.merge_name.setVisible(merge)
        self.format.setEnabled(not merge)
        is_size = self.mode.currentData() == "size"
        self.target_form.setRowVisible(self.dpi, not is_size)
        self.target_form.setRowVisible(self.max_mib, is_size)
        self.advanced_form.setRowVisible(self.quality, not is_size and not self.artwork.isChecked())
        self.advanced_form.setRowVisible(self.min_quality, is_size and not self.artwork.isChecked())
        self.mode_help.setText(
            "해상도와 품질을 자동으로 조절합니다."
            if is_size
            else "인쇄 크기를 유지하며 해상도를 조절합니다."
        )
        self.quality.setEnabled(not self.artwork.isChecked())
        self.export_button.setText("PDF 저장" if merge else "저장")
        self.after.clear_image("계산 후 표시")
        # Invalidate all previous measurements when settings or selection change.
        for row in range(self.table.rowCount()):
            self.table.item(row, 4).setText("—")
            if self.table.item(row, 5).text() in {"계산 완료", "용량 미달"}:
                self.table.item(row, 5).setText("대기")
        asset = self.selected_asset()
        self.edit_button.setEnabled(
            self._editable(asset) and not self.loaders and not self.exporter
        )
        self.edit_button.setToolTip(
            "선택한 사진을 편집합니다. 원본 파일은 유지됩니다."
            if self._editable(asset)
            else "편집은 단일 8비트 이미지에서 사용할 수 있습니다. PDF·다중 페이지·고비트 이미지는 압축만 지원합니다."
        )
        self.before_title.setText("편집한 이미지" if asset and asset.edits.active else "원본")
        self.inspector.setVisible(asset is not None)
        self.measure_button.setEnabled(asset is not None and not self.loaders and not self.exporter)
        if asset != self.preview_asset:
            self.preview_asset = asset
            self.before.clear_image("미리보기 준비 중" if asset else "원본")
            if self.previewer:
                self.previewer.cancel.set()
            if asset in self.preview_cache:
                self.before.set_image(self.preview_cache[asset])
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
                    f"상한 {human_bytes(settings.max_bytes)}"
                    if is_size
                    else f"{settings.target_dpi:g} DPI"
                )
                self.estimate_title.setText("최종 PDF 전체 예상")
                note = "‘결과 확인’으로 전체 PDF의 용량과 DPI를 확인합니다."
            else:
                self.metric_label.setText(f"약 {dpi:.0f} DPI" if dpi else "이미지 DPI 분석 필요")
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
                    " · 편집에서 지정한 픽셀 크기 적용"
                    if not merge and asset.edits.width is not None
                    else ""
                )
                + (" · 모자이크 보호를 위해 메타데이터 삭제" if asset.edits.mosaics else "")
                + (
                    " · 여러 페이지 중 첫 페이지의 빠른 추정"
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

    @staticmethod
    def _editable(asset):
        return bool(
            asset
            and not asset.is_pdf
            and asset.frames == 1
            and asset.color_mode in {"RGB", "RGBA", "L", "LA", "CMYK", "P", "1"}
        )

    def _open_editor(self):
        asset = self.selected_asset()
        if not self._editable(asset) or self.exporter or self.loaders:
            return
        from .editor import EditorDialog

        self.debounce.stop()
        if self.previewer:
            self.previewer.cancel.set()
        if self.estimator:
            self.estimator.cancel.set()
        if self.sampler:
            self.sampler.cancel.set()
        preview = self.preview_cache.get(asset) if not asset.edits.active else None
        dialog = EditorDialog(asset, self, preview_data=preview)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_edits(asset, dialog.edits)
        dialog.deleteLater()
        self.debounce.start()

    def _apply_edits(self, asset, edits):
        if asset not in self.assets or edits == asset.edits:
            return
        edits.validate()
        row = self.assets.index(asset)
        changed = replace(asset, edits=edits)
        self.assets[row] = changed
        self.trial_cache = None
        self.preview_cache.pop(asset, None)
        self.sample_cache.pop(asset, None)
        self.table.item(row, 2).setText(" × ".join(str(v) for v in changed.pixel_size))
        self.table.item(row, 5).setText("편집됨" if edits.active else "대기")
        self._settings_changed()

    def _start_preview(self):
        if self.closing or self.exporter or self.loaders:
            return
        asset = self.selected_asset()
        if asset is None or self.previewer is not None:
            return
        if asset in self.preview_cache:
            self.preview_cache.move_to_end(asset)
            self.before.set_image(self.preview_cache[asset])
            self._start_sample()
            return
        self.previewer = Previewer(asset, self)
        self.previewer.ready.connect(self._preview_ready)
        self.previewer.failed.connect(self._preview_failed)
        self.previewer.finished.connect(self._preview_finished)
        self.previewer.start()

    def _preview_ready(self, asset, data, sample):
        if self.closing or asset.path not in self.asset_paths:
            return
        self.preview_cache[asset] = data
        self.sample_cache[asset] = sample
        while len(self.preview_cache) > 12:
            expired, _ = self.preview_cache.popitem(last=False)
            self.sample_cache.pop(expired, None)
        if self.selected_asset() == asset:
            self.before.set_image(data)
            self._start_sample()

    def _start_sample(self):
        asset = self.selected_asset()
        if (
            self.closing
            or self.exporter
            or self.estimator
            or self.sampler
            or self.operation.currentIndex() == 1
            or self.mode.currentData() != "size"
            or self.measured_revision == self.revision
            or not self.sample_cache.get(asset)
        ):
            return
        self.sampler = SampleEstimator(
            self.revision, asset, self.settings(), self.sample_cache[asset], self
        )
        self.sampler.ready.connect(self._sample_ready)
        self.sampler.finished.connect(self._sample_finished)
        self.sampler.start()

    def _sample_ready(self, revision, dpi, size, note):
        if revision == self.revision and self.measured_revision != revision and not self.estimator:
            self.metric_label.setText(f"약 {dpi:.0f} DPI" if dpi else "DPI 분석 필요")
            self.estimate_detail.setText(f"{note} · 약 {human_bytes(size)}")

    def _sample_finished(self):
        revision = self.sampler.revision
        self.sampler.deleteLater()
        self.sampler = None
        if not self.closing and revision != self.revision:
            self.debounce.start()

    def _preview_failed(self, message):
        if self.previewer and self.previewer.assets[0] == self.selected_asset():
            self.before.clear_image("미리보기 불가")
            self.before.setToolTip(message)

    def _preview_finished(self):
        previous = self.previewer.assets[0]
        cancelled = self.previewer.cancel.is_set()
        self.previewer.deleteLater()
        self.previewer = None
        if (
            self.selected_asset() is not None
            and (self.selected_asset() != previous or cancelled)
            and not self.closing
        ):
            self.debounce.start()

    def _start_estimate(self):
        if self.closing or self.exporter or self.loaders:
            return
        if self.estimator is not None:
            self.estimator.cancel.set()
            self._settings_changed()
            self.measure_button.setText("취소 중…")
            return
        asset = self.selected_asset()
        if asset is None:
            return
        try:
            settings = self.settings()
        except CompressionError:
            return
        merge = self.operation.currentIndex() == 1
        if self.sampler:
            self.sampler.cancel.set()
        for path in Path(self.trial_directory.name).iterdir():
            path.unlink(missing_ok=True)
        self.trial_cache = None
        self.estimator = Estimator(
            self.revision,
            list(self.assets) if merge else [asset],
            settings,
            merge,
            self,
            cache_path=Path(self.trial_directory.name) / "result",
        )
        self.estimator.ready.connect(self._estimate_ready)
        self.estimator.failed.connect(self._estimate_failed)
        self.estimator.finished.connect(self._estimate_finished)
        self.measure_button.setText("계산 취소")
        self.estimate_detail.setText("전체 시험 압축 중… 설정 변경이나 취소가 가능합니다.")
        self.estimator.start()

    def _estimate_finished(self):
        if self.estimator:
            self.estimator.deleteLater()
            self.estimator = None
        self.measure_button.setText("결과 확인")

    def _estimate_ready(self, revision, result, size_bytes, compressed):
        if revision != self.revision or self.closing:
            return
        self.measured_revision = revision
        self.trial_cache = self.estimator.cached if self.estimator else None
        self.estimate_title.setText("실제 계산 결과")
        self.after.set_image(compressed)
        dpi = f"{result.dpi_x:.0f} DPI" if result.dpi_x is not None else "벡터 / DPI 없음"
        self.metric_label.setText(dpi)
        cap = " · 목표 용량 충족" if self.mode.currentData() == "size" and result.target_met else ""
        if not result.target_met:
            cap = " · 목표 용량 미달 — 저장 안 함"
        self.estimate_detail.setText(f"시험 압축: {human_bytes(size_bytes)}{cap}\n{result.details}")
        scope = (
            " · 전체 페이지"
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
                self.table.item(row, 4).setText(human_bytes(size_bytes))
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
        self.debounce.stop()
        self.revision += 1
        if self.estimator:
            self.estimator.cancel.set()
        if self.previewer:
            self.previewer.cancel.set()
        self.measure_button.setEnabled(False)
        self.edit_button.setEnabled(False)
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
        sender = self.sender()
        if (
            self.closing
            or asset.path in self.asset_paths
            or (isinstance(sender, Loader) and sender.cancel.is_set())
        ):
            return
        self.assets.append(asset)
        self.asset_paths.add(asset.path)
        self.total_bytes += asset.size_bytes
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
        self.total.setText(
            f"{len(self.assets)}개 항목 · {human_bytes(self.total_bytes)}"
            if self.assets
            else "항목 없음"
        )
        for button in (self.up, self.down, self.remove, self.clear_all):
            button.setEnabled(bool(self.assets))
        self.export_button.setEnabled(bool(self.assets) and not self.loaders)

    def _remove(self):
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        self.table.blockSignals(True)
        for row in rows:
            self.total_bytes -= self.assets[row].size_bytes
            self.asset_paths.discard(self.assets[row].path)
            self.preview_cache.pop(self.assets[row], None)
            del self.assets[row]
            self.table.removeRow(row)
        self.table.blockSignals(False)
        self._queue_changed()
        self._settings_changed()

    def _clear(self):
        for loader in self.loaders:
            loader.cancel.set()
        self.table.blockSignals(True)
        self.assets.clear()
        self.asset_paths.clear()
        self.preview_cache.clear()
        self.sample_cache.clear()
        self.total_bytes = 0
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
        self.debounce.stop()
        if self.estimator:
            self.estimator.cancel.set()
        if self.previewer:
            self.previewer.cancel.set()
        name = self.merge_name.text().strip() if self.operation.currentIndex() == 1 else None
        self.exporter = Exporter(
            list(self.assets), settings, folder, name, self, cached=self.trial_cache
        )
        self.exporter.progress.connect(self._progress)
        self.exporter.saved.connect(self._saved)
        self.exporter.failed.connect(self._log_error)
        self.exporter.completed.connect(self._export_completed)
        self.exporter.finished.connect(self._export_finished)
        self._busy(True)
        self.exporter.start()

    def _busy(self, active: bool):
        if active and self.sampler:
            self.sampler.cancel.set()
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
        self.cancel_button.setVisible(active)
        self.progress.setVisible(active)
        self.measure_button.setEnabled(not active and bool(self.assets))
        self.edit_button.setEnabled(not active and self._editable(self.selected_asset()))

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

    def _export_completed(self, results, errors, cancelled):
        self.progress.setRange(0, 100)
        self.progress.setValue(0 if cancelled or (errors and not results) else 100)
        status = "완료"
        if errors:
            status = "일부 실패" if results else "저장 실패"
        if cancelled:
            status = "취소됨"
        self.status.setText(f"{status} · {len(results)}개 저장 · {len(errors)}개 오류")

    def _export_finished(self):
        self.exporter.deleteLater()
        self.exporter = None
        self._busy(False)
        if self.before.source.isNull() and not self.closing:
            self.debounce.start()

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
            for worker in [
                self.estimator,
                self.previewer,
                self.sampler,
                self.exporter,
                *self.loaders,
            ]
            if worker and worker.isRunning()
        ]
        if running:
            for worker in running:
                worker.cancel.set()
            self.status.setText("작업을 안전하게 종료하는 중…")
            event.ignore()
            QTimer.singleShot(150, self.close)
        else:
            self.trial_directory.cleanup()
            event.accept()


def main() -> int:
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        from .selftest import run

        index = sys.argv.index("--self-test")
        path = Path(sys.argv[index + 1]) if len(sys.argv) > index + 1 else None
        return run(path)
    app = QApplication(sys.argv)
    app.setApplicationName("Pictumi")
    app.setOrganizationName("Pictumi")
    configure_theme(app)
    app.styleHints().colorSchemeChanged.connect(lambda _: configure_theme(app))
    window = MainWindow()
    window.show()
    paths = [Path(arg) for arg in sys.argv[1:] if not arg.startswith("-")]
    if paths:
        window.add_paths(paths)
    exit_code = app.exec()
    # Destroy widgets while QApplication is still alive. Python signal closures
    # can otherwise defer widget destruction until after Qt has shut down.
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
