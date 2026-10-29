"""Axis detail page: live status, decoded bits, EPICS motor record, live trend, actions."""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QHeaderView)

from ...core import rules
from ...core.models import ControllerSnapshot
from ...core.status_bits import decode_msta
from .. import theme
from ..context import AppContext
from ..widgets import Badge, BitGrid, Card, Kpi, hbox, label, vbox


class AxesPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self._hist_t: Deque[float] = deque(maxlen=1200)
        self._hist_fe: Deque[float] = deque(maxlen=1200)
        self._hist_v: Deque[float] = deque(maxlen=1200)
        self._hist_out: Deque[float] = deque(maxlen=1200)
        self._t0 = time.time()
        self._last_axis = ctx.current_axis

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        head.addWidget(label("Axis detail", "title"))
        self.axis_combo = QComboBox()
        self.axis_combo.setMinimumWidth(200)
        head.addWidget(self.axis_combo)
        self.badge = Badge("-", "neutral")
        head.addWidget(self.badge)
        head.addStretch(1)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        grid = QGridLayout(inner)
        grid.setSpacing(14)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # live values
        live = Card("Live values")
        kr = QGridLayout()
        self.k_pos = Kpi("Position (controller)")
        self.k_rbv = Kpi("Position (EPICS)")
        self.k_fe = Kpi("Following error")
        self.k_vel = Kpi("Velocity")
        self.k_out = Kpi("Servo output")
        self.k_i2t = Kpi("I2T integrator")
        for i, k in enumerate((self.k_pos, self.k_rbv, self.k_fe, self.k_vel, self.k_out, self.k_i2t)):
            kr.addWidget(k, i // 3, i % 3)
        live.body.addLayout(kr)
        grid.addWidget(live, 0, 0, 1, 2)

        # status bits
        sb = Card("Status word")
        self.raw = label("", "mono")
        self.bits = BitGrid(columns=4)
        sb.body.addWidget(self.raw)
        sb.body.addWidget(self.bits)
        grid.addWidget(sb, 1, 0)

        # actions
        act = Card("Actions")
        act.body.addWidget(label("Direct-link commands are sent to the controller; with EPICS only, the motor record is used.",
                                 "muted", wrap=True))
        g = QGridLayout()
        g.setSpacing(6)
        self.b_enable = QPushButton("Enable / close loop")
        self.b_kill = QPushButton("Kill")
        self.b_kill.setProperty("kind", "danger")
        self.b_stop = QPushButton("Stop")
        self.b_jogp = QPushButton("Jog +")
        self.b_jogm = QPushButton("Jog −")
        self.b_home = QPushButton("Home")
        self.b_phase = QPushButton("Phase (#n$)")
        self.b_clear = QPushButton("Clear faults")
        self.jog_dist = QDoubleSpinBox()
        self.jog_dist.setRange(0.0001, 1e9)
        self.jog_dist.setDecimals(3)
        self.jog_dist.setValue(1000)
        self.jog_dist.setToolTip("Incremental jog distance in controller units (counts / motor units)")
        g.addWidget(self.b_enable, 0, 0)
        g.addWidget(self.b_kill, 0, 1)
        g.addWidget(self.b_stop, 0, 2)
        g.addWidget(QLabel("Distance"), 1, 0)
        g.addWidget(self.jog_dist, 1, 1)
        g.addWidget(self.b_jogm, 2, 0)
        g.addWidget(self.b_jogp, 2, 1)
        g.addWidget(self.b_home, 2, 2)
        g.addWidget(self.b_phase, 3, 0)
        g.addWidget(self.b_clear, 3, 1)
        act.body.addLayout(g)
        act.body.addStretch(1)
        grid.addWidget(act, 1, 1)

        # trend
        tr = Card("Trend (last 10 minutes of polls)")
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(220)
        theme.style_plot(self.plot.getPlotItem(), "", "time (s)", "")
        self.plot.addLegend(offset=(10, 10), labelTextColor=theme.INK_2)
        self.c_fe = self.plot.plot([], [], pen=pg.mkPen(theme.SERIES[1], width=2), name="following error")
        self.c_v = self.plot.plot([], [], pen=pg.mkPen(theme.SERIES[0], width=2), name="velocity")
        self.c_out = self.plot.plot([], [], pen=pg.mkPen(theme.SERIES[2], width=2), name="servo out / 100")
        self.plot.getPlotItem().setMenuEnabled(False)
        tr.body.addWidget(self.plot)
        grid.addWidget(tr, 2, 0, 1, 2)

        # EPICS
        epc = Card("EPICS motor record")
        self.ep_table = QTableWidget(0, 2)
        self.ep_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.ep_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ep_table.verticalHeader().setVisible(False)
        self.ep_table.setMinimumHeight(260)
        self.ep_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.msta_bits = BitGrid(columns=4)
        epc.body.addWidget(self.ep_table)
        epc.body.addWidget(label("MSTA", "cardtitle"))
        epc.body.addWidget(self.msta_bits)
        grid.addWidget(epc, 3, 0)

        # findings
        fc = Card("Findings for this axis")
        self.find_box = QVBoxLayout()
        self.find_box.setSpacing(4)
        fc.body.addLayout(self.find_box)
        fc.body.addStretch(1)
        self._find_items = []
        grid.addWidget(fc, 3, 1)
        grid.setRowStretch(4, 1)

        # wiring
        self.axis_combo.currentIndexChanged.connect(self._axis_combo_changed)
        ctx.axis_changed.connect(self._axis_changed)
        ctx.snapshot_changed.connect(self.update_snapshot)
        ctx.connection_changed.connect(self._fill_axes)
        self.b_enable.clicked.connect(lambda: self._cmd("#%dj/", "enable axis %d (close the loop)"))
        self.b_kill.clicked.connect(lambda: self._cmd("#%dk", "KILL axis %d"))
        self.b_stop.clicked.connect(lambda: self._cmd("#%dj/", "stop axis %d", epics_stop=True))
        self.b_jogp.clicked.connect(lambda: self._jog(+1))
        self.b_jogm.clicked.connect(lambda: self._jog(-1))
        self.b_home.clicked.connect(lambda: self._cmd("#%dhm", "HOME axis %d (motor will move!)"))
        self.b_phase.clicked.connect(lambda: self._cmd("#%d$", "run the phasing search on axis %d (motor will move!)"))
        self.b_clear.clicked.connect(self._clear_faults)
        self._fill_axes()

    # ------------------------------------------------------------------
    def _fill_axes(self):
        self.axis_combo.blockSignals(True)
        cur = self.ctx.current_axis
        self.axis_combo.clear()
        snap = self.ctx.last_snapshot
        for n in range(1, self.ctx.naxes + 1):
            desc = ""
            if snap and n in snap.axes:
                desc = snap.axes[n].desc
            self.axis_combo.addItem("Motor %d%s" % (n, (" - " + desc) if desc else ""), n)
        idx = self.axis_combo.findData(cur)
        self.axis_combo.setCurrentIndex(max(0, idx))
        self.axis_combo.blockSignals(False)

    def _axis_combo_changed(self, _i):
        n = self.axis_combo.currentData()
        if n is not None:
            self.ctx.set_axis(int(n))

    def _axis_changed(self, n: int):
        if self.axis_combo.currentData() != n:
            idx = self.axis_combo.findData(n)
            self.axis_combo.blockSignals(True)
            self.axis_combo.setCurrentIndex(max(0, idx))
            self.axis_combo.blockSignals(False)
        if n != self._last_axis:
            self._last_axis = n
            for d in (self._hist_t, self._hist_fe, self._hist_v, self._hist_out):
                d.clear()
        if self.ctx.last_snapshot:
            self.update_snapshot(self.ctx.last_snapshot)

    def _confirm(self, text: str) -> bool:
        if self.ctx.settings.value("confirm_moves", "true") not in ("true", True, "1"):
            return True
        r = QMessageBox.question(self, "Confirm", "Are you sure you want to %s?" % text,
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    def _cmd(self, fmt: str, what: str, epics_stop: bool = False):
        n = self.ctx.current_axis
        if not self._confirm(what % n):
            return
        s = self.ctx.session
        try:
            if s.direct_ok:
                s.send(fmt % n)
                self.ctx.ok("sent %s" % (fmt % n))
            elif s.epics is not None and epics_stop:
                s.epics.motor_stop(n)
                self.ctx.ok("EPICS STOP on motor %d" % n)
            elif s.epics is not None:
                s.epics.send_cmd(fmt % n)
                self.ctx.ok("sent %s through %s:SendCmd" % (fmt % n, s.epics_cfg.prefix))
            else:
                self.ctx.warn("not connected")
        except Exception as exc:
            self.ctx.error("command failed: %s" % exc)

    def _jog(self, sign: int):
        n = self.ctx.current_axis
        d = self.jog_dist.value() * sign
        s = self.ctx.session
        if not self._confirm("jog axis %d by %+g controller units" % (n, d)):
            return
        try:
            if s.direct_ok:
                s.send("#%dj:%g" % (n, d))
            elif s.epics is not None:
                snap = self.ctx.last_snapshot
                mres = float(snap.axes[n].epics.get("MRES", 1.0)) if snap and n in snap.axes else 1.0
                rbv = float(snap.axes[n].epics.get("RBV", 0.0)) if snap and n in snap.axes else 0.0
                s.epics.motor_move(n, rbv + d * mres)
            self.ctx.ok("jog %+g on axis %d" % (d, n))
        except Exception as exc:
            self.ctx.error("jog failed: %s" % exc)

    def _clear_faults(self):
        n = self.ctx.current_axis
        s = self.ctx.session
        if not self._confirm("clear faults on axis %d (BrickLV.Reset / re-enable)" % n):
            return
        try:
            if self.ctx.family == "power":
                s.send("#%dk" % n)
                try:
                    s.send("BrickLV.Reset=1")
                    time.sleep(0.5)
                    s.send("BrickLV.Monitor=1")
                except Exception:
                    pass
                s.send("#%dj/" % n)
            else:
                s.send("#%dk" % n)
                s.send("#%dj/" % n)
            self.ctx.ok("fault clear sequence sent for axis %d" % n)
        except Exception as exc:
            self.ctx.error("clear faults failed: %s" % exc)

    # ------------------------------------------------------------------
    def update_snapshot(self, snap: ControllerSnapshot):
        n = self.ctx.current_axis
        if self.axis_combo.count() != self.ctx.naxes:
            self._fill_axes()
        ax = snap.axes.get(n)
        if ax is None:
            return
        if ax.desc and self.axis_combo.currentText().endswith("Motor %d" % n):
            self._fill_axes()
        st = ax.status
        # KPIs
        self.k_pos.set(ax.v("ActPos"), "cts", fmt="{:.2f}")
        rbv = ax.epics.get("RBV")
        egu = ax.epics.get("EGU", "")
        self.k_rbv.set(float(rbv) if rbv is not None else None, str(egu), fmt="{:.4f}")
        self.k_fe.set(ax.v("PosError"), "cts", fmt="{:.2f}")
        self.k_vel.set(ax.v("ActVel"), "cts/cyc", fmt="{:.3f}")
        out = ax.v("ServoOut")
        maxdac = None
        d = self.ctx.session.dumps.get(n)
        if d is not None:
            maxdac = d.f("Motor[%d].MaxDac" % n) if snap.family == "power" else d.f("I%d69" % n)
        if out is not None and maxdac:
            self.k_out.set(out, "(%.0f%% of MaxDac)" % (abs(out) / maxdac * 100), fmt="{:.0f}")
        else:
            self.k_out.set(out, "16-bit", fmt="{:.0f}")
        i2t = ax.v("I2tSum")
        trip = d.f("Motor[%d].I2tTrip" % n) if (d is not None and snap.family == "power") else None
        if i2t is not None and trip:
            self.k_i2t.set(i2t / trip * 100, "% of trip", fmt="{:.0f}")
        else:
            self.k_i2t.set(i2t, "", fmt="{:.0f}")
        # status
        if st is not None:
            self.badge.set_severity({"ok": "ok", "info": "neutral", "warning": "warning", "fault": "fault"}[st.severity], st.summary)
            self.raw.setText("#%d?  →  $%s" % (n, st.raw_hex))
            self.bits.set_bits(st.decoded())
        else:
            self.badge.set_severity("neutral", "no data")
            self.raw.setText("")
        # trend
        t = time.time() - self._t0
        self._hist_t.append(t)
        self._hist_fe.append(ax.v("PosError", 0.0) or 0.0)
        self._hist_v.append(ax.v("ActVel", 0.0) or 0.0)
        self._hist_out.append((out or 0.0) / 100.0)
        tt = list(self._hist_t)
        self.c_fe.setData(tt, list(self._hist_fe))
        self.c_v.setData(tt, list(self._hist_v))
        self.c_out.setData(tt, list(self._hist_out))
        # EPICS table
        rows = []
        if ax.epics:
            order = ["PV", "DESC", "RBV", "VAL", "EGU", "MRES", "ERES", "OFF", "DIR", "VELO", "VMAX", "ACCL", "HLM", "LLM",
                     "DHLM", "DLLM", "RDBD", "RTRY", "RCNT", "MISS", "DMOV", "MOVN", "HLS", "LLS", "LVIO", "CNEN",
                     "SPMG", "UEIP", "STAT", "SEVR", ":FERROR", ":FERRORMAX", ":ELOSS", ":SCALE", "AXIS:POSITION",
                     "AXIS:FOLL_ERROR", "AXIS:VELOCITY"]
            for k in order:
                if k in ax.epics:
                    rows.append((k, ax.epics[k]))
        self.ep_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.ep_table.setItem(i, 0, QTableWidgetItem(k))
            self.ep_table.setItem(i, 1, QTableWidgetItem(_fmt(v)))
        msta = ax.epics.get("MSTA")
        if msta is not None:
            try:
                self.msta_bits.set_bits(decode_msta(int(msta)))
            except (TypeError, ValueError):
                pass
        # findings for this axis
        findings = [f for f in rules.evaluate(snap, self.ctx.session.dumps, self.ctx.session.global_dump,
                                              self.ctx.motor_specs, axes=[n]) if f.axis == n]
        for w in self._find_items:
            self.find_box.removeWidget(w)
            w.deleteLater()
        self._find_items.clear()
        if not findings:
            lb = QLabel("✓  No issues detected for this axis.")
            lb.setStyleSheet("color:%s;" % theme.GOOD)
            self.find_box.addWidget(lb)
            self._find_items.append(lb)
        for f in findings[:10]:
            lb = QLabel("%s  %s" % (theme.SEVERITY_ICON.get(f.severity, ""), f.title))
            lb.setToolTip(f.detail)
            lb.setWordWrap(True)
            lb.setStyleSheet("color:%s;" % theme.SEVERITY_COLOR.get(f.severity))
            self.find_box.addWidget(lb)
            self._find_items.append(lb)
        # enable/disable actions
        direct = snap.direct_ok
        for b in (self.b_phase, self.b_clear, self.b_home):
            b.setEnabled(direct or self.ctx.session.epics is not None)


def _fmt(v) -> str:
    if isinstance(v, float):
        return "%.6g" % v
    return str(v)
