"""Physical motor + mechanics model used by the built-in simulator.

The user enters ordinary datasheet values (torque constant, inertia, currents, encoder
resolution, screw/gearbox, friction) and this module derives everything the simulator and
the PMAC need:

* ``DerivedPlant`` - the per-servo-cycle constants the simulator integrates
* :func:`suggest_settings` - matching PMAC settings (MaxDac, I2TSet, I2tTrip, PwmSf,
  PhasePosSf, following-error limits, jog speed and a starting PID set)

Units
-----
Everything is SI internally. ``counts_per_si`` converts the mechanical coordinate to encoder
counts: counts per radian for a rotary motor, counts per metre for a linear motor. The PMAC
side works in counts ("motor units") and servo cycles, so the simulator stores

    ka  =  counts / cycle^2  per 16-bit output unit

Torque / current
----------------
A command of ``out`` 16-bit units asks for ``out/32768 * MaxAdc`` amps peak. The available
current falls with speed because the winding sees back-EMF and inductive reactance::

    i_max(w) = max(0, 0.95*Vbus - Ke*|w|) / sqrt(R^2 + (p*w*L)^2)

which reproduces the speed/torque droop of a servo and the pull-out curve of a stepper.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from . import units

TWO_PI = 2.0 * math.pi

BRUSHLESS = "brushless"
BRUSH = "brush"
STEPPER_ENC = "stepper_enc"
STEPPER_MICRO = "stepper_micro"
LINEAR = "linear"

MOTOR_TYPE_LABELS = {
    BRUSHLESS: "3-phase brushless servo (rotary)",
    BRUSH: "DC brush servo",
    STEPPER_ENC: "2-phase stepper with encoder (closed loop)",
    STEPPER_MICRO: "2-phase stepper, direct micro-stepping (no encoder)",
    LINEAR: "Linear motor / direct-drive linear stage",
}

ENCODER_LABELS = {
    "quadrature": "Digital quadrature (x4)",
    "sincos": "Sinusoidal 1 Vpp (interpolated)",
    "serial": "Serial absolute (EnDat/BiSS/SSI)",
    "resolver": "Resolver",
    "none": "None (open loop micro-stepping)",
}


@dataclass
class MotorSpec:
    """Datasheet-level description of a motor, its mechanics and its feedback."""

    name: str = "Generic brushless servo"
    motor_type: str = BRUSHLESS

    # ----- drive / electrical
    channel_rating: str = "5 A / 15 A"      # key into units.MAX_ADC_BY_RATING
    bus_voltage_v: float = 48.0
    motor_voltage_v: float = 48.0
    peak_current_a: float = 7.2
    cont_current_a: float = 3.6
    current_is_rms: bool = True
    time_at_peak_s: float = 1.0
    torque_constant: float = 0.35           # N.m/A (N/A for a linear motor)
    kt_is_per_rms_amp: bool = True
    resistance_ohm: float = 1.5
    inductance_mh: float = 3.0
    pole_pairs: float = 4                   # 50 for a 1.8 deg stepper
    step_angle_deg: float = 1.8             # steppers only
    holding_torque_nm: float = 0.0          # steppers: if > 0 it defines the torque constant

    # ----- mechanics (SI; for a linear motor inertias are masses in kg)
    rotor_inertia: float = 4.0e-5           # kg.m^2 on the motor shaft
    load_inertia: float = 6.0e-5            # kg.m^2 at the LOAD shaft (screw, coupling, table)
    payload_mass_kg: float = 0.0            # mass moved through the screw
    lead_mm: float = 0.0                    # screw lead mm/rev (0 = direct rotary drive)
    gear_ratio: float = 1.0                 # motor revolutions per load revolution
    coulomb_torque: float = 0.05            # N.m at the motor shaft (N for linear)
    viscous: float = 2.0e-4                 # N.m.s/rad (N.s/m for linear)
    coupling_stiffness: float = 0.0         # N.m/rad between motor and load (0 = rigid)
    coupling_damping_ratio: float = 0.05

    # ----- feedback
    encoder_type: str = "quadrature"
    counts_per_rev: float = 4000.0          # counts after decode on the motor shaft
                                            # (counts per metre when motor_type == LINEAR)
    encoder_noise_counts: float = 0.3
    quantize: bool = True

    # ----- travel
    travel_mm: float = 50.0                 # used for the simulated limit switches

    # ------------------------------------------------------------------ helpers
    @property
    def is_linear(self) -> bool:
        return self.motor_type == LINEAR

    @property
    def is_stepper(self) -> bool:
        return self.motor_type in (STEPPER_ENC, STEPPER_MICRO)

    @property
    def max_adc(self) -> float:
        return units.MAX_ADC_BY_RATING.get(self.channel_rating, units.DEFAULT_MAX_ADC)

    @property
    def counts_per_si(self) -> float:
        """Counts per radian (rotary) or per metre (linear)."""
        if self.is_linear:
            return max(1e-9, self.counts_per_rev)
        return max(1e-9, self.counts_per_rev) / TWO_PI

    @property
    def effective_pole_pairs(self) -> float:
        if self.is_stepper:
            # a 2-phase stepper has 360/(4*step angle) pole pairs
            return max(1.0, 360.0 / (4.0 * max(0.0001, self.step_angle_deg)))
        return max(1.0, self.pole_pairs)

    def torque_constant_peak(self) -> float:
        """Torque per amp of PEAK current, which is what the drive commands."""
        kt = self.torque_constant
        if self.is_stepper and self.holding_torque_nm > 0 and self.cont_current_a > 0:
            # steppers are usually specified by holding torque at rated current
            kt = self.holding_torque_nm / self.cont_current_a
            return kt
        if self.kt_is_per_rms_amp and not self.is_linear:
            return kt / math.sqrt(2.0)
        return kt

    def total_inertia(self) -> float:
        """Inertia (or mass) reflected to the motor shaft."""
        if self.is_linear:
            return max(1e-12, self.rotor_inertia + self.payload_mass_kg + self.load_inertia)
        j = self.rotor_inertia
        load = self.load_inertia
        if self.lead_mm > 0 and self.payload_mass_kg > 0:
            load += self.payload_mass_kg * (self.lead_mm * 1e-3 / TWO_PI) ** 2
        r = max(1e-6, self.gear_ratio)
        return max(1e-12, j + load / (r * r))

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "MotorSpec":
        spec = MotorSpec()
        for k, v in (d or {}).items():
            if hasattr(spec, k):
                setattr(spec, k, v)
        return spec


@dataclass
class DerivedPlant:
    """Per-servo-cycle constants handed to the simulator."""

    servo_period_s: float = 0.0005
    ka: float = 0.0                 # counts/cycle^2 per output unit (at full current)
    torque_per_out: float = 0.0     # N.m per output unit
    inertia_total: float = 0.0
    counts_per_si: float = 0.0
    coulomb_out: float = 0.0        # output units needed to break static friction
    viscous_per_cycle: float = 0.0
    max_vel_cps: float = 0.0        # back-EMF speed limit, counts per servo cycle
    resonance_hz: float = 0.0
    resonance_zeta: float = 0.05
    full_step_counts: float = 0.0   # stepper: counts per full step
    pull_out_out: float = 0.0       # stepper: output units available at standstill
    # electrical, for the per-cycle current limit
    kt_peak: float = 0.0
    ke: float = 0.0
    resistance: float = 0.5
    inductance_h: float = 0.0
    pole_pairs: float = 1.0
    bus_voltage: float = 48.0
    max_adc: float = units.DEFAULT_MAX_ADC
    counts_per_rev: float = 4000.0
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def available_current(self, vel_cps: float) -> float:
        """Peak amps the drive can push at this speed (back-EMF + reactance limited)."""
        omega = abs(vel_cps) / max(1e-12, self.counts_per_si * self.servo_period_s)
        head = self.bus_voltage * 0.95 - self.ke * omega
        if head <= 0:
            return 0.0
        z = math.hypot(max(0.05, self.resistance), self.pole_pairs * omega * self.inductance_h)
        return head / max(1e-6, z)

    def accel_counts(self, out_units: float, vel_cps: float) -> float:
        """Acceleration in counts/cycle^2 produced by a command of ``out_units``."""
        i_cmd = out_units / 32768.0 * self.max_adc
        i_max = self.available_current(vel_cps)
        i = max(-i_max, min(i_max, i_cmd))
        torque = self.kt_peak * i
        return torque / self.inertia_total * self.counts_per_si * self.servo_period_s ** 2


def derive(spec: MotorSpec, servo_period_s: float) -> DerivedPlant:
    """Compute the simulator constants for ``spec`` at the given servo period."""
    T = max(1e-6, servo_period_s)
    j = spec.total_inertia()
    kt = spec.torque_constant_peak()
    cps = spec.counts_per_si
    max_adc = spec.max_adc
    torque_per_out = kt * max_adc / 32768.0
    p = DerivedPlant(servo_period_s=T, inertia_total=j, counts_per_si=cps, kt_peak=kt,
                     torque_per_out=torque_per_out, resistance=max(0.05, spec.resistance_ohm),
                     inductance_h=max(0.0, spec.inductance_mh) * 1e-3,
                     pole_pairs=spec.effective_pole_pairs, bus_voltage=max(1.0, spec.bus_voltage_v),
                     max_adc=max_adc, counts_per_rev=spec.counts_per_rev)
    p.ka = torque_per_out / j * cps * T * T
    p.coulomb_out = abs(spec.coulomb_torque) / max(1e-12, torque_per_out)
    p.viscous_per_cycle = max(0.0, spec.viscous) / j * T
    p.ke = kt  # SI: V.s/rad numerically equals N.m/A
    # back-EMF speed ceiling (no current left to overcome anything)
    omega_max = (p.bus_voltage * 0.95) / max(1e-9, p.ke)
    p.max_vel_cps = omega_max * cps * T
    # compliant coupling -> two-mass resonance
    res_note = ""
    if spec.coupling_stiffness > 0 and not spec.is_linear:
        jm = max(1e-12, spec.rotor_inertia)
        jl = j - jm
        if jl < 0.02 * jm:
            res_note = ("Coupling compliance ignored: the reflected load inertia (%.2g) is "
                        "negligible next to the rotor (%.2g), so there is no two-mass mode."
                        % (max(jl, 0.0), jm))
        else:
            wn = math.sqrt(spec.coupling_stiffness * (jm + jl) / (jm * jl))
            f = wn / TWO_PI
            if f > 0.2 / T:
                res_note = ("Coupling resonance at %.0f Hz is above the servo bandwidth "
                            "(%.0f Hz sampling) and is not simulated." % (f, 1.0 / T))
            else:
                p.resonance_hz = f
                p.resonance_zeta = max(0.005, spec.coupling_damping_ratio)
    if spec.is_stepper:
        p.full_step_counts = spec.counts_per_rev * spec.step_angle_deg / 360.0
        rated = spec.cont_current_a * (math.sqrt(2.0) if spec.current_is_rms else 1.0)
        p.pull_out_out = min(32767.0, rated / max_adc * 32768.0)
    notes = []
    notes.append("Reflected inertia %.3g %s, torque %.4g %s per output unit."
                 % (j, "kg" if spec.is_linear else "kg.m^2", torque_per_out,
                    "N" if spec.is_linear else "N.m"))
    notes.append("Ka = %.4g counts/cycle^2 per output unit at %.4g kHz servo."
                 % (p.ka, 1.0 / (T * 1000.0)))
    if spec.is_linear:
        notes.append("Top speed from back-EMF: %.3g m/s." % (p.max_vel_cps / (cps * T)))
    else:
        notes.append("Top speed from back-EMF: %.0f rpm." % (p.max_vel_cps / (cps * T) * 60.0 / TWO_PI))
    if p.resonance_hz:
        notes.append("Coupling resonance at %.0f Hz (zeta %.2f) - expect ringing above it."
                     % (p.resonance_hz, p.resonance_zeta))
    elif res_note:
        notes.append(res_note)
    if spec.is_stepper:
        notes.append("Full step = %.1f counts; pull-out current %.2f A peak."
                     % (p.full_step_counts, p.pull_out_out / 32768.0 * max_adc))
    p.notes = notes
    return p


def travel_counts(spec: MotorSpec) -> float:
    """Usable travel in encoder counts, for the simulated limit switches.

    A direct-drive rotary motor has no end stops, so a very large value is returned.
    """
    if spec.travel_mm <= 0:
        return 2.0e6
    if spec.is_linear:
        return spec.travel_mm * 1e-3 * spec.counts_per_rev
    if spec.lead_mm > 0:
        return spec.travel_mm / spec.lead_mm * max(1e-6, spec.gear_ratio) * spec.counts_per_rev
    return 2.0e6


# ======================================================================================
# Matching PMAC settings
# ======================================================================================
def suggest_settings(spec: MotorSpec, plant: DerivedPlant, axis: int, family: str = "power",
                     bandwidth_hz: float = 20.0, zeta: float = 0.9,
                     phase_over_servo: float = 0.25) -> Dict[str, float]:
    """PMAC settings consistent with this motor: limits, scaling, protection and start gains."""
    from . import tuning  # local import: tuning imports numpy

    cs = units.compute_i2t(spec.peak_current_a, spec.cont_current_a, spec.time_at_peak_s,
                           spec.max_adc, "brushless" if spec.motor_type == BRUSHLESS else "stepper",
                           spec.current_is_rms)
    pwm = units.pwm_sf(spec.motor_voltage_v or None, spec.bus_voltage_v or None)
    # a starting PID set from the identified plant
    pm = tuning.PlantModel(ok=True, ka=plant.ka, servo_period_s=plant.servo_period_s,
                           friction_out=plant.coulomb_out)
    g = tuning.design_gains(pm, bandwidth_hz, zeta, integral_ratio=0.1, feedforward=True)
    cpr = max(1.0, spec.counts_per_rev)
    jog_cps = plant.max_vel_cps * 0.4
    jog_counts_per_ms = jog_cps / (plant.servo_period_s * 1000.0)
    if spec.is_linear:
        fatal = max(50.0, spec.counts_per_rev * 1e-3)        # 1 mm of following error
    else:
        fatal = max(50.0, cpr / 10.0)                        # about a tenth of a turn
    warn = fatal / 4.0
    inpos = max(2.0, cpr / 2000.0)

    if family == "power":
        out: Dict[str, float] = {
            "Motor[%d].ServoCtrl" % axis: 1,
            "Motor[%d].MaxDac" % axis: round(min(32767.0, cs.max_dac), 1),
            "Motor[%d].I2TSet" % axis: round(cs.i2t_set, 1),
            "Motor[%d].I2tTrip" % axis: round(cs.i2t_trip, 1),
            "Motor[%d].PwmSf" % axis: round(pwm, 1),
            "Motor[%d].FatalFeLimit" % axis: round(fatal, 1),
            "Motor[%d].WarnFeLimit" % axis: round(warn, 1),
            "Motor[%d].InPosBand" % axis: round(inpos, 2),
            "Motor[%d].JogSpeed" % axis: round(max(1.0, jog_counts_per_ms), 3),
            "Motor[%d].MaxSpeed" % axis: round(max(2.0, jog_counts_per_ms * 2), 3),
            "Motor[%d].Servo.Kp" % axis: round(g.kp, 5),
            "Motor[%d].Servo.Kvfb" % axis: round(g.kvfb, 5),
            "Motor[%d].Servo.Kvff" % axis: round(g.kvff, 5),
            "Motor[%d].Servo.Kaff" % axis: round(g.kaff, 5),
            "Motor[%d].Servo.Ki" % axis: round(g.ki, 7),
            "Motor[%d].Servo.SwZvInt" % axis: 1,
        }
        if spec.motor_type == BRUSHLESS:
            out["Motor[%d].PhaseCtrl" % axis] = 4
            out["Motor[%d].PhaseOffset" % axis] = 683
            out["Motor[%d].PhaseMode" % axis] = 0
            out["Motor[%d].PhasePosSf" % axis] = units.phase_pos_sf_quadrature(spec.pole_pairs, cpr)
        elif spec.motor_type == STEPPER_ENC:
            out["Motor[%d].PhaseCtrl" % axis] = 4
            out["Motor[%d].PhaseOffset" % axis] = 512
            out["Motor[%d].PhaseMode" % axis] = 1
            out["Motor[%d].PhasePosSf" % axis] = units.phase_pos_sf_quadrature(
                spec.effective_pole_pairs, cpr)
        elif spec.motor_type == STEPPER_MICRO:
            # Direct micro-stepping follows the manual's recipe (p.154-156, 167):
            # no position loop tuning, MaxDac is a SPEED limit, IdCmd sets the holding current.
            out["Motor[%d].PhaseCtrl" % axis] = 6
            out["Motor[%d].PhaseOffset" % axis] = 512
            out["Motor[%d].PhaseMode" % axis] = 1
            out["Motor[%d].PhasePosSf" % axis] = 0
            out["Motor[%d].IdCmd" % axis] = round(cs.i2t_set / 2.0, 1)
            out["Motor[%d].SlipGain" % axis] = round(phase_over_servo, 6)
            servo_ms = plant.servo_period_s * 1000.0
            out["Motor[%d].AdvGain" % axis] = round(
                1.0 / 16.0 * phase_over_servo * (0.25 / servo_ms / max(1e-9, phase_over_servo)), 6)
            rpm = plant.max_vel_cps / (plant.counts_per_si * plant.servo_period_s) * 60.0 / TWO_PI
            max_dac = rpm / 60000.0 * (360.0 / (4.0 * max(1e-6, spec.step_angle_deg))) * 2048.0 * servo_ms
            out["Motor[%d].MaxDac" % axis] = round(min(32767.0, max(1.0, max_dac)), 1)
            for k in ("Kp", "Kvff", "Kaff"):
                out["Motor[%d].Servo.%s" % (axis, k)] = 1
            for k in ("Kvfb", "Kvifb", "Kviff", "Ki", "Kfff"):
                out["Motor[%d].Servo.%s" % (axis, k)] = 0
            out["Motor[%d].Servo.SwZvInt" % axis] = 0
        elif spec.motor_type == BRUSH:
            out["Motor[%d].PhaseCtrl" % axis] = 4
            out["Motor[%d].PhaseOffset" % axis] = 512
            out["Motor[%d].PhaseMode" % axis] = 3
            out["Motor[%d].PhasePosSf" % axis] = 0
        else:  # linear
            out["Motor[%d].PhaseCtrl" % axis] = 4
            out["Motor[%d].PhaseOffset" % axis] = 683
            out["Motor[%d].PhasePosSf" % axis] = 0
        return out

    # Turbo PMAC equivalents
    tg = g.as_turbo(axis)
    return {
        "I%d00" % axis: 1,
        "I%d69" % axis: round(min(32767.0, cs.max_dac), 1),
        "I%d57" % axis: round(cs.i2t_set, 1),
        "I%d58" % axis: round(cs.i2t_trip / 100.0, 1),
        "I%d66" % axis: round(pwm, 1),
        "I%d11" % axis: round(fatal * 16.0, 1),
        "I%d12" % axis: round(warn * 16.0, 1),
        "I%d28" % axis: round(inpos * 16.0, 1),
        "I%d22" % axis: round(max(1.0, jog_counts_per_ms), 3),
        "I%d16" % axis: round(max(2.0, jog_counts_per_ms * 2), 3),
        "I%d30" % axis: round(tg["I%d30" % axis], 3),
        "I%d31" % axis: round(tg["I%d31" % axis], 3),
        "I%d32" % axis: round(tg["I%d32" % axis], 3),
        "I%d33" % axis: round(tg["I%d33" % axis], 5),
        "I%d35" % axis: round(tg["I%d35" % axis], 3),
        "I%d34" % axis: 1,
    }


# ======================================================================================
# Presets (generic examples, not vendor data)
# ======================================================================================
def presets() -> Dict[str, MotorSpec]:
    p: Dict[str, MotorSpec] = {}
    p["Brushless servo, small stage (default)"] = MotorSpec()
    p["Brushless servo, 400 W direct drive"] = MotorSpec(
        name="Brushless servo 400 W", motor_type=BRUSHLESS, torque_constant=0.55,
        peak_current_a=9.0, cont_current_a=3.0, resistance_ohm=1.1, inductance_mh=4.2,
        pole_pairs=4, rotor_inertia=1.7e-4, load_inertia=2.0e-4, counts_per_rev=8000,
        coulomb_torque=0.08, viscous=4e-4, coupling_stiffness=80.0, travel_mm=100)
    p["Brushless servo + 5:1 gearbox + 2 mm screw"] = MotorSpec(
        name="Geared screw stage", motor_type=BRUSHLESS, torque_constant=0.3,
        peak_current_a=6.0, cont_current_a=2.0, resistance_ohm=2.0, inductance_mh=5.0,
        pole_pairs=4, rotor_inertia=3.0e-5, load_inertia=2.0e-5, payload_mass_kg=8.0,
        lead_mm=2.0, gear_ratio=5.0, counts_per_rev=2000, coulomb_torque=0.03,
        viscous=1.5e-4, travel_mm=25)
    p["Stepper 1.8 deg NEMA 23 with encoder"] = MotorSpec(
        name="Stepper NEMA 23 closed loop", motor_type=STEPPER_ENC, step_angle_deg=1.8,
        holding_torque_nm=1.2, peak_current_a=4.0, cont_current_a=2.8, current_is_rms=False,
        resistance_ohm=0.9, inductance_mh=3.6, rotor_inertia=2.8e-5, load_inertia=2.0e-5,
        payload_mass_kg=5.0, lead_mm=1.0, counts_per_rev=2000, coulomb_torque=0.04,
        viscous=1.0e-4, channel_rating="1 A / 3 A", bus_voltage_v=48, motor_voltage_v=48,
        travel_mm=25)
    p["Stepper 1.8 deg, direct micro-stepping"] = MotorSpec(
        name="Stepper direct micro-stepping", motor_type=STEPPER_MICRO, step_angle_deg=1.8,
        holding_torque_nm=0.5, peak_current_a=2.0, cont_current_a=1.4, current_is_rms=False,
        resistance_ohm=2.2, inductance_mh=5.5, rotor_inertia=5.7e-6, load_inertia=5.0e-6,
        payload_mass_kg=2.0, lead_mm=1.0, counts_per_rev=102400, encoder_type="none",
        coulomb_torque=0.02, viscous=5e-5, channel_rating="1 A / 3 A", travel_mm=25)
    p["DC brush motor with gearbox"] = MotorSpec(
        name="DC brush geared", motor_type=BRUSH, torque_constant=0.08, kt_is_per_rms_amp=False,
        peak_current_a=2.9, cont_current_a=0.75, current_is_rms=False, resistance_ohm=3.5,
        inductance_mh=1.2, rotor_inertia=8.0e-6, load_inertia=4.0e-5, gear_ratio=20.0,
        counts_per_rev=2000, coulomb_torque=0.01, viscous=6e-5, channel_rating="1 A / 3 A",
        bus_voltage_v=24, motor_voltage_v=24, travel_mm=20)
    p["Linear motor stage, 1 um scale"] = MotorSpec(
        name="Linear motor stage", motor_type=LINEAR, torque_constant=12.0,
        kt_is_per_rms_amp=True, peak_current_a=6.0, cont_current_a=2.0, resistance_ohm=2.5,
        inductance_mh=4.0, pole_pairs=1, rotor_inertia=1.2, payload_mass_kg=3.0,
        load_inertia=0.0, counts_per_rev=1.0e6, coulomb_torque=4.0, viscous=20.0,
        encoder_type="sincos", encoder_noise_counts=1.5, travel_mm=150)
    return p
