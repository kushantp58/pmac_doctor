"""Dashboard: controller summary + one card per motor."""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core import rules
from ...core.models import AxisSnapshot, ControllerSnapshot
from .. import theme
from ..context import AppContext
from ..widgets import Badge, BitGrid, Card, Kpi, StatusDot, hbox, label, vbox


class AxisCard(Card):
    def __init__(self, n: int, ctx: AppContext):
        super().__init__("", clickable=True)
        self.n = n
        self.ctx = ctx
        self.setMinimumWidth(230)
        head = QHBoxLayout()
        self.name = label("Motor %d" % n, "kpi-small")
        self.badge = Badge("-", "neutral")
        head.addWidget(self.name)
        head.addStretch(1)
        head.addWidget(self.badge)
        self.body.addLayout(head)
        self.desc = label("", "muted")
        self.desc.setMaximumHeight(18)
        self.body.addWidget(self.desc)
        row = QHBoxLayout()
        self.pos = Kpi("Position", small=True)
        self.fe = Kpi("Foll. error", small=True)
        row.addWidget(self.pos)
        row.addWidget(self.fe)
        self.body.addLayout(row)
        self.flags = QLabel("")
        self.flags.setStyleSheet("color:%s;font-size:8.5pt;" % theme.INK_2)
        self.flags.setWordWrap(True)
        self.body.addWidget(self.flags)
        self.clicked.connect(self._open)

    def _open(self):
        self.ctx.set_axis(self.n)
        self.ctx.request_page.emit("axes")

    def update_from(self, ax: AxisSnapshot, family: str):
        st = ax.status
        self.desc.setText(ax.desc or (ax.epics.get("PV", "") if ax.epics else ""))
        egu = ax.epics.get("EGU") if ax.epics else None
        rbv = ax.epics.get("RBV") if ax.epics else None
        prec = ax.epics.get("PREC") if ax.epics else None
        if rbv is not None:
            try:
                p = int(prec) if prec is not None else 3
                self.pos.set("{:.{p}f}".format(float(rbv), p=max(0, min(6, p))), str(egu or ""))
            except (TypeError, ValueError):
                self.pos.set(str(rbv), str(egu or ""))
        else:
            self.pos.set(ax.v("ActPos"), "cts", fmt="{:.1f}")
        fe = ax.v("PosError")
        self.fe.set(fe, "cts", fmt="{:.2f}")
        if st is None:
            self.badge.set_severity("neutral", "no data")
            self.set_tone("ok")
            self.flags.setText("")
            return
        sev = st.severity
        self.badge.set_severity({"ok": "ok", "info": "neutral", "warning": "warning", "fault": "fault"}[sev], st.summary)
        self.set_tone("fault" if sev == "fault" else "warning" if sev == "warning" else "ok")
        parts = []
        parts.append("AMP %s" % ("on" if st.amp_enabled else "off"))
        parts.append("loop %s" % ("closed" if st.closed_loop else "OPEN"))
        parts.append("homed" if st.home_complete else "not homed")
        if family == "power":
            parts.append("phased" if st.phase_found else "NOT PHASED")
        if st.plus_limit:
            parts.append("+LIMIT")
        if st.minus_limit:
            parts.append("-LIMIT")
        if st.in_position and st.amp_enabled:
            parts.append("in pos")
        if st.cs_number:
            parts.append("CS%d" % st.cs_number)
        self.flags.setText("  ·  ".join(parts))


class DashboardPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.cards: Dict[int, AxisCard] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        head.addWidget(label("Dashboard", "title"))
        head.addStretch(1)
        self.diag_btn = QPushButton("Run full diagnosis")
        self.diag_btn.setProperty("kind", "primary")
        self.diag_btn.clicked.connect(lambda: ctx.request_page.emit("diagnostics:run"))
        head.addWidget(self.diag_btn)
        root.addLayout(head)

        top = QHBoxLayout()
        top.setSpacing(14)
        # controller card
        self.ctrl = Card("Controller")
        self.ctrl_name = label("Not connected", "kpi-small")
        self.ctrl_sub = label("Connect in Settings, or start the simulator.", "muted", wrap=True)
        krow = QHBoxLayout()
        self.k_servo = Kpi("Servo rate", small=True)
        self.k_cpu = Kpi("CPU", small=True)
        self.k_comm = Kpi("Poll time", small=True)
        self.k_axes = Kpi("Motors", small=True)
        for k in (self.k_servo, self.k_cpu, self.k_comm, self.k_axes):
            krow.addWidget(k)
        self.ctrl.body.addWidget(self.ctrl_name)
        self.ctrl.body.addWidget(self.ctrl_sub)
        self.ctrl.body.addLayout(krow)
        self.gbits = BitGrid(columns=4)
        self.ctrl.body.addWidget(label("GLOBAL STATUS", "cardtitle"))
        self.ctrl.body.addWidget(self.gbits)
        top.addWidget(self.ctrl, 3)
        # health card
        self.health = Card("Health")
        self.h_badge = Badge("no data", "neutral")
        self.h_badge.setStyleSheet(self.h_badge.styleSheet() + "QLabel{font-size:11pt;padding:6px 14px;}")
        self.h_text = label("", "muted", wrap=True)
        self.h_list = QVBoxLayout()
        self.h_list.setSpacing(3)
        self.health.body.addWidget(self.h_badge, 0, Qt.AlignLeft)
        self.health.body.addWidget(self.h_text)
        self.health.body.addLayout(self.h_list)
        self.health.body.addStretch(1)
        top.addWidget(self.health, 2)
        # links card
        self.links = Card("Links")
        self.l_epics = StatusDot("EPICS: not connected")
        self.l_direct = StatusDot("Direct: not connected")
        self.l_brick = StatusDot("Amplifier: -")
        self.links.body.addWidget(self.l_epics)
        self.links.body.addWidget(self.l_direct)
        self.links.body.addWidget(self.l_brick)
        self.links.body.addStretch(1)
        top.addWidget(self.links, 2)
        root.addLayout(top)

        root.addWidget(label("MOTORS", "cardtitle"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.grid = QGridLayout(inner)
        self.grid.setSpacing(12)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        self._health_items: List[QWidget] = []
        ctx.snapshot_changed.connect(self.update_snapshot)
        ctx.connection_changed.connect(self._conn_changed)
        self._build_cards(ctx.naxes)

    def _build_cards(self, n: int):
        for c in self.cards.values():
            self.grid.removeWidget(c)
            c.deleteLater()
        self.cards.clear()
        cols = 4
        for k in range(1, n + 1):
            c = AxisCard(k, self.ctx)
            self.cards[k] = c
            self.grid.addWidget(c, (k - 1) // cols, (k - 1) % cols)
        self.grid.setRowStretch((n - 1) // cols + 1, 1)

    def _conn_changed(self):
        if len(self.cards) != self.ctx.naxes:
            self._build_cards(self.ctx.naxes)
        s = self.ctx.session
        self.l_direct.set_state(theme.GOOD if s.direct_ok else theme.NEUTRAL,
                                "Direct: %s" % (s.direct.info.description if s.direct_ok else "not connected"))
        if s.epics is not None:
            self.l_epics.set_state(theme.GOOD if s.epics_ok else theme.WARNING, "EPICS: %s" % s.epics_cfg.prefix)
        else:
            self.l_epics.set_state(theme.NEUTRAL, "EPICS: not connected")
        if not s.direct_ok and s.epics is None:
            self.ctrl_name.setText("Not connected")
            self.ctrl_sub.setText("Connect in Settings, or start the simulator.")

    def update_snapshot(self, snap: ControllerSnapshot):
        if len(self.cards) != len(snap.axes) and snap.axes:
            self._build_cards(max(snap.axes))
        fam_label = "Power PMAC" if snap.family == "power" else "Turbo PMAC"
        self.ctrl_name.setText(snap.description or fam_label)
        sub = "%s · firmware %s · %s" % (fam_label, snap.firmware or "?", snap.cpu or "")
        self.ctrl_sub.setText(sub)
        if snap.servo_period_ms:
            self.k_servo.set(1.0 / snap.servo_period_ms, "kHz", fmt="{:.3g}")
        cpu = snap.sysf("Sys.FgCpuUsage")
        if cpu is None:
            try:
                cpu = float(snap.epics.get("CPULOAD_0")) if snap.epics.get("CPULOAD_0") is not None else None
            except (TypeError, ValueError):
                cpu = None
        self.k_cpu.set(cpu, "%", fmt="{:.0f}")
        self.k_comm.set(snap.comm_ms, "ms", fmt="{:.0f}")
        self.k_axes.set(len(snap.axes), "")
        self.gbits.set_bits(snap.global_bits, hide_reserved=True)
        for n, ax in snap.axes.items():
            if n in self.cards:
                self.cards[n].update_from(ax, snap.family)
        # amplifier line
        uv = snap.sysf("BrickLV.BusUnderVoltage")
        ov = snap.sysf("BrickLV.BusOverVoltage")
        ot = snap.sysf("BrickLV.OverTemp")
        mon = snap.sysf("BrickLV.Monitor")
        if snap.family == "power" and mon is not None:
            if mon <= 0:
                self.l_brick.set_state(theme.WARNING, "Amplifier: BrickLV monitoring off")
            elif uv == 1 or ov == 1 or ot == 1:
                self.l_brick.set_state(theme.CRITICAL, "Amplifier: %s" % ", ".join(
                    x for x, f in (("bus under-voltage", uv == 1), ("bus over-voltage", ov == 1), ("over-temp", ot == 1)) if f))
            else:
                self.l_brick.set_state(theme.GOOD, "Amplifier: bus OK, temperature OK")
        else:
            self.l_brick.set_state(theme.NEUTRAL, "Amplifier: n/a")
        self.l_direct.set_state(theme.GOOD if snap.direct_ok else theme.NEUTRAL,
                                "Direct: %s" % (snap.description if snap.direct_ok else "not connected"))
        if self.ctx.session.epics is not None:
            self.l_epics.set_state(theme.GOOD if snap.epics_ok else theme.WARNING,
                                   "EPICS: %s%s" % (self.ctx.session.epics_cfg.prefix, "" if snap.epics_ok else " (no PVs yet)"))
        # quick health using status-only rules (dumps if available)
        findings = rules.evaluate(snap, self.ctx.session.dumps, self.ctx.session.global_dump, self.ctx.motor_specs)
        counts = rules.summarize(findings)
        for w in self._health_items:
            self.h_list.removeWidget(w)
            w.deleteLater()
        self._health_items.clear()
        if counts["fault"]:
            self.h_badge.set_severity("fault", "%d fault%s" % (counts["fault"], "s" if counts["fault"] > 1 else ""))
        elif counts["warning"]:
            self.h_badge.set_severity("warning", "%d warning%s" % (counts["warning"], "s" if counts["warning"] > 1 else ""))
        elif snap.direct_ok or snap.epics_ok:
            self.h_badge.set_severity("ok", "Healthy")
        else:
            self.h_badge.set_severity("neutral", "no data")
        self.h_text.setText("%d faults · %d warnings · %d notes. Click a motor for details, or run the full diagnosis." % (
            counts["fault"], counts["warning"], counts["info"]))
        for f in [x for x in findings if x.severity in ("fault", "warning")][:6]:
            lb = QLabel("%s  %s" % (theme.SEVERITY_ICON.get(f.severity, ""), f.title))
            lb.setStyleSheet("color:%s;font-size:9pt;" % theme.SEVERITY_COLOR.get(f.severity))
            lb.setWordWrap(True)
            self.h_list.addWidget(lb)
            self._health_items.append(lb)
