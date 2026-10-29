"""Background threads: a periodic poller and a one-shot task runner."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..comm.session import Session


class Poller(QThread):
    snapshot = Signal(object)
    error = Signal(str)

    def __init__(self, session: Session, interval_ms: int = 500, parent=None):
        super().__init__(parent)
        self.session = session
        self.interval_ms = interval_ms
        self._stop = False
        self.paused = False

    def run(self):
        while not self._stop:
            if not self.paused and (self.session.direct_ok or self.session.epics is not None):
                try:
                    snap = self.session.poll()
                    self.snapshot.emit(snap)
                except Exception as exc:  # pragma: no cover
                    self.error.emit("%s" % exc)
            self.msleep(self.interval_ms)

    def stop(self):
        self._stop = True
        self.wait(3000)


class Task(QThread):
    """Runs ``fn(progress_cb)`` in a thread; emits progress(str), done(result) or failed(str)."""

    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            res = self.fn(self.progress.emit)
            self.done.emit(res)
        except Exception as exc:
            self.failed.emit("%s\n%s" % (exc, traceback.format_exc(limit=3)))


class TaskKeeper(QObject):
    """Keeps references to running tasks so they are not garbage collected."""

    def __init__(self):
        super().__init__()
        self._tasks = []

    def run(self, fn: Callable, on_done=None, on_failed=None, on_progress=None) -> Task:
        t = Task(fn)
        if on_done:
            t.done.connect(on_done)
        if on_failed:
            t.failed.connect(on_failed)
        if on_progress:
            t.progress.connect(on_progress)
        t.finished.connect(lambda: self._cleanup(t))
        self._tasks.append(t)
        t.start()
        return t

    def _cleanup(self, t):
        if t in self._tasks:
            self._tasks.remove(t)
        t.deleteLater()

    def busy(self) -> bool:
        return any(t.isRunning() for t in self._tasks)
