"""Live scope: watch a (virtual) motor move.

Rolling traces of commanded vs actual position, following error against the warning and fatal
limits, and motor current against I2TSet and MaxDac - plus live PID sliders and an animated
view of the axis, so the effect of a gain change, a tighter limit or a fault is visible as it
happens.

On the built-in simulator the samples come from a ring buffer filled at the servo rate, so the
traces have full resolution. On real hardware the page follows the normal polling rate, which
is far slower; use the Tuning page's gather-based step capture for fine detail there.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)

from ...comm.base import PmacError
from ...comm.simulator import SimulatedPmac
from ...core import tuning
from .. import theme
from ..context import AppContext
from ..widgets import Badge, Card, Kpi, hbox, label

# gain, label, a sensible full-scale when the axis has no baseline yet
GAINS = [("Kp", "Kp  proportional", 50.0),
         ("Kvfb", "Kvfb  derivative", 1000.0),
         ("Kvff", "Kvff  velocity ff", 1000.0),
         ("Kaff", "Kaff  accel ff", 10000.0),
         ("Ki", "Ki  integral", 1.0)]
TURBO_SUFFIX = {"Kp": "30", "Kvfb": "31", "Kvff": "32", "Ki": "33", "Kaff": "35"}


class MotionView(QWidget):
    """A travel bar with the commanded and actual positions, limits and a current gauge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.setMaximumHeight(118)
        self.lo, self.hi = -1000.0, 1000.0
        self.lim_lo, self.lim_hi = None, None
        self.des = self.act = 0.0
        self.cur_frac = 0.0        # |current| / MaxDac
        self.i2t_frac = 1.0        # I2TSet / MaxDac
        self.on_limit = False
        self.killed = False
        self.message = ""
        self.units = "counts"

    def set_state(self, lo, hi, des, act, cur_frac, i2t_frac, on_limit, killed, message,
                  lim_lo=None, lim_hi=None, units="counts"):
        self.lo, self.hi = lo, hi
        self.lim_lo, self.lim_hi = lim_lo, lim_hi
        self.des, self.act = des, act
        self.cur_frac, self.i2t_frac = cur_frac, i2t_frac
        self.on_limit, self.killed, self.message, self.units = on_limit, killed, message, units
        self.update()

    def _x(self, pos: float, x0: float, w: float) -> float:
        span = max(1e-9, self.hi - self.lo)
        f = min(1.0, max(0.0, (pos - self.lo) / span))
        return x0 + f * w

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.SURFACE))
        gauge_w = 54
        x0, x1 = 16, w - gauge_w - 28
        track_w = max(10, x1 - x0)
        ty, th = 54, 26
        f = QFont()
        f.setPointSizeF(8.0)

        # travel track
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(x0, ty, track_w, th, 5, 5)
        # end-of-travel zones, drawn only where they actually fall in view
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.CRITICAL if self.on_limit else theme.SURFACE_3))
        for lim, side in ((self.lim_lo, -1), (self.lim_hi, 1)):
            if lim is None or not (self.lo <= lim <= self.hi):
                continue
            xl = self._x(lim, x0, track_w)
            p.drawRect(int(xl) if side > 0 else int(xl) - 6, ty, 6, th)
        # commanded marker
        xc = self._x(self.des, x0, track_w)
        p.setPen(QPen(QColor(theme.SERIES[0]), 2))
        p.drawLine(int(xc), ty - 8, int(xc), ty + th + 8)
        # actual block
        xa = self._x(self.act, x0, track_w)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.CRITICAL if self.killed else theme.SERIES[1]))
        p.drawRoundedRect(int(xa) - 9, ty + 3, 18, th - 6, 4, 4)

        # value row along the top
        p.setFont(f)
        p.setPen(QPen(QColor(theme.SERIES[0])))
        p.drawText(x1 - 330, 22, "commanded %.2f %s" % (self.des, self.units))
        p.setPen(QPen(QColor(theme.SERIES[1])))
        p.drawText(x1 - 185, 22, "actual %.2f" % self.act)
        p.setPen(QPen(QColor(theme.INK_2)))
        p.drawText(x1 - 75, 22, "error %.2f" % (self.des - self.act))
        # scale ends under the track
        p.setPen(QPen(QColor(theme.MUTED)))
        p.drawText(x0, h - 10, "%.4g" % self.lo)
        hi_text = "%.4g" % self.hi
        p.drawText(int(x1 - 8 * len(hi_text)), h - 10, hi_text)
        if self.message:
            p.setPen(QPen(QColor(theme.CRITICAL if self.killed else theme.WARNING)))
            fb = QFont()
            fb.setPointSizeF(9.5)
            fb.setBold(True)
            p.setFont(fb)
            p.drawText(x0, 24, self.message)

        # current gauge
        gx = w - gauge_w - 10
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(gx, 14, 18, h - 40, 4, 4)
        gh = h - 44
        cf = min(1.0, max(0.0, self.cur_frac))
        col = theme.GOOD if cf < self.i2t_frac else (theme.WARNING if cf < 0.98 else theme.CRITICAL)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(col))
        p.drawRoundedRect(gx + 2, int(14 + 2 + gh * (1 - cf)), 14, max(2, int(gh * cf)), 3, 3)
        # I2T continuous-rating tick
        yl = int(14 + 2 + gh * (1 - min(1.0, self.i2t_frac)))
        p.setPen(QPen(QColor(theme.INK_2), 1, Qt.DashLine))
        p.drawLine(gx - 3, yl, gx + 21, yl)
        p.setPen(QPen(QColor(theme.MUTED)))
        p.setFont(f)
        p.drawText(gx - 6, h - 10, "%3.0f%%" % (cf * 100))
        p.end()


class ScopePage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.win_s = 5.0
        self._buf: Dict[str, deque] = {}
        self._reset_buffers()
        self._seg: List[tuple] = []
        self._baseline: Dict[str, float] = {}
        self._gain_widgets: Dict[str, tuple] = {}
        self._writing = False
        self._saved_jog: Dict[str, Optional[str]] = {}
        self._target_high = False
        self._maxdac = 1.0
        self._i2tset = 0.0
        self._fatal = 0.0
        self._warn = 0.0
        self._tripped = False
        self._last_t = 0.0
        self._t0: Optional[float] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Live scope", "title"))
        head.addStretch(1)
        head.addWidget(QLabel("Axis"))
        self.axis_combo = QComboBox()
        self.axis_combo.setMinimumWidth(120)
        head.addWidget(self.axis_combo)
        self.rate = label("", "muted")
        head.addWidget(self.rate)
        self.badge = Badge("idle", "neutral")
        head.addWidget(self.badge)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)
        body.addWidget(self._build_left(), 0)
        body.addLayout(self._build_right(), 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.setInterval(40)
        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self._next_move)

        self.axis_combo.currentIndexChanged.connect(self._axis_combo_changed)
        ctx.axis_changed.connect(self._axis_changed)
        ctx.connection_changed.connect(self._conn_changed)
        ctx.snapshot_changed.connect(self._slow_sample)
        self._fill_axes()
        self._conn_changed()

    # ================================================================== build
    def _build_left(self) -> QWidget:
        col = QVBoxLayout()
        col.setSpacing(12)

        mc = Card("Motion")
        mc.body.addWidget(label("Repeats a move so there is always something to watch. Simulator "
                                "only: the app will not drive real hardware in a loop.", "muted", wrap=True))
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignRight)
        self.mode = QComboBox()
        self.mode.addItem("Step (shows overshoot)", "step")
        self.mode.addItem("Profiled move (normal accel)", "profile")
        self.amp = QDoubleSpinBox()
        self.amp.setRange(1, 1e7)
        self.amp.setDecimals(1)
        self.amp.setValue(500)
        self.dwell = QDoubleSpinBox()
        self.dwell.setRange(0.1, 60)
        self.dwell.setDecimals(2)
        self.dwell.setSingleStep(0.1)
        self.dwell.setValue(1.2)
        self.dwell.setSuffix(" s")
        f.addRow("Move", self.mode)
        f.addRow("Amplitude", self.amp)
        f.addRow("Period", self.dwell)
        mc.body.addLayout(f)
        self.run_btn = QPushButton("Start moving")
        self.run_btn.setProperty("kind", "primary")
        self.enable_btn = QPushButton("Re-enable")
        mc.body.addLayout(hbox(self.enable_btn, "stretch", self.run_btn))
        col.addWidget(mc)

        gc = Card("Live servo gains")
        gc.body.addWidget(label("Written to the controller as you drag. Watch the overshoot and the "
                                "following error change.", "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        for row, (key, text, full) in enumerate(GAINS):
            lb = QLabel(text)
            lb.setStyleSheet("font-size:9pt;color:%s;" % theme.INK_2)
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 1e7)
            sp.setDecimals(4)
            sp.setMaximumWidth(104)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 1000)
            sl.setMinimumWidth(110)
            grid.addWidget(lb, row * 2, 0, 1, 2)
            grid.addWidget(sl, row * 2 + 1, 0)
            grid.addWidget(sp, row * 2 + 1, 1)
            self._gain_widgets[key] = (sl, sp, full)
            sl.valueChanged.connect(lambda v, k=key: self._slider_changed(k, v))
            sp.valueChanged.connect(lambda v, k=key: self._spin_changed(k, v))
        gc.body.addLayout(grid)
        self.reset_btn = QPushButton("Back to the values I started with")
        gc.body.addWidget(self.reset_btn)
        col.addWidget(gc)

        pc = Card("Protection")
        pc.body.addWidget(label("Tighten the fatal limit or cut MaxDac and watch the axis trip.",
                                "muted", wrap=True))
        f2 = QFormLayout()
        f2.setLabelAlignment(Qt.AlignRight)
        self.sp_fatal = QDoubleSpinBox()
        self.sp_fatal.setRange(0, 1e7)
        self.sp_fatal.setDecimals(1)
        self.sp_warn = QDoubleSpinBox()
        self.sp_warn.setRange(0, 1e7)
        self.sp_warn.setDecimals(1)
        self.sp_maxdac = QDoubleSpinBox()
        self.sp_maxdac.setRange(0, 32767)
        self.sp_maxdac.setDecimals(0)
        f2.addRow("FatalFeLimit", self.sp_fatal)
        f2.addRow("WarnFeLimit", self.sp_warn)
        f2.addRow("MaxDac", self.sp_maxdac)
        pc.body.addLayout(f2)
        col.addWidget(pc)

        rc = Card("Last completed move")
        g2 = QGridLayout()
        self.k_os = Kpi("Overshoot", small=True)
        self.k_rise = Kpi("Rise 10-90%", small=True)
        self.k_settle = Kpi("Settle 2%", small=True)
        self.k_peakfe = Kpi("Peak foll. error", small=True)
        self.k_peaki = Kpi("Peak current", small=True)
        self.k_sat = Kpi("Saturation", small=True)
        for i, k in enumerate((self.k_os, self.k_rise, self.k_settle, self.k_peakfe, self.k_peaki, self.k_sat)):
            g2.addWidget(k, i // 2, i % 2)
        rc.body.addLayout(g2)
        col.addWidget(rc)
        col.addStretch(1)

        w = QWidget()
        w.setLayout(col)
        w.setStyleSheet("background:transparent;")
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea{background:transparent;}")
        sc.setWidget(w)
        sc.setMinimumWidth(330)
        sc.setMaximumWidth(380)

        self.run_btn.clicked.connect(self._toggle_run)
        self.enable_btn.clicked.connect(self._re_enable)
        self.reset_btn.clicked.connect(self._restore_baseline)
        self.sp_fatal.valueChanged.connect(lambda v: self._write_protect("FatalFeLimit", "11", v))
        self.sp_warn.valueChanged.connect(lambda v: self._write_protect("WarnFeLimit", "12", v))
        self.sp_maxdac.valueChanged.connect(lambda v: self._write_protect("MaxDac", "69", v))
        return sc

    def _build_right(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        self.view = MotionView()
        col.addWidget(self.view)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Window"))
        self.win = QComboBox()
        for s in (2, 5, 10, 20):
            self.win.addItem("%d s" % s, float(s))
        self.win.setCurrentIndex(1)
        self.win.currentIndexChanged.connect(self._win_changed)
        bar.addWidget(self.win)
        self.pause = QCheckBox("pause")
        bar.addWidget(self.pause)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._reset_buffers)
        bar.addWidget(clear)
        bar.addStretch(1)
        self.trip_label = label("", "")
        self.trip_label.setStyleSheet("color:%s;font-weight:600;" % theme.CRITICAL)
        bar.addWidget(self.trip_label)
        col.addLayout(bar)

        self.p_pos = pg.PlotWidget()
        theme.style_plot(self.p_pos.getPlotItem(), "Position", "", "motor units")
        self.p_pos.addLegend(offset=(10, 8), labelTextColor=theme.INK_2)
        self.c_des = self.p_pos.plot([], [], pen=pg.mkPen(theme.SERIES[0], width=2), name="commanded")
        self.c_act = self.p_pos.plot([], [], pen=pg.mkPen(theme.SERIES[1], width=2), name="actual")

        self.p_fe = pg.PlotWidget()
        theme.style_plot(self.p_fe.getPlotItem(), "Following error", "", "motor units")
        self.c_fe = self.p_fe.plot([], [], pen=pg.mkPen(theme.SERIES[2], width=2))
        self.l_fatal_p = self._threshold(self.p_fe, theme.CRITICAL, "fatal")
        self.l_fatal_n = self._threshold(self.p_fe, theme.CRITICAL)
        self.l_warn_p = self._threshold(self.p_fe, theme.WARNING, "warning")
        self.l_warn_n = self._threshold(self.p_fe, theme.WARNING)

        self.p_cur = pg.PlotWidget()
        theme.style_plot(self.p_cur.getPlotItem(), "Motor current", "time (s)", "A peak")
        self.c_cur = self.p_cur.plot([], [], pen=pg.mkPen(theme.SERIES[3], width=2))
        self.l_i2t_p = self._threshold(self.p_cur, theme.WARNING, "continuous (I2TSet)")
        self.l_i2t_n = self._threshold(self.p_cur, theme.WARNING)
        self.l_max_p = self._threshold(self.p_cur, theme.CRITICAL, "peak (MaxDac)")
        self.l_max_n = self._threshold(self.p_cur, theme.CRITICAL)

        for p in (self.p_fe, self.p_cur):
            p.setXLink(self.p_pos)
        for p in (self.p_pos, self.p_fe, self.p_cur):
            p.getPlotItem().setMenuEnabled(False)
            p.setMouseEnabled(x=False, y=False)
        col.addWidget(self.p_pos, 3)
        col.addWidget(self.p_fe, 2)
        col.addWidget(self.p_cur, 2)
        return col

    @staticmethod
    def _threshold(plot, color: str, name: str = ""):
        line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color, width=1, style=Qt.DashLine),
                               label=name or None,
                               labelOpts={"color": color, "position": 0.04, "fill": (0, 0, 0, 0)})
        line.setVisible(False)
        plot.addItem(line, ignoreBounds=True)
        return line

    # ================================================================== state
    def _sim(self) -> Optional[SimulatedPmac]:
        d = self.ctx.session.direct
        return d if isinstance(d, SimulatedPmac) else None

    def _reset_buffers(self):
        n = 60000
        for k in ("t", "des", "act", "fe", "cur", "flags"):
            self._buf[k] = deque(maxlen=n)
        self._seg = []
        self._tripped = False
        if hasattr(self, "trip_label"):
            self.trip_label.setText("")

    def _fill_axes(self):
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        for n in range(1, self.ctx.naxes + 1):
            self.axis_combo.addItem("Motor %d" % n, n)
        i = self.axis_combo.findData(self.ctx.current_axis)
        self.axis_combo.setCurrentIndex(max(0, i))
        self.axis_combo.blockSignals(False)

    def _axis_combo_changed(self, _):
        n = self.axis_combo.currentData()
        if n is not None:
            self.ctx.set_axis(int(n))

    def _axis_changed(self, n: int):
        i = self.axis_combo.findData(n)
        if i >= 0 and self.axis_combo.currentIndex() != i:
            self.axis_combo.blockSignals(True)
            self.axis_combo.setCurrentIndex(i)
            self.axis_combo.blockSignals(False)
        self._stop_moving()
        self._reset_buffers()
        sim = self._sim()
        if sim is not None:
            sim.scope_start(n)
        self._read_settings()

    def _conn_changed(self):
        if self.axis_combo.count() != self.ctx.naxes:
            self._fill_axes()
        sim = self._sim()
        direct = self.ctx.session.direct_ok
        self.run_btn.setEnabled(sim is not None)
        for w in (self.sp_fatal, self.sp_warn, self.sp_maxdac, self.reset_btn, self.enable_btn):
            w.setEnabled(direct)
        for sl, sp, _ in self._gain_widgets.values():
            sl.setEnabled(direct)
            sp.setEnabled(direct)
        if sim is not None:
            sim.scope_start(self.ctx.current_axis)
            self.rate.setText("%.0f samples/s" % sim.scope_rate_hz())
            self.badge.set_severity("ok", "simulator")
        else:
            self.rate.setText("limited to the poll rate" if direct or self.ctx.session.epics else "")
            self.badge.set_severity("neutral", "no simulator" if direct else "not connected")
        if direct:
            self._read_settings()
        self.timer.start()

    # ================================================================== settings
    def _power(self) -> bool:
        return self.ctx.family == "power"

    def _gain_name(self, key: str) -> str:
        n = self.ctx.current_axis
        if self._power():
            return "Motor[%d].Servo.%s" % (n, key)
        return "I%d%s" % (n, TURBO_SUFFIX[key])

    def _read_settings(self):
        link = self.ctx.session.direct
        if link is None or not link.connected:
            return
        n = self.ctx.current_axis
        names = [self._gain_name(k) for k, _, _ in GAINS]
        if self._power():
            names += ["Motor[%d].FatalFeLimit" % n, "Motor[%d].WarnFeLimit" % n, "Motor[%d].MaxDac" % n,
                      "Motor[%d].I2TSet" % n]
        else:
            names += ["I%d11" % n, "I%d12" % n, "I%d69" % n, "I%d57" % n]
        try:
            vals = link.read_many_floats(names)
        except PmacError:
            return
        self._writing = True
        try:
            for key, _, full in GAINS:
                v = vals.get(self._gain_name(key)) or 0.0
                self._baseline[key] = v
                sl, sp, _f = self._gain_widgets[key]
                sp.setValue(v)
                sl.setValue(self._to_slider(key, v))
            scale = 1.0 if self._power() else 1.0 / 16.0
            self._fatal = (vals.get(names[-4]) or 0.0) * scale
            self._warn = (vals.get(names[-3]) or 0.0) * scale
            self._maxdac = max(1.0, vals.get(names[-2]) or 1.0)
            self._i2tset = vals.get(names[-1]) or 0.0
            self.sp_fatal.setValue(self._fatal)
            self.sp_warn.setValue(self._warn)
            self.sp_maxdac.setValue(self._maxdac)
        finally:
            self._writing = False
        self._update_thresholds()

    def _full_scale(self, key: str) -> float:
        base = self._baseline.get(key, 0.0)
        default = dict((k, f) for k, _, f in GAINS)[key]
        return max(base * 3.0, default)

    def _to_slider(self, key: str, value: float) -> int:
        fs = self._full_scale(key)
        return int(max(0, min(1000, round(value / fs * 1000.0))))

    def _slider_changed(self, key: str, v: int):
        if self._writing:
            return
        value = v / 1000.0 * self._full_scale(key)
        self._writing = True
        self._gain_widgets[key][1].setValue(value)
        self._writing = False
        self._write_gain(key, value)

    def _spin_changed(self, key: str, value: float):
        if self._writing:
            return
        self._writing = True
        self._gain_widgets[key][0].setValue(self._to_slider(key, value))
        self._writing = False
        self._write_gain(key, value)

    def _write_gain(self, key: str, value: float):
        link = self.ctx.session.direct
        if link is None or not link.connected:
            return
        try:
            link.write_value(self._gain_name(key), value)
        except PmacError as exc:
            self.ctx.error("could not write %s: %s" % (self._gain_name(key), exc))

    def _write_protect(self, power_elem: str, turbo_suffix: str, value: float):
        if self._writing:
            return
        link = self.ctx.session.direct
        if link is None or not link.connected:
            return
        n = self.ctx.current_axis
        name = "Motor[%d].%s" % (n, power_elem) if self._power() else "I%d%s" % (n, turbo_suffix)
        raw = value if self._power() else (value * 16.0 if turbo_suffix in ("11", "12") else value)
        try:
            link.write_value(name, raw)
        except PmacError as exc:
            self.ctx.error("could not write %s: %s" % (name, exc))
            return
        if power_elem == "FatalFeLimit":
            self._fatal = value
        elif power_elem == "WarnFeLimit":
            self._warn = value
        else:
            self._maxdac = max(1.0, value)
        self._update_thresholds()

    def _restore_baseline(self):
        self._writing = True
        try:
            for key, _, _ in GAINS:
                v = self._baseline.get(key, 0.0)
                sl, sp, _f = self._gain_widgets[key]
                sp.setValue(v)
                sl.setValue(self._to_slider(key, v))
                self._write_gain(key, v)
        finally:
            self._writing = False
        self.ctx.ok("gains back to the values this page started with")

    def _update_thresholds(self):
        sim = self._sim()
        amps_per_unit = 1.0
        if sim is not None:
            mot = sim.motors.get(self.ctx.current_axis)
            if mot is not None:
                amps_per_unit = mot.plant.max_adc / 32768.0
        for line, val in ((self.l_fatal_p, self._fatal), (self.l_fatal_n, -self._fatal),
                          (self.l_warn_p, self._warn), (self.l_warn_n, -self._warn)):
            line.setVisible(abs(val) > 0)
            line.setPos(val)
        imax = self._maxdac * amps_per_unit
        i2t = self._i2tset * amps_per_unit
        for line, val in ((self.l_max_p, imax), (self.l_max_n, -imax)):
            line.setVisible(imax > 0)
            line.setPos(val)
        for line, val in ((self.l_i2t_p, i2t), (self.l_i2t_n, -i2t)):
            line.setVisible(i2t > 0)
            line.setPos(val)

    # ================================================================== motion
    def _toggle_run(self):
        if self.move_timer.isActive():
            self._stop_moving()
        else:
            self._start_moving()

    def _start_moving(self):
        sim = self._sim()
        if sim is None:
            return
        n = self.ctx.current_axis
        link = self.ctx.session.direct
        # a true step creates its whole amplitude as following error in one servo cycle, so an
        # amplitude near FatalFeLimit kills the axis before it can move
        if self.mode.currentData() == "step" and self._fatal > 0 and self.amp.value() > 0.7 * self._fatal:
            safe = max(1.0, round(self._fatal * 0.4, 1))
            self.ctx.warn("step amplitude %g is too close to FatalFeLimit (%g) and would trip it; "
                          "using %g instead" % (self.amp.value(), self._fatal, safe))
            self.amp.setValue(safe)
        try:
            if self._power():
                keys = ["Motor[%d].JogTa" % n, "Motor[%d].JogTs" % n, "Motor[%d].JogSpeed" % n]
            else:
                keys = ["I%d20" % n, "I%d21" % n, "I%d22" % n]
            self._saved_jog = link.read_values(keys)
            if self.mode.currentData() == "step":
                amp = self.amp.value()
                if self._power():
                    link.send("Motor[%d].JogTa=-0.000001 Motor[%d].JogTs=0 Motor[%d].JogSpeed=%g"
                              % (n, n, n, max(1.0, amp * 40)))
                else:
                    link.send("I%d20=1 I%d21=0 I%d22=%g" % (n, n, n, max(1.0, amp * 40)))
            link.send("#%dj/" % n)
        except PmacError as exc:
            self.ctx.error("could not start: %s" % exc)
            return
        self._tripped = False
        self.trip_label.setText("")
        self._target_high = False
        self._seg = []
        self.move_timer.setInterval(int(self.dwell.value() * 1000))
        self.move_timer.start()
        self._next_move()
        self.run_btn.setText("Stop")
        self.run_btn.setProperty("kind", "danger")
        self.run_btn.style().unpolish(self.run_btn)
        self.run_btn.style().polish(self.run_btn)
        self.badge.set_severity("ok", "moving")

    def _stop_moving(self):
        self.move_timer.stop()
        link = self.ctx.session.direct
        if link is not None and link.connected and self._saved_jog:
            for k, v in self._saved_jog.items():
                if v is not None:
                    try:
                        link.send("%s=%s" % (k, v))
                    except PmacError:
                        pass
        self._saved_jog = {}
        if hasattr(self, "run_btn"):
            self.run_btn.setText("Start moving")
            self.run_btn.setProperty("kind", "primary")
            self.run_btn.style().unpolish(self.run_btn)
            self.run_btn.style().polish(self.run_btn)
        if self._sim() is not None:
            self.badge.set_severity("ok", "simulator")

    def _next_move(self):
        link = self.ctx.session.direct
        if link is None or not link.connected:
            self._stop_moving()
            return
        self._analyse_segment()
        n = self.ctx.current_axis
        self._target_high = not self._target_high
        target = self.amp.value() if self._target_high else 0.0
        try:
            link.send("#%dj=%g" % (n, target))
        except PmacError as exc:
            self.ctx.warn("move refused: %s" % exc)
            self._stop_moving()

    def _re_enable(self):
        n = self.ctx.current_axis
        try:
            if self._power():
                self.ctx.session.send("#%dk" % n)
                try:
                    self.ctx.session.send("BrickLV.Reset=1")
                    self.ctx.session.send("BrickLV.Monitor=1")
                except PmacError:
                    pass
            self.ctx.session.send("#%dj/" % n)
            self._tripped = False
            self.trip_label.setText("")
            self.ctx.ok("axis %d re-enabled" % n)
        except PmacError as exc:
            self.ctx.error("could not re-enable: %s" % exc)

    def _analyse_segment(self):
        if len(self._seg) < 20:
            self._seg = []
            return
        t = [s[0] for s in self._seg]
        des = [s[1] for s in self._seg]
        act = [s[2] for s in self._seg]
        cur = [abs(s[4]) for s in self._seg]
        sim = self._sim()
        amps_per_unit = 1.0
        if sim is not None:
            mot = sim.motors.get(self.ctx.current_axis)
            if mot is not None:
                amps_per_unit = mot.plant.max_adc / 32768.0
        out = [s[4] / max(1e-9, amps_per_unit) for s in self._seg]
        m = tuning.analyze_step(t, des, act, out, self._maxdac)
        self._seg = []
        if not m.ok:
            return
        self.k_os.set(m.overshoot_pct, "%", fmt="{:.1f}")
        self.k_rise.set(m.rise_time_ms, "ms", fmt="{:.1f}")
        self.k_settle.set(m.settling_time_ms, "ms" if m.settling_time_ms is not None else "never", fmt="{:.0f}")
        self.k_peakfe.set(m.peak_error, "units", fmt="{:.2f}")
        self.k_peaki.set(max(cur) if cur else None, "A", fmt="{:.2f}")
        self.k_sat.set(m.saturation_pct, "% of time", fmt="{:.0f}")

    # ================================================================== sampling
    def _slow_sample(self, snap):
        """Fallback for real hardware: one sample per controller poll."""
        if self._sim() is not None or self.pause.isChecked():
            return
        ax = snap.axes.get(self.ctx.current_axis)
        if ax is None or ax.v("ActPos") is None:
            return
        if self._t0 is None:
            self._t0 = time.perf_counter()
        self._last_t = time.perf_counter() - self._t0
        des = ax.v("DesPos")
        act = ax.v("ActPos")
        fe = ax.v("PosError", 0.0) or 0.0
        if des is None:
            des = (act or 0.0) + fe
        out = ax.v("ServoOut", 0.0) or 0.0
        flags = 0
        if ax.status is not None:
            if ax.status.fe_fatal:
                flags |= SimulatedPmac.SCOPE_FATAL
            if ax.status.fe_warn:
                flags |= SimulatedPmac.SCOPE_WARN
            if ax.status.amp_enabled:
                flags |= SimulatedPmac.SCOPE_ENABLED
            if ax.status.plus_limit or ax.status.minus_limit:
                flags |= SimulatedPmac.SCOPE_LIMIT
        self._append((self._last_t, des, act or 0.0, fe, out, 0.0, 0.0, flags))

    def _append(self, s: tuple):
        self._buf["t"].append(s[0])
        self._buf["des"].append(s[1])
        self._buf["act"].append(s[2])
        self._buf["fe"].append(s[3])
        self._buf["cur"].append(s[5])
        self._buf["flags"].append(s[7])
        self._seg.append(s)

    def _tick(self):
        if not self.isVisible():
            return
        sim = self._sim()
        if sim is not None and not self.pause.isChecked():
            for s in sim.scope_drain():
                self._append(s)
        self._redraw()

    def _win_changed(self):
        self.win_s = float(self.win.currentData())

    def _redraw(self):
        t = self._buf["t"]
        if not t:
            return
        tn = np.fromiter(t, dtype=float, count=len(t))
        t_end = tn[-1]
        keep = tn >= t_end - self.win_s
        tt = tn[keep] - t_end
        des = np.fromiter(self._buf["des"], dtype=float, count=len(self._buf["des"]))[keep]
        act = np.fromiter(self._buf["act"], dtype=float, count=len(self._buf["act"]))[keep]
        fe = np.fromiter(self._buf["fe"], dtype=float, count=len(self._buf["fe"]))[keep]
        cur = np.fromiter(self._buf["cur"], dtype=float, count=len(self._buf["cur"]))[keep]
        flags = np.fromiter(self._buf["flags"], dtype=float, count=len(self._buf["flags"]))[keep]
        self.c_des.setData(tt, des)
        self.c_act.setData(tt, act)
        self.c_fe.setData(tt, fe)
        self.c_cur.setData(tt, cur)
        self.p_pos.setXRange(-self.win_s, 0, padding=0)

        last_flags = int(flags[-1]) if len(flags) else 0
        killed = not (last_flags & SimulatedPmac.SCOPE_ENABLED)
        fatal_now = bool(last_flags & SimulatedPmac.SCOPE_FATAL)
        if fatal_now and not self._tripped:
            self._tripped = True
            self.trip_label.setText("FATAL FOLLOWING ERROR - motor killed")
            self.move_timer.stop()
            self._stop_moving()
            self.ctx.warn("axis %d tripped a fatal following error" % self.ctx.current_axis)
        msg = ""
        if last_flags & SimulatedPmac.SCOPE_FATAL:
            msg = "KILLED: fatal following error"
        elif last_flags & SimulatedPmac.SCOPE_AMPFAULT:
            msg = "KILLED: amplifier fault"
        elif last_flags & SimulatedPmac.SCOPE_STALL:
            msg = "STALLED: losing steps"
        elif last_flags & SimulatedPmac.SCOPE_I2T:
            msg = "I2T trip"
        elif last_flags & SimulatedPmac.SCOPE_LIMIT:
            msg = "on a limit switch"
        elif last_flags & SimulatedPmac.SCOPE_WARN:
            msg = "following error warning"

        lo, hi, lim_lo, lim_hi = self._view_range(des, act)
        amps_per_unit = 1.0
        sim = self._sim()
        if sim is not None:
            mot = sim.motors.get(self.ctx.current_axis)
            if mot is not None:
                amps_per_unit = mot.plant.max_adc / 32768.0
        imax = max(1e-9, self._maxdac * amps_per_unit)
        self.view.set_state(lo, hi, float(des[-1]), float(act[-1]),
                            abs(float(cur[-1])) / imax, (self._i2tset * amps_per_unit) / imax,
                            bool(last_flags & SimulatedPmac.SCOPE_LIMIT), killed, msg,
                            lim_lo=lim_lo, lim_hi=lim_hi)

    def _view_range(self, des, act):
        """Scale the travel bar to what is actually happening, keeping the end stops in view
        when they are close enough to matter."""
        a = self.amp.value()
        lo = min(float(np.min(des)), float(np.min(act)), 0.0)
        hi = max(float(np.max(des)), float(np.max(act)), a)
        pad = 0.15 * max(1e-9, hi - lo)
        lo, hi = lo - pad, hi + pad
        lim_lo = lim_hi = None
        sim = self._sim()
        if sim is not None:
            mot = sim.motors.get(self.ctx.current_axis)
            if mot is not None and abs(mot.limit_hi) < 1e6:
                lim_lo, lim_hi = mot.limit_lo, mot.limit_hi
                span = hi - lo
                # pull the end stops into view when they are within a couple of spans
                if lim_lo > lo - 2 * span:
                    lo = min(lo, lim_lo - 0.05 * span)
                if lim_hi < hi + 2 * span:
                    hi = max(hi, lim_hi + 0.05 * span)
        return lo, hi, lim_lo, lim_hi

    # ================================================================== lifecycle
    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        sim = self._sim()
        if sim is not None:
            sim.scope_start(self.ctx.current_axis)
        if self.ctx.session.direct_ok:
            self._read_settings()
        self.timer.start()

    def hideEvent(self, ev):  # noqa: N802
        super().hideEvent(ev)
        self.timer.stop()
        self._stop_moving()
        sim = self._sim()
        if sim is not None:
            sim.scope_stop()
