"""In-process PMAC simulator (Power PMAC / Power Brick LV flavour, with a Turbo mode).

It speaks a useful subset of the on-line command language so the rest of the app can be
exercised without hardware:

* variable read / write for ``Motor[x].*``, ``Sys.*``, ``BrickLV.*``, ``PowerBrick[i].Chan[j].*``,
  ``EncTable[n].*``, ``Coord[c].*``, ``P/Q/M`` variables (Power) and ``I/P/M/Q`` variables (Turbo)
* motor commands ``#n?  #np #nv #nf  #nj+ #nj- #nj/ #nj= #nj:d #nj^d  #nk  #nhm #nhmz  #n$  #noutX``
* ``?`` / ``???`` global status, ``cid cpu vers type echo size save``
* ``Gather.*`` data gathering with an in-memory buffer
* ``sim.scenario=<name>`` fault injection (see SCENARIOS)

The servo model is a double integrator with viscous + Coulomb friction and an optional
mechanical resonance, closed by the Power PMAC PID law::

    out = Kp*(DesPos-ActPos) + Kvff*DesVel - Kvfb*ActVel + Kaff*DesAcc + Ki*Int + Kfff*sign(DesVel)
"""
from __future__ import annotations

import math
import random
import re
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Sequence

from .base import ACK, BELL, DirectLink, LinkInfo, PmacError
from ..core import motor_model, units

SERVO_PERIOD_MS = 0.5  # 2 kHz simulated servo

SCENARIOS = {
    "healthy": "All eight motors configured and tuned reasonably.",
    "i2t_trip": "Motor 2: I2tTrip set far too low - trips I2T / amp fault shortly after a move starts.",
    "encoder_loss": "Motor 3: differential encoder cable unplugged (LossStatus latched, EncLoss kill).",
    "limits_not_wired": "Motor 4: pLimits points at the flag word but no limit switches are wired - both limits read active.",
    "wrong_feedback_sign": "Motor 5: encoder decode reversed relative to commutation - runaway into fatal following error.",
    "not_phased": "Motor 6: PhaseFound = 0 (never phased after power-up).",
    "oscillating": "Motor 7: Kp far too high and no damping - limit cycles when enabled.",
    "sluggish": "Motor 8: gains far too low - huge following error, misses WarnFeLimit on every move.",
    "bus_undervoltage": "Main DC bus is off (BrickLV.BusUnderVoltage=1): amps enable but cannot produce torque.",
    "watchdog": "Sys.WDTFault = 1 (foreground watchdog) - controller halted.",
    "saturating": "Motor 1: MaxDac too low for the load - output saturates, following error grows on accel.",
    "resonance": "Motor 1: 120 Hz mechanical resonance lightly damped - visible ringing on step response.",
}


def _hex32(v: int) -> str:
    return "%08X" % (v & 0xFFFFFFFF)


class SimMotor:
    def __init__(self, n: int):
        self.n = n
        # ---- physical model (see core.motor_model). Every motor starts from a generic
        # brushless servo whose load inertia varies slightly from axis to axis.
        spec = motor_model.MotorSpec()
        spec.name = "Generic brushless servo (axis %d)" % n
        spec.load_inertia *= 1.0 + 0.15 * (n - 1)
        self.spec = spec
        self.plant = motor_model.derive(spec, SERVO_PERIOD_MS / 1000.0)
        self.res_pos = 0.0
        self.res_vel = 0.0
        # ---- fault injection / wiring (restored from the plant by reset_faults)
        self.feedback_sign = 1.0
        self.limits_wired = True
        self.limit_lo = -2.0e6
        self.limit_hi = 2.0e6
        self.force_limits_active = False
        self.enc_loss_input = False
        self.bus_ok = True
        self.extra_friction = 1.0   # multiplies Coulomb friction (jam simulation)
        self.res_freq_hz = self.plant.resonance_hz
        self.res_zeta = self.plant.resonance_zeta
        self.noise = spec.encoder_noise_counts
        # ---- stepper specifics
        self.stalled = False
        self.lost_steps = 0.0
        self.rotor_lag = 0.0
        # state
        self.phys_pos = 0.0         # true mechanical position in counts
        self.act_pos = 0.0
        self.act_vel = 0.0
        self.des_pos = 0.0
        self.des_vel = 0.0
        self.des_acc = 0.0
        self.prev_des_vel = 0.0
        self.integ = 0.0
        self.servo_out = 0.0
        self.iq = 0.0
        self.i2t_sum = 0.0
        self.home_pos = 0.0
        self.phase_pos = 0.0
        # flags
        self.amp_ena = False
        self.closed_loop = False
        self.open_loop_out = 0.0
        self.amp_fault = False
        self.fe_fatal = False
        self.fe_warn = False
        self.i2t_fault = False
        self.enc_loss = False
        self.limit_stop = False
        self.home_complete = False
        self.homing = False
        self.phase_found = True
        self.in_pos = True
        self.dac_limit = False
        # jog trajectory
        self.traj_target: Optional[float] = None
        self.traj_dir = 0  # continuous jog direction
        self.jog_speed_override: Optional[float] = None
        self.in_pos_count = 0
        self.home_timer = 0
        self.apply_spec(self.spec, SERVO_PERIOD_MS / 1000.0)

    # ------------------------------------------------------------------ model
    def apply_spec(self, spec: "motor_model.MotorSpec", servo_period_s: float):
        """Install a datasheet-level motor description and recompute the plant."""
        self.spec = spec
        self.plant = motor_model.derive(spec, servo_period_s)
        self.noise = spec.encoder_noise_counts
        half = motor_model.travel_counts(spec) / 2.0
        self.limit_lo, self.limit_hi = -half, half
        self.lost_steps = 0.0
        self.stalled = False
        self.restore_plant()

    def restore_plant(self):
        """Reset the fault-injection overrides back to what the motor model says."""
        self.res_freq_hz = self.plant.resonance_hz
        self.res_zeta = self.plant.resonance_zeta
        self.feedback_sign = 1.0
        self.limits_wired = True
        self.force_limits_active = False
        self.enc_loss_input = False
        self.bus_ok = True
        self.extra_friction = 1.0

    @property
    def micro_stepping(self) -> bool:
        return self.spec.motor_type == motor_model.STEPPER_MICRO


class SimulatedPmac(DirectLink):
    """A DirectLink that runs an internal model. ``family`` = 'power' or 'turbo'."""

    name = "simulator"

    def __init__(self, family: str = "power", n_motors: int = 8, scenario: str = "healthy",
                 realtime: bool = True):
        super().__init__()
        self.family = family
        self.n_motors = n_motors
        self.vars: Dict[str, str] = {}
        self.motors: Dict[int, SimMotor] = {n: SimMotor(n) for n in range(1, n_motors + 1)}
        self._slock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.realtime = realtime
        self.cycle = 0
        self.scenario = scenario
        self.echo = 0
        self.plc_running = {n: (n in (1, 2, 5)) for n in range(32)}
        # gather
        self.gather_addr: List[str] = ["" for _ in range(32)]
        self.gather_items = 0
        self.gather_period = 1
        self.gather_max = 0
        self.gather_enable = 0
        self.gather_buf: List[List[float]] = []
        # live scope: a ring buffer filled at the servo rate for the UI to drain
        self.scope_axis: Optional[int] = None
        self.scope_decimate = 2
        self.scope_buf: deque = deque(maxlen=1)
        self._init_variables()
        self.apply_scenario(scenario)

    # ============================================================== defaults
    def _init_variables(self):
        v = self.vars
        if self.family == "power":
            v.update({
                "Sys.ServoPeriod": "%.6f" % SERVO_PERIOD_MS, "Sys.PhaseOverServoPeriod": "0.25",
                "Sys.RtIntPeriod": "0", "Sys.MaxMotors": str(self.n_motors), "Sys.MaxCoords": "4",
                "Sys.WpKey": "$0", "Sys.pAbortAll": "PowerBrick[0].GpioData[0].a", "Sys.AbortAllBit": "4",
                "Sys.AbortAllLimit": "0", "Sys.WDTFault": "0", "Sys.WDTReset": "0", "Sys.Status": "$0",
                "Sys.NoClocks": "0", "Sys.PwrOnFault": "0", "Sys.ConfigLoadErr": "0", "Sys.Default": "0",
                "Sys.CpuTemp": "47.5", "Sys.FgCpuUsage": "23.4", "Sys.BgCpuUsage": "11.2",
                "Sys.BgSleepTime": "1000", "Sys.MaxRtPlc": "0", "Sys.CompEnable": "0",
                "Sys.PosCtrl": "$1", "Sys.ServoCtrl": "$0", "Sys.CK3WConfigErr": "0",
                "BrickLV.Monitor": "1", "BrickLV.MonitorPeriod": "0", "BrickLV.Config": "0",
                "BrickLV.Reset": "0", "BrickLV.BusOverVoltage": "0", "BrickLV.BusUnderVoltage": "0",
                "BrickLV.OverTemp": "0", "BrickLV.Vers": "2.3",
            })
            for i in range(2):
                v.update({
                    "PowerBrick[%d].PhaseFreq" % i: "9.0352", "PowerBrick[%d].PhaseClockDiv" % i: "0",
                    "PowerBrick[%d].ServoClockDiv" % i: "3", "PowerBrick[%d].EncClockDiv" % i: "5",
                    "PowerBrick[%d].PfmClockDiv" % i: "5", "PowerBrick[%d].AdcAmpClockDiv" % i: "5",
                })
                for j in range(4):
                    p = "PowerBrick[%d].Chan[%d]." % (i, j)
                    v.update({
                        p + "EncCtrl": "7", p + "TimerMode": "0", p + "OutputMode": "0", p + "OutputPol": "0",
                        p + "PwmFreqMult": "1", p + "PackInData": "0", p + "PackOutData": "0",
                        p + "CaptCtrl": "1", p + "CaptFlagSel": "0", p + "AdcOffset[0]": "0",
                        p + "AdcOffset[1]": "0", p + "PfmWidth": "15", p + "PfmFormat": "0",
                        p + "SerialEncCmd": "0", p + "Status": "$0", p + "PlusLimit": "0",
                        p + "MinusLimit": "0", p + "HomeFlag": "0", p + "UserFlag": "0", p + "AmpFault": "0",
                        p + "AmpEna": "0", p + "CountError": "0", p + "LossStatus": "0", p + "SosError": "0",
                        p + "SumOfSquares": "0", p + "UVW": "5", p + "ServoCapt": "0", p + "PhaseCapt": "0",
                        p + "AdcAmp[0]": "0", p + "AdcAmp[1]": "0", p + "AdcEnc[0]": "0", p + "AdcEnc[1]": "0",
                        p + "SerialEncDataA": "0", p + "SerialEncDataB": "0",
                    })
                    v["BrickLV.Chan[%d].I2tWarnOnly" % (i * 4 + j)] = "0"
                    v["BrickLV.Chan[%d].TwoPhaseMode" % (i * 4 + j)] = "0"
                    v["BrickLV.Chan[%d].ActivePhaseMode" % (i * 4 + j)] = "0"
                    v["BrickLV.Chan[%d].I2tFaultStatus" % (i * 4 + j)] = "0"
                    v["BrickLV.Chan[%d].OverCurrent" % (i * 4 + j)] = "0"
            for c in range(1, 5):
                v.update({"Coord[%d].Ta" % c: "100", "Coord[%d].Td" % c: "100", "Coord[%d].Ts" % c: "0",
                          "Coord[%d].FeedTime" % c: "1000", "Coord[%d].SegMoveTime" % c: "0",
                          "Coord[%d].ProgRunning" % c: "0", "Coord[%d].InPos" % c: "1",
                          "Coord[%d].AmpEna" % c: "0", "Coord[%d].ErrorStatus" % c: "0",
                          "Coord[%d].Status" % c: "$0"})
            for n in range(1, self.n_motors + 1):
                i, j = (n - 1) // 4, (n - 1) % 4
                cur = units.compute_i2t(7.2, 3.6, 1.0, 33.85, "brushless", rms=True)
                m = "Motor[%d]." % n
                v.update({
                    m + "ServoCtrl": "1", m + "PhaseCtrl": "4", m + "pDac": "PowerBrick[%d].Chan[%d].Pwm[0].a" % (i, j),
                    m + "pEnc": "EncTable[%d].a" % n, m + "pEnc2": "EncTable[%d].a" % n,
                    m + "pLimits": "PowerBrick[%d].Chan[%d].Status.a" % (i, j), m + "LimitBits": "9",
                    m + "pAmpFault": "PowerBrick[%d].Chan[%d].Status.a" % (i, j), m + "AmpFaultLevel": "1",
                    m + "AmpFaultBit": "7", m + "pAmpEnable": "PowerBrick[%d].Chan[%d].OutCtrl.a" % (i, j),
                    m + "pEncLoss": "PowerBrick[%d].Chan[%d].Status.a" % (i, j), m + "EncLossBit": "28",
                    m + "EncLossLevel": "1", m + "EncLossLimit": "0",
                    m + "pCaptFlag": "PowerBrick[%d].Chan[%d].Status.a" % (i, j),
                    m + "pCaptPos": "PowerBrick[%d].Chan[%d].HomeCapt.a" % (i, j), m + "CaptFlagBit": "20",
                    m + "CaptureMode": "0", m + "pAbsPos": "0", m + "AbsPosFormat": "$0", m + "AbsPosSf": "1",
                    m + "PowerOnMode": "0", m + "AdcMask": "$FFFC0000", m + "Coord": "1",
                    m + "pMasterEnc": "0", m + "MasterCtrl": "0",
                    m + "PosSf": "1", m + "Pos2Sf": "1", m + "PosUnit": "0", m + "PhasePosSf": "%.8g" % (2048.0 * 4 / (256.0 * 8000)),
                    m + "pPhaseEnc": "PowerBrick[%d].Chan[%d].PhaseCapt.a" % (i, j),
                    m + "PhaseEncLeftShift": "0", m + "PhaseEncRightShift": "0", m + "PhaseOffset": "683",
                    m + "PhaseMode": "0", m + "PhaseTableBias": "0", m + "SlipGain": "0", m + "AdvGain": "0",
                    m + "pAbsPhasePos": "0", m + "AbsPhasePosFormat": "$0", m + "AbsPhasePosSf": "0",
                    m + "AbsPhasePosOffset": "409.6", m + "AbsPhasePosForce": "0",
                    m + "PhaseFindingDac": "%.1f" % (cur.i2t_set / 2), m + "PhaseFindingTime": "3000",
                    m + "PhasePos": "0", m + "PhaseFound": "1",
                    m + "PwmSf": "%.1f" % (0.95 * 16384), m + "IiGain": "0.05", m + "IpfGain": "2.5", m + "IpbGain": "0",
                    m + "IdCmd": "0", m + "IaBias": "0", m + "IbBias": "0", m + "MaxDac": "%.1f" % cur.max_dac,
                    m + "I2TSet": "%.1f" % cur.i2t_set, m + "I2tTrip": "%.1f" % cur.i2t_trip, m + "I2tSum": "0",
                    m + "IqMeas": "0", m + "IdMeas": "0", m + "IqCmd": "0", m + "ServoOut": "0", m + "DacBias": "0",
                    m + "Servo.Kp": "20", m + "Servo.Kvfb": "500", m + "Servo.Kvifb": "0", m + "Servo.Kvff": "500",
                    m + "Servo.Kviff": "0", m + "Servo.Kaff": "0", m + "Servo.Kfff": "0", m + "Servo.Ki": "0.0",
                    m + "Servo.SwZvInt": "0", m + "Servo.MaxInt": "28000", m + "Servo.MaxPosErr": "1000",
                    m + "Servo.Kbreak": "1", m + "Servo.BreakPosErr": "0", m + "Servo.OutDbOn": "0",
                    m + "Servo.OutDbOff": "0", m + "Servo.OutDbSeed": "0", m + "Servo.Kc1": "0", m + "Servo.Kc2": "0",
                    m + "Servo.Kd1": "0", m + "Servo.Kd2": "0", m + "Ctrl": "Sys.ServoCtrl",
                    m + "FatalFeLimit": "2000", m + "WarnFeLimit": "500", m + "MaxPos": "0", m + "MinPos": "0",
                    m + "MaxSpeed": "100", m + "InvAmax": "0.002", m + "InvDmax": "0.002", m + "InvJmax": "0.0002",
                    m + "AbortTa": "-2", m + "AbortTs": "0", m + "BlSize": "0", m + "BlSlewRate": "10",
                    m + "BlHysteresis": "0",
                    m + "JogSpeed": "50", m + "JogTa": "50", m + "JogTs": "10", m + "HomeVel": "5",
                    m + "HomeOffset": "0", m + "InPosBand": "4", m + "InPosTime": "10", m + "SettleTime": "0",
                    m + "HomeComplete": "0", m + "HomePos": "0",
                })
                e = "EncTable[%d]." % n
                v.update({e + "type": "1", e + "pEnc": "PowerBrick[%d].Chan[%d].ServoCapt.a" % (i, j),
                          e + "pEnc1": "Sys.pushm", e + "index1": "0", e + "index2": "0", e + "index3": "0",
                          e + "index4": "0", e + "index5": "0", e + "index6": "0", e + "ScaleFactor": "1",
                          e + "MaxDelta": "0", e + "PrevEnc": "0", e + "DeltaPos": "0", e + "PrevDelta": "0"})
            for k in range(0, 1024):
                v["P%d" % k] = "0"
            v["P1"] = "3.14159"
            v["P100"] = "1"
            for k in range(0, 64):
                v["M%d" % k] = "0"
            for k in range(0, 256):
                v["Q%d" % k] = "0"
        else:  # turbo
            v.update({"I0": "0", "I1": "0", "I2": "1", "I3": "2", "I4": "0", "I5": "2", "I6": "1", "I7": "0",
                      "I8": "2", "I9": "2", "I10": "3713707", "I11": "0", "I12": "0", "I13": "0", "I14": "0",
                      "I15": "0", "I19": "7007", "I37": "0", "I42": "0", "I51": "0", "I52": "7", "I59": "0",
                      "I60": "0", "I61": "0", "I68": "15", "I80": "16", "I81": "32", "I82": "2"})
            for n in range(1, self.n_motors + 1):
                i, j = (n - 1) // 4, (n - 1) % 4 + 1
                d = {
                    "00": "1", "01": "1", "02": "$78%d02" % (i * 1 + 0), "03": "$3501", "04": "$3501",
                    "05": "$3503", "06": "0", "07": "96", "08": "96", "09": "96", "10": "0", "11": "32000",
                    "12": "16000", "13": "0", "14": "0", "15": "0.25", "16": "32", "17": "0.5", "19": "0.015625",
                    "20": "50", "21": "10", "22": "50", "23": "5", "24": "$120001", "25": "$78%d00" % i, "26": "0",
                    "27": "0", "28": "160", "29": "0", "30": "2000", "31": "1280", "32": "1280", "33": "0",
                    "34": "1", "35": "0", "36": "0", "37": "0", "38": "0", "39": "0", "40": "0", "41": "0",
                    "42": "$78%d00" % i, "43": "$78%d00" % i, "55": "0", "56": "0", "57": "9000", "58": "22000",
                    "59": "0", "60": "0", "61": "0.05", "62": "2.5", "63": "4194304", "64": "0", "65": "16",
                    "66": "6527", "67": "4194176", "68": "0", "69": "20480", "70": "1", "71": "2000", "72": "683",
                    "73": "4500", "74": "3000", "75": "0", "76": "0", "77": "0", "78": "0", "79": "0", "80": "0",
                    "81": "0", "82": "$78%d06" % i, "83": "$78%d01" % i, "84": "$FFF000", "85": "0", "86": "0",
                    "87": "64", "88": "0", "90": "1", "91": "$0", "92": "10", "95": "$0", "96": "0", "97": "0",
                }
                for k, val in d.items():
                    v["I%d%s" % (n, k)] = val
                for k in range(0, 10):
                    v["I7%d%d%d" % (i, j, k)] = ["7", "0", "1", "0", "0", "0", "0", "0", "0", "0"][k]
            for i in range(2):
                for k, val in enumerate(["6527", "0", "3", "2258", "15", "$7FFFC0", "$FFFFFE"]):
                    v["I7%d0%d" % (i, k)] = val
            for k in range(0, 1024):
                v["P%d" % k] = "0"
                v["Q%d" % k] = "0"
            for k in range(0, 256):
                v["M%d" % k] = "0"
                v["M%d->" % k] = "*"

    # ============================================================== scenarios
    def apply_scenario(self, name: str):
        with self._slock:
            self.scenario = name
            for m in self.motors.values():
                m.restore_plant()
                m.phase_found = True
            self.vars["Sys.WDTFault"] = "0"
            if self.family == "power":
                self.vars["BrickLV.BusUnderVoltage"] = "0"
                for n in range(1, self.n_motors + 1):
                    self._set_chan(n, "LossStatus", "0")
            if name == "i2t_trip" and 2 in self.motors:
                self._setv(2, "I2tTrip", "50", "58")
                self._setv(2, "I2TSet", "300", "57")
            elif name == "encoder_loss" and 3 in self.motors:
                self.motors[3].enc_loss_input = True
                if self.family == "power":
                    self._set_chan(3, "LossStatus", "1")
            elif name == "limits_not_wired" and 4 in self.motors:
                self.motors[4].force_limits_active = True
            elif name == "wrong_feedback_sign" and 5 in self.motors:
                self.motors[5].feedback_sign = -1.0
            elif name == "not_phased" and 6 in self.motors:
                self.motors[6].phase_found = False
                if self.family == "power":
                    self.vars["Motor[6].PhaseFound"] = "0"
            elif name == "oscillating" and 7 in self.motors:
                self._setv(7, "Servo.Kp", "2500", "30")
                self._setv(7, "Servo.Kvfb", "0", "31")
            elif name == "sluggish" and 8 in self.motors:
                self._setv(8, "Servo.Kp", "0.8", "30")
                self._setv(8, "Servo.Kvfb", "0.3", "31")
                self._setv(8, "Servo.Kvff", "0", "32")
            elif name == "bus_undervoltage":
                for m in self.motors.values():
                    m.bus_ok = False
                if self.family == "power":
                    self.vars["BrickLV.BusUnderVoltage"] = "1"
            elif name == "watchdog":
                self.vars["Sys.WDTFault"] = "1"
            elif name == "saturating" and 1 in self.motors:
                self._setv(1, "MaxDac", "900", "69")
            elif name == "resonance" and 1 in self.motors:
                self.motors[1].res_freq_hz = 120.0
                self.motors[1].res_zeta = 0.04

    # ============================================================== motor model
    def apply_motor(self, axis: int, spec: "motor_model.MotorSpec", configure: bool = True,
                    bandwidth_hz: float = 20.0, zeta: float = 0.9):
        """Install a datasheet-level motor on ``axis``.

        When ``configure`` is true the matching PMAC settings (current limits, protection,
        scaling and a starting PID set) are written into the simulated controller as well,
        so the axis behaves like a properly commissioned one.
        Returns ``(DerivedPlant, applied_settings)``.
        """
        with self._slock:
            if axis not in self.motors:
                raise PmacError("error #22: no motor %d" % axis)
            mot = self.motors[axis]
            self._kill(mot)
            mot.apply_spec(spec, SERVO_PERIOD_MS / 1000.0)
            mot.phys_pos = mot.act_pos = mot.des_pos = 0.0
            mot.act_vel = mot.des_vel = mot.integ = 0.0
            mot.amp_fault = mot.fe_fatal = mot.i2t_fault = mot.enc_loss = False
            mot.i2t_sum = 0.0
            mot.home_complete = False
            applied: Dict[str, float] = {}
            if configure:
                applied = motor_model.suggest_settings(spec, mot.plant, axis, self.family,
                                                       bandwidth_hz, zeta)
                lowered = {k.lower(): k for k in self.vars}
                for k, v in applied.items():
                    found = lowered.get(k.lower())
                    if found is not None:
                        self.vars[found] = ("%g" % v) if isinstance(v, float) else str(v)
            return mot.plant, applied

    # ============================================================== live scope
    # Sample layout: (t_s, des, act, following_error, servo_out, current_a, velocity, flags)
    SCOPE_FATAL = 1
    SCOPE_WARN = 2
    SCOPE_ENABLED = 4
    SCOPE_LIMIT = 8
    SCOPE_STALL = 16
    SCOPE_I2T = 32
    SCOPE_AMPFAULT = 64

    def scope_start(self, axis: int, decimate: int = 2, seconds: float = 30.0):
        """Record one axis into a ring buffer at the servo rate (decimated)."""
        with self._slock:
            self.scope_axis = axis
            self.scope_decimate = max(1, int(decimate))
            n = int(seconds * 1000.0 / (SERVO_PERIOD_MS * self.scope_decimate)) + 64
            self.scope_buf = deque(maxlen=n)

    def scope_stop(self):
        with self._slock:
            self.scope_axis = None
            self.scope_buf = deque(maxlen=1)

    def scope_drain(self) -> List[tuple]:
        """Return everything recorded since the last call."""
        with self._slock:
            out = list(self.scope_buf)
            self.scope_buf.clear()
            return out

    def scope_rate_hz(self) -> float:
        return 1000.0 / (SERVO_PERIOD_MS * self.scope_decimate)

    def _scope_sample(self):
        mot = self.motors.get(self.scope_axis)
        if mot is None:
            return
        flags = 0
        if mot.fe_fatal:
            flags |= self.SCOPE_FATAL
        if mot.fe_warn:
            flags |= self.SCOPE_WARN
        if mot.amp_ena:
            flags |= self.SCOPE_ENABLED
        if self._plus_limit(mot) or self._minus_limit(mot):
            flags |= self.SCOPE_LIMIT
        if mot.stalled:
            flags |= self.SCOPE_STALL
        if mot.i2t_fault:
            flags |= self.SCOPE_I2T
        if mot.amp_fault:
            flags |= self.SCOPE_AMPFAULT
        self.scope_buf.append((
            self.cycle * SERVO_PERIOD_MS / 1000.0, mot.des_pos, mot.act_pos,
            mot.des_pos - mot.act_pos, mot.servo_out,
            mot.iq / 32768.0 * mot.plant.max_adc, mot.act_vel, flags))

    def set_fault(self, axis: int, kind: str, on: bool = True):
        """Fault injection for one axis: enc_loss, limits_unwired, limits_active,
        reverse_feedback, no_bus, jam, resonance, amp_fault, not_phased."""
        with self._slock:
            mot = self.motors[axis]
            if kind == "enc_loss":
                mot.enc_loss_input = on
                self._set_chan(axis, "LossStatus", "1" if on else "0")
                if not on:
                    mot.enc_loss = False
            elif kind == "limits_unwired":
                mot.limits_wired = not on
            elif kind == "limits_active":
                mot.force_limits_active = on
            elif kind == "reverse_feedback":
                mot.feedback_sign = -1.0 if on else 1.0
            elif kind == "no_bus":
                mot.bus_ok = not on
                if self.family == "power":
                    self.vars["BrickLV.BusUnderVoltage"] = "1" if on else "0"
            elif kind == "jam":
                mot.extra_friction = 6.0 if on else 1.0
            elif kind == "resonance":
                mot.res_freq_hz = (mot.plant.resonance_hz or 120.0) if on else 0.0
                mot.res_zeta = 0.04 if on else mot.plant.resonance_zeta
            elif kind == "amp_fault":
                mot.amp_fault = on
                if on:
                    self._kill(mot)
            elif kind == "not_phased":
                mot.phase_found = not on
                if self.family == "power":
                    self.vars["Motor[%d].PhaseFound" % axis] = "0" if on else "1"
            else:
                raise PmacError("error #22: unknown fault %r" % kind)

    def _setv(self, n: int, power_elem: str, value: str, turbo_suffix: str):
        if self.family == "power":
            self.vars["Motor[%d].%s" % (n, power_elem)] = value
        else:
            self.vars["I%d%s" % (n, turbo_suffix)] = value

    def _set_chan(self, n: int, elem: str, value: str):
        i, j = (n - 1) // 4, (n - 1) % 4
        self.vars["PowerBrick[%d].Chan[%d].%s" % (i, j, elem)] = value

    # ============================================================== link API
    def _open(self):
        self._stop.clear()
        if self.realtime and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name="pmac-sim", daemon=True)
            self._thread.start()

    def _close(self):
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)
        self._thread = None

    def _identify(self):
        if self.family == "power":
            self.info = LinkInfo(family="power", description="Simulated Power Brick LV (PBL8)",
                                 cid=604020, cpu="arm,LS1021A", firmware="2.6.1.1",
                                 servo_period_ms=SERVO_PERIOD_MS, phase_over_servo=0.25,
                                 max_motors=self.n_motors)
        else:
            self.info = LinkInfo(family="turbo", description="Simulated Geobrick LV",
                                 cid=603382, cpu="DSP56321", firmware="1.947",
                                 servo_period_ms=3713707 / 8388608.0, max_motors=self.n_motors)

    def supports_gather(self) -> bool:
        return self.family == "power"

    def gather(self, axis, items, period, samples, trigger):
        with self._slock:
            self.gather_enable = 0
            self.gather_addr = [it + ".a" for it in items] + [""] * (32 - len(items))
            self.gather_items = len(items)
            self.gather_period = max(1, int(period))
            self.gather_max = int(samples)
            self.gather_buf = []
            self.gather_enable = 2
        time.sleep(0.03)  # like real hardware: a few pre-trigger samples are collected
        trigger()
        total = samples * period * SERVO_PERIOD_MS / 1000.0
        deadline = time.time() + total + 2.0
        while time.time() < deadline:
            with self._slock:
                if len(self.gather_buf) >= self.gather_max:
                    break
            time.sleep(0.02)
        with self._slock:
            self.gather_enable = 0
            cols = {it: [row[k] for row in self.gather_buf] for k, it in enumerate(items)}
        return cols

    # ============================================================== command parser
    def _transact(self, cmd: str) -> str:
        with self._slock:
            try:
                lines = self._execute(cmd.strip())
            except PmacError as exc:
                return BELL + str(exc) + "\n"
            return "\n".join(lines) + ("\n" if lines else "") + ACK

    _MULTI = re.compile(r"^(echo\s+\d+|list\s+\w+(\s*,\s*\d+)*|enable\s+plc\s+\d+|disable\s+plc\s+\d+|"
                        r"sim\.scenario\s*=\s*\w+|save|\$\$\$|\$\$\$\*\*\*|date|size|type|vers|cpu|cid)$",
                        re.I)

    def _execute(self, cmd: str) -> List[str]:
        if cmd == "":
            return []
        m = self._MULTI.match(cmd)
        if m:
            return self._keyword(cmd)
        out: List[str] = []
        for tok in self._tokenise(cmd):
            out.extend(self._token(tok))
        return out

    @staticmethod
    def _tokenise(cmd: str) -> List[str]:
        # keep "Name = value" together, split other whitespace
        cmd = re.sub(r"\s*=\s*", "=", cmd)
        return [t for t in re.split(r"\s+", cmd) if t]

    def _keyword(self, cmd: str) -> List[str]:
        low = cmd.lower()
        if low.startswith("echo"):
            self.echo = int(low.split()[1])
            return []
        if low == "cid":
            return [str(self.info.cid or (604020 if self.family == "power" else 603382))]
        if low == "cpu":
            return ["arm,LS1021A" if self.family == "power" else "DSP56321"]
        if low == "vers":
            return ["2.6.1.1" if self.family == "power" else "1.947"]
        if low == "type":
            return ["PowerPMAC,ARM" if self.family == "power" else "TURBO3"]
        if low == "date":
            return [time.strftime("%m/%d/%Y")]
        if low == "size":
            return ["User Buffer = 1048576", "Program Buffer = 16777216"]
        if low in ("save", "$$$", "$$$***"):
            return ["Saved" if low == "save" else ""]
        if low.startswith("list plc"):
            return ["open plc %s" % low.split("plc")[1].strip().split(",")[0], "// simulated", "close"]
        if low.startswith("list"):
            return ["(no program)"]
        if low.startswith("enable plc") or low.startswith("disable plc"):
            n = int(low.split()[-1])
            self.plc_running[n] = low.startswith("enable")
            return []
        if low.startswith("sim.scenario"):
            name = cmd.split("=")[1].strip()
            if name not in SCENARIOS:
                raise PmacError("error #22: unknown scenario")
            self.apply_scenario(name)
            return []
        raise PmacError("error #20: ILLEGAL CMD")

    def _token(self, tok: str) -> List[str]:
        # motor commands
        m = re.match(r"^#(\d+)(.*)$", tok)
        if m:
            return self._motor_cmd(int(m.group(1)), m.group(2))
        m = re.match(r"^&(\d+)(.*)$", tok)
        if m:
            return self._cs_cmd(int(m.group(1)), m.group(2))
        if tok == "?":
            return [self._global_status()]
        if tok == "???":
            return [self._global_status()]
        if tok == "??":
            return ["000000000000"]
        if "=" in tok:
            name, val = tok.split("=", 1)
            return self._assign(name, val)
        # range query  P0..10 / I100..199 / Motor[1..3].Servo.Kp
        rng = re.match(r"^([A-Za-z]+)(\d+)\.\.(\d+)(->)?$", tok)
        if rng:
            pre, a, b, arrow = rng.groups()
            out = []
            for k in range(int(a), int(b) + 1):
                out.extend(self._read("%s%d%s" % (pre, k, arrow or "")))
            return out
        if tok.lower().startswith("plc[") and tok.lower().endswith("].running"):
            n = int(re.findall(r"\d+", tok)[0])
            return ["1" if self.plc_running.get(n) else "0"]
        if tok.lower().startswith("gather."):
            return self._gather_read(tok)
        return self._read(tok)

    # ------------------------------------------------------------- reads / writes
    def _fmt(self, name: str, value: str) -> str:
        if self.family == "power" and self.echo & 1 == 0:
            return "%s=%s" % (name, value)
        return value

    def _read(self, name: str) -> List[str]:
        key = self._canon(name)
        live = self._live_value(key)
        if live is not None:
            return [self._fmt(name, live)]
        if key in self.vars:
            return [self._fmt(name, self.vars[key])]
        # case-insensitive fallback
        for k in self.vars:
            if k.lower() == key.lower():
                return [self._fmt(name, self.vars[k])]
        raise PmacError("error #20: ILLEGAL CMD: %s" % name if self.family == "power" else "ERR003")

    def _canon(self, name: str) -> str:
        if self.family == "power":
            # normalise Motor[1].servo.kp -> proper case by lookup
            return name
        return name.upper()

    def _assign(self, name: str, val: str) -> List[str]:
        key = self._canon(name)
        low = key.lower()
        if low.startswith("gather."):
            return self._gather_write(key, val)
        if low.startswith("sim."):
            raise PmacError("error #20: ILLEGAL CMD")
        # expression: numbers, hex, or reference to another variable
        value = val.strip()
        if re.match(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$", value) or re.match(r"^\$[0-9A-Fa-f]+$", value):
            pass
        elif value in self.vars:
            value = self.vars[value]
        elif re.match(r"^[A-Za-z_][\w\.\[\]]*(\.a)?$", value):
            pass  # address pointer - store as string
        else:
            try:
                value = repr(float(eval(value, {"__builtins__": {}}, {})))  # noqa: S307 - arithmetic only
            except Exception:
                raise PmacError("error #22: ILLEGAL PARAMETER")
        found = None
        for k in self.vars:
            if k.lower() == low:
                found = k
                break
        if found is None:
            if self.family == "power" and re.match(r"^[PQM]\d+$", key):
                found = key
            elif self.family == "turbo" and re.match(r"^[IPQM]\d+(->)?$", key):
                found = key
            else:
                raise PmacError("error #20: ILLEGAL CMD: %s" % name)
        self.vars[found] = value
        self._on_write(found, value)
        return []

    def _on_write(self, key: str, value: str):
        m = re.match(r"^Motor\[(\d+)\]\.(\w+)$", key)
        if m and int(m.group(1)) in self.motors:
            mot = self.motors[int(m.group(1))]
            elem = m.group(2)
            if elem == "PhaseFound":
                mot.phase_found = value.strip() not in ("0", "0.0")
            if elem == "PhasePos":
                mot.phase_pos = float(value)
            if elem == "HomeComplete":
                mot.home_complete = value.strip() not in ("0",)
        if key == "BrickLV.Reset" and value.strip() == "1":
            for n in range(1, self.n_motors + 1):
                self._set_chan(n, "LossStatus", "1" if self.motors[n].enc_loss_input else "0")
                self.vars["BrickLV.Chan[%d].OverCurrent" % (n - 1)] = "0"
            self.vars["BrickLV.Reset"] = "0"
            self.vars["BrickLV.Monitor"] = "0"
        if key == "BrickLV.Config" and value.strip() == "1":
            self.vars["BrickLV.Config"] = "0"
            self.vars["BrickLV.Monitor"] = "0"

    def _live_value(self, key: str) -> Optional[str]:
        """Values computed from the model rather than stored."""
        m = re.match(r"^Motor\[(\d+)\]\.(.+)$", key)
        if m and self.family == "power":
            n = int(m.group(1))
            if n not in self.motors:
                return None
            mot = self.motors[n]
            e = m.group(2)
            live = {
                "ActPos": "%.4f" % mot.act_pos, "DesPos": "%.4f" % mot.des_pos,
                "PosError": "%.4f" % (mot.des_pos - mot.act_pos),
                "ActVel": "%.5f" % mot.act_vel, "DesVel": "%.5f" % mot.des_vel,
                "FltrVel": "%.5f" % mot.act_vel, "ServoOut": "%.2f" % mot.servo_out,
                "IqMeas": "%.2f" % mot.iq, "IdMeas": "0", "IqCmd": "%.2f" % mot.servo_out,
                "I2tSum": "%.1f" % mot.i2t_sum, "PhasePos": "%.1f" % mot.phase_pos,
                "AmpEna": "1" if mot.amp_ena else "0", "ClosedLoop": "1" if mot.closed_loop else "0",
                "AmpFault": "1" if mot.amp_fault else "0", "FeFatal": "1" if mot.fe_fatal else "0",
                "FeWarn": "1" if mot.fe_warn else "0", "I2tFault": "1" if mot.i2t_fault else "0",
                "EncLoss": "1" if mot.enc_loss else "0",
                "PlusLimit": "1" if self._plus_limit(mot) else "0",
                "MinusLimit": "1" if self._minus_limit(mot) else "0",
                "SoftPlusLimit": "0", "SoftMinusLimit": "0",
                "LimitStop": "1" if mot.limit_stop else "0", "InPos": "1" if mot.in_pos else "0",
                "DesVelZero": "1" if abs(mot.des_vel) < 1e-9 else "0",
                "DacLimit": "1" if mot.dac_limit else "0", "TriggerNotFound": "0", "AmpWarn": "0",
                "AuxFault": "0", "HomeInProgress": "1" if mot.homing else "0",
                "HomeComplete": "1" if mot.home_complete else "0", "HomePos": "%.2f" % mot.home_pos,
                "PhaseFound": "1" if mot.phase_found else "0",
                "Status[0]": "$" + _hex32(self._power_status_word(mot)),
            }
            return live.get(e)
        if self.family == "power":
            cm = re.match(r"^PowerBrick\[(\d)\]\.Chan\[(\d)\]\.(\w+)$", key)
            if cm:
                n = int(cm.group(1)) * 4 + int(cm.group(2)) + 1
                if n in self.motors:
                    mot = self.motors[n]
                    e = cm.group(3)
                    if e == "PlusLimit":
                        return "1" if self._plus_limit(mot) else "0"
                    if e == "MinusLimit":
                        return "1" if self._minus_limit(mot) else "0"
                    if e == "AmpEna":
                        return "1" if mot.amp_ena else "0"
                    if e == "AmpFault":
                        return "1" if mot.amp_fault else "0"
                    if e == "ServoCapt" or e == "PhaseCapt":
                        return str(int(mot.act_pos))
                    if e == "Status":
                        w = (int(self._plus_limit(mot)) << 9) | (int(self._minus_limit(mot)) << 8) | \
                            (int(mot.amp_fault) << 7) | (int(mot.enc_loss_input) << 28)
                        return "$" + _hex32(w)
            if key == "Sys.ServoCount":
                return str(self.cycle)
            if key == "Sys.Time":
                return "%.3f" % (self.cycle * SERVO_PERIOD_MS / 1000.0)
            if key == "Sys.Status":
                return "$" + _hex32(int(float(self.vars.get("Sys.WDTFault", "0"))) & 3)
            if key == "Sys.MotorsRunning":
                return str(sum(1 for m in self.motors.values() if abs(m.des_vel) > 1e-9))
        return None

    # ------------------------------------------------------------- motor commands
    def _motor_cmd(self, n: int, rest: str) -> List[str]:
        if n not in self.motors:
            raise PmacError("error #22: ILLEGAL PARAMETER" if self.family == "power" else "ERR003")
        mot = self.motors[n]
        r = rest.strip().lower()
        if r == "":
            return []
        if r == "?":
            return [self._axis_status(mot)]
        if r == "p":
            return ["%.4f" % mot.act_pos if self.family == "power" else "%.4f" % mot.act_pos]
        if r == "v":
            return ["%.5f" % (mot.act_vel * (1 if self.family == "power" else 32 * 96))]
        if r == "f":
            return ["%.4f" % (mot.des_pos - mot.act_pos)]
        if r == "k":
            self._kill(mot)
            return []
        if r in ("j/", "a"):
            self._enable(mot, closed=True)
            mot.traj_target = None
            mot.traj_dir = 0
            mot.des_pos = mot.act_pos if r == "j/" and not mot.closed_loop else mot.des_pos
            mot.des_vel = 0.0
            return []
        if r == "j+" or r == "j-":
            self._check_can_move(mot)
            mot.traj_target = None
            mot.traj_dir = 1 if r == "j+" else -1
            return []
        if r.startswith("j="):
            self._check_can_move(mot)
            if r == "j=":
                mot.traj_target = mot.des_pos
            else:
                mot.traj_target = float(r[2:])
            mot.traj_dir = 0
            return []
        if r.startswith("j:") or r.startswith("j^"):
            self._check_can_move(mot)
            d = float(r[2:])
            base = mot.des_pos if r.startswith("j:") else mot.act_pos
            mot.traj_target = base + d
            mot.traj_dir = 0
            return []
        if r.startswith("out"):
            val = float(r[3:]) if len(r) > 3 else 0.0
            self._enable(mot, closed=False)
            mot.open_loop_out = val / 100.0 * self._max_dac(mot)
            return []
        if r == "hmz":
            mot.home_pos = mot.act_pos
            mot.des_pos = mot.act_pos = mot.phys_pos = 0.0
            mot.home_complete = True
            return []
        if r == "hm":
            self._check_can_move(mot)
            mot.homing = True
            mot.home_timer = 2000  # cycles
            mot.traj_dir = -1
            return []
        if r == "$":
            if mot.amp_fault:
                raise PmacError("error #12: AMP FAULT")
            mot.phase_found = True
            if self.family == "power":
                self.vars["Motor[%d].PhaseFound" % n] = "1"
            return []
        if r == "$*":
            mot.amp_fault = mot.fe_fatal = mot.i2t_fault = mot.enc_loss = False
            return []
        if r.startswith("->"):
            return ["&1A" if n == 1 else "0"]
        raise PmacError("error #20: ILLEGAL CMD" if self.family == "power" else "ERR003")

    def _cs_cmd(self, c: int, rest: str) -> List[str]:
        r = rest.strip().lower()
        if r == "":
            return []
        if r == "?" or r == "??":
            return ["$00000000" if self.family == "power" else "000000000000"]
        if r == "%":
            return ["100"]
        if r in ("a", "q", "/", "abort"):
            for m in self.motors.values():
                m.traj_target = None
                m.traj_dir = 0
            return []
        if r.startswith("q"):
            return self._read(("Q" if self.family == "turbo" else "Q") + r[1:])
        raise PmacError("error #20: ILLEGAL CMD")

    def _check_can_move(self, mot: SimMotor):
        if not mot.amp_ena:
            self._enable(mot, closed=True)
        if not mot.closed_loop:
            raise PmacError("error #13: NOT CLOSED LOOP" if self.family == "power" else "ERR003")

    def _enable(self, mot: SimMotor, closed: bool):
        if self.vars.get("Sys.WDTFault", "0") not in ("0", ""):
            raise PmacError("error #38: WATCHDOG")
        if mot.amp_fault or mot.enc_loss:
            raise PmacError("error #12: AMP FAULT" if self.family == "power" else "ERR003")
        if self.family == "power" and not mot.phase_found and self.vars.get("Motor[%d].PhaseCtrl" % mot.n, "0") not in ("0",):
            raise PmacError("error #37: NOT PHASED")
        mot.amp_ena = True
        mot.closed_loop = closed
        mot.fe_fatal = False
        mot.limit_stop = False
        if closed:
            mot.des_pos = mot.act_pos
            mot.des_vel = 0.0
            mot.integ = 0.0
            mot.open_loop_out = 0.0

    def _kill(self, mot: SimMotor):
        mot.amp_ena = False
        mot.closed_loop = False
        mot.open_loop_out = 0.0
        mot.servo_out = 0.0
        mot.traj_target = None
        mot.traj_dir = 0
        mot.des_vel = 0.0
        mot.homing = False

    # ------------------------------------------------------------- status words
    def _plus_limit(self, mot: SimMotor) -> bool:
        if not mot.limits_wired:
            return False
        return mot.force_limits_active or mot.phys_pos > mot.limit_hi

    def _minus_limit(self, mot: SimMotor) -> bool:
        if not mot.limits_wired:
            return False
        return mot.force_limits_active or mot.phys_pos < mot.limit_lo

    def _power_status_word(self, mot: SimMotor) -> int:
        w = 0
        bits = {
            30: mot.homing, 29: self._minus_limit(mot), 28: self._plus_limit(mot), 27: mot.fe_warn,
            26: mot.fe_fatal, 25: mot.limit_stop, 24: mot.amp_fault, 21: mot.i2t_fault, 18: mot.enc_loss,
            15: mot.home_complete, 14: abs(mot.des_vel) < 1e-9, 13: mot.closed_loop, 12: mot.amp_ena,
            11: mot.in_pos, 9: mot.phase_found,
        }
        for b, v in bits.items():
            if v:
                w |= 1 << b
        return w

    def _axis_status(self, mot: SimMotor) -> str:
        if self.family == "power":
            w2 = (int(self.vars.get("Motor[%d].Coord" % mot.n, "1")) & 0xF) << 0
            return "$" + _hex32(self._power_status_word(mot)) + _hex32(w2)
        # turbo
        w1 = 0
        if True:
            w1 |= 1 << 23  # activated
        if self._minus_limit(mot):
            w1 |= 1 << 22
        if self._plus_limit(mot):
            w1 |= 1 << 21
        if mot.amp_ena:
            w1 |= 1 << 19
        if not mot.closed_loop:
            w1 |= 1 << 18
        if abs(mot.des_vel) < 1e-9:
            w1 |= 1 << 13
        if mot.homing:
            w1 |= 1 << 10
        w1 |= 1 << 6
        w2 = 0
        if mot.in_pos:
            w2 |= 1
        if mot.fe_warn:
            w2 |= 1 << 1
        if mot.fe_fatal:
            w2 |= 1 << 2
        if mot.amp_fault:
            w2 |= 1 << 3
        if mot.i2t_fault:
            w2 |= 1 << 5
        if mot.home_complete:
            w2 |= 1 << 10
        if mot.limit_stop:
            w2 |= 1 << 11
        if not mot.phase_found:
            w2 |= 1 << 9
        w2 |= 1 << 15  # assigned to CS 1 (bits 20-23 = 0)
        return "%06X%06X" % (w1, w2)

    def _global_status(self) -> str:
        if self.family == "power":
            return "$" + _hex32(int(float(self.vars.get("Sys.WDTFault", "0"))) & 3)
        return "%06X%06X" % (0, 0)

    # ------------------------------------------------------------- gather
    def _gather_read(self, tok: str) -> List[str]:
        low = tok.lower()
        if low == "gather.samples":
            return [str(len(self.gather_buf))]
        if low == "gather.enable":
            return [str(self.gather_enable)]
        if low == "gather.items":
            return [str(self.gather_items)]
        if low == "gather.period":
            return [str(self.gather_period)]
        if low == "gather.maxsamples" or low == "gather.maxlines":
            return [str(self.gather_max)]
        m = re.match(r"gather\.addr\[(\d+)\]", low)
        if m:
            return [self.gather_addr[int(m.group(1))] or "Sys.pushm"]
        raise PmacError("error #20: ILLEGAL CMD")

    def _gather_write(self, key: str, val: str) -> List[str]:
        low = key.lower()
        m = re.match(r"gather\.addr\[(\d+)\]", low)
        if m:
            self.gather_addr[int(m.group(1))] = val
            return []
        if low == "gather.items":
            self.gather_items = int(float(val))
        elif low == "gather.period":
            self.gather_period = max(1, int(float(val)))
        elif low in ("gather.maxsamples", "gather.maxlines"):
            self.gather_max = int(float(val))
        elif low == "gather.enable":
            e = int(float(val))
            if e == 2 or (e == 1 and self.gather_enable == 0):
                self.gather_buf = []
            self.gather_enable = e
        else:
            raise PmacError("error #20: ILLEGAL CMD")
        return []

    def _gather_sample(self):
        if self.gather_enable not in (1, 2):
            return
        if self.cycle % self.gather_period:
            return
        if len(self.gather_buf) >= self.gather_max:
            self.gather_enable = 0
            return
        row = []
        for k in range(self.gather_items):
            name = self.gather_addr[k][:-2] if self.gather_addr[k].endswith(".a") else self.gather_addr[k]
            try:
                v = self._live_value(name)
                if v is None:
                    v = self.vars.get(name, "0")
                row.append(float(v.lstrip("$")) if not v.startswith("$") else float(int(v[1:], 16)))
            except Exception:
                row.append(0.0)
        self.gather_buf.append(row)

    # ------------------------------------------------------------- physics
    def _gains(self, mot: SimMotor) -> Dict[str, float]:
        n = mot.n
        g = {}
        if self.family == "power":
            def f(e, d=0.0):
                try:
                    return float(self.vars.get("Motor[%d].%s" % (n, e), d))
                except ValueError:
                    return d
            g["kp"], g["kvfb"], g["kvff"] = f("Servo.Kp", 40), f("Servo.Kvfb", 15), f("Servo.Kvff", 15)
            g["kaff"], g["ki"], g["kfff"] = f("Servo.Kaff"), f("Servo.Ki"), f("Servo.Kfff")
            g["maxint"] = f("Servo.MaxInt", 28000)
            g["maxdac"] = f("MaxDac", 28377)
            g["i2tset"], g["i2ttrip"] = f("I2TSet"), f("I2tTrip")
            g["fefatal"], g["fewarn"] = f("FatalFeLimit", 2000), f("WarnFeLimit", 500)
            g["jogspeed"], g["jogta"] = f("JogSpeed", 50), f("JogTa", 50)
            g["inposband"] = f("InPosBand", 4)
            g["homevel"] = f("HomeVel", 5)
            g["swzvint"] = f("Servo.SwZvInt")
        else:
            def fi(s, d=0.0):
                try:
                    return float(self.vars.get("I%d%s" % (n, s), d))
                except ValueError:
                    return d
            i08, i09 = fi("08", 96), fi("09", 96)
            i30, i31, i32, i33, i35 = fi("30", 2000), fi("31", 1280), fi("32", 1280), fi("33"), fi("35")
            g["kp"] = units.turbo_natural_kp(i30, i08)
            g["kvfb"] = units.turbo_natural_kvfb(i30, i31, i09)
            g["kvff"] = units.turbo_natural_kvfb(i30, i32, i09)
            g["kaff"] = i30 * i35 * i09 / (128.0 * 16384.0)
            g["ki"] = i30 * i08 / 16384.0 * i33 / 8388608.0
            g["kfff"] = fi("68") * i08 / 16384.0
            g["maxint"] = 28000.0
            g["maxdac"] = fi("69", 20480)
            g["i2tset"], g["i2ttrip"] = fi("57"), fi("58") * 100.0
            g["fefatal"], g["fewarn"] = fi("11", 32000) / 16.0, fi("12", 16000) / 16.0
            g["jogspeed"], g["jogta"] = fi("22", 32), fi("20", 50)
            g["inposband"] = fi("28", 160) / 16.0
            g["homevel"] = fi("23", 5)
            g["swzvint"] = fi("34", 1)
        return g

    def _max_dac(self, mot: SimMotor) -> float:
        return self._gains(mot)["maxdac"]

    def _step_microstepping(self, mot: SimMotor, p, coulomb: float):
        """Direct micro-stepping: the rotor follows the commanded phase position with a load
        angle and slips (loses steps) when the demand exceeds the available pull-out torque."""
        if not mot.amp_ena:
            mot.act_vel = 0.0
            mot.stalled = False
            return
        demand = 0.0
        if p.ka > 0:
            demand = mot.des_acc / p.ka
            demand += coulomb * (1 if mot.des_vel > 0 else -1 if mot.des_vel < 0 else 0)
            demand += p.viscous_per_cycle * mot.des_vel / p.ka
        available = p.available_current(mot.des_vel) / max(1e-9, p.max_adc) * 32768.0
        if p.pull_out_out > 0:
            available = min(available, p.pull_out_out)
        available = max(1e-6, available)
        mot.stalled = abs(demand) > available
        if mot.stalled:
            slip = min(abs(mot.des_vel), (abs(demand) - available) / available * abs(mot.des_vel))
            mot.lost_steps += slip * (1 if mot.des_vel > 0 else -1 if mot.des_vel < 0 else 0)
        mot.rotor_lag = max(-p.full_step_counts, min(p.full_step_counts,
                                                     demand / available * p.full_step_counts))
        mot.phys_pos = mot.des_pos - mot.lost_steps - mot.rotor_lag
        mot.act_vel = mot.des_vel

    def _run(self):
        last = time.perf_counter()
        while not self._stop.is_set():
            time.sleep(0.01)
            now = time.perf_counter()
            n_cycles = int((now - last) * 1000.0 / SERVO_PERIOD_MS)
            n_cycles = min(n_cycles, 200)
            if n_cycles <= 0:
                continue
            last += n_cycles * SERVO_PERIOD_MS / 1000.0
            with self._slock:
                for _ in range(n_cycles):
                    self.step()

    def step(self, n_cycles: int = 1):
        for _ in range(n_cycles):
            self.cycle += 1
            for mot in self.motors.values():
                self._step_motor(mot, self._gains(mot))
            self._gather_sample()
            if self.scope_axis is not None and self.cycle % self.scope_decimate == 0:
                self._scope_sample()

    def _step_motor(self, mot: SimMotor, g: Dict[str, float]):
        dt_ms = SERVO_PERIOD_MS
        # -------- trajectory (jog) generation: trapezoidal in units/ms with accel time JogTa
        vmax = g["jogspeed"] * dt_ms  # counts per cycle
        jogta = g["jogta"]
        if jogta > 0:
            amax = vmax / max(1.0, jogta / dt_ms)
        elif jogta < 0:
            amax = (1.0 / abs(jogta)) * dt_ms * dt_ms  # inverse accel rate (ms^2/unit)
        else:
            amax = vmax  # instantaneous
        if mot.homing:
            mot.home_timer -= 1
            if mot.home_timer <= 0:
                mot.homing = False
                mot.traj_dir = 0
                mot.home_complete = True
                mot.home_pos = mot.act_pos
                mot.des_pos = mot.act_pos
                mot.des_vel = 0.0
                # re-zero
                mot.act_pos = mot.des_pos = mot.phys_pos = 0.0
            else:
                vh = g["homevel"] * dt_ms
                mot.des_vel = -abs(vh)
                mot.des_pos += mot.des_vel
        elif mot.closed_loop and mot.amp_ena:
            target_vel = 0.0
            if mot.traj_dir != 0:
                target_vel = mot.traj_dir * vmax
            elif mot.traj_target is not None:
                dist = mot.traj_target - mot.des_pos
                stop_dist = mot.des_vel ** 2 / (2 * amax) if amax > 0 else 0.0
                sgn = 1.0 if dist > 0 else -1.0
                target_vel = sgn * vmax if abs(dist) > stop_dist * 1.05 else 0.0
                dv = max(-amax, min(amax, target_vel - mot.des_vel))
                new_vel = mot.des_vel + dv
                if sgn * new_vel >= 0 and abs(new_vel) >= abs(dist):
                    # would reach or pass the target this cycle -> land exactly
                    mot.des_pos = mot.traj_target
                    mot.des_vel = 0.0
                    mot.traj_target = None
                    target_vel = None
            if target_vel is not None:
                dv = target_vel - mot.des_vel
                dv = max(-amax, min(amax, dv))
                mot.des_vel += dv
                mot.des_pos += mot.des_vel
            # limits stop motion
            if (mot.des_vel > 0 and self._plus_limit(mot)) or (mot.des_vel < 0 and self._minus_limit(mot)):
                mot.des_vel = 0.0
                mot.traj_dir = 0
                mot.traj_target = None
                mot.limit_stop = True
        mot.des_acc = mot.des_vel - mot.prev_des_vel
        mot.prev_des_vel = mot.des_vel

        # -------- servo law
        if mot.amp_ena:
            if mot.closed_loop:
                err = mot.des_pos - mot.act_pos
                if g["swzvint"] == 0 or abs(mot.des_vel) < 1e-9:
                    mot.integ += g["ki"] * err
                    mot.integ = max(-g["maxint"], min(g["maxint"], mot.integ))
                out = (g["kp"] * err + g["kvff"] * mot.des_vel - g["kvfb"] * mot.act_vel
                       + g["kaff"] * mot.des_acc + mot.integ
                       + g["kfff"] * (1 if mot.des_vel > 0 else (-1 if mot.des_vel < 0 else 0)))
            else:
                out = mot.open_loop_out
            lim = g["maxdac"]
            mot.dac_limit = abs(out) > lim
            out = max(-lim, min(lim, out))
        else:
            out = 0.0
            mot.dac_limit = False
        mot.servo_out = out
        mot.iq = out * (0.98 + 0.04 * random.random()) if mot.bus_ok else 0.0

        # -------- plant (physical model from core.motor_model)
        p = mot.plant
        out_cmd = mot.iq if mot.bus_ok else 0.0
        coulomb = p.coulomb_out * mot.extra_friction
        fric_accel = abs(p.accel_counts(coulomb, 0.0))
        if mot.micro_stepping:
            self._step_microstepping(mot, p, coulomb)
        else:
            i_accel = p.accel_counts(out_cmd, mot.act_vel)
            if abs(mot.act_vel) < 1e-4 and abs(i_accel) <= fric_accel:
                acc = 0.0
                mot.act_vel = 0.0
            else:
                sgn = 1.0 if mot.act_vel > 0 else (-1.0 if mot.act_vel < 0 else
                                                   (1.0 if i_accel > 0 else -1.0))
                acc = i_accel - fric_accel * sgn - p.viscous_per_cycle * mot.act_vel
            # compliant coupling: a lightly damped mode excited by the acceleration
            if mot.res_freq_hz > 0:
                wn = 2 * math.pi * mot.res_freq_hz * dt_ms / 1000.0
                res_acc = -2 * mot.res_zeta * wn * mot.res_vel - wn * wn * mot.res_pos + acc * 0.6
                mot.res_vel += res_acc
                mot.res_pos += mot.res_vel
            mot.act_vel += acc
            mot.phys_pos += mot.act_vel
            if mot.res_freq_hz > 0:
                mot.phys_pos += mot.res_vel
            if mot.spec.is_stepper and p.pull_out_out > 0:
                mot.stalled = abs(out_cmd) > p.pull_out_out * 1.05
        # -------- feedback: direction, quantisation and noise
        if mot.micro_stepping:
            # direct micro-stepping feeds the commanded phase position back, so the
            # controller sees no following error even while the rotor is losing steps
            mot.act_pos = mot.des_pos
        else:
            fb = mot.feedback_sign * mot.phys_pos
            if not math.isfinite(fb):  # numerical blow-up: treat as a dead axis
                mot.phys_pos = fb = 0.0
                mot.act_vel = mot.res_vel = mot.res_pos = 0.0
                self._kill(mot)
            if mot.noise > 0 and mot.amp_ena:
                fb += random.uniform(-mot.noise, mot.noise)
            if mot.spec.quantize:
                fb = math.floor(fb)
            mot.act_pos = fb
        if mot.enc_loss_input:
            mot.enc_loss = True
            if mot.amp_ena:
                self._kill(mot)

        # -------- protections
        if mot.amp_ena and mot.closed_loop:
            err = mot.des_pos - mot.act_pos
            mot.fe_warn = abs(err) > g["fewarn"] if g["fewarn"] > 0 else False
            if g["fefatal"] > 0 and abs(err) > g["fefatal"]:
                mot.fe_fatal = True
                self._kill(mot)
        # I2T uses output^2 in 16-bit units; trip units ~ (16bit^2 * s)
        if g["i2ttrip"] > 0:
            mot.i2t_sum += (mot.iq ** 2 - g["i2tset"] ** 2) * dt_ms / 1000.0
            mot.i2t_sum = max(0.0, mot.i2t_sum)
            if mot.i2t_sum > g["i2ttrip"] and mot.amp_ena:
                mot.i2t_fault = True
                mot.amp_fault = True
                self._kill(mot)
        # in position
        if mot.amp_ena and mot.closed_loop and abs(mot.des_vel) < 1e-9 and \
                abs(mot.des_pos - mot.act_pos) <= g["inposband"]:
            mot.in_pos_count += 1
        else:
            mot.in_pos_count = 0
        mot.in_pos = mot.in_pos_count >= 10 or (not mot.amp_ena)
        if not mot.amp_ena:
            mot.act_vel *= 0.9  # coast to a stop when killed
            mot.act_pos += mot.feedback_sign * mot.act_vel * 0.0
