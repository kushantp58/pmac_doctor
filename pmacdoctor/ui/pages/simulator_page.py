"""Virtual motor page: describe a real stepper or servo from its datasheet, install it on a
simulated axis and optionally configure the simulated PMAC to match."""
from __future__ import annotations

import json
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...comm.simulator import SimulatedPmac
from ...core import motor_model, units
from ...core.motor_model import (BRUSH, BRUSHLESS, ENCODER_LABELS, LINEAR, MOTOR_TYPE_LABELS,
                                 MotorSpec, STEPPER_ENC, STEPPER_MICRO)
from .. import theme
from ..context import AppContext
from ..widgets import Badge, Card, Kpi, hbox, label

GCM2 = 1e-7          # kg.m^2 per g.cm^2
FAULTS = [
    ("enc_loss", "Encoder cable unplugged (loss detected)"),
    ("limits_unwired", "Limit switches not wired (pLimits still set)"),
    ("limits_active", "Both limit inputs asserted"),
    ("reverse_feedback", "Encoder direction reversed (runaway)"),
    ("no_bus", "DC bus off (no torque)"),
    ("jam", "Mechanical jam (6x friction)"),
    ("resonance", "Excite the coupling resonance"),
    ("not_phased", "Lose the phase reference"),
    ("amp_fault", "Amplifier fault"),
]


def _spin(lo, hi, val, dec=3, suffix="", step=None):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(val)
    if suffix:
        s.setSuffix(" " + suffix)
    if step:
        s.setSingleStep(step)
    s.setMinimumWidth(96)
    return s


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignRight)
    f.setRowWrapPolicy(QFormLayout.DontWrapRows)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setHorizontalSpacing(10)
    return f


class SimulatorPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self._loading = False
        self._fault_boxes: Dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Virtual motor", "title"))
        head.addStretch(1)
        head.addWidget(QLabel("Axis"))
        self.axis_combo = QComboBox()
        self.axis_combo.setMinimumWidth(130)
        head.addWidget(self.axis_combo)
        self.state = Badge("simulator not connected", "neutral")
        head.addWidget(self.state)
        root.addLayout(head)
        self.banner = label("Enter the motor datasheet values and press Apply. The simulator then "
                            "behaves like that motor: torque, inertia, friction, back-EMF speed "
                            "limit, encoder resolution and noise, coupling resonance, and step loss "
                            "for steppers. Connect the built-in simulator in Settings to use this page.",
                            "subtitle", wrap=True)
        root.addWidget(self.banner)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)
        body.addWidget(self._build_form(), 0)
        body.addWidget(self._build_results(), 1)

        self.axis_combo.currentIndexChanged.connect(self._axis_combo_changed)
        ctx.axis_changed.connect(self._axis_changed)
        ctx.connection_changed.connect(self._conn_changed)
        ctx.snapshot_changed.connect(self._snapshot)
        self._fill_axes()
        self._conn_changed()

    # ================================================================== build
    def _build_form(self) -> QWidget:
        col = QVBoxLayout()
        col.setSpacing(12)

        pc = Card("Preset")
        self.preset = QComboBox()
        self._presets = motor_model.presets()
        for k in self._presets:
            self.preset.addItem(k, k)
        load = QPushButton("Load preset")
        self.open_btn = QPushButton("Open...")
        self.save_btn = QPushButton("Save...")
        pc.body.addWidget(self.preset)
        pc.body.addLayout(hbox(self.open_btn, self.save_btn, "stretch", load))
        col.addWidget(pc)

        mc = Card("Motor")
        f = _form()
        self.f_name = QLineEdit()
        self.f_type = QComboBox()
        for k, v in MOTOR_TYPE_LABELS.items():
            self.f_type.addItem(v, k)
        self.f_rating = QComboBox()
        for k in units.MAX_ADC_BY_RATING:
            self.f_rating.addItem("%s  (Max ADC %.4g A)" % (k, units.MAX_ADC_BY_RATING[k]), k)
        self.f_kt = _spin(0.0001, 1000, 0.35, 4, "N.m/A")
        self.f_kt_rms = QCheckBox("torque constant is per A rms")
        self.f_peak = _spin(0.01, 100, 7.2, 2, "A")
        self.f_cont = _spin(0.01, 100, 3.6, 2, "A")
        self.f_rms = QCheckBox("current ratings are rms")
        self.f_tpeak = _spin(0.1, 60, 1.0, 1, "s")
        self.f_res = _spin(0.01, 200, 1.5, 3, "ohm")
        self.f_ind = _spin(0.0, 500, 3.0, 3, "mH")
        self.f_poles = _spin(1, 100, 4, 0)
        self.f_step = _spin(0.05, 90, 1.8, 3, "deg")
        self.f_hold = _spin(0.0, 100, 0.0, 3, "N.m")
        self.f_bus = _spin(5, 400, 48, 1, "V")
        self.f_mv = _spin(5, 400, 48, 1, "V")
        f.addRow("Name", self.f_name)
        f.addRow("Type", self.f_type)
        f.addRow("Drive channel", self.f_rating)
        f.addRow("Torque constant", self.f_kt)
        f.addRow("", self.f_kt_rms)
        f.addRow("Peak current", self.f_peak)
        f.addRow("Continuous current", self.f_cont)
        f.addRow("", self.f_rms)
        f.addRow("Time at peak", self.f_tpeak)
        f.addRow("Winding resistance", self.f_res)
        f.addRow("Winding inductance", self.f_ind)
        f.addRow("Pole pairs", self.f_poles)
        f.addRow("Step angle", self.f_step)
        f.addRow("Holding torque", self.f_hold)
        f.addRow("DC bus voltage", self.f_bus)
        f.addRow("Motor rated voltage", self.f_mv)
        mc.body.addLayout(f)
        col.addWidget(mc)

        kc = Card("Mechanics")
        f2 = _form()
        self.f_jr = _spin(0.0001, 1e9, 400, 3, "g.cm^2")
        self.f_jl = _spin(0.0, 1e9, 600, 3, "g.cm^2")
        self.f_mass = _spin(0.0, 10000, 0.0, 3, "kg")
        self.f_lead = _spin(0.0, 1000, 0.0, 3, "mm/rev")
        self.f_gear = _spin(0.01, 10000, 1.0, 3)
        self.f_coul = _spin(0.0, 10000, 50.0, 3, "mN.m")
        self.f_visc = _spin(0.0, 1e6, 0.2, 4, "mN.m.s/rad")
        self.f_stiff = _spin(0.0, 1e7, 0.0, 2, "N.m/rad")
        self.f_zeta = _spin(0.005, 1.0, 0.05, 3)
        self.f_travel = _spin(0.0, 100000, 50.0, 2, "mm")
        f2.addRow("Rotor inertia", self.f_jr)
        f2.addRow("Load inertia", self.f_jl)
        f2.addRow("Payload mass", self.f_mass)
        f2.addRow("Screw lead", self.f_lead)
        f2.addRow("Gear ratio", self.f_gear)
        f2.addRow("Coulomb friction", self.f_coul)
        f2.addRow("Viscous friction", self.f_visc)
        f2.addRow("Coupling stiffness", self.f_stiff)
        f2.addRow("Coupling damping", self.f_zeta)
        f2.addRow("Travel", self.f_travel)
        kc.body.addLayout(f2)
        col.addWidget(kc)

        ec = Card("Feedback")
        f3 = _form()
        self.f_enc = QComboBox()
        for k, v in ENCODER_LABELS.items():
            self.f_enc.addItem(v, k)
        self.f_cpr = _spin(1, 1e9, 4000, 2, "counts/rev")
        self.f_noise = _spin(0.0, 1000, 0.3, 2, "counts")
        self.f_quant = QCheckBox("quantise to whole counts")
        f3.addRow("Encoder", self.f_enc)
        f3.addRow("Resolution", self.f_cpr)
        f3.addRow("Noise (peak)", self.f_noise)
        f3.addRow("", self.f_quant)
        ec.body.addLayout(f3)
        col.addWidget(ec)

        ac = Card("Apply")
        self.f_configure = QCheckBox("also configure the simulated PMAC for this motor")
        self.f_configure.setChecked(True)
        self.f_configure.setToolTip("Writes current limits, I2T, PWM scale, commutation, following-error "
                                    "limits, jog speed and a starting PID set derived from the model.")
        f4 = _form()
        self.f_bw = _spin(1, 500, 20, 1, "Hz")
        self.f_damp = _spin(0.3, 2.0, 0.9, 2)
        f4.addRow("Start-up bandwidth", self.f_bw)
        f4.addRow("Damping ratio", self.f_damp)
        self.apply_btn = QPushButton("Apply to simulator")
        self.apply_btn.setProperty("kind", "primary")
        ac.body.addWidget(self.f_configure)
        ac.body.addLayout(f4)
        ac.body.addLayout(hbox("stretch", self.apply_btn))
        col.addWidget(ac)
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
        sc.setMinimumWidth(468)
        sc.setMaximumWidth(520)

        self.f_type.currentIndexChanged.connect(self._type_changed)
        self.preset.currentIndexChanged.connect(lambda _: None)
        load.clicked.connect(self._load_preset)
        self.open_btn.clicked.connect(self._open)
        self.save_btn.clicked.connect(self._save)
        self.apply_btn.clicked.connect(self._apply)
        return sc

    def _build_results(self) -> QWidget:
        col = QVBoxLayout()
        col.setSpacing(12)
        dc = Card("Derived plant")
        g = QGridLayout()
        self.k_ka = Kpi("Ka (counts/cycle^2 per unit)", small=True)
        self.k_inertia = Kpi("Reflected inertia", small=True)
        self.k_torque = Kpi("Torque per output unit", small=True)
        self.k_speed = Kpi("Top speed (back-EMF)", small=True)
        self.k_res = Kpi("Coupling resonance", small=True)
        self.k_fric = Kpi("Friction", small=True)
        for i, k in enumerate((self.k_ka, self.k_inertia, self.k_torque, self.k_speed, self.k_res, self.k_fric)):
            g.addWidget(k, i // 3, i % 3)
        dc.body.addLayout(g)
        self.notes = label("", "", wrap=True)
        self.notes.setStyleSheet("color:%s;" % theme.INK_2)
        dc.body.addWidget(self.notes)
        col.addWidget(dc)

        sc = Card("Controller settings written")
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(240)
        sc.body.addWidget(self.table)
        col.addWidget(sc, 1)

        fc = Card("Fault injection")
        fc.body.addWidget(label("Applied live to the selected axis. Untick to clear.", "muted"))
        for kind, text in FAULTS:
            cb = QCheckBox(text)
            cb.toggled.connect(lambda on, k=kind: self._set_fault(k, on))
            self._fault_boxes[kind] = cb
            fc.body.addWidget(cb)
        col.addWidget(fc)

        lc = Card("Live")
        lg = QGridLayout()
        self.k_lost = Kpi("Lost steps", small=True)
        self.k_lag = Kpi("Rotor load angle", small=True)
        self.k_stall = Kpi("Stall", small=True)
        for i, k in enumerate((self.k_lost, self.k_lag, self.k_stall)):
            lg.addWidget(k, 0, i)
        lc.body.addLayout(lg)
        self.live_note = label("Stepper figures only. A direct micro-stepping motor reports no "
                               "following error even while it loses steps, which is exactly why "
                               "lost motion goes unnoticed on real machines.", "muted", wrap=True)
        lc.body.addWidget(self.live_note)
        col.addWidget(lc)
        col.addStretch(1)
        w = QWidget()
        w.setLayout(col)
        w.setStyleSheet("background:transparent;")
        return w

    # ================================================================== state
    def _sim(self) -> Optional[SimulatedPmac]:
        d = self.ctx.session.direct
        return d if isinstance(d, SimulatedPmac) else None

    def _fill_axes(self):
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        for n in range(1, self.ctx.naxes + 1):
            self.axis_combo.addItem("Motor %d" % n, n)
        idx = self.axis_combo.findData(self.ctx.current_axis)
        self.axis_combo.setCurrentIndex(max(0, idx))
        self.axis_combo.blockSignals(False)

    def _axis_combo_changed(self, _):
        n = self.axis_combo.currentData()
        if n is not None:
            self.ctx.set_axis(int(n))

    def _axis_changed(self, n: int):
        idx = self.axis_combo.findData(n)
        if idx >= 0 and self.axis_combo.currentIndex() != idx:
            self.axis_combo.blockSignals(True)
            self.axis_combo.setCurrentIndex(idx)
            self.axis_combo.blockSignals(False)
        self._load_from_sim()

    def _conn_changed(self):
        if self.axis_combo.count() != self.ctx.naxes:
            self._fill_axes()
        sim = self._sim()
        on = sim is not None
        self.apply_btn.setEnabled(on)
        for cb in self._fault_boxes.values():
            cb.setEnabled(on)
        self.state.set_severity("ok" if on else "neutral",
                                "simulator connected" if on else "simulator not connected")
        if on:
            self._load_from_sim()

    def _load_from_sim(self):
        sim = self._sim()
        if sim is None:
            return
        n = self.ctx.current_axis
        mot = sim.motors.get(n)
        if mot is None:
            return
        self._spec_to_form(mot.spec)
        self._show_plant(mot.plant)
        self._refresh_fault_boxes(mot)

    def _refresh_fault_boxes(self, mot):
        state = {
            "enc_loss": mot.enc_loss_input, "limits_unwired": not mot.limits_wired,
            "limits_active": mot.force_limits_active, "reverse_feedback": mot.feedback_sign < 0,
            "no_bus": not mot.bus_ok, "jam": mot.extra_friction > 1.5,
            "resonance": mot.res_freq_hz > 0 and mot.plant.resonance_hz == 0,
            "not_phased": not mot.phase_found, "amp_fault": mot.amp_fault,
        }
        for k, cb in self._fault_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(state.get(k)))
            cb.blockSignals(False)

    def _set_fault(self, kind: str, on: bool):
        sim = self._sim()
        if sim is None:
            return
        try:
            sim.set_fault(self.ctx.current_axis, kind, on)
            self.ctx.info("axis %d: %s %s" % (self.ctx.current_axis, kind, "injected" if on else "cleared"))
        except Exception as exc:
            self.ctx.error("fault injection failed: %s" % exc)

    # ================================================================== form <-> spec
    def _type_changed(self):
        t = self.f_type.currentData()
        linear = t == LINEAR
        stepper = t in (STEPPER_ENC, STEPPER_MICRO)
        self.f_jr.setSuffix(" kg" if linear else " g.cm^2")
        self.f_jl.setSuffix(" kg" if linear else " g.cm^2")
        self.f_kt.setSuffix(" N/A" if linear else " N.m/A")
        self.f_coul.setSuffix(" N" if linear else " mN.m")
        self.f_visc.setSuffix(" N.s/m" if linear else " mN.m.s/rad")
        self.f_hold.setSuffix(" N.m")
        self.f_cpr.setSuffix(" counts/mm" if linear else " counts/rev")
        for w in (self.f_step, self.f_hold):
            w.setEnabled(stepper)
        for w in (self.f_lead, self.f_gear, self.f_stiff, self.f_zeta):
            w.setEnabled(not linear)
        self.f_poles.setEnabled(not stepper)
        self.f_kt.setEnabled(not stepper or self.f_hold.value() <= 0)
        self.f_kt_rms.setEnabled(not linear and not stepper)
        self.live_note.setVisible(stepper)

    def _spec_to_form(self, spec: MotorSpec):
        self._loading = True
        try:
            self.f_name.setText(spec.name)
            i = self.f_type.findData(spec.motor_type)
            self.f_type.setCurrentIndex(max(0, i))
            self._type_changed()
            i = self.f_rating.findData(spec.channel_rating)
            self.f_rating.setCurrentIndex(max(0, i))
            linear = spec.is_linear
            self.f_kt.setValue(spec.torque_constant)
            self.f_kt_rms.setChecked(spec.kt_is_per_rms_amp)
            self.f_peak.setValue(spec.peak_current_a)
            self.f_cont.setValue(spec.cont_current_a)
            self.f_rms.setChecked(spec.current_is_rms)
            self.f_tpeak.setValue(spec.time_at_peak_s)
            self.f_res.setValue(spec.resistance_ohm)
            self.f_ind.setValue(spec.inductance_mh)
            self.f_poles.setValue(spec.pole_pairs)
            self.f_step.setValue(spec.step_angle_deg)
            self.f_hold.setValue(spec.holding_torque_nm)
            self.f_bus.setValue(spec.bus_voltage_v)
            self.f_mv.setValue(spec.motor_voltage_v)
            self.f_jr.setValue(spec.rotor_inertia if linear else spec.rotor_inertia / GCM2)
            self.f_jl.setValue(spec.load_inertia if linear else spec.load_inertia / GCM2)
            self.f_mass.setValue(spec.payload_mass_kg)
            self.f_lead.setValue(spec.lead_mm)
            self.f_gear.setValue(spec.gear_ratio)
            self.f_coul.setValue(spec.coulomb_torque if linear else spec.coulomb_torque * 1e3)
            self.f_visc.setValue(spec.viscous if linear else spec.viscous * 1e3)
            self.f_stiff.setValue(spec.coupling_stiffness)
            self.f_zeta.setValue(spec.coupling_damping_ratio)
            self.f_travel.setValue(spec.travel_mm)
            i = self.f_enc.findData(spec.encoder_type)
            self.f_enc.setCurrentIndex(max(0, i))
            self.f_cpr.setValue(spec.counts_per_rev / (1000.0 if linear else 1.0))
            self.f_noise.setValue(spec.encoder_noise_counts)
            self.f_quant.setChecked(spec.quantize)
        finally:
            self._loading = False

    def _form_to_spec(self) -> MotorSpec:
        s = MotorSpec()
        s.name = self.f_name.text().strip() or "Motor"
        s.motor_type = self.f_type.currentData()
        linear = s.is_linear
        s.channel_rating = self.f_rating.currentData()
        s.torque_constant = self.f_kt.value()
        s.kt_is_per_rms_amp = self.f_kt_rms.isChecked()
        s.peak_current_a = self.f_peak.value()
        s.cont_current_a = self.f_cont.value()
        s.current_is_rms = self.f_rms.isChecked()
        s.time_at_peak_s = self.f_tpeak.value()
        s.resistance_ohm = self.f_res.value()
        s.inductance_mh = self.f_ind.value()
        s.pole_pairs = self.f_poles.value()
        s.step_angle_deg = self.f_step.value()
        s.holding_torque_nm = self.f_hold.value()
        s.bus_voltage_v = self.f_bus.value()
        s.motor_voltage_v = self.f_mv.value()
        s.rotor_inertia = self.f_jr.value() * (1.0 if linear else GCM2)
        s.load_inertia = self.f_jl.value() * (1.0 if linear else GCM2)
        s.payload_mass_kg = self.f_mass.value()
        s.lead_mm = self.f_lead.value()
        s.gear_ratio = self.f_gear.value()
        s.coulomb_torque = self.f_coul.value() * (1.0 if linear else 1e-3)
        s.viscous = self.f_visc.value() * (1.0 if linear else 1e-3)
        s.coupling_stiffness = self.f_stiff.value()
        s.coupling_damping_ratio = self.f_zeta.value()
        s.travel_mm = self.f_travel.value()
        s.encoder_type = self.f_enc.currentData()
        s.counts_per_rev = self.f_cpr.value() * (1000.0 if linear else 1.0)
        s.encoder_noise_counts = self.f_noise.value()
        s.quantize = self.f_quant.isChecked()
        return s

    # ================================================================== actions
    def _load_preset(self):
        key = self.preset.currentData()
        spec = self._presets.get(key)
        if spec:
            self._spec_to_form(spec)
            self.ctx.info("loaded preset '%s' - press Apply to install it on axis %d"
                          % (key, self.ctx.current_axis))

    def _apply(self):
        sim = self._sim()
        if sim is None:
            QMessageBox.information(self, "Simulator required",
                                    "Connect the built-in simulator in Settings to use the virtual motor.")
            return
        n = self.ctx.current_axis
        spec = self._form_to_spec()
        configure = self.f_configure.isChecked()
        try:
            plant, applied = sim.apply_motor(n, spec, configure, self.f_bw.value(), self.f_damp.value())
        except Exception as exc:
            self.ctx.error("could not apply the motor model: %s" % exc)
            return
        self._show_plant(plant)
        self.table.setRowCount(len(applied))
        for i, (k, v) in enumerate(sorted(applied.items())):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem("%g" % v if isinstance(v, float) else str(v)))
        # remember the ratings so the diagnostics can compare against the motor
        self.ctx.motor_specs[n] = dict(
            spec.to_dict(), peak_a=spec.peak_current_a, cont_a=spec.cont_current_a,
            rms=spec.current_is_rms, time_at_peak_s=spec.time_at_peak_s, max_adc=spec.max_adc,
            rating=spec.channel_rating, bus_v=spec.bus_voltage_v, motor_v=spec.motor_voltage_v,
            motor_type=("brushless" if spec.motor_type == BRUSHLESS else
                        "brushed" if spec.motor_type == BRUSH else
                        "microstep" if spec.motor_type == STEPPER_MICRO else "stepper"))
        self.ctx.save_specs()
        self.ctx.session.dumps.pop(n, None)
        self.ctx.ok("axis %d is now a '%s'%s" % (n, spec.name,
                                                 " and the controller was configured to match" if configure else ""))
        self._refresh_fault_boxes(sim.motors[n])

    def _show_plant(self, plant: motor_model.DerivedPlant):
        spec = self._form_to_spec()
        self.k_ka.set(plant.ka, "", fmt="{:.4g}")
        self.k_inertia.set(plant.inertia_total, "kg" if spec.is_linear else "kg.m^2", fmt="{:.4g}")
        self.k_torque.set(plant.torque_per_out, "N" if spec.is_linear else "N.m", fmt="{:.4g}")
        if plant.counts_per_si > 0:
            v = plant.max_vel_cps / (plant.counts_per_si * plant.servo_period_s)
            self.k_speed.set(v if spec.is_linear else v * 60.0 / (2 * 3.141592653589793),
                             "m/s" if spec.is_linear else "rpm", fmt="{:.4g}")
        self.k_res.set(plant.resonance_hz or None, "Hz" if plant.resonance_hz else "none", fmt="{:.0f}")
        self.k_fric.set(plant.coulomb_out, "output units", fmt="{:.0f}")
        self.notes.setText("\n".join("- " + n for n in plant.notes))

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open motor", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                self._spec_to_form(MotorSpec.from_dict(json.load(fh)))
            self.ctx.ok("loaded %s" % path)
        except (OSError, ValueError) as exc:
            self.ctx.error("could not read the motor file: %s" % exc)

    def _save(self):
        spec = self._form_to_spec()
        path, _ = QFileDialog.getSaveFileName(self, "Save motor",
                                              "%s.json" % spec.name.replace(" ", "_"), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(spec.to_dict(), fh, indent=2)
            self.ctx.ok("saved %s" % path)
        except OSError as exc:
            self.ctx.error("could not save: %s" % exc)

    # ================================================================== live
    def _snapshot(self, _snap):
        sim = self._sim()
        if sim is None or not self.isVisible():
            return
        mot = sim.motors.get(self.ctx.current_axis)
        if mot is None:
            return
        self.k_lost.set(mot.lost_steps, "counts", fmt="{:.1f}")
        self.k_lag.set(mot.rotor_lag, "counts", fmt="{:.2f}")
        self.k_stall.set("STALLED" if mot.stalled else "no", "")
        self.k_stall.value.setStyleSheet("color:%s;" % (theme.CRITICAL if mot.stalled else theme.INK))

