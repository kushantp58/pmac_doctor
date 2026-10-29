"""Application context shared by all pages: session, settings, background tasks, signals."""
from __future__ import annotations

import json
from typing import Dict, Optional

from PySide6.QtCore import QObject, QSettings, Signal

from .. import __app_name__, __org__
from ..comm.session import Session
from ..core.models import ControllerSnapshot
from .worker import Poller, TaskKeeper


class AppContext(QObject):
    snapshot_changed = Signal(object)      # ControllerSnapshot
    axis_changed = Signal(int)
    connection_changed = Signal()
    log = Signal(str, str)                 # message, level (info|warn|error|ok)
    request_page = Signal(str)             # navigate to page by key

    def __init__(self):
        super().__init__()
        self.session = Session()
        self.settings = QSettings(__org__, __app_name__)
        self.tasks = TaskKeeper()
        self.poller: Optional[Poller] = None
        self.current_axis = 1
        self.last_snapshot: Optional[ControllerSnapshot] = None
        self.motor_specs: Dict[int, dict] = self._load_specs()
        self.session.status_listeners.append(lambda m: self.log.emit(m, "info"))

    # ------------------------------------------------------------------ polling
    def start_polling(self, interval_ms: int = 500):
        if self.poller is None:
            self.poller = Poller(self.session, interval_ms)
            self.poller.snapshot.connect(self._on_snapshot)
            self.poller.error.connect(lambda m: self.log.emit(m, "error"))
            self.poller.start()
        else:
            self.poller.interval_ms = interval_ms

    def stop_polling(self):
        if self.poller is not None:
            self.poller.stop()
            self.poller = None

    def pause_polling(self, paused: bool):
        if self.poller is not None:
            self.poller.paused = paused

    def _on_snapshot(self, snap):
        self.last_snapshot = snap
        self.snapshot_changed.emit(snap)

    # ------------------------------------------------------------------ axis
    def set_axis(self, n: int):
        if n != self.current_axis:
            self.current_axis = n
            self.axis_changed.emit(n)

    @property
    def naxes(self) -> int:
        return self.session.naxes

    @property
    def family(self) -> str:
        return self.session.family

    # ------------------------------------------------------------------ motor specs
    def _load_specs(self) -> Dict[int, dict]:
        raw = self.settings.value("motor_specs", "")
        try:
            d = json.loads(raw) if raw else {}
            return {int(k): v for k, v in d.items()}
        except Exception:
            return {}

    def save_specs(self):
        self.settings.setValue("motor_specs", json.dumps(self.motor_specs))

    def info(self, msg: str):
        self.log.emit(msg, "info")

    def warn(self, msg: str):
        self.log.emit(msg, "warn")

    def error(self, msg: str):
        self.log.emit(msg, "error")

    def ok(self, msg: str):
        self.log.emit(msg, "ok")
