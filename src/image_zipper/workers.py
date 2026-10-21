from __future__ import annotations

import multiprocessing
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from .compute import run_job
from .imaging import discover, probe
from .models import Cancelled, Settings, check_cancel


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


class ProcessWorker(QThread):
    """Relay a spawn-process job without blocking the GUI or sharing Qt objects."""

    def __init__(self, kind, assets, settings, parent=None, **options):
        super().__init__(parent)
        self.kind, self.assets, self.settings = kind, assets, settings
        self.options = options
        self.cancel = Event()

    def run(self):
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        cancelled = context.Event()
        process = context.Process(
            target=run_job,
            args=(send, self.kind, self.assets, self.settings, cancelled),
            kwargs=self.options,
            daemon=True,
        )
        started = False
        try:
            if self.cancel.is_set():
                return
            process.start()
            started = True
            send.close()
            received_done = False
            while True:
                if self.cancel.is_set():
                    cancelled.set()
                    if self.kind != "export":
                        # Trial/preview jobs never write files and can be stopped
                        # immediately, including inside a slow native codec.
                        process.terminate()
                        break
                if receive.poll(0.05):
                    try:
                        message = receive.recv()
                        if message[0] == "done":
                            received_done = True
                            break
                        self.message(message)
                    except EOFError:
                        break
                elif not process.is_alive():
                    break
            process.join()
            if process.exitcode and not self.cancel.is_set():
                self.error(f"처리 프로세스가 종료되었습니다 (코드 {process.exitcode}).")
            elif not received_done and not self.cancel.is_set():
                self.error("작업 완료 신호를 받기 전에 처리 프로세스가 종료되었습니다.")
        except Exception as exc:
            self.error(str(exc))
        finally:
            send.close()
            receive.close()
            if started and process.is_alive():
                process.terminate()
                process.join()
            process.close()


class Previewer(ProcessWorker):
    ready = Signal(object, bytes)
    failed = Signal(str)

    def __init__(self, asset, parent=None):
        super().__init__("preview", [asset], None, parent)

    def message(self, message):
        if message[0] == "preview":
            self.ready.emit(self.assets[0], message[1])
        elif message[0] == "error":
            self.error(message[1])

    def error(self, message):
        self.failed.emit(message)


class Estimator(ProcessWorker):
    ready = Signal(int, object, int, bytes)
    failed = Signal(int, str)

    def __init__(self, revision: int, assets: list, settings: Settings, merge: bool, parent=None):
        super().__init__("estimate_merge" if merge else "estimate", assets, settings, parent)
        self.revision = revision

    def message(self, message):
        if message[0] == "estimate":
            self.ready.emit(self.revision, *message[1:])
        elif message[0] == "error":
            self.error(message[1])

    def error(self, message):
        self.failed.emit(self.revision, message)


class Exporter(ProcessWorker):
    progress = Signal(int, int, str)
    saved = Signal(object)
    failed = Signal(str)
    completed = Signal(object, object, str, bool)

    def __init__(self, assets, settings, folder, merge_name, parent=None):
        super().__init__("export", assets, settings, parent, folder=folder, merge_name=merge_name)

    def message(self, message):
        name, *values = message
        if name == "error":
            self.error(*values)
        else:
            getattr(self, name).emit(*values)

    def error(self, message):
        self.failed.emit(message)
