"""Diagnostic rule engine: turns a controller snapshot (+ optional variable dumps and EPICS
motor-record data) into a ranked list of :class:`Finding` objects, each with probable causes
and corrective actions.

References in findings point to the Power Brick LV ARM User Manual (MN-000143) sections.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import units
from .models import AxisSnapshot, ControllerSnapshot, Finding, VariableSet
from .status_bits import FAULT, INFO, OK, WARN, decode_msta

MANUAL = "Power Brick LV ARM User Manual"


class Ctx:
    def __init__(self, snap: ControllerSnapshot, dumps: Dict[int, VariableSet],
                 gdump: Optional[VariableSet], motor_specs: Dict[int, dict]):
        self.snap = snap
        self.dumps = dumps
        self.gdump = gdump
        self.specs = motor_specs  # per-axis user supplied motor data (peak/cont current, channel rating...)
        self.findings: List[Finding] = []

    @property
    def power(self) -> bool:
        return self.snap.family == "power"

    def var(self, axis: int, power_elem: str, turbo_suffix: str = "") -> Optional[float]:
        d = self.dumps.get(axis)
        if d is None:
            return None
        if self.power:
            return d.f("Motor[%d].%s" % (axis, power_elem))
        if turbo_suffix:
            return d.f("I%d%s" % (axis, turbo_suffix))
        return None

    def svar(self, axis: int, power_elem: str) -> Optional[str]:
        d = self.dumps.get(axis)
        if d is None:
            return None
        return d.s("Motor[%d].%s" % (axis, power_elem))

    def chan(self, axis: int, elem: str) -> Optional[float]:
        d = self.dumps.get(axis)
        if d is None or not self.power:
            return None
        i, j = (axis - 1) // 4, (axis - 1) % 4
        return d.f("PowerBrick[%d].Chan[%d].%s" % (i, j, elem))

    def brick(self, axis: int, elem: str) -> Optional[float]:
        d = self.dumps.get(axis)
        if d is None or not self.power:
            return None
        return d.f("BrickLV.Chan[%d].%s" % (axis - 1, elem))

    def gvar(self, name: str) -> Optional[float]:
        v = self.snap.sysf(name)
        if v is not None:
            return v
        if self.gdump is not None:
            v = self.gdump.f(name)
            if v is not None:
                return v
        for d in self.dumps.values():
            v = d.f(name)
            if v is not None:
                return v
        return None

    def add(self, f: Finding):
        self.findings.append(f)


Rule = Callable[[Ctx], None]
GLOBAL_RULES: List[Rule] = []
AXIS_RULES: List[Callable[[Ctx, AxisSnapshot], None]] = []


def global_rule(fn: Rule) -> Rule:
    GLOBAL_RULES.append(fn)
    return fn


def axis_rule(fn):
    AXIS_RULES.append(fn)
    return fn


def evaluate(snap: ControllerSnapshot, dumps: Optional[Dict[int, VariableSet]] = None,
             gdump: Optional[VariableSet] = None, motor_specs: Optional[Dict[int, dict]] = None,
             axes: Optional[List[int]] = None) -> List[Finding]:
    ctx = Ctx(snap, dumps or {}, gdump, motor_specs or {})
    for r in GLOBAL_RULES:
        try:
            r(ctx)
        except Exception as exc:  # a broken rule must never hide the others
            ctx.add(Finding("rule-error", INFO, "Rule %s failed" % r.__name__, str(exc)))
    for n, ax in sorted(snap.axes.items()):
        if axes is not None and n not in axes:
            continue
        for r in AXIS_RULES:
            try:
                r(ctx, ax)
            except Exception as exc:
                ctx.add(Finding("rule-error", INFO, "Rule %s failed on axis %d" % (r.__name__, n), str(exc), axis=n))
    ctx.findings.sort(key=lambda f: (f.rank, f.axis or 0))
    return ctx.findings


# ======================================================================================
# GLOBAL RULES
# ======================================================================================
@global_rule
def r_connection(ctx: Ctx):
    s = ctx.snap
    if not s.direct_ok and not s.epics_ok:
        ctx.add(Finding("conn", FAULT, "No connection to the controller",
                        "Neither the EPICS IOC nor a direct link is delivering data.",
                        causes=["IOC not running or wrong PV prefix", "EPICS_CA_ADDR_LIST not set for this subnet",
                                "Controller powered off / network unreachable", "Wrong SSH credentials (Power PMAC default root/deltatau)"],
                        actions=["Check the prefix in Settings (e.g. BL99P-MO-BRICK-01)", "Try `caget <prefix>:PMACTYPE` from this PC",
                                 "Ping the controller; for Power PMAC try an SSH login"]))
    elif not s.direct_ok:
        ctx.add(Finding("conn-direct", INFO, "EPICS only - deep diagnostics limited",
                        "Variable dumps, tuning tests and current-loop checks need a direct link (SSH for Power PMAC, "
                        "Ethernet for Turbo). Status, positions, following errors and motor records are available.",
                        actions=["Add a direct link in Settings to enable the Variables and Tuning pages"]))
    for e in s.errors:
        ctx.add(Finding("comm-error", WARN, "Communication error", e))


@global_rule
def r_global_status(ctx: Ctx):
    s = ctx.snap
    for b in s.global_bits:
        if b.value and b.severity in (FAULT, WARN):
            sev = b.severity
            causes, actions = [], []
            if "WDT" in b.name or "Watchdog" in b.name:
                causes = ["CPU starved by a PLC/C program infinite loop", "Servo/phase clock set too fast for the CPU",
                          "Logic power sag below 12 V (hard watchdog)"]
                actions = ["Check foreground CPU usage (IDE Task Manager / Sys.FgCpuUsage)", "Raise Sys.RtIntPeriod or lower servo frequency",
                           "Power-cycle the controller if the WD LED is red (hard watchdog)"]
            elif b.name in ("ProjectLoadErr", "ConfigLoadErr", "FileConfigErr", "BufSizeErr", "FlashSizeErr"):
                causes = ["Saved project/configuration incompatible with firmware", "Corrupt flash after a power loss during save"]
                actions = ["Re-download the project from the IDE and SAVE", "If persistent: $$$*** (factory reset) then reload"]
            elif b.name == "HWChangeErr":
                causes = ["A Gate3/accessory card was added or removed", "ACC card failed at power-up"]
                actions = ["Verify hardware, then SAVE to accept the new configuration"]
            elif b.name == "NoClocks":
                causes = ["No servo/phase clock source: Gate3 clock settings lost", "Faulty Gate3 ASIC"]
                actions = ["Check Sys.WpKey/Gate3[i].PhaseFreq, ServoClockDiv; reload System Gates settings"]
            elif b.name == "AbortAll":
                causes = ["+24 V abort input (X18) not present", "E-stop chain open"]
                actions = ["Check the abort input wiring / Sys.pAbortAll"]
            elif b.name == "Default":
                causes = ["Controller booted to factory defaults ($$$*** or config load failure)"]
                actions = ["Reload the project configuration"]
            ctx.add(Finding("gstat-" + b.name, sev, "Controller status: %s" % b.name, b.description,
                            causes=causes, actions=actions, evidence={"global_word": "$%08X" % s.global_word},
                            reference="%s - Watchdog Faults (p.232)" % MANUAL if "WDT" in b.name else ""))
    wdt = s.sysf("Sys.WDTFault")
    if wdt and wdt > 0 and not any(f.rule_id.startswith("gstat-WDT") for f in ctx.findings):
        ctx.add(Finding("wdt", FAULT, "Soft watchdog fault (Sys.WDTFault=%d)" % wdt,
                        "%s watchdog could not be reset - the controller has halted servo tasks." % ("Foreground" if wdt == 1 else "Background"),
                        causes=["CPU overload (PLC/C loops, too high servo frequency)"],
                        actions=["Reduce CPU load, then $$$ to reset", "See manual 'Watchdog Faults'"],
                        reference="%s p.232" % MANUAL))


@global_rule
def r_power_brick_global(ctx: Ctx):
    if not ctx.power:
        return
    s = ctx.snap
    mon = s.sysf("BrickLV.Monitor")
    if mon is not None and mon <= 0:
        ctx.add(Finding("bricklv-monitor", WARN if mon == 0 else FAULT,
                        "Power Brick LV amplifier monitoring is %s (BrickLV.Monitor=%g)" % ("off" if mon == 0 else "in error", mon),
                        "BrickLV.* status elements (bus voltage, over-temp, over-current, I2T) are not being updated. "
                        "Monitoring stops after every BrickLV.Reset/Config and must be restarted.",
                        causes=["Power-on PLC never set BrickLV.Monitor=1", "Packed ADC data (PackInData>0) -> error -6", "Incompatible/undetected amplifier hardware (-3/-4/-7)"],
                        actions=["Set BrickLV.Monitor=1 (after Sys.WDTReset bump as in the manual PLC)", "Check PowerBrick[i].Chan[j].PackInData/PackOutData = 0"],
                        reference="%s - BrickLV.Monitor (p.237)" % MANUAL))
    if s.sysf("BrickLV.BusUnderVoltage") == 1:
        ctx.add(Finding("bus-uv", FAULT, "DC bus under-voltage (< 12 V)",
                        "The main DC bus (J1) is missing or below 12 V. Motors can be enabled but produce no torque -> "
                        "huge following errors or 'motor does nothing'.",
                        causes=["Main bus supply off / STO active with dynamic braking", "Blown bus fuse or loose J1 connector",
                                "Power-on sequence: logic up before bus"],
                        actions=["Measure the DC bus at J1", "Check the STO (A11) inputs and the 7-segment display code"],
                        reference="%s - Main Bus Power Supply (p.33), STO (p.29)" % MANUAL))
    if s.sysf("BrickLV.BusOverVoltage") == 1:
        ctx.add(Finding("bus-ov", FAULT, "DC bus over-voltage (> 80 V)",
                        "All channels fault while the bus is above 80 V.",
                        causes=["Regenerative energy from decelerating loads without a shunt", "Supply set above 60 V"],
                        actions=["Add/verify the external shunt regulator", "Lower the supply voltage"],
                        reference="%s p.240" % MANUAL))
    if s.sysf("BrickLV.OverTemp") == 1:
        ctx.add(Finding("over-temp", FAULT, "Power board over-temperature (> 70 C)",
                        "All channels fault until the board cools.",
                        causes=["Blocked airflow / failed fan", "Ambient above spec", "Continuous current above rating"],
                        actions=["Check fans and cabinet cooling", "Review I2T settings and duty cycle"],
                        reference="%s p.241" % MANUAL))
    pab = s.sys_values.get("Sys.pAbortAll")
    if pab is not None and str(pab).strip() in ("0", "0.0"):
        ctx.add(Finding("abort-disabled", INFO, "Abort-all input disabled (Sys.pAbortAll=0)",
                        "With the abort input disabled the controller tries to close the loop on every enable, which can "
                        "make an un-commissioned motor jump.", reference="%s p.138/153" % MANUAL))
    fg = s.sysf("Sys.FgCpuUsage")
    if fg is not None and fg > 80:
        ctx.add(Finding("cpu-fg", WARN, "Foreground CPU usage %.0f%%" % fg,
                        "Servo/phase/RTI tasks consume most of the CPU - watchdog faults become likely at peak load.",
                        actions=["Lower the servo or phase frequency, raise Sys.RtIntPeriod, move logic to background PLCs"]))
    for i in range(4):
        v = s.epics.get("CPULOAD_%d" % i)
        try:
            if v is not None and float(v) > 85:
                ctx.add(Finding("cpu-epics-%d" % i, WARN, "CPU core %d load %.0f%% (EPICS)" % (i, float(v)),
                                "High CPU load reported by the IOC."))
        except (TypeError, ValueError):
            pass


@global_rule
def r_epics_controller(ctx: Ctx):
    s = ctx.snap
    if not s.epics:
        return
    if _truthy(s.epics.get("GLOBAL_PROBLEM_RBV")):
        ctx.add(Finding("epics-global", WARN, "IOC reports a controller problem (GLOBAL_PROBLEM_RBV)",
                        "The Diamond driver masks the global status word against its hardware-problem bits "
                        "(watchdog, flash/DPRAM/checksum errors, servo error, config load errors)."))
    if _truthy(s.epics.get("FEEDRATE_PROBLEM_RBV")):
        ctx.add(Finding("epics-feedrate", WARN, "Feedrate override below limit",
                        "A coordinate system's % feedrate (&n%%) is below FEEDRATE_LIMIT (%s). Programmed and motor-record moves "
                        "will run slow or appear stuck." % s.epics.get("FEEDRATE_LIMIT", "100"),
                        actions=["Send &1%100 (and other CSs) via the Terminal / SendCmd"]))
    t = s.epics.get("STAT_AVE_TIME_RBV")
    try:
        if t is not None and float(t) > 200:
            ctx.add(Finding("epics-comms-slow", WARN, "Slow IOC <-> PMAC communications (avg %.0f ms/msg)" % float(t),
                            "Average message time is high; status updates will lag and motor record moves may time out.",
                            causes=["Network congestion / duplex mismatch", "Too many variables polled by the IOC", "CPU overload on the controller"],
                            actions=["Check STAT_MAX_TIME_RBV, reduce polled variables, check network path"]))
    except (TypeError, ValueError):
        pass
    if not ctx.power:
        for key, want, label in (("IO_HANDSHAKE", (2,), "I3 should be 2 (ACK handshake) for the EPICS driver"),
                                 ("ERRREPMODE", (1, 3), "I6 should be 1 or 3 so errors are reported as ERRxxx")):
            v = s.epics.get(key)
            try:
                if v is not None and int(v) not in want:
                    ctx.add(Finding("turbo-" + key, WARN, "Communication setting %s=%s" % (key, v), label))
            except (TypeError, ValueError):
                pass
    plc0 = s.epics.get("READ_PLCBITS00")
    try:
        if plc0 is not None and int(plc0) & 0xFFFF == 0xFFFF:
            ctx.add(Finding("plc-all-disabled", WARN, "All PLCs 0-15 are disabled",
                            "Power-on / safety PLCs are not running (I5 or enable plc). Amplifier monitoring, brakes and "
                            "homing logic may be inactive.", actions=["Check I5 (Turbo) or 'enable plc n'"]))
    except (TypeError, ValueError):
        pass
    me = s.epics.get("MACROERRS")
    try:
        if me is not None and float(me) > 0:
            ctx.add(Finding("macro-errors", WARN, "MACRO ring errors counted (%g)" % float(me),
                            "Ring breaks or noise on the MACRO fibre/copper link.",
                            causes=["Loose/dirty fibre", "Ring order mismatch", "A station powered off"]))
    except (TypeError, ValueError):
        pass


# ======================================================================================
# AXIS RULES
# ======================================================================================
@axis_rule
def r_axis_status(ctx: Ctx, ax: AxisSnapshot):
    st = ax.status
    n = ax.axis
    if st is None:
        return
    fe = ax.v("PosError")
    out = ax.v("ServoOut")
    maxdac = ctx.var(n, "MaxDac", "69")
    i2t_set = ctx.var(n, "I2TSet", "57")
    i2t_trip = ctx.var(n, "I2tTrip", "58")
    fatal = ctx.var(n, "FatalFeLimit", "11")
    if fatal is not None and not ctx.power:
        fatal = fatal / 16.0

    if st.amp_fault:
        causes = []
        actions = ["Clear with #%d$* (Power) / re-enable with #%dj/ after the cause is fixed" % (n, n),
                   "Power Brick LV: BrickLV.Reset=1 clears latched amplifier faults; read the 7-segment display code"]
        if st.i2t_fault:
            causes.append("I2T integrated-current trip (software, Motor[x].I2tTrip) - motor working too hard or I2T limits too low")
        oc = ctx.brick(n, "OverCurrent")
        if oc == 1:
            causes.append("Hardware over-current / short circuit on the channel (BrickLV.Chan[%d].OverCurrent=1) - check motor cable and phases" % (n - 1))
        if ctx.brick(n, "I2tFaultStatus") == 1:
            causes.append("Amplifier-stage I2T excess (channel rating 0.75/3/15 A exceeded)")
        if ctx.snap.sysf("BrickLV.BusUnderVoltage") == 1:
            causes.append("DC bus under-voltage")
        if ctx.snap.sysf("BrickLV.BusOverVoltage") == 1:
            causes.append("DC bus over-voltage (regeneration)")
        if ctx.snap.sysf("BrickLV.OverTemp") == 1:
            causes.append("Power board over-temperature")
        if not causes:
            causes = ["I2T trip (check I2tFault / I2tSum)", "Over-current: shorted motor phase or cable, wrong motor type (TwoPhaseMode)",
                      "STO (A11) opened while enabled", "Bus over/under-voltage or over-temperature",
                      "Wrong amp-fault polarity: Motor[x].AmpFaultLevel must be 1 on Power Brick LV",
                      "Motor[x].pAmpFault pointing at the wrong channel"]
        ctx.add(Finding("amp-fault", FAULT, "Axis %d: amplifier fault" % n,
                        "The amplifier fault flag is set; the motor was killed. %s" % (ax.desc or ""),
                        axis=n, causes=causes, actions=actions,
                        evidence=_ev(status=st.raw_hex, i2t_sum=ax.v("I2tSum"), i2t_trip=i2t_trip),
                        reference="%s - I2T Protection (p.166), D1 Error Codes (p.202)" % MANUAL))
    elif st.i2t_fault:
        ctx.add(Finding("i2t", FAULT, "Axis %d: I2T fault" % n, "Integrated current exceeded Motor[x].I2tTrip.",
                        axis=n, causes=["Duty cycle too high / motor undersized", "Mechanical binding increases current",
                                        "I2TSet/I2tTrip computed for the wrong channel rating or RMS vs peak"],
                        actions=["Recompute MaxDac/I2TSet/I2tTrip with the Current Calculator", "Check for binding, brake not released"],
                        reference="%s p.166-169" % MANUAL))

    if st.fe_fatal:
        causes = ["Motor not phased or phased wrongly (brushless): check PhaseFound and run the open-loop test",
                  "Encoder direction reversed relative to commutation (EncCtrl 3<->7) - runaway",
                  "Load jammed, brake engaged, hard stop reached",
                  "No bus power / amplifier produced no torque",
                  "Gains far too low for the move (sluggish) or output saturating (MaxDac too low)",
                  "FatalFeLimit too tight for this encoder resolution / move speed",
                  "Encoder loss or lost counts (CountError)"]
        if out is not None and maxdac and abs(out) >= 0.95 * maxdac:
            causes.insert(0, "Output saturated at MaxDac (%.0f) when the fault occurred" % maxdac)
        if not st.phase_found:
            causes.insert(0, "PhaseFound=0: the motor is NOT phased")
        ctx.add(Finding("fe-fatal", FAULT, "Axis %d: fatal following error" % n,
                        "Following error exceeded the fatal limit%s and the motor was killed." % (
                            " (%g units)" % fatal if fatal else ""),
                        axis=n, causes=causes,
                        actions=["Run the open-loop test from the Tuning page before closing the loop again",
                                 "Jog slowly with a large WarnFeLimit and watch PosError vs ServoOut",
                                 "Re-phase (#%d$) if PhaseFound=0" % n],
                        evidence=_ev(following_error=fe, servo_out=out, max_dac=maxdac, fatal_limit=fatal),
                        reference="%s - Open Loop Test (p.179), Establishing Phase Reference (p.173)" % MANUAL))
    elif st.fe_warn:
        ctx.add(Finding("fe-warn", WARN, "Axis %d: following error warning" % n,
                        "PosError is above WarnFeLimit. Tuning is marginal for this move or the load is heavier than expected.",
                        axis=n, causes=["Low gains / missing feedforward (Kvff, Kaff)", "Output saturation on acceleration",
                                        "Increased friction (lubrication, misalignment)"],
                        actions=["Capture a step response on the Tuning page", "Compare ServoOut with MaxDac during moves"],
                        evidence=_ev(following_error=fe)))

    if st.enc_loss:
        ctx.add(Finding("enc-loss", FAULT, "Axis %d: encoder loss" % n,
                        "The encoder-loss detector tripped (differential pair not driven / sum-of-squares too low / serial timeout).",
                        axis=n, causes=["Encoder cable unplugged or broken wire", "Encoder power (5 V, X19/X1-X8) missing",
                                        "Single-ended encoder on a differential input", "Wrong EncLossBit for the encoder type"],
                        actions=["Check PowerBrick[i].Chan[j].LossStatus / SosError / SerialEncDataB bit 31",
                                 "Fix the cabling then BrickLV.Reset=1 (clears latched LossStatus) and re-enable"],
                        reference="%s - Encoder Loss Detection (p.217-221)" % MANUAL))
    if ctx.chan(n, "CountError") == 1:
        ctx.add(Finding("count-error", WARN, "Axis %d: encoder count error latched" % n,
                        "A and B changed in the same sample - counts were lost. Position will drift and following "
                        "errors / wrong commutation will eventually follow.",
                        axis=n, causes=["Noisy/damaged encoder signals", "Moving faster than the encoder or sampling clock allows",
                                        "Sampling clock (EncClockDiv) too slow for a high-resolution encoder"],
                        actions=["Lower EncClockDiv (e.g. 5 -> 4 doubles the clock)", "Check shielding and termination"],
                        reference="%s - Encoder Count Error (p.216)" % MANUAL))

    if st.plus_limit and st.minus_limit:
        ctx.add(Finding("both-limits", FAULT, "Axis %d: BOTH limit switches active" % n,
                        "Both hardware limits read as tripped - almost always a wiring/configuration issue, not a mechanical one.",
                        axis=n, causes=["Limit switches not wired but Motor[x].pLimits still points at the flag word",
                                        "Limits need +24 V / sinking-sourcing mismatch on X13/X14", "LimitBits pointing at the wrong bit"],
                        actions=["If no switches are wired: Motor[%d].pLimits=0" % n,
                                 "Otherwise check PowerBrick[i].Chan[j].PlusLimit/MinusLimit raw inputs and the X13/X14 wiring"],
                        reference="%s - Limits, Flags and EQU (p.115-118)" % MANUAL))
    elif st.plus_limit or st.minus_limit:
        which = "positive" if st.plus_limit else "negative"
        ctx.add(Finding("limit", WARN, "Axis %d: on %s hardware limit" % (n, which),
                        "Moves in the %s direction are blocked; the motor can be jogged away in the other direction." % which,
                        axis=n, causes=["Axis truly at end of travel", "Limit switch polarity inverted (normally-closed expected)",
                                        "Limit input wired to the wrong channel"],
                        actions=["Jog off the limit (#%dj%s)" % (n, "-" if st.plus_limit else "+")]))
    if st.soft_plus_limit or st.soft_minus_limit:
        ctx.add(Finding("soft-limit", INFO, "Axis %d: at a software limit" % n,
                        "MaxPos/MinPos (or EPICS HLM/LLM) reached.", axis=n))
    if st.limit_stop and not (st.plus_limit or st.minus_limit or st.soft_plus_limit or st.soft_minus_limit):
        ctx.add(Finding("limit-stop", INFO, "Axis %d: stopped on a limit earlier" % n,
                        "LimitStop is latched from a previous move; a new move command clears it.", axis=n))

    if st.amp_enabled and not st.closed_loop:
        ctx.add(Finding("open-loop", WARN, "Axis %d: amplifier enabled in OPEN LOOP" % n,
                        "Output is being commanded directly (#%dout). Fine during tests, dangerous if left." % n, axis=n,
                        actions=["#%dk to kill, or #%dj/ to close the loop" % (n, n)]))
    if ctx.power and not st.phase_found and st.activated:
        pc = ctx.var(n, "PhaseCtrl")
        if pc is None or pc != 0:
            ctx.add(Finding("not-phased", WARN if not st.amp_enabled else FAULT, "Axis %d: not phased (PhaseFound=0)" % n,
                            "A PMAC-commutated motor must establish its phase reference after every power-up before it can be enabled.",
                            axis=n, causes=["Power-on phasing not configured (PowerOnMode bit 1) and no PLC issued #%d$" % n,
                                            "Last phasing search failed (PhaseFindingDac too small, time too short, motor blocked)",
                                            "Hall/absolute encoder phasing misconfigured (AbsPhasePosFormat/Sf/Offset)"],
                            actions=["Issue #%d$ (stepper phasing) and check PhaseFound" % n,
                                     "Manual force phasing: Motor[%d].IbBias=I2TSet/2, #%dout0, settle, PhasePos=0, #%dk, IbBias=0, PhaseFound=1" % (n, n, n)],
                            reference="%s - Establishing Phase Reference (p.173-178)" % MANUAL))
    if st.phasing_error:
        ctx.add(Finding("phasing-error", FAULT, "Axis %d: phasing search error" % n,
                        "Turbo PMAC reports a phasing reference/search error.", axis=n,
                        causes=["Ixx73/Ixx74 (phase finding output/time) inadequate", "Encoder direction mismatch", "Motor blocked"],
                        actions=["Review Ixx70-Ixx75 and retry #%d$" % n]))
    if not st.activated:
        ctx.add(Finding("deactivated", INFO, "Axis %d is deactivated (Ixx00=0 / ServoCtrl=0)" % n,
                        "The motor is not being servoed by the controller.", axis=n))
    if st.amp_enabled and st.closed_loop and not st.home_complete:
        ctx.add(Finding("not-homed", INFO, "Axis %d not homed" % n, "Position reference is not established.", axis=n))

    # saturation while sitting still
    if st.amp_enabled and st.closed_loop and st.des_vel_zero and out is not None and maxdac and abs(out) > 0.5 * maxdac:
        ctx.add(Finding("holding-torque", WARN, "Axis %d: large static output (%.0f%% of MaxDac) while stationary" % (n, abs(out) / maxdac * 100),
                        "The servo needs a lot of current just to hold position.",
                        axis=n, causes=["Vertical/unbalanced load with the brake released", "Motor fighting a mechanical preload or misalignment",
                                        "Integrator wound up against a hard stop", "Wrong phasing (torque lost)"],
                        actions=["Check I2tSum trend", "Verify phasing; consider a brake or counterbalance"],
                        evidence=_ev(servo_out=out, max_dac=maxdac)))
    i2t_sum = ax.v("I2tSum")
    if i2t_sum is not None and i2t_trip and i2t_trip > 0 and i2t_sum > 0.6 * i2t_trip:
        ctx.add(Finding("i2t-warn", WARN, "Axis %d: I2T integrator at %.0f%% of trip" % (n, i2t_sum / i2t_trip * 100),
                        "The motor is running near its continuous current for too long.", axis=n,
                        causes=["Heavy duty cycle", "Binding", "I2TSet below the real continuous rating"]))


@axis_rule
def r_axis_config(ctx: Ctx, ax: AxisSnapshot):
    """Checks on the configuration dump (needs a direct link)."""
    n = ax.axis
    d = ctx.dumps.get(n)
    if d is None:
        return
    P = ctx.power
    maxdac = ctx.var(n, "MaxDac", "69")
    i2t_set = ctx.var(n, "I2TSet", "57")
    i2t_trip = ctx.var(n, "I2tTrip", "58")
    fatal = ctx.var(n, "FatalFeLimit", "11")
    warn = ctx.var(n, "WarnFeLimit", "12")
    kp = ctx.var(n, "Servo.Kp", "30")
    kvfb = ctx.var(n, "Servo.Kvfb", "31")
    kvff = ctx.var(n, "Servo.Kvff", "32")
    ki = ctx.var(n, "Servo.Ki", "33")
    kaff = ctx.var(n, "Servo.Kaff", "35")
    activated = ctx.var(n, "ServoCtrl", "00")
    if activated == 0:
        return
    spec = ctx.specs.get(n, {})

    # ---- following error limits
    if fatal is not None and fatal == 0:
        ctx.add(Finding("fatal-fe-off", FAULT, "Axis %d: fatal following error protection DISABLED" % n,
                        "FatalFeLimit (Ixx11) = 0. A runaway (wrong phasing, encoder loss) will not be stopped.", axis=n,
                        actions=["Set FatalFeLimit as tight as the application allows (manual: 'as tight as possible' during phasing)"],
                        reference="%s p.173" % MANUAL))
    if fatal and warn and warn >= fatal:
        ctx.add(Finding("fe-limits-order", WARN, "Axis %d: WarnFeLimit >= FatalFeLimit" % n,
                        "The warning limit should be below the fatal limit (typically 1/4).", axis=n))
    if fatal and fatal > 0 and spec.get("counts_per_unit"):
        pass

    # ---- current / I2T
    if maxdac is not None:
        if maxdac > 32767:
            ctx.add(Finding("maxdac-range", FAULT, "Axis %d: MaxDac=%g exceeds 16-bit full scale" % (n, maxdac),
                            "Values above 32767 are meaningless; the drive's peak current (Max ADC) is the ceiling.", axis=n,
                            reference="%s p.166" % MANUAL))
        if maxdac <= 0:
            ctx.add(Finding("maxdac-zero", FAULT, "Axis %d: MaxDac=%g - no output allowed" % (n, maxdac),
                            "The motor cannot produce any torque.", axis=n))
    if i2t_trip is not None and i2t_trip == 0:
        ctx.add(Finding("i2t-off", WARN, "Axis %d: I2T protection disabled (I2tTrip=0)" % n,
                        "The motor relies solely on the amplifier's own I2T (protects the transistors, not the motor windings).", axis=n,
                        actions=["Compute I2TSet/I2tTrip from the motor datasheet with the Current Calculator"],
                        reference="%s p.166-169" % MANUAL))
    if i2t_set is not None and maxdac is not None and i2t_set > maxdac > 0:
        ctx.add(Finding("i2t-set-high", WARN, "Axis %d: I2TSet (%g) > MaxDac (%g)" % (n, i2t_set, maxdac),
                        "Continuous limit above the peak limit: I2T can never trip and the numbers are inconsistent.", axis=n))
    if i2t_set is not None and maxdac and i2t_set > 0 and i2t_set < 0.1 * maxdac:
        ctx.add(Finding("i2t-set-low", INFO, "Axis %d: I2TSet is only %.0f%% of MaxDac" % (n, i2t_set / maxdac * 100),
                        "Unusually low continuous/peak ratio - check RMS vs peak conversion (sqrt2, cos30).", axis=n))
    # compare against user supplied motor data
    max_adc = spec.get("max_adc", units.DEFAULT_MAX_ADC)
    if spec.get("peak_a") and spec.get("cont_a") and maxdac is not None:
        cs = units.compute_i2t(float(spec["peak_a"]), float(spec["cont_a"]), float(spec.get("time_at_peak_s", 1.0)),
                               float(max_adc), spec.get("motor_type", "brushless"), bool(spec.get("rms", True)))
        if maxdac > cs.max_dac * 1.1:
            ctx.add(Finding("maxdac-vs-spec", FAULT, "Axis %d: MaxDac %g allows %.1f A peak but the motor is rated %.1f A" % (
                n, maxdac, units.dac_to_amps(maxdac, float(max_adc), spec.get("motor_type") == "brushless", bool(spec.get("rms", True))), float(spec["peak_a"])),
                "Peak current limit above the motor rating (expected ~%.0f)." % cs.max_dac, axis=n,
                actions=["Set Motor[%d].MaxDac=%.0f, I2TSet=%.0f, I2tTrip=%.0f" % (n, cs.max_dac, cs.i2t_set, cs.i2t_trip)],
                reference="%s p.167-169" % MANUAL))
        if i2t_set is not None and i2t_set > cs.i2t_set * 1.1:
            ctx.add(Finding("i2tset-vs-spec", WARN, "Axis %d: I2TSet %g above the motor's continuous rating (expected ~%.0f)" % (n, i2t_set, cs.i2t_set),
                            "The motor may overheat before I2T intervenes.", axis=n))

    # ---- servo gains
    if kp is not None and kp == 0:
        ctx.add(Finding("kp-zero", FAULT, "Axis %d: proportional gain is zero" % n,
                        "The position loop is effectively open - the motor will drift and cannot hold position.", axis=n))
    if kp and kvfb is not None and kvfb == 0 and P:
        ctx.add(Finding("no-damping", WARN, "Axis %d: no velocity feedback (Kvfb=0)" % n,
                        "A PD loop without derivative gain is undamped -> oscillation/overshoot.", axis=n,
                        actions=["Run the auto-tuner or set Kvfb from the step response"]))
    if kvfb and kvff is not None and kvff == 0:
        ctx.add(Finding("no-kvff", INFO, "Axis %d: no velocity feedforward (Kvff=0)" % n,
                        "Without Kvff ~ Kvfb the following error grows proportionally to speed during moves.", axis=n))
    if kvfb and kvff and abs(kvff - kvfb) > 0.5 * kvfb:
        ctx.add(Finding("kvff-mismatch", INFO, "Axis %d: Kvff (%g) differs a lot from Kvfb (%g)" % (n, kvff, kvfb),
                        "Usually Kvff is set equal to Kvfb to cancel the velocity-proportional following error.", axis=n))
    if kaff is not None and kaff == 0 and kp:
        ctx.add(Finding("no-kaff", INFO, "Axis %d: no acceleration feedforward (Kaff=0)" % n,
                        "Following error during accel/decel can be reduced with Kaff (= 1/Ka from the open-loop test).", axis=n))
    if ki and ki > 0 and ctx.var(n, "Servo.SwZvInt", "34") == 0:
        ctx.add(Finding("ki-always", INFO, "Axis %d: integrator active during moves (SwZvInt=0)" % n,
                        "Integrating while moving can cause overshoot at the end of moves; SwZvInt=1 integrates only at rest.", axis=n))
    if P:
        maxposerr = ctx.var(n, "Servo.MaxPosErr")
        if maxposerr is not None and fatal and maxposerr < fatal:
            ctx.add(Finding("maxposerr", INFO, "Axis %d: Servo.MaxPosErr (%g) < FatalFeLimit (%g)" % (n, maxposerr, fatal),
                            "The servo filter clamps the error before the fatal limit is reached - gain is lost on large errors "
                            "(the manual suggests raising MaxPosErr for high-resolution encoders).", axis=n, reference="%s p.183" % MANUAL))
        ii, ipf = ctx.var(n, "IiGain"), ctx.var(n, "IpfGain")
        pc = ctx.var(n, "PhaseCtrl")
        if pc and pc != 0 and ii is not None and ipf is not None and ii == 0 and ipf == 0:
            ctx.add(Finding("cur-loop-zero", FAULT, "Axis %d: current loop gains are zero" % n,
                            "A commutated motor with IiGain=IpfGain=0 has no current control - no torque.", axis=n,
                            actions=["Run the IDE current-loop auto-tune or start with IiGain~0.05, IpfGain~2 (conservative)"],
                            reference="%s - Current Loop Tuning (p.171)" % MANUAL))
        if ipf is not None and ipf > 10:
            ctx.add(Finding("cur-loop-high", INFO, "Axis %d: IpfGain=%g is unusually high" % (n, ipf),
                            "Very stiff current loops can degrade position-loop performance on low performance systems.", axis=n))
        pwm = ctx.var(n, "PwmSf")
        if pwm is not None and pc and pc != 0 and abs(pwm) > 0.95 * 16384 * 1.01:
            ctx.add(Finding("pwmsf", WARN, "Axis %d: |PwmSf|=%g above 0.95*16384" % (n, abs(pwm)),
                            "Over-modulation; the manual recommends PwmSf = 0.95*16384 (scaled down when bus V > motor V).", axis=n,
                            reference="%s p.157" % MANUAL))
        if pwm is not None and pc and pc != 0 and pwm == 0:
            ctx.add(Finding("pwmsf-zero", FAULT, "Axis %d: PwmSf=0" % n, "No PWM output possible.", axis=n))
        ibias = ctx.var(n, "IbBias")
        if ibias and ibias != 0:
            ctx.add(Finding("ibbias", WARN, "Axis %d: IbBias=%g left non-zero" % (n, ibias),
                            "IbBias is only used during manual force phasing; it must be 0 in normal operation.", axis=n,
                            reference="%s p.176" % MANUAL))
        ptb = ctx.var(n, "PhaseTableBias")
        if ptb:
            ctx.add(Finding("phasetablebias", WARN, "Axis %d: PhaseTableBias=%g" % (n, ptb),
                            "Only +/-512 during brush-motor current-loop tuning; set back to 0.", axis=n, reference="%s p.171" % MANUAL))
        afl = ctx.var(n, "AmpFaultLevel")
        pamp = ctx.svar(n, "pAmpFault")
        if pamp and pamp.strip() not in ("0", "0.0") and afl == 0:
            ctx.add(Finding("ampfaultlevel", WARN, "Axis %d: AmpFaultLevel=0 (low-true)" % n,
                            "Power Brick LV amplifier fault is high-true; with level 0 the fault is either ignored or permanently asserted.",
                            axis=n, actions=["Motor[%d].AmpFaultLevel=1" % n], reference="%s p.153" % MANUAL))
        if pamp and pamp.strip() in ("0", "0.0"):
            ctx.add(Finding("ampfault-ignored", INFO, "Axis %d: amplifier fault input ignored (pAmpFault=0)" % n,
                            "Drive faults (over-current, I2T, bus) will not kill the motor from the flag input.", axis=n))
        pel = ctx.svar(n, "pEncLoss")
        if pel is not None and pel.strip() in ("0", "0.0"):
            ctx.add(Finding("encloss-off", INFO, "Axis %d: encoder-loss kill disabled (pEncLoss=0)" % n,
                            "A broken encoder cable will cause a runaway until the fatal following error trips.", axis=n,
                            actions=["Motor[%d].pEncLoss=PowerBrick[i].Chan[j].Status.a, EncLossBit=28, EncLossLevel=1 (quadrature)" % n],
                            reference="%s p.218" % MANUAL))
        mon = ctx.gvar("BrickLV.Monitor")
        adcmask = ctx.svar(n, "AdcMask")
        if adcmask and adcmask.strip().upper() not in ("$FFFC0000", "4294705152", "$FFFC0000.0"):
            ctx.add(Finding("adcmask", INFO, "Axis %d: AdcMask=%s (Power Brick LV expects $FFFC0000)" % (n, adcmask.strip()),
                            "Current ADC mask differs from the manual's recommended value for the 18-bit Brick ADCs.", axis=n,
                            reference="%s p.153" % MANUAL))
        pft = ctx.var(n, "PhaseFindingTime")
        if pc and pc != 0 and pft is not None and pft != 0 and not (255 < pft < 32768):
            ctx.add(Finding("phasefindingtime", WARN, "Axis %d: PhaseFindingTime=%g outside 256..32767" % (n, pft),
                            "Automatic stepper phasing needs 255 < PhaseFindingTime < 32768.", axis=n, reference="%s p.175" % MANUAL))
        pfd = ctx.var(n, "PhaseFindingDac")
        if pc and pc != 0 and pfd is not None and i2t_set and pfd > i2t_set:
            ctx.add(Finding("phasefindingdac", INFO, "Axis %d: PhaseFindingDac (%g) above I2TSet (%g)" % (n, pfd, i2t_set),
                            "Manual suggests starting at I2TSet/2.", axis=n, reference="%s p.175" % MANUAL))
        maxp, minp = ctx.var(n, "MaxPos"), ctx.var(n, "MinPos")
        if maxp is not None and minp is not None and (maxp != 0 or minp != 0) and maxp <= minp:
            ctx.add(Finding("softlimits", WARN, "Axis %d: MaxPos <= MinPos" % n, "Software limits are inverted - no motion possible.", axis=n))
        if maxp == 0 and minp == 0:
            ctx.add(Finding("softlimits-off", INFO, "Axis %d: software limits disabled (MaxPos=MinPos=0)" % n,
                            "Only the hardware limits protect the travel.", axis=n))
        jogta = ctx.var(n, "JogTa")
        jogspeed = ctx.var(n, "JogSpeed")
        if jogspeed is not None and jogspeed <= 0:
            ctx.add(Finding("jogspeed", WARN, "Axis %d: JogSpeed=%g" % (n, jogspeed), "Jog and homing moves cannot happen.", axis=n))
        maxspeed = ctx.var(n, "MaxSpeed")
        if maxspeed and jogspeed and jogspeed > maxspeed * 1.5:
            ctx.add(Finding("jog-gt-max", INFO, "Axis %d: JogSpeed (%g) exceeds MaxSpeed (%g)" % (n, jogspeed, maxspeed), "", axis=n))
        inposband = ctx.var(n, "InPosBand")
        if inposband is not None and inposband <= 0:
            ctx.add(Finding("inposband", INFO, "Axis %d: InPosBand=0" % n,
                            "In-position will only be true at exactly zero error - EPICS moves may never report done.", axis=n))
        pp = ctx.var(n, "PhasePosSf")
        if pc and pc != 0 and pp is not None and pp == 0 and ctx.var(n, "PhaseMode") not in (1, 3) and pc != 6:
            ctx.add(Finding("phaseposf", WARN, "Axis %d: PhasePosSf=0 on a commutated motor" % n,
                            "Only brush motors (PhaseMode=3) and direct microstepping (PhaseCtrl=6) use PhasePosSf=0.", axis=n,
                            reference="%s p.158" % MANUAL))
        tp = ctx.brick(n, "TwoPhaseMode")
        pm = ctx.var(n, "PhaseMode")
        if tp == 1 and pm is not None and int(pm) & 1 == 0:
            ctx.add(Finding("twophase", WARN, "Axis %d: channel in 2-phase mode but Motor PhaseMode bit0 = 0" % n,
                            "BrickLV.Chan[j].TwoPhaseMode=1 requires Motor[x].PhaseMode bit 0 = 1.", axis=n, reference="%s p.243" % MANUAL))
        enc = ctx.chan(n, "EncCtrl")
        if enc is not None and int(enc) not in (3, 7, 8, 11, 12, 15):
            ctx.add(Finding("encctrl", INFO, "Axis %d: EncCtrl=%d" % (n, int(enc)), "Unusual decode mode (x4 quadrature is 3 or 7).", axis=n))
        encloss_raw = ctx.chan(n, "LossStatus")
        if encloss_raw == 1 and not (ax.status and ax.status.enc_loss):
            ctx.add(Finding("lossstatus", WARN, "Axis %d: raw encoder-loss flag latched (Chan LossStatus=1)" % n,
                            "The hardware detector saw a loss but the motor is not configured to kill on it.", axis=n,
                            actions=["Check cabling; BrickLV.Reset=1 clears the latch"], reference="%s p.218" % MANUAL))
    else:
        i24 = ctx.var(n, "", "24")
        if i24 is not None and int(i24) & 0x20000:
            ctx.add(Finding("limits-disabled", WARN, "Axis %d: overtravel limits DISABLED (Ixx24 bit 17)" % n,
                            "The Diamond driver disables limits only during homing; if set permanently the axis can run into the hard stops.", axis=n))
        if i24 is not None and int(i24) & 0x100000:
            ctx.add(Finding("ampfault-disabled", INFO, "Axis %d: amplifier fault input disabled (Ixx24 bit 20)" % n, "", axis=n))
        i66 = ctx.var(n, "", "66")
        i01 = ctx.var(n, "", "01")
        if i01 and i66 == 0:
            ctx.add(Finding("pwmsf-zero", FAULT, "Axis %d: Ixx66=0" % n, "No PWM output possible on a commutated motor.", axis=n))



@axis_rule
def r_axis_epics(ctx: Ctx, ax: AxisSnapshot):
    """EPICS motor-record consistency checks (work with or without a direct link)."""
    if not ax.epics:
        return
    n = ax.axis
    fatal = ctx.var(n, "FatalFeLimit", "11")
    if fatal is not None and not ctx.power:
        fatal = fatal / 16.0
    kp = ctx.var(n, "Servo.Kp", "30")
    _epics_axis_rules(ctx, ax, fatal, kp)


def _epics_axis_rules(ctx: Ctx, ax: AxisSnapshot, fatal: Optional[float], kp: Optional[float]):
    n = ax.axis
    e = ax.epics
    msta = e.get("MSTA")
    if msta is not None:
        try:
            bits = {b.name: b.value for b in decode_msta(int(msta))}
            if bits.get("PROBLEM"):
                ctx.add(Finding("msta-problem", WARN, "Axis %d: motor record reports PROBLEM (MSTA bit 10)" % n,
                                "The Diamond driver sets this for amp faults, fatal following errors, limits unexpectedly disabled, "
                                "global problems or comms errors.", axis=n))
            if bits.get("COMM_ERR"):
                ctx.add(Finding("msta-comm", FAULT, "Axis %d: motor record communication error" % n, "", axis=n))
            if bits.get("SLIP_STALL"):
                ctx.add(Finding("msta-slip", WARN, "Axis %d: slip/stall reported" % n, "", axis=n))
        except (TypeError, ValueError):
            pass
    # position consistency between EPICS and controller
    rbv = _f(e.get("RBV"))
    mres = _f(e.get("MRES"))
    off = _f(e.get("OFF"))
    dirn = _f(e.get("DIR")) or 0
    act = ax.v("ActPos")
    scale = _f(e.get(":SCALE")) or 1.0
    if rbv is not None and mres is not None and act is not None and mres != 0:
        expect = act * mres * scale * (-1 if dirn else 1) + (off or 0.0)
        if abs(expect - rbv) > max(abs(mres) * 20, 1e-6) and abs(expect - rbv) > 0.001 * max(abs(rbv), 1):
            ctx.add(Finding("rbv-mismatch", WARN, "Axis %d: EPICS RBV (%.4g) disagrees with controller position (%.4g expected)" % (n, rbv, expect),
                            "RBV should equal ActPos*MRES*SCALE(+OFF). A mismatch means MRES/SCALE in the IOC do not match the "
                            "controller's units, or a different encoder (UEIP/RDBL) feeds the record.",
                            axis=n, causes=["MRES not equal to 1/counts-per-EGU", "Motor[x].PosSf or EncTable ScaleFactor changed",
                                            "DIR field inverted", "Stale/disconnected monitor"],
                            evidence=_ev(RBV=rbv, MRES=mres, SCALE=scale, ActPos=act)))
    # following error alarm thresholds vs controller limits
    fehigh = _f(e.get(":FERRORMAX"))
    if fehigh is not None and fatal and mres:
        if fehigh > fatal * abs(mres) * 0.9:
            ctx.add(Finding("ferrormax", WARN, "Axis %d: EPICS max following error %.4g EGU is close to the fatal limit (%.4g EGU)" % (
                n, fehigh, fatal * abs(mres)), "Moves have come within 10% of a fatal-following-error kill.", axis=n))
    hlm, llm = _f(e.get("HLM")), _f(e.get("LLM"))
    maxp, minp = ctx.var(n, "MaxPos", "13"), ctx.var(n, "MinPos", "14")
    if hlm is not None and llm is not None and mres and maxp is not None and minp is not None and (maxp or minp):
        c_h, c_l = sorted([maxp * mres * scale + (off or 0), minp * mres * scale + (off or 0)])
        if hlm > c_h + abs(mres) * 2 or llm < c_l - abs(mres) * 2:
            ctx.add(Finding("softlimit-mismatch", INFO, "Axis %d: EPICS soft limits (%.4g..%.4g) extend beyond controller MaxPos/MinPos (%.4g..%.4g)" % (
                n, llm, hlm, c_l, c_h), "The controller will stop the move before EPICS expects - the motor record may report a problem.", axis=n))
    if _truthy(e.get("LVIO")):
        ctx.add(Finding("lvio", INFO, "Axis %d: last EPICS move rejected by soft limit (LVIO)" % n, "", axis=n))
    if _truthy(e.get("MISS")):
        ctx.add(Finding("miss", INFO, "Axis %d: last EPICS move missed its target (MISS) after retries" % n,
                        "Retry deadband RDBD too tight for this axis, or backlash/friction.", axis=n))
    velo, vmax = _f(e.get("VELO")), _f(e.get("VMAX"))
    jog = ctx.var(n, "JogSpeed", "22")
    if velo is not None and mres and jog is not None:
        velo_cts = abs(velo / (mres * scale)) / 1000.0  # EGU/s -> counts/ms
        pass
    eloss = _f(e.get(":ELOSS"))
    if eloss:
        ctx.add(Finding("epics-eloss", FAULT, "Axis %d: IOC encoder-loss flag set (P%d)" % (n, 464 + n),
                        "The encoder-loss PLC on the controller flagged this axis.", axis=n,
                        actions=["Fix cabling then write 0 to the ELOSSRC record"]))
    if e.get("DMOV") == 0 and ax.status is not None and ax.status.des_vel_zero and ax.status.amp_enabled:
        ctx.add(Finding("dmov-stuck", INFO, "Axis %d: motor record thinks it is still moving (DMOV=0) but the controller is stopped" % n,
                        "Usually the in-position band / InPosTime is too tight, or a deferred/CS move is pending.", axis=n))


# ======================================================================================
def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v) -> bool:
    try:
        return bool(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip().lower() in ("yes", "true", "on", "error", "problem")


def _ev(**kw) -> Dict[str, object]:
    return {k: v for k, v in kw.items() if v is not None}


def summarize(findings: List[Finding]) -> Dict[str, int]:
    out = {FAULT: 0, WARN: 0, INFO: 0, OK: 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
