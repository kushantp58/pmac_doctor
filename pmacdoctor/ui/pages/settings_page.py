"""Connections & preferences."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...comm.epics_client import EpicsConfig, HAVE_EPICS
from ...comm.simulator import SCENARIOS
from ..context import AppContext
from ..widgets import Card, StatusDot, hbox, label, vbox
from .. import theme


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        st = ctx.settings
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Connections", "title"))
        root.addWidget(label("Attach to the EPICS IOC (DiamondLightSource pmac driver) and/or directly to the "
                             "controller. A direct link unlocks variable dumps and tuning tests.", "subtitle", wrap=True))

        grid = QGridLayout()
        grid.setSpacing(14)
        root.addLayout(grid)

        # ---------------- EPICS card
        ep = Card("EPICS IOC (Channel Access)")
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignRight)
        self.ep_prefix = QLineEdit(st.value("epics/prefix", "BL99P-MO-BRICK-01"))
        self.ep_prefix.setPlaceholderText("controller $(P) prefix, e.g. XF:31IDA-CT{MC:01}")
        self.ep_naxes = QSpinBox()
        self.ep_naxes.setRange(1, 32)
        self.ep_naxes.setValue(int(st.value("epics/naxes", 8)))
        self.ep_motor = QLineEdit(st.value("epics/motor_template", "{P}:M{n}"))
        self.ep_motor.setPlaceholderText("{P}:M{n}   or   XF:31IDA-OP{{Mir:1-Ax:{n}}}Mtr")
        self.ep_motor.setToolTip("Motor record name template. {P} = prefix, {n} = axis number, {nn} = zero padded.")
        self.ep_addr = QLineEdit(st.value("epics/addr_list", ""))
        self.ep_addr.setPlaceholderText("optional EPICS_CA_ADDR_LIST, e.g. 10.66.1.255 (blank = environment)")
        f.addRow("PV prefix", self.ep_prefix)
        f.addRow("Axes", self.ep_naxes)
        f.addRow("Motor record", self.ep_motor)
        f.addRow("CA address list", self.ep_addr)
        ep.body.addLayout(f)
        self.ep_dot = StatusDot("not connected")
        self.ep_btn = QPushButton("Connect EPICS")
        self.ep_btn.setProperty("kind", "primary")
        self.ep_off = QPushButton("Disconnect")
        ep.body.addLayout(hbox(self.ep_dot, "stretch", self.ep_off, self.ep_btn))
        if not HAVE_EPICS:
            ep.body.addWidget(label("pyepics is not installed - EPICS features disabled.", "muted"))
            self.ep_btn.setEnabled(False)
        grid.addWidget(ep, 0, 0, Qt.AlignTop)

        # ---------------- Direct card
        dc = Card("Direct link to the controller")
        f2 = QFormLayout()
        f2.setLabelAlignment(Qt.AlignRight)
        self.d_kind = QComboBox()
        self.d_kind.addItem("Power PMAC via SSH (gpascii) - Power Brick LV/AC, CK3E, UMAC", "ssh")
        self.d_kind.addItem("Turbo PMAC / Geobrick via Ethernet (port 1025)", "ethernet")
        self.d_kind.addItem("Built-in simulator (no hardware)", "simulator")
        kind = st.value("direct/kind", "simulator")
        idx = self.d_kind.findData(kind)
        self.d_kind.setCurrentIndex(max(0, idx))
        self.d_host = QLineEdit(st.value("direct/host", "192.168.0.200"))
        self.d_port = QSpinBox()
        self.d_port.setRange(0, 65535)
        self.d_port.setValue(int(st.value("direct/port", 0)))
        self.d_port.setSpecialValueText("default")
        self.d_user = QLineEdit(st.value("direct/user", "root"))
        self.d_pass = QLineEdit(st.value("direct/pass", "deltatau"))
        self.d_pass.setEchoMode(QLineEdit.Password)
        self.d_family = QComboBox()
        self.d_family.addItem("Power PMAC (Power Brick LV)", "power")
        self.d_family.addItem("Turbo PMAC (Geobrick)", "turbo")
        self.d_scenario = QComboBox()
        for k, v in SCENARIOS.items():
            self.d_scenario.addItem(k, k)
            self.d_scenario.setItemData(self.d_scenario.count() - 1, v, Qt.ToolTipRole)
        f2.addRow("Link type", self.d_kind)
        f2.addRow("Host / IP", self.d_host)
        f2.addRow("Port", self.d_port)
        f2.addRow("SSH user", self.d_user)
        f2.addRow("SSH password", self.d_pass)
        f2.addRow("Simulated family", self.d_family)
        f2.addRow("Simulated scenario", self.d_scenario)
        dc.body.addLayout(f2)
        self.d_dot = StatusDot("not connected")
        self.d_btn = QPushButton("Connect direct link")
        self.d_btn.setProperty("kind", "primary")
        self.d_off = QPushButton("Disconnect")
        dc.body.addLayout(hbox(self.d_dot, "stretch", self.d_off, self.d_btn))
        grid.addWidget(dc, 0, 1, Qt.AlignTop)

        # ---------------- preferences
        pc = Card("Preferences")
        f3 = QFormLayout()
        self.poll_ms = QSpinBox()
        self.poll_ms.setRange(100, 5000)
        self.poll_ms.setSingleStep(100)
        self.poll_ms.setSuffix(" ms")
        self.poll_ms.setValue(int(st.value("poll_ms", 500)))
        self.confirm_moves = QCheckBox("Ask for confirmation before any command that can move or kill a motor")
        self.confirm_moves.setChecked(st.value("confirm_moves", "true") in ("true", True, "1"))
        self.autoconnect = QCheckBox("Reconnect the last links automatically at start-up")
        self.autoconnect.setChecked(st.value("autoconnect", "false") in ("true", True, "1"))
        f3.addRow("Poll interval", self.poll_ms)
        f3.addRow("", self.confirm_moves)
        f3.addRow("", self.autoconnect)
        pc.body.addLayout(f3)
        grid.addWidget(pc, 1, 0, 1, 2)
        root.addStretch(1)

        # wiring
        self.ep_btn.clicked.connect(self.connect_epics)
        self.ep_off.clicked.connect(self.disconnect_epics)
        self.d_btn.clicked.connect(self.connect_direct)
        self.d_off.clicked.connect(self.disconnect_direct)
        self.d_kind.currentIndexChanged.connect(self._kind_changed)
        self.poll_ms.valueChanged.connect(lambda v: (st.setValue("poll_ms", v), ctx.start_polling(v)))
        self.confirm_moves.toggled.connect(lambda v: st.setValue("confirm_moves", "true" if v else "false"))
        self.autoconnect.toggled.connect(lambda v: st.setValue("autoconnect", "true" if v else "false"))
        ctx.connection_changed.connect(self.refresh_state)
        self._kind_changed()
        self.refresh_state()

    # ------------------------------------------------------------------
    def _kind_changed(self):
        kind = self.d_kind.currentData()
        is_sim = kind == "simulator"
        is_ssh = kind == "ssh"
        for w in (self.d_host, self.d_port):
            w.setEnabled(not is_sim)
        for w in (self.d_user, self.d_pass):
            w.setEnabled(is_ssh)
        for w in (self.d_family, self.d_scenario):
            w.setEnabled(is_sim)

    def _save(self):
        st = self.ctx.settings
        st.setValue("epics/prefix", self.ep_prefix.text().strip())
        st.setValue("epics/naxes", self.ep_naxes.value())
        st.setValue("epics/motor_template", self.ep_motor.text().strip() or "{P}:M{n}")
        st.setValue("epics/addr_list", self.ep_addr.text().strip())
        st.setValue("direct/kind", self.d_kind.currentData())
        st.setValue("direct/host", self.d_host.text().strip())
        st.setValue("direct/port", self.d_port.value())
        st.setValue("direct/user", self.d_user.text().strip())
        st.setValue("direct/pass", self.d_pass.text())

    def connect_epics(self):
        self._save()
        import os
        addr = self.ep_addr.text().strip()
        if addr:
            os.environ["EPICS_CA_ADDR_LIST"] = addr
            os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
        cfg = EpicsConfig(prefix=self.ep_prefix.text().strip(), naxes=self.ep_naxes.value(),
                          motor_pv_template=self.ep_motor.text().strip() or "{P}:M{n}")
        self.ep_btn.setEnabled(False)
        self.ep_dot.set_state(theme.WARNING, "connecting...")

        def work(progress):
            self.ctx.session.connect_epics(cfg)
            return cfg

        def done(cfg):
            self.ep_btn.setEnabled(True)
            self.ctx.ok("EPICS monitors started for %s (%d axes)" % (cfg.prefix, cfg.naxes))
            self.ctx.start_polling(self.poll_ms.value())
            self.ctx.connection_changed.emit()

        def failed(msg):
            self.ep_btn.setEnabled(True)
            self.ctx.error("EPICS connect failed: %s" % msg.splitlines()[0])
            self.ep_dot.set_state(theme.CRITICAL, "failed")

        self.ctx.tasks.run(work, done, failed)

    def disconnect_epics(self):
        self.ctx.session.disconnect_epics()
        self.ctx.connection_changed.emit()

    def connect_direct(self):
        self._save()
        kind = self.d_kind.currentData()
        host, port = self.d_host.text().strip(), self.d_port.value()
        user, pw = self.d_user.text().strip(), self.d_pass.text()
        fam, scen = self.d_family.currentData(), self.d_scenario.currentData()
        self.d_btn.setEnabled(False)
        self.d_dot.set_state(theme.WARNING, "connecting...")

        def work(progress):
            return self.ctx.session.connect_direct(kind, host, port, user, pw, scenario=scen, family=fam)

        def done(info):
            self.d_btn.setEnabled(True)
            self.ctx.ok("Direct link up: %s (cid %s, fw %s, servo %.4g ms)" % (
                info.description, info.cid, info.firmware or "?", info.servo_period_ms))
            self.ctx.start_polling(self.poll_ms.value())
            self.ctx.connection_changed.emit()

        def failed(msg):
            self.d_btn.setEnabled(True)
            self.ctx.error("Direct connect failed: %s" % msg.splitlines()[0])
            self.d_dot.set_state(theme.CRITICAL, "failed")

        self.ctx.tasks.run(work, done, failed)

    def connect_simulator(self, scenario: str = "healthy"):
        """Programmatic shortcut (used by ``--simulate``)."""
        self.d_kind.setCurrentIndex(self.d_kind.findData("simulator"))
        idx = self.d_scenario.findData(scenario)
        if idx >= 0:
            self.d_scenario.setCurrentIndex(idx)
        self.connect_direct()

    def disconnect_direct(self):
        self.ctx.session.disconnect_direct()
        self.ctx.connection_changed.emit()

    def refresh_state(self):
        s = self.ctx.session
        if s.direct_ok:
            self.d_dot.set_state(theme.GOOD, "connected - %s" % s.direct.info.description)
        else:
            self.d_dot.set_state(theme.NEUTRAL, "not connected")
        if s.epics is not None:
            self.ep_dot.set_state(theme.GOOD if s.epics_ok else theme.WARNING,
                                  "monitoring %s" % s.epics_cfg.prefix if s.epics_ok else "waiting for PVs (%s)" % s.epics_cfg.prefix)
        else:
            self.ep_dot.set_state(theme.NEUTRAL, "not connected")

    def try_autoconnect(self):
        if self.autoconnect.isChecked():
            kind = self.d_kind.currentData()
            if kind:
                self.connect_direct()
            if self.ctx.settings.value("epics/was_connected", "false") == "true" and HAVE_EPICS:
                self.connect_epics()
