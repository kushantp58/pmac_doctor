"""Tuning page: step response capture & analysis, open-loop test, auto-tune, current calculator."""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QHeaderView, QSplitter)

from ...core import tuning, units
from ...core.autotuner import AutoTuner, TuneResult
from .. import theme
from ..context import AppContext
from ..widgets import Badge, Card, Kpi, LogView, hbox, label, vbox


def _scroll_column(layout: QVBoxLayout, min_w: int = 400, max_w: int = 460) -> QScrollArea:
    """Wrap a column of cards in a scroll area so they never get squeezed vertically."""
    lw = QWidget()
    lw.setLayout(layout)
    lw.setStyleSheet("background:transparent;")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setStyleSheet("QScrollArea{background:transparent;}")
    scroll.setWidget(lw)
    scroll.setMinimumWidth(min_w)
    scroll.setMaximumWidth(max_w)
    return scroll


def _dspin(lo, hi, val, dec=3, step=None, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(val)
    if step:
        s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    return s


class TuningPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.step_data: Optional[Dict[str, List[float]]] = None
        self.step_metrics: Optional[tuning.StepMetrics] = None
        self.ol_data: Optional[Dict[str, List[float]]] = None
        self.plant: Optional[tuning.PlantModel] = None
        self.saved_gains: Dict[str, Optional[str]] = {}
        self.tuner: Optional[AutoTuner] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Tuning", "title"))
        head.addStretch(1)
        head.addWidget(QLabel("Axis"))
        self.axis_combo = QComboBox()
        self.axis_combo.setMinimumWidth(130)
        head.addWidget(self.axis_combo)
        self.status_badge = Badge("-", "neutral")
        head.addWidget(self.status_badge)
        root.addLayout(head)
        warn = label("⚠  Tuning tests MOVE THE MOTOR. Make sure the axis is free to travel the test distance in both "
                     "directions, limits are working and FatalFeLimit is set. Nothing is saved to flash: use the "
                     "Terminal 'save' command once you are happy.", "muted", wrap=True)
        warn.setStyleSheet("color:%s;" % theme.WARNING)
        root.addWidget(warn)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_step_tab(), "Step response")
        self.tabs.addTab(self._build_openloop_tab(), "Open-loop test")
        self.tabs.addTab(self._build_autotune_tab(), "Auto-tune")
        self.tabs.addTab(self._build_current_tab(), "Current && I2T calculator")

        self.axis_combo.currentIndexChanged.connect(self._axis_combo_changed)
        ctx.axis_changed.connect(self._axis_changed)
        ctx.connection_changed.connect(self._fill_axes)
        ctx.snapshot_changed.connect(self._snap)
        self._fill_axes()

    # ================================================================== tabs
    def _build_step_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(10)
        c = Card("Step test")
        f = QFormLayout()
        self.step_size = _dspin(-1e7, 1e7, 500, 2)
        self.step_size.setToolTip("Step size in controller units (counts / motor units). Start small!")
        self.step_dur = _dspin(0.1, 10, 0.6, 2, suffix=" s")
        self.step_gather = QCheckBox("use controller data gather (Power PMAC)")
        self.step_gather.setChecked(True)
        self.step_return = QCheckBox("move back afterwards")
        self.step_return.setChecked(True)
        f.addRow("Step size", self.step_size)
        f.addRow("Capture time", self.step_dur)
        f.addRow("", self.step_gather)
        f.addRow("", self.step_return)
        c.body.addLayout(f)
        self.step_btn = QPushButton("Capture step")
        self.step_btn.setProperty("kind", "primary")
        self.step_export = QPushButton("Export data...")
        c.body.addLayout(hbox(self.step_export, "stretch", self.step_btn))
        left.addWidget(c)

        m = Card("Metrics")
        self.m_verdict = Badge("no capture", "neutral")
        m.body.addWidget(self.m_verdict, 0, Qt.AlignLeft)
        g = QGridLayout()
        self.k_rise = Kpi("Rise 10-90%", small=True)
        self.k_os = Kpi("Overshoot", small=True)
        self.k_settle = Kpi("Settling 2%", small=True)
        self.k_sse = Kpi("Steady-state err", small=True)
        self.k_peak = Kpi("Peak error", small=True)
        self.k_ring = Kpi("Ringing", small=True)
        self.k_sat = Kpi("Saturation", small=True)
        self.k_noise = Kpi("Noise RMS", small=True)
        for i, k in enumerate((self.k_rise, self.k_os, self.k_settle, self.k_sse, self.k_peak, self.k_ring, self.k_sat, self.k_noise)):
            g.addWidget(k, i // 2, i % 2)
        m.body.addLayout(g)
        self.m_rec = label("", "", wrap=True)
        self.m_rec.setStyleSheet("color:%s;" % theme.INK_2)
        m.body.addWidget(self.m_rec)
        left.addWidget(m)

        gc = Card("Servo gains")
        self.gain_table = QTableWidget(0, 2)
        self.gain_table.setHorizontalHeaderLabels(["Gain", "Value"])
        self.gain_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.gain_table.verticalHeader().setVisible(False)
        self.gain_table.setMinimumHeight(230)
        gc.body.addWidget(self.gain_table)
        self.g_read = QPushButton("Read")
        self.g_apply = QPushButton("Apply edits")
        self.g_restore = QPushButton("Restore saved")
        self.g_save = QPushButton("Remember current")
        gc.body.addLayout(hbox(self.g_read, self.g_save, "stretch", self.g_restore, self.g_apply))
        self.g_note = label("Edit the Value column, then Apply. 'Remember current' stores a copy you can restore.", "muted", wrap=True)
        gc.body.addWidget(self.g_note)
        left.addWidget(gc)
        left.addStretch(1)
        lay.addWidget(_scroll_column(left))

        right = QVBoxLayout()
        self.p_pos = pg.PlotWidget()
        theme.style_plot(self.p_pos.getPlotItem(), "Position", "", "motor units")
        self.p_pos.addLegend(offset=(10, 10), labelTextColor=theme.INK_2)
        self.c_des = self.p_pos.plot([], [], pen=pg.mkPen(theme.SERIES[0], width=2), name="commanded")
        self.c_act = self.p_pos.plot([], [], pen=pg.mkPen(theme.SERIES[1], width=2), name="actual")
        self.p_err = pg.PlotWidget()
        theme.style_plot(self.p_err.getPlotItem(), "Following error", "", "units")
        self.c_err = self.p_err.plot([], [], pen=pg.mkPen(theme.SERIES[2], width=2))
        self.p_out = pg.PlotWidget()
        theme.style_plot(self.p_out.getPlotItem(), "Servo output", "time (ms)", "16-bit units")
        self.c_out = self.p_out.plot([], [], pen=pg.mkPen(theme.SERIES[3], width=2))
        self.p_err.setXLink(self.p_pos)
        self.p_out.setXLink(self.p_pos)
        for p in (self.p_pos, self.p_err, self.p_out):
            p.getPlotItem().setMenuEnabled(False)
        right.addWidget(self.p_pos, 3)
        right.addWidget(self.p_err, 2)
        right.addWidget(self.p_out, 2)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)

        self.step_btn.clicked.connect(self.capture_step)
        self.step_export.clicked.connect(self.export_step)
        self.g_read.clicked.connect(self.read_gains)
        self.g_apply.clicked.connect(self.apply_gains)
        self.g_restore.clicked.connect(self.restore_gains)
        self.g_save.clicked.connect(self.remember_gains)
        return w

    def _build_openloop_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        left = QVBoxLayout()
        c = Card("Open-loop test")
        c.body.addWidget(label("Applies +M% then −M% of MaxDac as a raw output command, then kills the motor. A healthy "
                               "axis shows a velocity saw-tooth: positive command → positive velocity. Verifies current "
                               "loop, commutation/phasing and encoder direction (manual p.179).", "muted", wrap=True))
        f = QFormLayout()
        self.ol_mag = _dspin(0.1, 50, 5.0, 1, suffix=" %")
        self.ol_pulse = _dspin(0.02, 2.0, 0.1, 2, suffix=" s")
        self.ol_reenable = QCheckBox("close the loop again afterwards (#nj/)")
        self.ol_reenable.setChecked(True)
        f.addRow("Magnitude", self.ol_mag)
        f.addRow("Pulse time", self.ol_pulse)
        f.addRow("", self.ol_reenable)
        c.body.addLayout(f)
        self.ol_btn = QPushButton("Run open-loop test")
        self.ol_btn.setProperty("kind", "primary")
        c.body.addLayout(hbox("stretch", self.ol_btn))
        left.addWidget(c)
        r = Card("Result")
        self.ol_badge = Badge("not run", "neutral")
        self.ol_text = label("", "", wrap=True)
        self.ol_text.setStyleSheet("color:%s;" % theme.INK_2)
        self.ol_plant = label("", "mono", wrap=True)
        r.body.addWidget(self.ol_badge, 0, Qt.AlignLeft)
        r.body.addWidget(self.ol_text)
        r.body.addWidget(self.ol_plant)
        left.addWidget(r)
        left.addStretch(1)
        lay.addWidget(_scroll_column(left))
        right = QVBoxLayout()
        self.p_olv = pg.PlotWidget()
        theme.style_plot(self.p_olv.getPlotItem(), "Velocity", "", "units / servo cycle")
        self.c_olv = self.p_olv.plot([], [], pen=pg.mkPen(theme.SERIES[0], width=2))
        self.p_olo = pg.PlotWidget()
        theme.style_plot(self.p_olo.getPlotItem(), "Output command", "time (ms)", "16-bit units")
        self.c_olo = self.p_olo.plot([], [], pen=pg.mkPen(theme.SERIES[3], width=2))
        self.p_olo.setXLink(self.p_olv)
        right.addWidget(self.p_olv, 3)
        right.addWidget(self.p_olo, 2)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)
        self.ol_btn.clicked.connect(self.run_open_loop)
        return w

    def _build_autotune_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        left = QVBoxLayout()
        c = Card("Auto-tune")
        c.body.addWidget(label("1) open-loop identification of the plant (Ka, friction)  2) model-based PID design for "
                               "the requested bandwidth and damping  3) verification step, iterating the bandwidth "
                               "until overshoot/ringing are acceptable  4) feedforward (Kvff=Kvfb, Kaff=1/Ka). "
                               "Original gains are remembered and can be restored.", "muted", wrap=True))
        f = QFormLayout()
        self.at_bw = _dspin(1, 500, 20, 1, suffix=" Hz")
        self.at_zeta = _dspin(0.3, 2.0, 0.8, 2, 0.05)
        self.at_int = _dspin(0.0, 1.0, 0.2, 2, 0.05)
        self.at_step = _dspin(-1e7, 1e7, 300, 1)
        self.at_iter = QSpinBox()
        self.at_iter.setRange(1, 8)
        self.at_iter.setValue(3)
        self.at_ol = QCheckBox("run the open-loop identification first (recommended)")
        self.at_ol.setChecked(True)
        self.at_olmag = _dspin(0.1, 50, 5.0, 1, suffix=" %")
        self.at_maxos = _dspin(0, 50, 10, 0, suffix=" %")
        f.addRow("Target bandwidth", self.at_bw)
        f.addRow("Damping ratio ζ", self.at_zeta)
        f.addRow("Integral ratio", self.at_int)
        f.addRow("Verification step", self.at_step)
        f.addRow("Max iterations", self.at_iter)
        f.addRow("Max overshoot", self.at_maxos)
        f.addRow("", self.at_ol)
        f.addRow("Open-loop magnitude", self.at_olmag)
        c.body.addLayout(f)
        self.at_ack = QCheckBox("I confirm the axis can safely move ± the test distances")
        c.body.addWidget(self.at_ack)
        self.at_btn = QPushButton("Run auto-tune")
        self.at_btn.setProperty("kind", "primary")
        self.at_abort = QPushButton("Abort")
        self.at_restore = QPushButton("Restore original gains")
        c.body.addLayout(hbox(self.at_restore, "stretch", self.at_abort, self.at_btn))
        left.addWidget(c)
        res = Card("Result")
        self.at_badge = Badge("not run", "neutral")
        self.at_summary = label("", "", wrap=True)
        self.at_summary.setStyleSheet("color:%s;" % theme.INK_2)
        self.at_gains = QTableWidget(0, 3)
        self.at_gains.setHorizontalHeaderLabels(["Gain", "Original", "New"])
        self.at_gains.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.at_gains.verticalHeader().setVisible(False)
        self.at_gains.setMinimumHeight(200)
        res.body.addWidget(self.at_badge, 0, Qt.AlignLeft)
        res.body.addWidget(self.at_summary)
        res.body.addWidget(self.at_gains)
        left.addWidget(res)
        left.addStretch(1)
        lay.addWidget(_scroll_column(left, 420, 480))
        right = QVBoxLayout()
        self.at_log = LogView()
        right.addWidget(label("PROGRESS", "cardtitle"))
        right.addWidget(self.at_log, 1)
        right.addWidget(label("The verification step of the best iteration is shown on the 'Step response' tab.", "muted"))
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)
        self.at_btn.clicked.connect(self.run_autotune)
        self.at_abort.clicked.connect(self._abort_autotune)
        self.at_restore.clicked.connect(self.restore_gains)
        return w

    def _build_current_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        c = Card("Motor & channel data")
        c.body.addWidget(label("Computes Motor[x].MaxDac, I2TSet, I2tTrip and PwmSf exactly as the Power Brick LV manual "
                               "('I2T Protection', 'PWM Scale Factor'). The data is also remembered per axis so the "
                               "diagnostics can compare the controller settings to the motor rating.", "muted", wrap=True))
        f = QFormLayout()
        self.cc_rating = QComboBox()
        for k in units.MAX_ADC_BY_RATING:
            self.cc_rating.addItem("%s channel (Max ADC %.4g A)" % (k, units.MAX_ADC_BY_RATING[k]), k)
        self.cc_rating.setCurrentIndex(2)
        self.cc_type = QComboBox()
        for k, v in (("brushless", "3-phase brushless servo"), ("stepper", "2-phase stepper with encoder"),
                     ("microstep", "stepper, direct microstepping (no encoder)"), ("brushed", "DC brush")):
            self.cc_type.addItem(v, k)
        self.cc_peak = _dspin(0.01, 100, 7.2, 2, suffix=" A")
        self.cc_cont = _dspin(0.01, 100, 3.6, 2, suffix=" A")
        self.cc_rms = QCheckBox("datasheet values are RMS (uncheck if peak)")
        self.cc_rms.setChecked(True)
        self.cc_time = _dspin(0.1, 60, 1.0, 1, suffix=" s")
        self.cc_bus = _dspin(0, 100, 48, 1, suffix=" V")
        self.cc_motv = _dspin(0, 100, 48, 1, suffix=" V")
        f.addRow("Channel rating", self.cc_rating)
        f.addRow("Motor type", self.cc_type)
        f.addRow("Peak current", self.cc_peak)
        f.addRow("Continuous current", self.cc_cont)
        f.addRow("", self.cc_rms)
        f.addRow("Time at peak", self.cc_time)
        f.addRow("DC bus voltage", self.cc_bus)
        f.addRow("Motor rated voltage", self.cc_motv)
        c.body.addLayout(f)
        self.cc_calc = QPushButton("Calculate")
        self.cc_calc.setProperty("kind", "primary")
        self.cc_write = QPushButton("Write to motor")
        self.cc_remember = QPushButton("Remember for diagnostics")
        c.body.addLayout(hbox(self.cc_remember, "stretch", self.cc_write, self.cc_calc))
        c.setMaximumWidth(520)
        lay.addWidget(c)
        r = Card("Result")
        g = QGridLayout()
        self.cc_maxdac = Kpi("MaxDac")
        self.cc_i2tset = Kpi("I2TSet")
        self.cc_i2ttrip = Kpi("I2tTrip")
        self.cc_pwmsf = Kpi("PwmSf")
        for i, k in enumerate((self.cc_maxdac, self.cc_i2tset, self.cc_i2ttrip, self.cc_pwmsf)):
            g.addWidget(k, i // 2, i % 2)
        r.body.addLayout(g)
        self.cc_notes = label("", "", wrap=True)
        self.cc_notes.setStyleSheet("color:%s;" % theme.INK_2)
        r.body.addWidget(self.cc_notes)
        self.cc_current = label("", "mono", wrap=True)
        r.body.addWidget(label("CURRENT CONTROLLER VALUES", "cardtitle"))
        r.body.addWidget(self.cc_current)
        r.body.addStretch(1)
        lay.addWidget(r, 1)
        self.cc_calc.clicked.connect(self.calc_current)
        self.cc_write.clicked.connect(self.write_current)
        self.cc_remember.clicked.connect(self.remember_spec)
        self._cc = None
        return w

    # ================================================================== axis plumbing
    def _fill_axes(self):
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        for n in range(1, self.ctx.naxes + 1):
            self.axis_combo.addItem("Motor %d" % n, n)
        idx = self.axis_combo.findData(self.ctx.current_axis)
        self.axis_combo.setCurrentIndex(max(0, idx))
        self.axis_combo.blockSignals(False)
        self._load_spec()

    def _axis_combo_changed(self, _):
        n = self.axis_combo.currentData()
        if n is not None:
            self.ctx.set_axis(int(n))

    def _axis_changed(self, n):
        idx = self.axis_combo.findData(n)
        if idx >= 0 and self.axis_combo.currentIndex() != idx:
            self.axis_combo.blockSignals(True)
            self.axis_combo.setCurrentIndex(idx)
            self.axis_combo.blockSignals(False)
        self._load_spec()

    def _snap(self, snap):
        ax = snap.axes.get(self.ctx.current_axis)
        if ax and ax.status:
            st = ax.status
            self.status_badge.set_severity({"ok": "ok", "info": "neutral", "warning": "warning", "fault": "fault"}[st.severity], st.summary)

    def _need_direct(self) -> bool:
        if not self.ctx.session.direct_ok:
            QMessageBox.warning(self, "Direct link required",
                                "Tuning tests need a direct link to the controller (SSH for Power PMAC, Ethernet for Turbo). "
                                "Connect one in Settings.")
            return False
        return True

    def _confirm(self, text: str) -> bool:
        if self.ctx.settings.value("confirm_moves", "true") not in ("true", True, "1"):
            return True
        return QMessageBox.question(self, "Confirm", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    # ================================================================== step
    def capture_step(self):
        if not self._need_direct():
            return
        n = self.ctx.current_axis
        size = self.step_size.value()
        if not self._confirm("Axis %d will step %+g controller units (and back). Continue?" % (n, size)):
            return
        dur = self.step_dur.value()
        use_g = self.step_gather.isChecked()
        back = self.step_return.isChecked()
        self.step_btn.setEnabled(False)
        self.ctx.pause_polling(True)
        s = self.ctx.session

        def work(progress):
            at = AutoTuner(s, n, progress)
            err = at.preflight(need_closed_loop=True) or at.check_step(size)
            if err:
                raise RuntimeError(err)
            data = s.acquire_step(n, size, duration_s=dur, settle_s=0.2, use_gather=use_g, progress=progress)
            maxdac = s.direct.read_float("Motor[%d].MaxDac" % n) if s.family == "power" else s.direct.read_float("I%d69" % n)
            inpos = s.direct.read_float("Motor[%d].InPosBand" % n) if s.family == "power" else None
            if back:
                time.sleep(0.2)
                s.direct.send("#%dj:%g" % (n, -size))
            return data, maxdac, inpos

        def done(res):
            data, maxdac, inpos = res
            self.step_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.show_step(data, maxdac, inpos)
            self.read_gains()

        def failed(msg):
            self.step_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.ctx.error("step capture failed: %s" % msg.splitlines()[0])

        self.ctx.tasks.run(work, done, failed, lambda m: self.ctx.info(m))

    def show_step(self, data: Dict[str, List[float]], maxdac: Optional[float], inpos: Optional[float] = None):
        self.step_data = data
        t = [x * 1e3 for x in data["t"]]
        self.c_des.setData(t, data["des"])
        self.c_act.setData(t, data["act"])
        err = [d - a for d, a in zip(data["des"], data["act"])]
        self.c_err.setData(t, err)
        if data.get("out"):
            self.c_out.setData(t[:len(data["out"])], data["out"])
        else:
            self.c_out.setData([], [])
        m = tuning.analyze_step(data["t"], data["des"], data["act"], data.get("out") or None, maxdac, inpos)
        self.step_metrics = m
        if not m.ok:
            self.m_verdict.set_severity("fault", "analysis failed")
            self.m_rec.setText(m.message)
            return
        sev = {"Good response": "ok", "Needs attention": "warning", "Poorly tuned": "fault"}.get(m.verdict, "neutral")
        self.m_verdict.set_severity(sev, m.verdict)
        self.k_rise.set(m.rise_time_ms, "ms", fmt="{:.1f}")
        self.k_os.set(m.overshoot_pct, "%", fmt="{:.1f}")
        self.k_settle.set(m.settling_time_ms, "ms" if m.settling_time_ms is not None else "never", fmt="{:.0f}")
        self.k_sse.set(m.steady_state_error, "units", fmt="{:.3g}")
        self.k_peak.set(m.peak_error, "units", fmt="{:.3g}")
        self.k_ring.set(m.ring_freq_hz, "Hz" if m.ring_freq_hz else "none", fmt="{:.0f}")
        self.k_sat.set(m.saturation_pct, "% of time", fmt="{:.0f}")
        self.k_noise.set(m.noise_rms, "units", fmt="{:.3g}")
        self.m_rec.setText("\n".join("- " + r for r in m.recommendations))
        self.ctx.ok("step analysed: %s (overshoot %.1f%%, rise %s ms)" % (
            m.verdict, m.overshoot_pct or 0, "%.1f" % m.rise_time_ms if m.rise_time_ms is not None else "-"))

    def export_step(self):
        if not self.step_data:
            self.ctx.warn("no step data")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export step data", "step_axis%d.csv" % self.ctx.current_axis, "CSV (*.csv)")
        if not path:
            return
        d = self.step_data
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("t_s,des,act,out\n")
            for i in range(len(d["t"])):
                o = d["out"][i] if d.get("out") and i < len(d["out"]) else ""
                fh.write("%g,%g,%g,%s\n" % (d["t"][i], d["des"][i], d["act"][i], o))
        self.ctx.ok("exported %s" % path)

    # ================================================================== gains
    def _gain_names(self) -> List[str]:
        n = self.ctx.current_axis
        if self.ctx.family == "power":
            p = "Motor[%d].Servo." % n
            return [p + k for k in ("Kp", "Kvfb", "Kvifb", "Kvff", "Kviff", "Kaff", "Kfff", "Ki", "SwZvInt", "MaxInt",
                                    "MaxPosErr", "BreakPosErr", "Kbreak")] + ["Motor[%d].MaxDac" % n]
        return ["I%d%s" % (n, k) for k in ("30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "63", "64", "65", "67", "68", "69")]

    def read_gains(self):
        if not self.ctx.session.direct_ok:
            return
        names = self._gain_names()

        def work(progress):
            return self.ctx.session.direct.read_values(names)

        def done(vals):
            self.gain_table.setRowCount(len(names))
            for i, k in enumerate(names):
                it0 = QTableWidgetItem(k.split(".")[-1] if "Servo." in k else k)
                it0.setData(Qt.UserRole, k)
                it0.setFlags(it0.flags() & ~Qt.ItemIsEditable)
                self.gain_table.setItem(i, 0, it0)
                self.gain_table.setItem(i, 1, QTableWidgetItem("-" if vals.get(k) is None else str(vals.get(k))))

        self.ctx.tasks.run(work, done, lambda m: self.ctx.error(m.splitlines()[0]))

    def apply_gains(self):
        if not self._need_direct():
            return
        writes = {}
        for i in range(self.gain_table.rowCount()):
            k = self.gain_table.item(i, 0).data(Qt.UserRole)
            v = self.gain_table.item(i, 1).text().strip()
            if v and v != "-":
                writes[k] = v
        if not writes:
            return
        if not self._confirm("Write %d servo parameters to axis %d?" % (len(writes), self.ctx.current_axis)):
            return
        try:
            link = self.ctx.session.direct
            for k, v in writes.items():
                link.send("%s=%s" % (k, v))
            self.ctx.ok("gains applied to axis %d (not saved to flash)" % self.ctx.current_axis)
            self.read_gains()
        except Exception as exc:
            self.ctx.error("apply failed: %s" % exc)

    def remember_gains(self):
        if not self.ctx.session.direct_ok:
            return
        self.saved_gains = self.ctx.session.direct.read_values(self._gain_names())
        self.ctx.settings.setValue("saved_gains/%s/%d" % (self.ctx.family, self.ctx.current_axis), json.dumps(self.saved_gains))
        self.ctx.ok("remembered %d gains for axis %d" % (len(self.saved_gains), self.ctx.current_axis))

    def restore_gains(self):
        if not self._need_direct():
            return
        saved = self.saved_gains
        if not saved:
            raw = self.ctx.settings.value("saved_gains/%s/%d" % (self.ctx.family, self.ctx.current_axis), "")
            saved = json.loads(raw) if raw else {}
        if self.tuner is not None and getattr(self.tuner, "_result", None) and self.tuner._result.original:
            saved = dict(saved) or {}
            saved.update({k: v for k, v in self.tuner._result.original.items() if v is not None})
        if not saved:
            self.ctx.warn("no remembered gains for this axis")
            return
        if not self._confirm("Restore %d remembered gains on axis %d?" % (len(saved), self.ctx.current_axis)):
            return
        try:
            for k, v in saved.items():
                if v is not None:
                    self.ctx.session.direct.send("%s=%s" % (k, v))
            self.ctx.ok("gains restored")
            self.read_gains()
        except Exception as exc:
            self.ctx.error("restore failed: %s" % exc)

    # ================================================================== open loop
    def run_open_loop(self):
        if not self._need_direct():
            return
        n = self.ctx.current_axis
        mag, pulse = self.ol_mag.value(), self.ol_pulse.value()
        if not self._confirm("Axis %d will be driven OPEN LOOP at ±%.1f%% of MaxDac for %.0f ms each way, then killed. "
                             "Continue?" % (n, mag, pulse * 1e3)):
            return
        self.ol_btn.setEnabled(False)
        self.ctx.pause_polling(True)
        s = self.ctx.session
        reenable = self.ol_reenable.isChecked()

        def work(progress):
            at = AutoTuner(s, n, progress)
            err = at.preflight(need_closed_loop=False)
            if err and "closed loop" not in err:
                raise RuntimeError(err)
            data = s.acquire_open_loop(n, mag, pulse, progress)
            T = (s.direct.info.servo_period_ms or 0.5) / 1000.0
            if s.family == "power":
                plant = tuning.identify_plant(data["t"], data["vel"], data["out"], T, pulse)
            else:
                i69 = s.direct.read_float("I%d69" % n) or 20480.0
                out = [mag / 100.0 * i69 * (1 if k < len(data["t"]) / 2 else -1) for k in range(len(data["t"]))]
                data["out"] = out
                plant = tuning.identify_plant(data["t"], [v / 32.0 for v in data["vel"]], out, T, pulse)
            chk = tuning.open_loop_velocity_profile_ok(data["vel"], data["out"])
            if reenable and chk.get("ok"):
                time.sleep(0.3)
                s.direct.send("#%dj/" % n)
            return data, plant, chk

        def done(res):
            data, plant, chk = res
            self.ol_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.ol_data, self.plant = data, plant
            t = [x * 1e3 for x in data["t"]]
            self.c_olv.setData(t, data["vel"])
            self.c_olo.setData(t, data["out"])
            self.ol_badge.set_severity("ok" if chk.get("ok") else "fault", "PASS" if chk.get("ok") else "FAIL")
            self.ol_text.setText(chk.get("message", ""))
            self.ol_plant.setText(plant.message)
            self.ctx.ok("open-loop test: %s" % ("pass" if chk.get("ok") else "FAIL"))

        def failed(msg):
            self.ol_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.ctx.error("open-loop test failed: %s" % msg.splitlines()[0])

        self.ctx.tasks.run(work, done, failed, lambda m: self.ctx.info(m))

    # ================================================================== autotune
    def run_autotune(self):
        if not self._need_direct():
            return
        if not self.at_ack.isChecked():
            QMessageBox.information(self, "Safety", "Please tick the safety confirmation first.")
            return
        n = self.ctx.current_axis
        params = dict(step_size=self.at_step.value(), bandwidth_hz=self.at_bw.value(), zeta=self.at_zeta.value(),
                      integral_ratio=self.at_int.value(), open_loop_pct=self.at_olmag.value(),
                      do_open_loop=self.at_ol.isChecked(), iterations=self.at_iter.value(),
                      max_overshoot_pct=self.at_maxos.value())
        if not self._confirm("Auto-tune axis %d with %d iterations of ±%g unit steps%s?" % (
                n, params["iterations"], params["step_size"], " plus an open-loop pulse" if params["do_open_loop"] else "")):
            return
        self.at_btn.setEnabled(False)
        self.at_log.clear()
        self.at_badge.set_severity("neutral", "running...")
        self.ctx.pause_polling(True)
        tuner = AutoTuner(self.ctx.session, n)
        self.tuner = tuner

        def work(progress):
            tuner.progress = progress
            r = tuner.run(**params)
            tuner._result = r
            return r

        def done(r: TuneResult):
            self.at_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.at_badge.set_severity("ok" if r.ok else "fault", "done" if r.ok else "failed")
            self.at_summary.setText(r.message + ("\n" + r.plant.message if r.plant else ""))
            self.at_gains.setRowCount(0)
            keys = list(r.applied.keys()) if r.applied else []
            self.at_gains.setRowCount(len(keys))
            for i, k in enumerate(keys):
                self.at_gains.setItem(i, 0, QTableWidgetItem(k.split(".")[-1] if "." in k else k))
                self.at_gains.setItem(i, 1, QTableWidgetItem(str(r.original.get(k, "-"))))
                self.at_gains.setItem(i, 2, QTableWidgetItem("%.5g" % r.applied[k]))
            if r.step_data:
                maxdac = None
                try:
                    maxdac = self.ctx.session.direct.read_float("Motor[%d].MaxDac" % n) if self.ctx.family == "power" \
                        else self.ctx.session.direct.read_float("I%d69" % n)
                except Exception:
                    pass
                self.show_step(r.step_data, maxdac)
            if r.open_loop_data:
                t = [x * 1e3 for x in r.open_loop_data["t"]]
                self.c_olv.setData(t, r.open_loop_data["vel"])
                self.c_olo.setData(t, r.open_loop_data["out"])
            self.read_gains()
            (self.ctx.ok if r.ok else self.ctx.error)("auto-tune: %s" % r.message)

        def failed(msg):
            self.at_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.at_badge.set_severity("fault", "error")
            self.ctx.error("auto-tune error: %s" % msg.splitlines()[0])

        self.ctx.tasks.run(work, done, failed, lambda m: (self.at_log.append_line(m), self.ctx.info(m)))

    def _abort_autotune(self):
        if self.tuner:
            self.tuner.abort = True
            self.at_log.append_line("abort requested...", theme.WARNING)

    # ================================================================== current calculator
    def calc_current(self):
        rating = self.cc_rating.currentData()
        max_adc = units.MAX_ADC_BY_RATING[rating]
        cs = units.compute_i2t(self.cc_peak.value(), self.cc_cont.value(), self.cc_time.value(), max_adc,
                               self.cc_type.currentData(), self.cc_rms.isChecked())
        pwm = units.pwm_sf(self.cc_motv.value() or None, self.cc_bus.value() or None)
        self._cc = (cs, pwm)
        self.cc_maxdac.set(cs.max_dac, "", fmt="{:.0f}")
        self.cc_i2tset.set(cs.i2t_set, "", fmt="{:.0f}")
        self.cc_i2ttrip.set(cs.i2t_trip, "", fmt="{:.0f}")
        self.cc_pwmsf.set(pwm, "", fmt="{:.0f}")
        notes = ["Max ADC %.4g A → 32768 units; %s%s" % (
            max_adc, "×√2 (RMS→peak) " if self.cc_rms.isChecked() else "", "×cos30 deg (3-phase)" if self.cc_type.currentData() == "brushless" else "")]
        if cs.notes:
            notes.append(cs.notes)
        if self.cc_type.currentData() == "microstep":
            notes.append("Direct microstepping: also set IdCmd between I2TSet/2 and I2TSet (manual p.170).")
        self.cc_notes.setText("\n".join(notes))
        if self.ctx.session.direct_ok and self.ctx.family == "power":
            n = self.ctx.current_axis
            try:
                v = self.ctx.session.direct.read_values(["Motor[%d].MaxDac" % n, "Motor[%d].I2TSet" % n,
                                                         "Motor[%d].I2tTrip" % n, "Motor[%d].PwmSf" % n])
                self.cc_current.setText("\n".join("%s = %s" % (k, val) for k, val in v.items()))
            except Exception:
                pass

    def write_current(self):
        if not self._need_direct() or self._cc is None:
            if self._cc is None:
                self.ctx.warn("calculate first")
            return
        cs, pwm = self._cc
        n = self.ctx.current_axis
        if self.ctx.family != "power":
            self.ctx.warn("writing the current limits is implemented for Power PMAC only (Turbo: Ixx69/Ixx57/Ixx58/Ixx66)")
            return
        if not self._confirm("Write MaxDac=%.0f, I2TSet=%.0f, I2tTrip=%.0f, PwmSf=%.0f to Motor[%d]?" % (
                cs.max_dac, cs.i2t_set, cs.i2t_trip, pwm, n)):
            return
        try:
            link = self.ctx.session.direct
            link.send("Motor[%d].MaxDac=%.1f Motor[%d].I2TSet=%.1f Motor[%d].I2tTrip=%.1f Motor[%d].PwmSf=%.1f" % (
                n, cs.max_dac, n, cs.i2t_set, n, cs.i2t_trip, n, pwm))
            self.ctx.ok("current limits written to Motor[%d] (not saved to flash)" % n)
            self.calc_current()
        except Exception as exc:
            self.ctx.error("write failed: %s" % exc)

    def remember_spec(self):
        n = self.ctx.current_axis
        self.ctx.motor_specs[n] = {"peak_a": self.cc_peak.value(), "cont_a": self.cc_cont.value(),
                                   "rms": self.cc_rms.isChecked(), "time_at_peak_s": self.cc_time.value(),
                                   "max_adc": units.MAX_ADC_BY_RATING[self.cc_rating.currentData()],
                                   "rating": self.cc_rating.currentData(), "motor_type": self.cc_type.currentData(),
                                   "bus_v": self.cc_bus.value(), "motor_v": self.cc_motv.value()}
        self.ctx.save_specs()
        self.ctx.ok("motor data remembered for axis %d - the diagnostics will compare MaxDac/I2TSet against it" % n)

    def _load_spec(self):
        sp = self.ctx.motor_specs.get(self.ctx.current_axis)
        if not sp:
            return
        idx = self.cc_rating.findData(sp.get("rating"))
        if idx >= 0:
            self.cc_rating.setCurrentIndex(idx)
        idx = self.cc_type.findData(sp.get("motor_type"))
        if idx >= 0:
            self.cc_type.setCurrentIndex(idx)
        self.cc_peak.setValue(float(sp.get("peak_a", 1)))
        self.cc_cont.setValue(float(sp.get("cont_a", 1)))
        self.cc_rms.setChecked(bool(sp.get("rms", True)))
        self.cc_time.setValue(float(sp.get("time_at_peak_s", 1)))
        self.cc_bus.setValue(float(sp.get("bus_v", 48)))
        self.cc_motv.setValue(float(sp.get("motor_v", 48)))
