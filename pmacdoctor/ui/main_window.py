"""Main window: sidebar navigation, stacked pages, status bar and message log."""
from __future__ import annotations

import os
import time
import webbrowser

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget, QSizePolicy)

from .. import __app_name__, __version__
from . import theme
from .context import AppContext
from .pages.axes_page import AxesPage
from .pages.dashboard_page import DashboardPage
from .pages.diagnostics_page import DiagnosticsPage
from .pages.scope_page import ScopePage
from .pages.settings_page import SettingsPage
from .pages.simulator_page import SimulatorPage
from .pages.terminal_page import TerminalPage
from .pages.tuning_page import TuningPage
from .pages.variables_page import VariablesPage
from .widgets import LogView, StatusDot, label

NAV = [("dashboard", "⌂  Dashboard"), ("axes", "⚙  Axes"), ("variables", "☰  Variables"), ("tuning", "∿  Tuning"),
       ("diagnostics", "✚  Diagnostics"), ("simulator", "⚗  Virtual motor"), ("scope", "◉  Live scope"),
       ("terminal", ">_  Terminal"), ("settings", "⚒  Settings")]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("%s %s" % (__app_name__, __version__))
        self.resize(1480, 920)
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setCentralWidget(central)

        # sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)
        brand = QLabel("PMAC\nDoctor")
        brand.setStyleSheet("font-size:18pt;font-weight:700;color:%s;line-height:0.9;" % theme.INK)
        sl.addWidget(brand)
        sub = QLabel("motor & drive diagnostics")
        sub.setStyleSheet("color:%s;font-size:8.5pt;margin-bottom:14px;" % theme.MUTED)
        sl.addWidget(sub)
        self.nav_buttons = {}
        for key, text in NAV:
            b = QPushButton(text)
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            sl.addWidget(b)
            self.nav_buttons[key] = b
        sl.addStretch(1)
        self.sd_epics = StatusDot("EPICS")
        self.sd_direct = StatusDot("Direct")
        sl.addWidget(self.sd_epics)
        sl.addWidget(self.sd_direct)
        ver = QLabel("v%s · NSLS-II" % __version__)
        ver.setStyleSheet("color:%s;font-size:8pt;margin-top:8px;" % theme.MUTED)
        sl.addWidget(ver)
        lay.addWidget(side)

        # pages
        self.stack = QStackedWidget()
        self.pages = {
            "dashboard": DashboardPage(ctx),
            "axes": AxesPage(ctx),
            "variables": VariablesPage(ctx),
            "tuning": TuningPage(ctx),
            "diagnostics": DiagnosticsPage(ctx),
            "simulator": SimulatorPage(ctx),
            "scope": ScopePage(ctx),
            "terminal": TerminalPage(ctx),
            "settings": SettingsPage(ctx),
        }
        for key, _ in NAV:
            self.stack.addWidget(self.pages[key])
        lay.addWidget(self.stack, 1)

        # log dock
        self.log = LogView()
        dock = QDockWidget("Messages", self)
        dock.setWidget(self.log)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.setMinimumHeight(90)
        self.dock = dock
        self.resizeDocks([dock], [130], Qt.Vertical)

        # status bar
        self.sb_msg = QLabel("Ready")
        self.sb_poll = QLabel("")
        self.sb_axis = QLabel("")
        self.statusBar().addWidget(self.sb_msg, 1)
        self.statusBar().addPermanentWidget(self.sb_axis)
        self.statusBar().addPermanentWidget(self.sb_poll)

        # menu
        m_file = self.menuBar().addMenu("&File")
        a_quit = QAction("Quit", self)
        a_quit.setShortcut(QKeySequence.Quit)
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)
        m_view = self.menuBar().addMenu("&View")
        a_log = dock.toggleViewAction()
        a_log.setText("Messages panel")
        m_view.addAction(a_log)
        m_help = self.menuBar().addMenu("&Help")
        a_guide = QAction("User guide", self)
        a_guide.triggered.connect(self._open_guide)
        a_about = QAction("About", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_guide)
        m_help.addAction(a_about)

        ctx.log.connect(self._on_log)
        ctx.request_page.connect(self._request_page)
        ctx.connection_changed.connect(self._conn_changed)
        ctx.snapshot_changed.connect(self._on_snapshot)
        ctx.axis_changed.connect(lambda n: self.sb_axis.setText("axis %d  " % n))
        self.show_page("dashboard")
        self._conn_changed()
        self.sb_axis.setText("axis %d  " % ctx.current_axis)
        QTimer.singleShot(400, self.pages["settings"].try_autoconnect)

    # ------------------------------------------------------------------
    def show_page(self, key: str):
        for k, b in self.nav_buttons.items():
            b.setChecked(k == key)
        self.stack.setCurrentWidget(self.pages[key])

    def _request_page(self, key: str):
        if key.startswith("diagnostics:run"):
            self.show_page("diagnostics")
            self.pages["diagnostics"].run()
        else:
            self.show_page(key)

    def _on_log(self, msg: str, level: str):
        if level == "traffic":
            self.pages["terminal"].log_traffic(msg)
            return
        color = {"error": theme.CRITICAL, "warn": theme.WARNING, "ok": theme.GOOD}.get(level, theme.INK_2)
        self.log.append_line("%s  %s" % (time.strftime("%H:%M:%S"), msg), color)
        self.sb_msg.setText(msg)
        self.sb_msg.setStyleSheet("color:%s;" % color)

    def _conn_changed(self):
        s = self.ctx.session
        self.sd_direct.set_state(theme.GOOD if s.direct_ok else theme.NEUTRAL,
                                 "Direct: %s" % ("on" if s.direct_ok else "off"))
        self.sd_epics.set_state(theme.GOOD if s.epics_ok else (theme.WARNING if s.epics else theme.NEUTRAL),
                                "EPICS: %s" % ("on" if s.epics_ok else ("waiting" if s.epics else "off")))

    def _on_snapshot(self, snap):
        self.sb_poll.setText("poll %.0f ms · %s  " % (snap.comm_ms, time.strftime("%H:%M:%S")))
        self._conn_changed()

    def _open_guide(self):
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for cand in (os.path.join(here, "docs", "USER_GUIDE.md"), os.path.join(os.path.dirname(here), "docs", "USER_GUIDE.md"),
                     os.path.join(here, "USER_GUIDE.md")):
            if os.path.exists(cand):
                webbrowser.open("file:///" + cand.replace("\\", "/"))
                return
        QMessageBox.information(self, "User guide", "USER_GUIDE.md was not found next to the application.")

    def _about(self):
        QMessageBox.about(self, "About %s" % __app_name__,
                          "<b>%s %s</b><br>Diagnostic & tuning assistant for Delta Tau / Omron PMAC controllers "
                          "(Power Brick LV, Geobrick, ...) attached to EPICS IOCs running the DiamondLightSource "
                          "<i>pmac</i> driver.<br><br>Status decoding, variable browser, rule-based fault analysis, "
                          "step-response/open-loop tests and model-based auto-tuning.<br><br>NSLS-II, Brookhaven "
                          "National Laboratory." % (__app_name__, __version__))

    def closeEvent(self, ev):  # noqa: N802
        self.ctx.settings.setValue("epics/was_connected", "true" if self.ctx.session.epics is not None else "false")
        self.ctx.stop_polling()
        try:
            self.ctx.session.disconnect_all()
        except Exception:
            pass
        super().closeEvent(ev)
