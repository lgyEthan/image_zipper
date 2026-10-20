from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from .imaging import discover, preview_encoded, probe, thumbnail
from .models import Cancelled, Settings, check_cancel
from .pdf import merge_pdf
from .service import measure, save_asset, save_merged, save_report


class Loader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.paths = paths
        self.cancel = Event()

    def run(self):
        try:
            for path in discover(self.paths):
                check_cancel(self.cancel)
                try:
                    self.loaded.emit(probe(path))
                except Exception as exc:
                    self.failed.emit(f"{path.name}: {exc}")
        except Cancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class Estimator(QThread):
    ready = Signal(int, object, bytes, bytes)
    failed = Signal(int, str)

    def __init__(self, revision: int, assets: list, settings: Settings, merge: bool, parent=None):
        super().__init__(parent)
        self.revision, self.assets, self.settings, self.merge = revision, assets, settings, merge
        self.cancel = Event()

    def run(self):
        try:
            original = thumbnail(self.assets[0])
            check_cancel(self.cancel)
            result = (
                merge_pdf(self.assets, self.settings, self.cancel)
                if self.merge
                else measure(self.assets[0], self.settings, self.cancel)
            )
            check_cancel(self.cancel)
            compressed = preview_encoded(result)
            check_cancel(self.cancel)
            self.ready.emit(self.revision, result, original, compressed)
        except Cancelled:
            pass
        except Exception as exc:
            self.failed.emit(self.revision, str(exc))


class Exporter(QThread):
    progress = Signal(int, int, str)
    saved = Signal(object)
    failed = Signal(str)
    completed = Signal(object, object, str, bool)

    def __init__(
        self, assets: list, settings: Settings, folder: Path, merge_name: str | None, parent=None
    ):
        super().__init__(parent)
        self.assets, self.settings, self.folder, self.merge_name = (
            assets,
            settings,
            folder,
            merge_name,
        )
        self.cancel = Event()

    def run(self):
        results, errors, cancelled, report = [], [], False, ""

        def saved(result):
            results.append(result)
            self.saved.emit(result)

        try:
            if self.merge_name is not None:
                saved(
                    save_merged(
                        self.assets,
                        self.settings,
                        self.folder,
                        self.merge_name,
                        self.cancel,
                        self.progress.emit,
                    )
                )
            else:
                for index, asset in enumerate(self.assets):
                    check_cancel(self.cancel)
                    self.progress.emit(index, len(self.assets), asset.path.name)
                    try:
                        save_asset(asset, self.settings, self.folder, self.cancel, saved)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        errors.append(f"{asset.path.name}: {exc}")
                        self.failed.emit(errors[-1])
        except Cancelled:
            cancelled = True
        except Exception as exc:
            errors.append(str(exc))
            self.failed.emit(str(exc))
        finally:
            if results or errors:
                try:
                    report = str(
                        save_report(
                            self.folder,
                            results,
                            errors + (["사용자가 취소했습니다."] if cancelled else []),
                        )
                    )
                except Exception as exc:
                    errors.append(f"결과 보고서를 저장할 수 없습니다: {exc}")
            self.completed.emit(results, errors, report, cancelled)
