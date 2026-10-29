"""Auto-tune orchestration (runs in a worker thread, reports progress through callbacks).

Sequence
--------
1. Safety checks: axis enabled & closed loop, no faults, travel available.
2. Open-loop identification (optional, brushless/brush/stepper-with-encoder): +/- output pulse
   -> plant acceleration constant Ka and friction (``tuning.identify_plant``).
3. Model-based gain design for the requested bandwidth and damping.
4. Apply the gains (previous values are saved for one-click restore).
5. Verification step response; iterate: shrink bandwidth while overshoot or ringing is
   excessive, grow it while the response is sluggish and clean (bounded number of passes).

Nothing is written to the controller's flash (no ``save``) - the user decides.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..comm.base import PmacError
from ..comm.session import Session
from . import tuning
from .status_bits import parse_power_axis_status, parse_turbo_axis_status

Progress = Callable[[str], None]


@dataclass
class TuneResult:
    ok: bool = False
    message: str = ""
    plant: Optional[tuning.PlantModel] = None
    gains: Optional[tuning.Gains] = None
    original: Dict[str, Optional[str]] = field(default_factory=dict)
    applied: Dict[str, float] = field(default_factory=dict)
    metrics: Optional[tuning.StepMetrics] = None
    iterations: List[Dict[str, object]] = field(default_factory=list)
    step_data: Optional[Dict[str, List[float]]] = None
    open_loop_data: Optional[Dict[str, List[float]]] = None


class AutoTuner:
    def __init__(self, session: Session, axis: int, progress: Optional[Progress] = None):
        self.s = session
        self.axis = axis
        self.progress = progress or (lambda m: None)
        self.abort = False

    # ------------------------------------------------------------------ helpers
    @property
    def link(self):
        link = self.s.direct
        if link is None or not link.connected:
            raise PmacError("direct link required for tuning")
        return link

    def _status(self):
        link = self.link
        r = link.send("#%d?" % self.axis)
        if link.family == "power":
            return parse_power_axis_status(self.axis, r)
        return parse_turbo_axis_status(self.axis, r)

    def gain_names(self) -> List[str]:
        a = self.axis
        if self.link.family == "power":
            p = "Motor[%d].Servo." % a
            return [p + k for k in ("Kp", "Kvfb", "Kvff", "Kaff", "Ki", "Kfff", "SwZvInt")]
        return ["I%d%s" % (a, k) for k in ("30", "31", "32", "35", "33", "68", "34")]

    def read_gains(self) -> Dict[str, Optional[str]]:
        return self.link.read_values(self.gain_names())

    def apply(self, values: Dict[str, float]):
        link = self.link
        for k, v in values.items():
            link.write_value(k, v)
        self.progress("applied: " + ", ".join("%s=%.5g" % (k.split(".")[-1] if "." in k else k, v) for k, v in values.items()))

    def restore(self, original: Dict[str, Optional[str]]):
        link = self.link
        for k, v in original.items():
            if v is not None:
                link.send("%s=%s" % (k, v))
        self.progress("original gains restored")

    def servo_period_s(self) -> float:
        return (self.link.info.servo_period_ms or 0.5) / 1000.0

    def fatal_fe_limit(self) -> Optional[float]:
        """FatalFeLimit in motor units (Turbo Ixx11 is in 1/16 counts)."""
        if self.link.family == "power":
            return self.link.read_float("Motor[%d].FatalFeLimit" % self.axis)
        v = self.link.read_float("I%d11" % self.axis)
        return None if v is None else v / 16.0

    def check_step(self, step_size: float) -> Optional[str]:
        """A position step larger than the fatal following error limit kills the motor the
        instant it is commanded. Returns an explanation when the step is unsafe."""
        f = self.fatal_fe_limit()
        if f and f > 0 and abs(step_size) > 0.7 * f:
            return ("a %g unit step would instantly create a following error near or above the "
                    "fatal limit (%g units) and kill the motor - use a step below %g units, or "
                    "raise FatalFeLimit if the machine allows it" % (abs(step_size), f, 0.5 * f))
        return None

    def preflight(self, need_closed_loop: bool = True) -> Optional[str]:
        st = self._status()
        if st.amp_fault or st.fe_fatal or st.i2t_fault or st.enc_loss:
            return "axis has an active fault (%s) - fix it first" % st.summary
        if need_closed_loop and not (st.amp_enabled and st.closed_loop):
            # try to close the loop ourselves (a killed, fault-free axis)
            try:
                self.progress("axis %d not in closed loop - sending #%dj/" % (self.axis, self.axis))
                self.link.send("#%dj/" % self.axis)
                time.sleep(0.4)
                st = self._status()
            except PmacError as exc:
                return "could not enable axis: %s" % exc
            if not (st.amp_enabled and st.closed_loop):
                return "axis must be enabled in closed loop (#%dj/) - it refused: %s" % (self.axis, st.summary)
        if st.plus_limit or st.minus_limit:
            return "axis is on a hardware limit"
        if not st.phase_found and self.link.family == "power":
            return "axis is not phased (PhaseFound=0)"
        return None

    # ------------------------------------------------------------------ main
    def run(self, step_size: float, bandwidth_hz: float = 20.0, zeta: float = 0.8,
            integral_ratio: float = 0.2, open_loop_pct: float = 5.0, open_loop_pulse_s: float = 0.1,
            do_open_loop: bool = True, iterations: int = 3, keep: bool = True,
            max_overshoot_pct: float = 10.0) -> TuneResult:
        res = TuneResult()
        link = self.link
        fam = link.family
        T = self.servo_period_s()
        try:
            res.original = self.read_gains()
            self.progress("saved current gains: " + ", ".join("%s=%s" % (k.split(".")[-1], v) for k, v in res.original.items()))
            err = self.preflight(need_closed_loop=True)
            if err:
                res.message = err
                return res
            err = self.check_step(step_size)
            if err:
                res.message = err
                return res
            plant = None
            if do_open_loop:
                self.progress("open-loop test: +/-%.1f%% output for %.0f ms each" % (open_loop_pct, open_loop_pulse_s * 1e3))
                data = self.s.acquire_open_loop(self.axis, open_loop_pct, open_loop_pulse_s, self.progress)
                res.open_loop_data = data
                if fam == "power":
                    plant = tuning.identify_plant(data["t"], data["vel"], data["out"], T, open_loop_pulse_s)
                else:
                    # Turbo #V reports counts/servo-cycle *32 ... normalise roughly; output unknown -> use % of Ixx69
                    i69 = link.read_float("I%d69" % self.axis) or 20480.0
                    out = [open_loop_pct / 100.0 * i69 * (1 if k < len(data["t"]) / 2 else -1) for k in range(len(data["t"]))]
                    vel = [v / 32.0 for v in data["vel"]]
                    plant = tuning.identify_plant(data["t"], vel, out, T, open_loop_pulse_s)
                self.progress(plant.message)
                # re-close the loop (acquire_open_loop kills the motor)
                link.send("#%dj/" % self.axis)
                time.sleep(0.3)
                if not plant.ok:
                    res.plant = plant
                    res.message = "open-loop test failed: " + plant.message
                    return res
            if plant is None:
                # estimate Ka from the existing gains by assuming they were sane (fn 15 Hz)
                g0 = res.original
                kp0 = _f(g0.get(self.gain_names()[0])) or 1.0
                if fam == "turbo":
                    kp0 = tuning.units.turbo_natural_kp(kp0)
                wn0 = 2 * 3.14159 * 15.0 * T
                plant = tuning.PlantModel(ok=True, ka=wn0 * wn0 / max(kp0, 1e-9), servo_period_s=T,
                                          message="Ka estimated from existing gains (no open-loop test)")
                self.progress(plant.message)
            res.plant = plant
            bw = bandwidth_hz
            int_ratio = integral_ratio
            best = None
            for it in range(max(1, iterations)):
                if self.abort:
                    res.message = "aborted"
                    break
                gains = tuning.design_gains(plant, bw, zeta, int_ratio, feedforward=True)
                i08 = link.read_float("I%d08" % self.axis) or 96.0 if fam == "turbo" else 96.0
                i09 = link.read_float("I%d09" % self.axis) or 96.0 if fam == "turbo" else 96.0
                vals = gains.as_power(self.axis) if fam == "power" else gains.as_turbo(self.axis, i08, i09)
                # the verification is a pure position step: feedforward terms would only inject a
                # one-cycle spike, so the PD(I) part is tested alone and feedforward applied at the end
                pd_only = tuning.Gains(kp=gains.kp, kvfb=gains.kvfb, kvff=0.0, kaff=0.0, ki=gains.ki,
                                       kfff=gains.kfff, swzvint=gains.swzvint)
                test_vals = pd_only.as_power(self.axis) if fam == "power" else pd_only.as_turbo(self.axis, i08, i09)
                self.progress("iteration %d: bandwidth %.1f Hz, zeta %.2f, integral ratio %.2f" % (it + 1, bw, zeta, int_ratio))
                self.apply(test_vals)
                time.sleep(0.2)
                err = self.preflight(need_closed_loop=True)
                if err:
                    self.progress("axis not ready after applying gains: %s - restoring" % err)
                    self.restore(res.original)
                    res.message = err
                    return res
                data = self.s.acquire_step(self.axis, step_size, duration_s=0.6, settle_s=0.2, progress=self.progress)
                maxdac = link.read_float("Motor[%d].MaxDac" % self.axis) if fam == "power" else link.read_float("I%d69" % self.axis)
                m = tuning.analyze_step(data["t"], data["des"], data["act"], data.get("out"), maxdac)
                # move back for the next iteration
                try:
                    link.send("#%dj:%g" % (self.axis, -step_size))
                except PmacError:
                    pass
                time.sleep(0.4)
                res.iterations.append({"bandwidth_hz": bw, "gains": vals, "metrics": m})
                self.progress("  -> %s: overshoot %s, rise %s ms, settle %s ms%s" % (
                    m.verdict, _fmt(m.overshoot_pct), _fmt(m.rise_time_ms), _fmt(m.settling_time_ms),
                    (", ringing %.0f Hz" % m.ring_freq_hz) if m.ring_freq_hz else ""))
                score = _score(m, max_overshoot_pct)
                if best is None or score > best[0]:
                    best = (score, gains, vals, m, data, bw)
                if not m.ok:
                    bw *= 0.6
                    continue
                ringing = bool(m.ring_freq_hz and m.ring_amplitude and m.ring_amplitude > 0.02 * abs(step_size))
                if (m.overshoot_pct or 0) > max_overshoot_pct and int_ratio > 0.04 and not ringing:
                    # overshoot on a pure step is usually integrator action -> soften the integrator first
                    int_ratio *= 0.5
                elif (m.overshoot_pct or 0) > max_overshoot_pct or ringing or (m.saturation_pct or 0) > 10:
                    bw *= 0.7
                elif (m.rise_time_ms or 0) > 80 and (m.overshoot_pct or 0) < max_overshoot_pct / 3:
                    bw *= 1.25
                else:
                    break
            if best is None:
                res.message = "no successful iteration"
                return res
            _, gains, vals, m, data, bw = best
            self.progress("applying best iteration (%.1f Hz) including feedforward (Kvff=Kvfb, Kaff=1/Ka)" % bw)
            self.apply(vals)
            res.gains, res.applied, res.metrics, res.step_data = gains, vals, m, data
            if not keep:
                self.restore(res.original)
            res.ok = True
            res.message = "done: %s (bandwidth %.1f Hz)" % (m.verdict, bw)
            return res
        except PmacError as exc:
            res.message = "controller error: %s" % exc
            try:
                if res.original:
                    self.restore(res.original)
            except Exception:
                pass
            return res


def _score(m: tuning.StepMetrics, max_os: float) -> float:
    if not m.ok:
        return -1e9
    s = 100.0
    s -= max(0.0, (m.overshoot_pct or 0) - max_os) * 12   # exceeding the overshoot budget is the worst
    s -= (m.rise_time_ms or 200) * 0.2
    s -= (m.settling_time_ms or 1000) * 0.03
    s -= (m.saturation_pct or 0) * 2
    if m.ring_freq_hz and m.ring_amplitude:
        s -= 20
    return s


def _fmt(v) -> str:
    return "-" if v is None else "%.1f" % v


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
