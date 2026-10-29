"""Servo tuning mathematics.

* :func:`analyze_step` - metrics for a closed-loop position step (rise time, overshoot,
  settling, steady-state error, ringing frequency, output saturation) plus plain-language
  recommendations.
* :func:`identify_plant` - estimate the plant acceleration constant from an open-loop
  +/- output pulse (the same test the Power PMAC IDE "open loop test" performs).
* :func:`design_gains` - model-based PID design for the Power PMAC servo law.
* :func:`gains_to_turbo` / :func:`gains_from_turbo` - approximate conversion to Ixx30-35.

Servo law (Power PMAC, Motor[x].Servo.*)::

    Out = Kp*Pe + Kvff*DesVel - Kvfb*ActVel + Kaff*DesAcc + Ki*Sum(Pe) + Kfff*sign(DesVel)

with velocities in motor-units per servo cycle and Out in 16-bit units. For a rigid inertia
with acceleration constant Ka (units/cycle^2 per Out unit) the closed loop is

    s^2 + Ka*Kvfb*s + Ka*Kp = 0   ->   wn = sqrt(Ka*Kp),  zeta = Ka*Kvfb / (2*wn)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import units


@dataclass
class StepMetrics:
    ok: bool = False
    message: str = ""
    step_size: float = 0.0
    rise_time_ms: Optional[float] = None       # 10 % -> 90 %
    overshoot_pct: Optional[float] = None
    settling_time_ms: Optional[float] = None   # within +/-2 % of step (or in-position band)
    peak_error: Optional[float] = None
    steady_state_error: Optional[float] = None
    noise_rms: Optional[float] = None          # error RMS once settled
    ring_freq_hz: Optional[float] = None
    ring_amplitude: Optional[float] = None
    damping_estimate: Optional[float] = None
    saturation_pct: Optional[float] = None     # fraction of samples with |out| >= 98 % MaxDac
    verdict: str = ""
    recommendations: List[str] = field(default_factory=list)
    t_step: float = 0.0


def _first_crossing(t: np.ndarray, y: np.ndarray, level: float, rising: bool) -> Optional[float]:
    if rising:
        idx = np.where(y >= level)[0]
    else:
        idx = np.where(y <= level)[0]
    if len(idx) == 0:
        return None
    i = idx[0]
    if i == 0:
        return float(t[0])
    # linear interpolation
    y0, y1 = y[i - 1], y[i]
    if y1 == y0:
        return float(t[i])
    f = (level - y0) / (y1 - y0)
    return float(t[i - 1] + f * (t[i] - t[i - 1]))


def analyze_step(t: Sequence[float], des: Sequence[float], act: Sequence[float],
                 out: Optional[Sequence[float]] = None, max_dac: Optional[float] = None,
                 in_pos_band: Optional[float] = None) -> StepMetrics:
    m = StepMetrics()
    t = np.asarray(t, dtype=float)
    des = np.asarray(des, dtype=float)
    act = np.asarray(act, dtype=float)
    if len(t) < 10 or len(des) != len(t) or len(act) != len(t):
        m.message = "not enough samples"
        return m
    d_des = np.diff(des)
    final = float(np.median(des[-max(3, len(des) // 10):]))
    if np.max(np.abs(d_des)) < 1e-9:
        # the capture may have started after the (instantaneous) command step: fall back to
        # treating the first actual position as the start of the step
        if abs(des[0] - act[0]) > max(1e-6, 0.01 * abs(des[0] - np.median(act[-5:])) if len(act) >= 5 else 0):
            i_step = 1
            start = float(act[0])
        else:
            m.message = ("commanded position never changed - the move did not execute "
                         "(killed? limit? not closed loop?)")
            return m
    else:
        # the command may be a true step or a short ramp, so the first sample is the best
        # estimate of the pre-move level; the step starts where the command first moves
        start = float(des[0])
        moved = np.where(np.abs(des - start) > 0.02 * max(abs(final - start), 1e-9))[0]
        i_step = max(1, int(moved[0]) if len(moved) else int(np.argmax(np.abs(d_des))) + 1)
    size = final - start
    if abs(size) < 1e-9:
        m.message = "step size is zero"
        return m
    m.step_size = size
    m.t_step = float(t[i_step - 1])
    sign = 1.0 if size > 0 else -1.0
    y = (act - start) * sign          # normalised: 0 -> |size|
    tt = t - m.t_step
    post = tt >= 0
    yp, tp = y[post], tt[post]
    err = (des - act)
    # rise time
    t10 = _first_crossing(tp, yp, 0.1 * abs(size), True)
    t90 = _first_crossing(tp, yp, 0.9 * abs(size), True)
    if t10 is not None and t90 is not None and t90 >= t10:
        m.rise_time_ms = (t90 - t10) * 1e3
    # overshoot
    peak = float(np.max(yp)) if len(yp) else abs(size)
    m.overshoot_pct = max(0.0, (peak - abs(size)) / abs(size) * 100.0)
    m.peak_error = float(np.max(np.abs(err[post]))) if post.any() else None
    # settling: last time outside band
    band = 0.02 * abs(size)
    if in_pos_band is not None and in_pos_band > 0:
        band = max(band, in_pos_band)
    outside = np.where(np.abs(yp - abs(size)) > band)[0]
    if len(outside) == 0:
        m.settling_time_ms = 0.0
    elif outside[-1] < len(tp) - 1:
        m.settling_time_ms = float(tp[outside[-1] + 1]) * 1e3
    else:
        m.settling_time_ms = None  # never settled
    # steady state
    tail = max(5, len(yp) // 10)
    m.steady_state_error = float(np.mean((abs(size) - yp[-tail:]))) * 1.0
    m.noise_rms = float(np.std(yp[-tail:]))
    # ringing: FFT of error after the step, excluding the first 10 % of samples
    try:
        e = err[post]
        n0 = max(3, len(e) // 10)
        e2 = e[n0:] - np.mean(e[n0:])
        if len(e2) > 16:
            dt = float(np.median(np.diff(tp))) if len(tp) > 1 else 0.001
            w = np.hanning(len(e2))
            spec = np.abs(np.fft.rfft(e2 * w))
            freqs = np.fft.rfftfreq(len(e2), dt)
            spec[0] = 0.0
            k = int(np.argmax(spec))
            if freqs[k] > 0 and spec[k] > 0:
                amp = 2 * spec[k] / np.sum(w)
                if amp > max(0.005 * abs(size), 2 * (m.noise_rms or 0.0) * 0.5):
                    m.ring_freq_hz = float(freqs[k])
                    m.ring_amplitude = float(amp)
    except Exception:
        pass
    # damping estimate from overshoot (2nd order approximation)
    if m.overshoot_pct is not None and m.overshoot_pct > 0.1:
        os_ = m.overshoot_pct / 100.0
        ln = math.log(os_)
        m.damping_estimate = -ln / math.sqrt(math.pi ** 2 + ln ** 2)
    elif m.overshoot_pct is not None:
        m.damping_estimate = 1.0
    # saturation
    if out is not None and len(out) == len(t) and max_dac:
        o = np.abs(np.asarray(out, dtype=float)[post])
        if len(o):
            m.saturation_pct = float(np.mean(o >= 0.98 * max_dac) * 100.0)
    m.ok = True
    _verdict(m)
    return m


def _verdict(m: StepMetrics):
    rec: List[str] = []
    problems = 0
    if m.saturation_pct is not None and m.saturation_pct > 5:
        problems += 1
        rec.append("The servo output saturated at MaxDac for %.0f%% of the step: the step is too large for the "
                   "available current, or MaxDac is set too low. Use a smaller step or raise MaxDac "
                   "(within the motor/drive peak rating)." % m.saturation_pct)
    if m.overshoot_pct is not None:
        if m.overshoot_pct > 15:
            problems += 1
            rec.append("Overshoot %.0f%% is excessive (under-damped). Increase Servo.Kvfb (derivative), reduce "
                       "Servo.Kp or Servo.Ki; check that Kvff ~ Kvfb." % m.overshoot_pct)
        elif m.overshoot_pct > 5:
            rec.append("Overshoot %.0f%% - acceptable for fast positioning but could be reduced with a little more "
                       "Kvfb." % m.overshoot_pct)
    if m.ring_freq_hz is not None and m.ring_amplitude is not None:
        if m.ring_freq_hz > 60:
            problems += 1
            rec.append("Ringing at %.0f Hz (amplitude %.2g) suggests a mechanical resonance or too much derivative "
                       "gain. Reduce Kvfb slightly or add a notch/low-pass filter (Servo.Kc*/Kd*) near %.0f Hz."
                       % (m.ring_freq_hz, m.ring_amplitude, m.ring_freq_hz))
        else:
            rec.append("Low-frequency oscillation at %.1f Hz - usually the position loop itself (Kp too high for "
                       "the damping) or integrator wind-up. Reduce Kp or Ki." % m.ring_freq_hz)
    if m.settling_time_ms is None:
        problems += 1
        rec.append("The axis never settled inside the 2%% band during the capture: look for a limit cycle "
                   "(friction + integrator), noisy feedback or too little gain.")
    if m.steady_state_error is not None and abs(m.steady_state_error) > max(2.0, 0.01 * abs(m.step_size)):
        problems += 1
        rec.append("Steady-state error %.3g units remains. Add integral gain (Servo.Ki, with SwZvInt=1 so it only "
                   "acts at rest) or reduce friction/deadband." % m.steady_state_error)
    if m.rise_time_ms is not None and m.rise_time_ms > 150:
        problems += 1
        rec.append("Rise time %.0f ms is slow. Increase Kp (and Kvfb proportionally to keep damping) or check for "
                   "output saturation / excessive friction." % m.rise_time_ms)
    if m.noise_rms is not None and m.step_size and m.noise_rms > 0.02 * abs(m.step_size):
        rec.append("Position noise at rest (RMS %.3g) is high - excessive Kvfb amplifying encoder noise, or an "
                   "electrical/encoder problem." % m.noise_rms)
    if problems == 0:
        m.verdict = "Good response"
        if not rec:
            rec.append("Response looks healthy: well damped, settles promptly, no saturation.")
    elif problems == 1:
        m.verdict = "Needs attention"
    else:
        m.verdict = "Poorly tuned"
    m.recommendations = rec


# ======================================================================================
# Plant identification from an open-loop pulse
# ======================================================================================
@dataclass
class PlantModel:
    ok: bool = False
    message: str = ""
    ka: float = 0.0                     # motor-units / cycle^2 per 16-bit output unit
    friction_out: float = 0.0           # Coulomb friction expressed in output units
    direction_ok: bool = True           # positive command -> positive velocity
    vel_pos: float = 0.0                # peak velocity reached for +pulse (units/cycle)
    vel_neg: float = 0.0
    out_units: float = 0.0              # magnitude of the pulse in 16-bit units
    servo_period_s: float = 0.0005


def identify_plant(t: Sequence[float], vel: Sequence[float], out: Sequence[float],
                   servo_period_s: float, pulse_s: float) -> PlantModel:
    """Fit the velocity slope during the +pulse and -pulse of an open-loop test.

    ``vel`` must be in motor units per servo cycle (Power PMAC ActVel). The Coulomb friction
    cancels when the two slopes are averaged; the difference gives the friction level.
    """
    pm = PlantModel(servo_period_s=servo_period_s)
    t = np.asarray(t, dtype=float)
    v = np.asarray(vel, dtype=float)
    o = np.asarray(out, dtype=float)
    if len(t) < 8:
        pm.message = "not enough samples"
        return pm
    pos = o > 0.5 * np.max(np.abs(o)) if np.max(np.abs(o)) > 0 else np.zeros_like(o, bool)
    neg = o < -0.5 * np.max(np.abs(o)) if np.max(np.abs(o)) > 0 else np.zeros_like(o, bool)
    if pos.sum() < 4 or neg.sum() < 4:
        pm.message = "could not see both the positive and the negative part of the pulse in the data"
        return pm
    mag = float(np.median(np.abs(o[pos | neg])))
    pm.out_units = mag

    def slope(mask):
        tt = t[mask]
        vv = v[mask]
        # use the middle 80 % to avoid transitions
        k0, k1 = int(len(tt) * 0.1), max(int(len(tt) * 0.9), int(len(tt) * 0.1) + 2)
        tt, vv = tt[k0:k1], vv[k0:k1]
        if len(tt) < 2:
            return 0.0
        a = np.polyfit(tt, vv, 1)[0]            # units/cycle per second
        return a * servo_period_s                # units/cycle per cycle

    s_pos = slope(pos)
    s_neg = slope(neg)
    pm.vel_pos = float(np.max(v[pos]))
    pm.vel_neg = float(np.min(v[neg]))
    # acceleration constant: average magnitude of the two slopes
    ka = (abs(s_pos) + abs(s_neg)) / 2.0 / mag if mag > 0 else 0.0
    pm.direction_ok = s_pos > 0 and s_neg < 0
    if not pm.direction_ok and s_pos < 0 and s_neg > 0:
        pm.message = ("INVERTED response: positive output produced negative velocity. Encoder decode direction "
                      "(EncCtrl 3 <-> 7) or commutation direction (PwmSf/PhaseOffset sign) is reversed. "
                      "Do NOT close the loop until this is fixed.")
    if ka <= 0 or not np.isfinite(ka):
        pm.message = pm.message or "no acceleration measured - motor did not move (killed, not phased, brake engaged, output too small?)"
        return pm
    pm.ka = ka
    # friction: with friction f (output units) the slopes are ka*(mag-f) and -ka*(mag-f); a
    # velocity-dependent asymmetry shows as |s_pos| != |s_neg| - use the smaller one.
    sm = min(abs(s_pos), abs(s_neg))
    pm.friction_out = max(0.0, mag - sm / ka) if ka > 0 else 0.0
    pm.ok = pm.direction_ok
    if pm.ok and not pm.message:
        pm.message = "Plant identified: Ka = %.3g units/cycle^2 per output unit, friction ~ %.0f output units" % (
            pm.ka, pm.friction_out)
    return pm


# ======================================================================================
# Gain design
# ======================================================================================
@dataclass
class Gains:
    kp: float = 0.0
    kvfb: float = 0.0
    kvff: float = 0.0
    kaff: float = 0.0
    ki: float = 0.0
    kfff: float = 0.0
    swzvint: int = 1
    notes: List[str] = field(default_factory=list)

    def as_power(self, axis: int) -> Dict[str, float]:
        p = "Motor[%d].Servo." % axis
        return {p + "Kp": self.kp, p + "Kvfb": self.kvfb, p + "Kvff": self.kvff, p + "Kaff": self.kaff,
                p + "Ki": self.ki, p + "Kfff": self.kfff, p + "SwZvInt": self.swzvint}

    def as_turbo(self, axis: int, ixx08: float = 96.0, ixx09: float = 96.0) -> Dict[str, float]:
        i30 = units.turbo_ixx30_from_natural(self.kp, ixx08)
        i31 = units.turbo_ixx31_from_natural(self.kvfb, i30, ixx09)
        i32 = units.turbo_ixx31_from_natural(self.kvff, i30, ixx09)
        i35 = units.turbo_ixx31_from_natural(self.kaff, i30, ixx09)
        i33 = self.ki / (i30 * ixx08 / 16384.0) * 8388608.0 if i30 else 0.0
        i68 = self.kfff * 16384.0 / ixx08
        return {"I%d30" % axis: i30, "I%d31" % axis: i31, "I%d32" % axis: i32, "I%d33" % axis: i33,
                "I%d34" % axis: self.swzvint, "I%d35" % axis: i35, "I%d68" % axis: i68}


def design_gains(plant: PlantModel, bandwidth_hz: float, zeta: float = 0.8,
                 integral_ratio: float = 0.2, feedforward: bool = True,
                 friction_ff: bool = False) -> Gains:
    """Model based design for a rigid plant (double integrator).

    bandwidth_hz: natural frequency of the dominant pole pair.
    zeta: damping ratio (0.7 - 1.0 typical; 1.0 = critically damped, no overshoot).
    integral_ratio: integrator corner as a fraction of wn (0 = no integrator).
    """
    g = Gains()
    T = plant.servo_period_s
    wn = 2 * math.pi * bandwidth_hz * T   # rad / cycle
    ka = plant.ka
    if ka <= 0:
        g.notes.append("invalid plant")
        return g
    g.kp = wn * wn / ka
    g.kvfb = 2 * zeta * wn / ka
    g.kvff = g.kvfb if feedforward else 0.0
    g.kaff = (1.0 / ka) if feedforward else 0.0
    g.ki = g.kp * wn * integral_ratio
    g.kfff = plant.friction_out * 0.8 if friction_ff else 0.0
    g.swzvint = 1 if integral_ratio > 0 else 0
    # sanity on the sampled-data limit: wn*T should stay well below ~0.3 rad/cycle
    if wn > 0.35:
        g.notes.append("Requested bandwidth is high relative to the servo rate (wn*T = %.2f rad/cycle); "
                       "expect reduced phase margin - consider a lower bandwidth." % wn)
    g.notes.append("Design: fn = %.1f Hz, zeta = %.2f, Ka = %.3g -> Kp = %.4g, Kvfb = %.4g" % (
        bandwidth_hz, zeta, ka, g.kp, g.kvfb))
    return g


def analyze_gains(kp: float, kvfb: float, ka: Optional[float], servo_period_s: float) -> Dict[str, float]:
    """Closed-loop natural frequency / damping implied by the current gains (needs Ka)."""
    out: Dict[str, float] = {}
    if ka and ka > 0 and kp > 0:
        wn = math.sqrt(ka * kp)
        out["fn_hz"] = wn / (2 * math.pi * servo_period_s)
        out["zeta"] = ka * kvfb / (2 * wn) if wn > 0 else 0.0
    return out


def scale_gains(g: Gains, factor: float) -> Gains:
    """Scale the bandwidth by ``factor`` keeping damping: Kp ~ wn^2, Kvfb ~ wn, Ki ~ wn^3."""
    return Gains(kp=g.kp * factor ** 2, kvfb=g.kvfb * factor, kvff=g.kvff * factor, kaff=g.kaff,
                 ki=g.ki * factor ** 3, kfff=g.kfff, swzvint=g.swzvint, notes=list(g.notes))


def gains_from_turbo(i30, i31, i32, i33, i35, i68=0.0, ixx08=96.0, ixx09=96.0) -> Gains:
    kp = units.turbo_natural_kp(i30, ixx08)
    return Gains(kp=kp, kvfb=units.turbo_natural_kvfb(i30, i31, ixx09),
                 kvff=units.turbo_natural_kvfb(i30, i32, ixx09),
                 kaff=units.turbo_natural_kvfb(i30, i35, ixx09),
                 ki=kp * i33 / 8388608.0, kfff=i68 * ixx08 / 16384.0)


def open_loop_velocity_profile_ok(vel: Sequence[float], out: Sequence[float]) -> Dict[str, object]:
    """Quick checks on an open loop test: sawtooth sign and symmetry."""
    v = np.asarray(vel, dtype=float)
    o = np.asarray(out, dtype=float)
    res: Dict[str, object] = {}
    if len(v) < 4:
        return {"ok": False, "message": "no data"}
    vmax, vmin = float(np.max(v)), float(np.min(v))
    res["vmax"], res["vmin"] = vmax, vmin
    moved = max(abs(vmax), abs(vmin)) > 1e-6
    res["moved"] = moved
    if not moved:
        res["ok"] = False
        res["message"] = "Motor did not move: killed, not phased, brake engaged, bus power off or output too small."
        return res
    pos_phase = o > 0
    neg_phase = o < 0
    corr = float(np.corrcoef(np.sign(o[pos_phase | neg_phase]), v[pos_phase | neg_phase])[0, 1]) \
        if (pos_phase | neg_phase).sum() > 3 else 0.0
    res["correlation"] = corr
    if corr < -0.3:
        res["ok"] = False
        res["message"] = ("Inverted saw-tooth: velocity opposes the command. Encoder direction (EncCtrl 7<->3) "
                          "or commutation direction (swap two motor leads / negate PwmSf & PhaseOffset) is wrong.")
    elif corr < 0.3:
        res["ok"] = False
        res["message"] = ("Velocity does not follow the command sign - motor may be cogging/locking to a phase. "
                          "Check PhasePosSf (commutation cycle size) and re-phase.")
    else:
        res["ok"] = True
        res["message"] = "Open loop test OK: positive output gives positive velocity (proper saw-tooth)."
    return res
