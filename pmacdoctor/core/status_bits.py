"""Status word decoding for Turbo PMAC and Power PMAC, plus EPICS motor record MSTA.

Bit tables are data so they can be displayed in the UI and used by the rule engine.

Sources:
  * Power PMAC Software Reference Manual, Motor[x].Status[0] (first word of ``#x?``)
    and Sys.Status (``?``).
  * Turbo PMAC Software Reference Manual, ``#x?`` (two 24-bit words) and ``???``.
  * DiamondLightSource/pmac pmacHardwarePower.cpp / pmacHardwareTurbo.cpp (which
    bits the EPICS driver maps into AXISn:READ_STAT1..3 / READ_CTRLSTAT1..3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# severity levels used across the app
OK = "ok"
INFO = "info"
WARN = "warning"
FAULT = "fault"


@dataclass(frozen=True)
class BitDef:
    bit: int
    name: str
    description: str
    severity_when_set: str = INFO  # ok / info / warning / fault
    severity_when_clear: str = OK  # some bits are bad when *clear* (e.g. PhaseFound)


# --------------------------------------------------------------------------------------
# Power PMAC  -  first 32-bit word returned by "#x?" (== Motor[x].Status[0])
# --------------------------------------------------------------------------------------
POWER_MOTOR_STATUS_WORD1: List[BitDef] = [
    BitDef(31, "TriggerMove", "Move-until-trigger in progress"),
    BitDef(30, "HomeInProgress", "Homing search move in progress"),
    BitDef(29, "MinusLimit", "Hardware negative limit switch active", WARN),
    BitDef(28, "PlusLimit", "Hardware positive limit switch active", WARN),
    BitDef(27, "FeWarn", "Warning following error limit (Motor[x].WarnFeLimit) exceeded", WARN),
    BitDef(26, "FeFatal", "Fatal following error (Motor[x].FatalFeLimit) exceeded - motor killed", FAULT),
    BitDef(25, "LimitStop", "Motor stopped on a hardware or software limit", WARN),
    BitDef(24, "AmpFault", "Amplifier fault input active (I2T, over-current, bus, STO, over-temp...)", FAULT),
    BitDef(23, "SoftMinusLimit", "Software negative limit (Motor[x].MinPos) active", WARN),
    BitDef(22, "SoftPlusLimit", "Software positive limit (Motor[x].MaxPos) active", WARN),
    BitDef(21, "I2tFault", "Integrated current (I2T) protection tripped", FAULT),
    BitDef(20, "TriggerNotFound", "Last move-until-trigger did not find its trigger", WARN),
    BitDef(19, "AmpWarn", "Amplifier warning input active", WARN),
    BitDef(18, "EncLoss", "Encoder loss detected (Motor[x].pEncLoss)", FAULT),
    BitDef(17, "AuxFault", "Auxiliary fault input active", FAULT),
    BitDef(16, "Reserved16", "Reserved"),
    BitDef(15, "HomeComplete", "Motor has been homed", INFO, WARN),
    BitDef(14, "DesVelZero", "Commanded velocity is zero (not moving)"),
    BitDef(13, "ClosedLoop", "Servo loop closed", INFO, INFO),
    BitDef(12, "AmpEna", "Amplifier enabled", INFO, INFO),
    BitDef(11, "InPos", "In position (within Motor[x].InPosBand for InPosTime)"),
    BitDef(10, "BlockRequest", "Motion program requesting next block"),
    BitDef(9, "PhaseFound", "Commutation phase reference established", INFO, WARN),
    BitDef(8, "TriggerSpeedSel", "Trigger speed select"),
    BitDef(7, "GantryHomed", "Gantry follower homed"),
    BitDef(6, "Reserved6", "Reserved"),
    BitDef(5, "Reserved5", "Reserved"),
    BitDef(4, "Reserved4", "Reserved"),
    BitDef(3, "Reserved3", "Reserved"),
    BitDef(2, "BlDir", "Backlash direction flag"),
    BitDef(1, "Reserved1", "Reserved"),
    BitDef(0, "Reserved0", "Reserved"),
]

# Named Power PMAC motor status *elements* that the app reads directly when it has a
# direct link (they are authoritative; the hex word is only used for EPICS-only mode).
POWER_MOTOR_STATUS_ELEMENTS = [
    "AmpEna", "ClosedLoop", "AmpFault", "FeFatal", "FeWarn", "I2tFault", "EncLoss",
    "PlusLimit", "MinusLimit", "SoftPlusLimit", "SoftMinusLimit", "LimitStop",
    "HomeComplete", "HomeInProgress", "InPos", "DesVelZero", "PhaseFound",
    "TriggerNotFound", "AmpWarn", "AuxFault", "DacLimit", "SoftLimit", "Csolve",
]

# --------------------------------------------------------------------------------------
# Power PMAC global status  -  "?" returns Sys.Status as 8 hex digits
# --------------------------------------------------------------------------------------
POWER_GLOBAL_STATUS: List[BitDef] = [
    BitDef(0, "WDTFault_b0", "Watchdog fault bit 0 (Sys.WDTFault=1: foreground)", FAULT),
    BitDef(1, "WDTFault_b1", "Watchdog fault bit 1 (Sys.WDTFault=2: background)", FAULT),
    BitDef(2, "PwrOnFault", "Power-on fault (reset occurred because of a fault)", WARN),
    BitDef(3, "ProjectLoadErr", "Project load error at power-up", FAULT),
    BitDef(4, "ConfigLoadErr", "Saved configuration load error", FAULT),
    BitDef(5, "HWChangeErr", "Hardware configuration changed since last save", FAULT),
    BitDef(6, "FileConfigErr", "Configuration file error", FAULT),
    BitDef(7, "Default", "Controller running factory default configuration", WARN),
    BitDef(8, "NoClocks", "No servo/phase clocks detected", FAULT),
    BitDef(9, "AbortAll", "Abort-all input (Sys.pAbortAll) is active", WARN),
    BitDef(10, "BufSizeErr", "Buffer size error", FAULT),
    BitDef(11, "FlashSizeErr", "Flash size error", FAULT),
    BitDef(12, "CK3WConfigErr_b0", "CK3W configuration error", WARN),
    BitDef(13, "CK3WConfigErr_b1", "CK3W configuration error", WARN),
    BitDef(14, "CK3WConfigErr_b2", "CK3W configuration error", WARN),
    BitDef(15, "CK3WHWChange", "CK3W hardware change detected", WARN),
]

# --------------------------------------------------------------------------------------
# Turbo PMAC  -  "#x?" returns 12 hex digits == two 24-bit words
# --------------------------------------------------------------------------------------
TURBO_MOTOR_STATUS_WORD1: List[BitDef] = [
    BitDef(23, "MotorActivated", "Motor activated (Ixx00=1)", INFO, WARN),
    BitDef(22, "NegLimit", "Negative hardware limit set", WARN),
    BitDef(21, "PosLimit", "Positive hardware limit set", WARN),
    BitDef(20, "HandwheelEnabled", "Extended servo algorithm / handwheel enabled"),
    BitDef(19, "AmpEnabled", "Amplifier enabled", INFO, INFO),
    BitDef(18, "OpenLoop", "Open-loop mode (servo loop NOT closed)", WARN),
    BitDef(17, "MoveTimerActive", "Move timer active (move in progress)"),
    BitDef(16, "IntegrationMode", "Integration mode (Ixx34)"),
    BitDef(15, "DwellInProgress", "Dwell in progress"),
    BitDef(14, "DataBlockError", "Data block error", WARN),
    BitDef(13, "DesVelZero", "Desired velocity zero"),
    BitDef(12, "AbortDecel", "Abort deceleration in progress", WARN),
    BitDef(11, "BlockRequest", "Block request"),
    BitDef(10, "HomeSearchInProgress", "Home search in progress"),
    BitDef(9, "UserPhaseEnable", "User-written phase routine enabled"),
    BitDef(8, "UserServoEnable", "User-written servo routine enabled"),
    BitDef(7, "AltSourceDest", "Alternate source/destination"),
    BitDef(6, "PhasedMotor", "Motor is PMAC-commutated (Ixx01)"),
    BitDef(5, "FollowingOffsetMode", "Following offset mode"),
    BitDef(4, "FollowingEnabled", "Position following enabled"),
    BitDef(3, "ErrorTrigger", "Error trigger"),
    BitDef(2, "SoftPosCapture", "Software position capture"),
    BitDef(1, "AltCmdOutputMode", "Alternate command output mode"),
    BitDef(0, "MaxRapidSpeed", "Maximum rapid speed"),
]

TURBO_MOTOR_STATUS_WORD2: List[BitDef] = [
    BitDef(15, "AssignedToCS", "Motor assigned to a coordinate system"),
    BitDef(14, "Reserved14", "Reserved"),
    BitDef(13, "ForegroundInPos", "Foreground in-position"),
    BitDef(12, "StoppedOnDesPosLimit", "Stopped on desired position limit", WARN),
    BitDef(11, "StoppedOnPosLimit", "Stopped on position limit", WARN),
    BitDef(10, "HomeComplete", "Home complete", INFO, WARN),
    BitDef(9, "PhasingSearchError", "Phasing search error", FAULT),
    BitDef(8, "PhasingRefError", "Phasing reference error", FAULT),
    BitDef(7, "TriggerMove", "Trigger move"),
    BitDef(6, "IntegratedFatalFE", "Integrated fatal following error", FAULT),
    BitDef(5, "I2TAmpFault", "I2T amplifier fault (Ixx57/Ixx58)", FAULT),
    BitDef(4, "BacklashDir", "Backlash direction flag"),
    BitDef(3, "AmpFault", "Amplifier fault", FAULT),
    BitDef(2, "FatalFE", "Fatal following error (Ixx11)", FAULT),
    BitDef(1, "WarningFE", "Warning following error (Ixx12)", WARN),
    BitDef(0, "InPosition", "In position"),
]

# Turbo "???" first 24-bit word (bits identified by the Diamond driver and the Turbo SRM).
TURBO_GLOBAL_STATUS_WORD1: List[BitDef] = [
    BitDef(23, "RTIReentry", "Real-time interrupt re-entry (servo overload)", FAULT),
    BitDef(22, "ServoError", "Servo error", FAULT),
    BitDef(21, "GatherOn", "Data gathering in progress"),
    BitDef(20, "ServoErrorFlag", "Servo error flag (Diamond driver bit 20)", FAULT),
    BitDef(15, "Watchdog", "Watchdog timer tripped / fatal", FAULT),
    BitDef(13, "ChecksumError", "PROM checksum error", FAULT),
    BitDef(11, "DPRAMError", "DPRAM error", FAULT),
    BitDef(10, "FlashError", "Flash error", FAULT),
    BitDef(9, "RTIReentryFlag", "Real-time interrupt re-entry", FAULT),
]

# --------------------------------------------------------------------------------------
# EPICS motor record .MSTA
# --------------------------------------------------------------------------------------
MSTA_BITS: List[BitDef] = [
    BitDef(0, "DIRECTION", "Last move direction positive"),
    BitDef(1, "DONE", "Motion complete"),
    BitDef(2, "PLUS_LS", "Plus limit switch", WARN),
    BitDef(3, "HOMELS", "Home switch active"),
    BitDef(5, "POSITION", "Closed-loop position control (motor can hold position)", INFO, WARN),
    BitDef(6, "SLIP_STALL", "Slip/stall detected", FAULT),
    BitDef(7, "HOME", "At home position"),
    BitDef(8, "PRESENT", "Encoder present"),
    BitDef(9, "PROBLEM", "Driver reports a problem (fault, limits disabled, comms...)", FAULT),
    BitDef(10, "MOVING", "Motor moving"),
    BitDef(11, "GAIN_SUPPORT", "Driver supports closed-loop toggle"),
    BitDef(12, "COMM_ERR", "Communication error", FAULT),
    BitDef(13, "MINUS_LS", "Minus limit switch", WARN),
    BitDef(14, "HOMED", "Motor homed", INFO, WARN),
]


@dataclass
class DecodedBit:
    name: str
    description: str
    bit: int
    value: bool
    severity: str


def decode_bits(word: int, defs: List[BitDef]) -> List[DecodedBit]:
    out = []
    for d in defs:
        v = bool((word >> d.bit) & 1)
        sev = d.severity_when_set if v else d.severity_when_clear
        out.append(DecodedBit(d.name, d.description, d.bit, v, sev))
    return out


def bits_to_dict(word: int, defs: List[BitDef]) -> Dict[str, bool]:
    return {d.name: bool((word >> d.bit) & 1) for d in defs}


@dataclass
class AxisStatus:
    """Normalised motor status, independent of controller family."""

    axis: int
    raw_hex: str = ""
    word1: int = 0
    word2: int = 0
    family: str = "power"  # power | turbo
    # normalised flags
    amp_enabled: bool = False
    closed_loop: bool = False
    amp_fault: bool = False
    fe_fatal: bool = False
    fe_warn: bool = False
    i2t_fault: bool = False
    enc_loss: bool = False
    plus_limit: bool = False
    minus_limit: bool = False
    soft_plus_limit: bool = False
    soft_minus_limit: bool = False
    limit_stop: bool = False
    home_complete: bool = False
    homing: bool = False
    in_position: bool = False
    des_vel_zero: bool = True
    phase_found: bool = True
    trigger_not_found: bool = False
    amp_warn: bool = False
    aux_fault: bool = False
    phasing_error: bool = False
    activated: bool = True
    cs_number: int = 0
    # dynamic quantities (filled when available)
    position: Optional[float] = None
    following_error: Optional[float] = None
    velocity: Optional[float] = None
    extras: Dict[str, object] = field(default_factory=dict)

    @property
    def moving(self) -> bool:
        return self.amp_enabled and not self.des_vel_zero

    @property
    def any_fault(self) -> bool:
        return any([self.amp_fault, self.fe_fatal, self.i2t_fault, self.enc_loss,
                    self.aux_fault, self.phasing_error])

    @property
    def any_warning(self) -> bool:
        return any([self.fe_warn, self.plus_limit, self.minus_limit, self.soft_plus_limit,
                    self.soft_minus_limit, self.limit_stop, self.amp_warn,
                    self.trigger_not_found]) or (self.amp_enabled and not self.phase_found)

    @property
    def summary(self) -> str:
        if self.any_fault:
            names = []
            if self.amp_fault:
                names.append("AMP FAULT")
            if self.fe_fatal:
                names.append("FATAL FE")
            if self.i2t_fault:
                names.append("I2T")
            if self.enc_loss:
                names.append("ENC LOSS")
            if self.aux_fault:
                names.append("AUX FAULT")
            if self.phasing_error:
                names.append("PHASING ERR")
            return " / ".join(names)
        if not self.amp_enabled:
            return "Killed"
        if not self.closed_loop:
            return "Open loop"
        if self.plus_limit or self.minus_limit:
            return "On limit"
        if self.moving:
            return "Moving"
        if self.in_position:
            return "In position"
        return "Enabled"

    @property
    def severity(self) -> str:
        if self.any_fault:
            return FAULT
        if self.any_warning:
            return WARN
        if not self.amp_enabled:
            return INFO
        return OK

    def decoded(self) -> List[DecodedBit]:
        if self.family == "power":
            return decode_bits(self.word1, POWER_MOTOR_STATUS_WORD1)
        return decode_bits(self.word1, TURBO_MOTOR_STATUS_WORD1) + decode_bits(
            self.word2, TURBO_MOTOR_STATUS_WORD2)


def parse_power_axis_status(axis: int, reply: str) -> AxisStatus:
    """Parse the reply to ``#x?`` from a Power PMAC: ``$`` + 16 hex digits."""
    s = reply.strip().lstrip("$")
    s = s.split()[0] if s else ""
    if len(s) < 8:
        raise ValueError("bad Power PMAC status reply %r" % reply)
    w1 = int(s[:8], 16)
    w2 = int(s[8:16], 16) if len(s) >= 16 else 0
    return power_status_from_words(axis, w1, w2, raw_hex=s)


def power_status_from_words(axis: int, w1: int, w2: int, raw_hex: str = "") -> AxisStatus:
    b = bits_to_dict(w1, POWER_MOTOR_STATUS_WORD1)
    st = AxisStatus(axis=axis, raw_hex=raw_hex or "%08X%08X" % (w1, w2), word1=w1, word2=w2,
                    family="power")
    st.amp_enabled = b["AmpEna"]
    st.closed_loop = b["ClosedLoop"]
    st.amp_fault = b["AmpFault"]
    st.fe_fatal = b["FeFatal"]
    st.fe_warn = b["FeWarn"]
    st.i2t_fault = b["I2tFault"]
    st.enc_loss = b["EncLoss"]
    st.plus_limit = b["PlusLimit"]
    st.minus_limit = b["MinusLimit"]
    st.soft_plus_limit = b["SoftPlusLimit"]
    st.soft_minus_limit = b["SoftMinusLimit"]
    st.limit_stop = b["LimitStop"]
    st.home_complete = b["HomeComplete"]
    st.homing = b["HomeInProgress"]
    st.in_position = b["InPos"]
    st.des_vel_zero = b["DesVelZero"]
    st.phase_found = b["PhaseFound"]
    st.trigger_not_found = b["TriggerNotFound"]
    st.amp_warn = b["AmpWarn"]
    st.aux_fault = b["AuxFault"]
    return st


def parse_turbo_axis_status(axis: int, reply: str) -> AxisStatus:
    """Parse the reply to ``#x?`` from a Turbo PMAC: 12 hex digits (two 24-bit words)."""
    s = reply.strip().lstrip("$")
    s = s.split()[0] if s else ""
    if len(s) < 12:
        raise ValueError("bad Turbo PMAC status reply %r" % reply)
    w1 = int(s[:6], 16)
    w2 = int(s[6:12], 16)
    return turbo_status_from_words(axis, w1, w2, raw_hex=s)


def turbo_status_from_words(axis: int, w1: int, w2: int, raw_hex: str = "") -> AxisStatus:
    b1 = bits_to_dict(w1, TURBO_MOTOR_STATUS_WORD1)
    b2 = bits_to_dict(w2, TURBO_MOTOR_STATUS_WORD2)
    st = AxisStatus(axis=axis, raw_hex=raw_hex or "%06X%06X" % (w1, w2), word1=w1, word2=w2,
                    family="turbo")
    st.activated = b1["MotorActivated"]
    st.amp_enabled = b1["AmpEnabled"]
    st.closed_loop = not b1["OpenLoop"]
    st.amp_fault = b2["AmpFault"]
    st.fe_fatal = b2["FatalFE"] or b2["IntegratedFatalFE"]
    st.fe_warn = b2["WarningFE"]
    st.i2t_fault = b2["I2TAmpFault"]
    st.plus_limit = b1["PosLimit"]
    st.minus_limit = b1["NegLimit"]
    st.limit_stop = b2["StoppedOnPosLimit"] or b2["StoppedOnDesPosLimit"]
    st.home_complete = b2["HomeComplete"]
    st.homing = b1["HomeSearchInProgress"]
    st.in_position = b2["InPosition"]
    st.des_vel_zero = b1["DesVelZero"]
    st.phasing_error = b2["PhasingSearchError"] or b2["PhasingRefError"]
    st.phase_found = not st.phasing_error
    if b2["AssignedToCS"]:
        st.cs_number = ((w2 >> 20) & 0xF) + 1
    return st


def parse_power_global_status(reply: str) -> Tuple[int, List[DecodedBit]]:
    s = reply.strip().lstrip("$").split()[0] if reply.strip() else "0"
    w = int(s, 16)
    return w, decode_bits(w, POWER_GLOBAL_STATUS)


def parse_turbo_global_status(reply: str) -> Tuple[int, List[DecodedBit]]:
    s = reply.strip().lstrip("$").split()[0] if reply.strip() else "0"
    w = int(s[:6], 16) if len(s) >= 6 else int(s, 16)
    return w, decode_bits(w, TURBO_GLOBAL_STATUS_WORD1)


def decode_msta(msta: int) -> List[DecodedBit]:
    return decode_bits(int(msta), MSTA_BITS)
