"""Variable / structure-element knowledge base.

Every entry is a template: ``{m}`` is the motor number (1-based), ``{i}`` the ASIC
(Gate3 / PowerBrick) index (0-based), ``{j}`` the channel index (0-based), ``{c}`` a
coordinate system and ``{e}`` an encoder-table entry.

Groups are used by the variable browser and the rule engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

SETUP = "setup"
STATUS = "status"


@dataclass(frozen=True)
class VarDef:
    key: str
    label: str
    group: str
    desc: str
    unit: str = ""
    kind: str = SETUP
    default: str = ""
    equiv: str = ""  # Turbo I-var equivalent for Power elements and vice versa

    def resolve(self, **kw) -> str:
        return self.key.format(**kw)


# ======================================================================================
# POWER PMAC - Motor[x] elements
# ======================================================================================
G_ACT = "Activation & pointers"
G_SCALE = "Scaling & commutation"
G_CUR = "Current loop & I2T"
G_SERVO = "Servo (PID) gains"
G_SAFE = "Safety & motion limits"
G_MOTION = "Jog / home / in-position"
G_STAT = "Live status"
G_GLOBAL = "Global (Sys)"
G_BRICK = "Power Brick LV amplifier"
G_CHAN = "Gate3 hardware channel"
G_ENC = "Encoder conversion table"
G_CS = "Coordinate system"

POWER_MOTOR: List[VarDef] = [
    # Activation / pointers
    VarDef("Motor[{m}].ServoCtrl", "ServoCtrl", G_ACT, "Motor activation: 0 = deactivated, 1 = active", default="0", equiv="Ixx00"),
    VarDef("Motor[{m}].PhaseCtrl", "PhaseCtrl", G_ACT, "Commutation enable/mode: 0 none, 1 direct PWM, 4 Gate3 4-step (Power Brick), 6 direct microstepping", default="0", equiv="Ixx01"),
    VarDef("Motor[{m}].pDac", "pDac", G_ACT, "Command output register address (PowerBrick[i].Chan[j].Pwm[0].a)", equiv="Ixx02"),
    VarDef("Motor[{m}].pEnc", "pEnc", G_ACT, "Position-loop feedback address (EncTable[n].a)", equiv="Ixx03"),
    VarDef("Motor[{m}].pEnc2", "pEnc2", G_ACT, "Velocity-loop feedback address", equiv="Ixx04"),
    VarDef("Motor[{m}].pLimits", "pLimits", G_ACT, "Overtravel limit flag address (PowerBrick[i].Chan[j].Status.a). 0 = limits not used", equiv="Ixx25"),
    VarDef("Motor[{m}].LimitBits", "LimitBits", G_ACT, "Bit number of the +limit flag in pLimits word (default 9 for Gate3 Status)", default="9"),
    VarDef("Motor[{m}].pAmpFault", "pAmpFault", G_ACT, "Amplifier fault flag address. 0 = fault input ignored", equiv="Ixx42"),
    VarDef("Motor[{m}].AmpFaultLevel", "AmpFaultLevel", G_ACT, "Amp fault polarity: 0 = low true, 1 = high true (Power Brick LV uses 1)", default="0"),
    VarDef("Motor[{m}].AmpFaultBit", "AmpFaultBit", G_ACT, "Bit of the amp fault flag in pAmpFault word", default="7"),
    VarDef("Motor[{m}].pAmpEnable", "pAmpEnable", G_ACT, "Amplifier enable output address"),
    VarDef("Motor[{m}].pEncLoss", "pEncLoss", G_ACT, "Encoder loss flag address (PowerBrick[i].Chan[j].Status.a bit 28 for quadrature). 0 = disabled"),
    VarDef("Motor[{m}].EncLossBit", "EncLossBit", G_ACT, "Bit number of the encoder-loss flag", default="28"),
    VarDef("Motor[{m}].EncLossLevel", "EncLossLevel", G_ACT, "Encoder-loss flag polarity (1 = high true)", default="1"),
    VarDef("Motor[{m}].EncLossLimit", "EncLossLimit", G_ACT, "Number of consecutive scans before encoder loss is declared", default="0"),
    VarDef("Motor[{m}].pCaptFlag", "pCaptFlag", G_ACT, "Capture flag address used for homing"),
    VarDef("Motor[{m}].pCaptPos", "pCaptPos", G_ACT, "Captured position register address"),
    VarDef("Motor[{m}].CaptFlagBit", "CaptFlagBit", G_ACT, "Bit of capture flag"),
    VarDef("Motor[{m}].CaptureMode", "CaptureMode", G_ACT, "Position capture mode"),
    VarDef("Motor[{m}].pAbsPos", "pAbsPos", G_ACT, "Absolute power-on position source address (0 = none)"),
    VarDef("Motor[{m}].AbsPosFormat", "AbsPosFormat", G_ACT, "Absolute position data format ($aabbccdd)"),
    VarDef("Motor[{m}].AbsPosSf", "AbsPosSf", G_ACT, "Absolute position scale factor"),
    VarDef("Motor[{m}].PowerOnMode", "PowerOnMode", G_ACT, "Power-on mode bits: bit1 = absolute phasing at power up, bit2 = auto HMZ", default="0", equiv="Ixx80"),
    VarDef("Motor[{m}].AdcMask", "AdcMask", G_ACT, "Current ADC mask ($FFFC0000 for Power Brick, 18-bit ADC data)", default="$FFF00000", equiv="Ixx84"),
    VarDef("Motor[{m}].Coord", "Coord", G_ACT, "Coordinate system this motor is assigned to", kind=STATUS),
    VarDef("Motor[{m}].pMasterEnc", "pMasterEnc", G_ACT, "Master (following) position address"),
    VarDef("Motor[{m}].MasterCtrl", "MasterCtrl", G_ACT, "Following mode: 0 off, 1 normal, 3 offset", default="0", equiv="Ixx06"),
    # Scaling / commutation
    VarDef("Motor[{m}].PosSf", "PosSf", G_SCALE, "Position scale factor applied to pEnc data", default="1", equiv="Ixx08"),
    VarDef("Motor[{m}].Pos2Sf", "Pos2Sf", G_SCALE, "Velocity-loop feedback scale factor", default="1", equiv="Ixx09"),
    VarDef("Motor[{m}].PosUnit", "PosUnit", G_SCALE, "Position unit code for IDE display (0 counts, 3 mm, 11 deg...)", default="0"),
    VarDef("Motor[{m}].PhasePosSf", "PhasePosSf", G_SCALE, "Commutation position scale factor = 2048*PolePairs/(256*CountsPerRev) for quadrature. 0 for brush/microstep", equiv="Ixx70/Ixx71"),
    VarDef("Motor[{m}].pPhaseEnc", "pPhaseEnc", G_SCALE, "Commutation feedback address (PowerBrick[i].Chan[j].PhaseCapt.a)", equiv="Ixx83"),
    VarDef("Motor[{m}].PhaseEncLeftShift", "PhaseEncLeftShift", G_SCALE, "Left shift applied to commutation feedback (serial encoders)", default="0"),
    VarDef("Motor[{m}].PhaseEncRightShift", "PhaseEncRightShift", G_SCALE, "Right shift applied to commutation feedback", default="0"),
    VarDef("Motor[{m}].PhaseOffset", "PhaseOffset", G_SCALE, "Angle between phases: 683 (120 deg) for 3-phase brushless, 512 (90 deg) for 2-phase stepper/brush", default="683", equiv="Ixx72"),
    VarDef("Motor[{m}].PhaseMode", "PhaseMode", G_SCALE, "Bit0: 1 = two-phase motor; bit1: 1 = Id integrator off (3 = brush motor)", default="0"),
    VarDef("Motor[{m}].PhaseTableBias", "PhaseTableBias", G_SCALE, "Commutation table bias (set +/-512 only during brush-motor current-loop tuning, else 0)", default="0"),
    VarDef("Motor[{m}].SlipGain", "SlipGain", G_SCALE, "Slip gain (induction / direct microstepping)", default="0", equiv="Ixx78"),
    VarDef("Motor[{m}].AdvGain", "AdvGain", G_SCALE, "Phase advance gain", default="0", equiv="Ixx56"),
    VarDef("Motor[{m}].pAbsPhasePos", "pAbsPhasePos", G_SCALE, "Absolute phase position source (halls / serial encoder / resolver). 0 = none", equiv="Ixx81"),
    VarDef("Motor[{m}].AbsPhasePosFormat", "AbsPhasePosFormat", G_SCALE, "Absolute phasing data format ($400030C for 120 deg halls)", equiv="Ixx91"),
    VarDef("Motor[{m}].AbsPhasePosSf", "AbsPhasePosSf", G_SCALE, "Absolute phasing scale factor (+/-2048/12 for halls)"),
    VarDef("Motor[{m}].AbsPhasePosOffset", "AbsPhasePosOffset", G_SCALE, "Absolute phasing offset, or qualifying travel for stepper phasing (2048/5)", equiv="Ixx75"),
    VarDef("Motor[{m}].AbsPhasePosForce", "AbsPhasePosForce", G_SCALE, "Hall phasing correction value (PhasePos at home)"),
    VarDef("Motor[{m}].PhaseFindingDac", "PhaseFindingDac", G_SCALE, "Output magnitude for stepper phasing search (start ~I2TSet/2)", equiv="Ixx73"),
    VarDef("Motor[{m}].PhaseFindingTime", "PhaseFindingTime", G_SCALE, "Stepper phasing time in RTI periods (must be 255..32767)", equiv="Ixx74"),
    VarDef("Motor[{m}].PhasePos", "PhasePos", G_SCALE, "Current commutation phase position (2048 = one cycle)", kind=STATUS),
    VarDef("Motor[{m}].PhaseFound", "PhaseFound", G_SCALE, "1 = phase reference established", kind=STATUS),
    # Current loop & I2T
    VarDef("Motor[{m}].PwmSf", "PwmSf", G_CUR, "PWM scale factor = 0.95*16384 (*MotorV/BusV if bus > motor voltage). Negative reverses commutation direction", default="0", equiv="Ixx66"),
    VarDef("Motor[{m}].IiGain", "IiGain", G_CUR, "Current-loop integral gain (transient effort)", default="0", equiv="Ixx61"),
    VarDef("Motor[{m}].IpfGain", "IpfGain", G_CUR, "Current-loop forward-path proportional gain (damping)", default="0", equiv="Ixx62"),
    VarDef("Motor[{m}].IpbGain", "IpbGain", G_CUR, "Current-loop back-path proportional gain", default="0", equiv="Ixx76"),
    VarDef("Motor[{m}].IdCmd", "IdCmd", G_CUR, "Direct (magnetisation) current command - direct microstepping only (I2TSet/2 .. I2TSet)", default="0", equiv="Ixx77"),
    VarDef("Motor[{m}].IaBias", "IaBias", G_CUR, "Phase A current bias (manual force phasing)", default="0", equiv="Ixx29"),
    VarDef("Motor[{m}].IbBias", "IbBias", G_CUR, "Phase B current bias (manual force phasing - must be 0 for normal operation)", default="0", equiv="Ixx79"),
    VarDef("Motor[{m}].MaxDac", "MaxDac", G_CUR, "Output command limit (16-bit units, 32767 max) = peak current limit", default="28377", equiv="Ixx69"),
    VarDef("Motor[{m}].I2TSet", "I2TSet", G_CUR, "Continuous current limit in 16-bit units (= RMS cont * 32768 * sqrt2 * cos30 / MaxAdc for brushless)", default="0", equiv="Ixx57"),
    VarDef("Motor[{m}].I2tTrip", "I2tTrip", G_CUR, "I2T trip level = (MaxDac^2 - I2TSet^2) * time_at_peak[s]. 0 disables I2T protection", default="0", equiv="Ixx58"),
    VarDef("Motor[{m}].I2tSum", "I2tSum", G_CUR, "Current I2T integrator value (compare to I2tTrip)", kind=STATUS),
    VarDef("Motor[{m}].IqMeas", "IqMeas", G_CUR, "Measured quadrature (torque) current, 16-bit units", kind=STATUS),
    VarDef("Motor[{m}].IdMeas", "IdMeas", G_CUR, "Measured direct current", kind=STATUS),
    VarDef("Motor[{m}].IqCmd", "IqCmd", G_CUR, "Commanded torque current", kind=STATUS),
    VarDef("Motor[{m}].ServoOut", "ServoOut", G_CUR, "Servo loop output (16-bit units); saturates at MaxDac", kind=STATUS),
    VarDef("Motor[{m}].DacBias", "DacBias", G_CUR, "Output offset / bias", default="0", equiv="Ixx29"),
    # Servo gains
    VarDef("Motor[{m}].Servo.Kp", "Servo.Kp", G_SERVO, "Proportional gain (16-bit output units per motor unit of error)", default="40", equiv="Ixx30"),
    VarDef("Motor[{m}].Servo.Kvfb", "Servo.Kvfb", G_SERVO, "Velocity feedback (derivative) gain", default="15", equiv="Ixx31"),
    VarDef("Motor[{m}].Servo.Kvifb", "Servo.Kvifb", G_SERVO, "Integrated velocity feedback gain", default="0"),
    VarDef("Motor[{m}].Servo.Kvff", "Servo.Kvff", G_SERVO, "Velocity feedforward gain (usually = Kvfb)", default="15", equiv="Ixx32"),
    VarDef("Motor[{m}].Servo.Kviff", "Servo.Kviff", G_SERVO, "Integrated velocity feedforward gain", default="0"),
    VarDef("Motor[{m}].Servo.Kaff", "Servo.Kaff", G_SERVO, "Acceleration feedforward gain", default="0", equiv="Ixx35"),
    VarDef("Motor[{m}].Servo.Kfff", "Servo.Kfff", G_SERVO, "Friction feedforward (sign of velocity)", default="0", equiv="Ixx68"),
    VarDef("Motor[{m}].Servo.Ki", "Servo.Ki", G_SERVO, "Integral gain", default="0", equiv="Ixx33"),
    VarDef("Motor[{m}].Servo.SwZvInt", "Servo.SwZvInt", G_SERVO, "1 = integrate only when desired velocity is zero", default="0", equiv="Ixx34"),
    VarDef("Motor[{m}].Servo.MaxInt", "Servo.MaxInt", G_SERVO, "Integrator clamp", default="28000", equiv="Ixx63"),
    VarDef("Motor[{m}].Servo.MaxPosErr", "Servo.MaxPosErr", G_SERVO, "Max position error used in servo filter (raise for high-res encoders)", default="1000", equiv="Ixx67"),
    VarDef("Motor[{m}].Servo.Kbreak", "Servo.Kbreak", G_SERVO, "Deadband gain factor", default="1", equiv="Ixx64"),
    VarDef("Motor[{m}].Servo.BreakPosErr", "Servo.BreakPosErr", G_SERVO, "Deadband size (motor units)", default="0", equiv="Ixx65"),
    VarDef("Motor[{m}].Servo.OutDbOn", "Servo.OutDbOn", G_SERVO, "Output deadband on threshold", default="0"),
    VarDef("Motor[{m}].Servo.OutDbOff", "Servo.OutDbOff", G_SERVO, "Output deadband off threshold", default="0"),
    VarDef("Motor[{m}].Servo.OutDbSeed", "Servo.OutDbSeed", G_SERVO, "Output deadband seed", default="0"),
    VarDef("Motor[{m}].Servo.Kc1", "Servo.Kc1", G_SERVO, "Servo output filter coefficient (notch/low-pass)", default="0"),
    VarDef("Motor[{m}].Servo.Kc2", "Servo.Kc2", G_SERVO, "Servo output filter coefficient", default="0"),
    VarDef("Motor[{m}].Servo.Kd1", "Servo.Kd1", G_SERVO, "Servo output filter denominator coefficient", default="0"),
    VarDef("Motor[{m}].Servo.Kd2", "Servo.Kd2", G_SERVO, "Servo output filter denominator coefficient", default="0"),
    VarDef("Motor[{m}].Ctrl", "Ctrl", G_SERVO, "Servo algorithm pointer (Sys.ServoCtrl.a = standard PID, Sys.PosCtrl.a = pos-only)"),
    # Safety / motion limits
    VarDef("Motor[{m}].FatalFeLimit", "FatalFeLimit", G_SAFE, "Fatal following error limit (motor units). 0 disables (dangerous!)", default="1000", equiv="Ixx11"),
    VarDef("Motor[{m}].WarnFeLimit", "WarnFeLimit", G_SAFE, "Warning following error limit", default="250", equiv="Ixx12"),
    VarDef("Motor[{m}].MaxPos", "MaxPos", G_SAFE, "Positive software limit (both 0 = disabled)", default="0", equiv="Ixx13"),
    VarDef("Motor[{m}].MinPos", "MinPos", G_SAFE, "Negative software limit", default="0", equiv="Ixx14"),
    VarDef("Motor[{m}].MaxSpeed", "MaxSpeed", G_SAFE, "Maximum program speed (motor units / msec)", default="10", equiv="Ixx16"),
    VarDef("Motor[{m}].InvAmax", "InvAmax", G_SAFE, "Inverse max program accel (msec^2 / motor unit)", equiv="Ixx17"),
    VarDef("Motor[{m}].InvDmax", "InvDmax", G_SAFE, "Inverse max program decel"),
    VarDef("Motor[{m}].InvJmax", "InvJmax", G_SAFE, "Inverse max program jerk"),
    VarDef("Motor[{m}].AbortTa", "AbortTa", G_SAFE, "Abort decel time (<0 means decel rate)", default="-1", equiv="Ixx15"),
    VarDef("Motor[{m}].AbortTs", "AbortTs", G_SAFE, "Abort S-curve time", default="0"),
    VarDef("Motor[{m}].LimitBits", "LimitBits", G_SAFE, "Overtravel limit bit number", default="9"),
    VarDef("Motor[{m}].BlSize", "BlSize", G_SAFE, "Backlash size", default="0", equiv="Ixx86"),
    VarDef("Motor[{m}].BlSlewRate", "BlSlewRate", G_SAFE, "Backlash take-up rate", default="10", equiv="Ixx85"),
    VarDef("Motor[{m}].BlHysteresis", "BlHysteresis", G_SAFE, "Backlash hysteresis", default="0", equiv="Ixx87"),
    # Jog / home / in-position
    VarDef("Motor[{m}].JogSpeed", "JogSpeed", G_MOTION, "Jog speed (motor units / msec)", default="10", equiv="Ixx22"),
    VarDef("Motor[{m}].JogTa", "JogTa", G_MOTION, "Jog accel time (msec), negative = inverse accel rate", default="50", equiv="Ixx20"),
    VarDef("Motor[{m}].JogTs", "JogTs", G_MOTION, "Jog S-curve time (msec)", default="20", equiv="Ixx21"),
    VarDef("Motor[{m}].HomeVel", "HomeVel", G_MOTION, "Homing speed & direction (motor units / msec)", default="1", equiv="Ixx23"),
    VarDef("Motor[{m}].HomeOffset", "HomeOffset", G_MOTION, "Home offset (motor units)", default="0", equiv="Ixx26"),
    VarDef("Motor[{m}].InPosBand", "InPosBand", G_MOTION, "In-position band (motor units)", default="1", equiv="Ixx28"),
    VarDef("Motor[{m}].InPosTime", "InPosTime", G_MOTION, "Consecutive servo cycles in band for InPos", default="0", equiv="Ixx88"),
    VarDef("Motor[{m}].SettleTime", "SettleTime", G_MOTION, "Settle time before InPos"),
    VarDef("Motor[{m}].HomeComplete", "HomeComplete", G_MOTION, "1 = homed", kind=STATUS),
    VarDef("Motor[{m}].HomePos", "HomePos", G_MOTION, "Home position (motor units)", kind=STATUS),
    # Live status
    VarDef("Motor[{m}].ActPos", "ActPos", G_STAT, "Actual position (motor units)", kind=STATUS),
    VarDef("Motor[{m}].DesPos", "DesPos", G_STAT, "Desired (commanded) position", kind=STATUS),
    VarDef("Motor[{m}].PosError", "PosError", G_STAT, "Following error (DesPos - ActPos)", kind=STATUS),
    VarDef("Motor[{m}].ActVel", "ActVel", G_STAT, "Actual velocity (motor units / servo cycle)", kind=STATUS),
    VarDef("Motor[{m}].DesVel", "DesVel", G_STAT, "Desired velocity", kind=STATUS),
    VarDef("Motor[{m}].FltrVel", "FltrVel", G_STAT, "Filtered actual velocity", kind=STATUS),
    VarDef("Motor[{m}].AmpEna", "AmpEna", G_STAT, "Amplifier enabled", kind=STATUS),
    VarDef("Motor[{m}].ClosedLoop", "ClosedLoop", G_STAT, "Servo loop closed", kind=STATUS),
    VarDef("Motor[{m}].AmpFault", "AmpFault", G_STAT, "Amplifier fault", kind=STATUS),
    VarDef("Motor[{m}].FeFatal", "FeFatal", G_STAT, "Fatal following error", kind=STATUS),
    VarDef("Motor[{m}].FeWarn", "FeWarn", G_STAT, "Warning following error", kind=STATUS),
    VarDef("Motor[{m}].I2tFault", "I2tFault", G_STAT, "I2T fault", kind=STATUS),
    VarDef("Motor[{m}].EncLoss", "EncLoss", G_STAT, "Encoder loss", kind=STATUS),
    VarDef("Motor[{m}].PlusLimit", "PlusLimit", G_STAT, "Hardware + limit", kind=STATUS),
    VarDef("Motor[{m}].MinusLimit", "MinusLimit", G_STAT, "Hardware - limit", kind=STATUS),
    VarDef("Motor[{m}].SoftPlusLimit", "SoftPlusLimit", G_STAT, "Software + limit", kind=STATUS),
    VarDef("Motor[{m}].SoftMinusLimit", "SoftMinusLimit", G_STAT, "Software - limit", kind=STATUS),
    VarDef("Motor[{m}].LimitStop", "LimitStop", G_STAT, "Stopped on limit", kind=STATUS),
    VarDef("Motor[{m}].InPos", "InPos", G_STAT, "In position", kind=STATUS),
    VarDef("Motor[{m}].DesVelZero", "DesVelZero", G_STAT, "Desired velocity zero", kind=STATUS),
    VarDef("Motor[{m}].DacLimit", "DacLimit", G_STAT, "Output saturated at MaxDac", kind=STATUS),
    VarDef("Motor[{m}].TriggerNotFound", "TriggerNotFound", G_STAT, "Trigger not found", kind=STATUS),
    VarDef("Motor[{m}].AmpWarn", "AmpWarn", G_STAT, "Amplifier warning", kind=STATUS),
    VarDef("Motor[{m}].AuxFault", "AuxFault", G_STAT, "Auxiliary fault", kind=STATUS),
    VarDef("Motor[{m}].HomeInProgress", "HomeInProgress", G_STAT, "Homing in progress", kind=STATUS),
]

# the elements read for the per-axis quick snapshot (status page); kept small for speed
POWER_MOTOR_SNAPSHOT = [
    "ActPos", "DesPos", "PosError", "ActVel", "DesVel", "ServoOut", "IqMeas", "I2tSum",
    "PhasePos", "Coord",
]

POWER_SYS: List[VarDef] = [
    VarDef("Sys.ServoPeriod", "ServoPeriod", G_GLOBAL, "Servo interrupt period (msec)", "ms", kind=STATUS, equiv="I10"),
    VarDef("Sys.PhaseOverServoPeriod", "PhaseOverServoPeriod", G_GLOBAL, "Phase/servo period ratio", kind=STATUS),
    VarDef("Sys.RtIntPeriod", "RtIntPeriod", G_GLOBAL, "Real-time interrupt period (servo cycles - 1)", default="0", equiv="I8"),
    VarDef("Sys.MaxMotors", "MaxMotors", G_GLOBAL, "Number of motors processed", default="32"),
    VarDef("Sys.MaxCoords", "MaxCoords", G_GLOBAL, "Number of coordinate systems processed", default="16"),
    VarDef("Sys.WpKey", "WpKey", G_GLOBAL, "Write-protect key ($AAAAAAAA unlocks hardware setup elements)"),
    VarDef("Sys.pAbortAll", "pAbortAll", G_GLOBAL, "Abort-all input address (0 = disabled; then motors try to close loop on enable!)"),
    VarDef("Sys.AbortAllBit", "AbortAllBit", G_GLOBAL, "Abort-all input bit"),
    VarDef("Sys.AbortAllLimit", "AbortAllLimit", G_GLOBAL, "Abort-all debounce"),
    VarDef("Sys.WDTFault", "WDTFault", G_GLOBAL, "Soft watchdog fault: 0 ok, 1 foreground, 2 background", kind=STATUS),
    VarDef("Sys.WDTReset", "WDTReset", G_GLOBAL, "Foreground watchdog threshold (0 = default)", default="0"),
    VarDef("Sys.Status", "Status", G_GLOBAL, "Global status word", kind=STATUS),
    VarDef("Sys.NoClocks", "NoClocks", G_GLOBAL, "1 = no servo/phase clock", kind=STATUS),
    VarDef("Sys.PwrOnFault", "PwrOnFault", G_GLOBAL, "Fault at last power-on", kind=STATUS),
    VarDef("Sys.ConfigLoadErr", "ConfigLoadErr", G_GLOBAL, "Saved configuration load error", kind=STATUS),
    VarDef("Sys.Default", "Default", G_GLOBAL, "1 = running factory defaults", kind=STATUS),
    VarDef("Sys.ServoCount", "ServoCount", G_GLOBAL, "Servo cycle counter", kind=STATUS),
    VarDef("Sys.Time", "Time", G_GLOBAL, "System time (s)", kind=STATUS),
    VarDef("Sys.CpuTemp", "CpuTemp", G_GLOBAL, "CPU temperature (deg C)", kind=STATUS),
    VarDef("Sys.FgCpuUsage", "FgCpuUsage", G_GLOBAL, "Foreground (servo/phase/RTI) CPU usage %", kind=STATUS),
    VarDef("Sys.BgCpuUsage", "BgCpuUsage", G_GLOBAL, "Background CPU usage %", kind=STATUS),
    VarDef("Sys.BgSleepTime", "BgSleepTime", G_GLOBAL, "Background sleep time (us)"),
    VarDef("Sys.MaxRtPlc", "MaxRtPlc", G_GLOBAL, "Highest RTI PLC number"),
    VarDef("Sys.CompEnable", "CompEnable", G_GLOBAL, "Number of compensation tables enabled"),
    VarDef("Sys.PosCtrl", "PosCtrl", G_GLOBAL, "Address of position-only servo algorithm", kind=STATUS),
    VarDef("Sys.ServoCtrl", "ServoCtrl", G_GLOBAL, "Address of standard servo algorithm", kind=STATUS),
    VarDef("Sys.CK3WConfigErr", "CK3WConfigErr", G_GLOBAL, "CK3W configuration error", kind=STATUS),
    VarDef("Sys.MotorsRunning", "MotorsRunning", G_GLOBAL, "Number of motors currently moving", kind=STATUS),
    VarDef("Sys.MaxMotors", "MaxMotors", G_GLOBAL, "Max motors", default="32"),
]

POWER_BRICKLV: List[VarDef] = [
    VarDef("BrickLV.Monitor", "Monitor", G_BRICK, "1 = status monitoring running (needed for BrickLV status bits to update). <0 = error code", default="0"),
    VarDef("BrickLV.MonitorPeriod", "MonitorPeriod", G_BRICK, "Status update period (ms, 0 = 50 ms)", default="0"),
    VarDef("BrickLV.Config", "Config", G_BRICK, "1 starts amplifier configuration; 0 done; <0 error (-3 wrong hw, -4 no hw, -7 no Gate3)", default="0"),
    VarDef("BrickLV.Reset", "Reset", G_BRICK, "1 = reset amplifier and clear latched faults; returns 0 when done", default="0"),
    VarDef("BrickLV.BusOverVoltage", "BusOverVoltage", G_BRICK, "DC bus > 80 V (fault on all channels)", kind=STATUS),
    VarDef("BrickLV.BusUnderVoltage", "BusUnderVoltage", G_BRICK, "DC bus < 12 V (warning - bus power missing?)", kind=STATUS),
    VarDef("BrickLV.OverTemp", "OverTemp", G_BRICK, "Power board > 70 C (fault on all channels)", kind=STATUS),
    VarDef("BrickLV.Vers", "Vers", G_BRICK, "Amplifier firmware version", kind=STATUS),
    VarDef("BrickLV.Chan[{j}].I2tWarnOnly", "Chan[{j}].I2tWarnOnly", G_BRICK, "1 = amplifier I2T only warns (no fault)", default="0"),
    VarDef("BrickLV.Chan[{j}].TwoPhaseMode", "Chan[{j}].TwoPhaseMode", G_BRICK, "1 = 2-phase output (stepper), 0 = 3-phase (brushless)", default="0"),
    VarDef("BrickLV.Chan[{j}].ActivePhaseMode", "Chan[{j}].ActivePhaseMode", G_BRICK, "Active output phase mode after Config/Reset", kind=STATUS),
    VarDef("BrickLV.Chan[{j}].I2tFaultStatus", "Chan[{j}].I2tFaultStatus", G_BRICK, "Amplifier-stage I2T excess", kind=STATUS),
    VarDef("BrickLV.Chan[{j}].OverCurrent", "Chan[{j}].OverCurrent", G_BRICK, "Hardware over-current / short circuit latched (clear with BrickLV.Reset=1)", kind=STATUS),
]

POWER_CHANNEL: List[VarDef] = [
    VarDef("PowerBrick[{i}].PhaseFreq", "PhaseFreq", G_CHAN, "Phase clock frequency (kHz)", "kHz", default="9.0352"),
    VarDef("PowerBrick[{i}].PhaseClockDiv", "PhaseClockDiv", G_CHAN, "Phase clock divider"),
    VarDef("PowerBrick[{i}].ServoClockDiv", "ServoClockDiv", G_CHAN, "Servo clock divider (servo = phase/(div+1))"),
    VarDef("PowerBrick[{i}].EncClockDiv", "EncClockDiv", G_CHAN, "Encoder sampling clock divider (5 = 3.125 MHz)", default="5"),
    VarDef("PowerBrick[{i}].PfmClockDiv", "PfmClockDiv", G_CHAN, "PFM clock divider"),
    VarDef("PowerBrick[{i}].AdcAmpClockDiv", "AdcAmpClockDiv", G_CHAN, "Current ADC clock divider"),
    VarDef("PowerBrick[{i}].Chan[{j}].EncCtrl", "Chan[{j}].EncCtrl", G_CHAN, "Encoder decode: 3 = x4 quadrature, 7 = x4 reversed, 8 = internal PFM, 12 = MLDT", default="7"),
    VarDef("PowerBrick[{i}].Chan[{j}].TimerMode", "Chan[{j}].TimerMode", G_CHAN, "Timer mode (3 = PFM loop-back)"),
    VarDef("PowerBrick[{i}].Chan[{j}].OutputMode", "Chan[{j}].OutputMode", G_CHAN, "Output mode (bits: 0 PWM A/B, 8 PFM on D)", default="0"),
    VarDef("PowerBrick[{i}].Chan[{j}].OutputPol", "Chan[{j}].OutputPol", G_CHAN, "Output polarity", default="0"),
    VarDef("PowerBrick[{i}].Chan[{j}].PwmFreqMult", "Chan[{j}].PwmFreqMult", G_CHAN, "PWM frequency multiplier"),
    VarDef("PowerBrick[{i}].Chan[{j}].PackInData", "Chan[{j}].PackInData", G_CHAN, "Packed ADC data (must be 0 for BrickLV monitoring)", default="0"),
    VarDef("PowerBrick[{i}].Chan[{j}].PackOutData", "Chan[{j}].PackOutData", G_CHAN, "Packed PWM data", default="0"),
    VarDef("PowerBrick[{i}].Chan[{j}].CaptCtrl", "Chan[{j}].CaptCtrl", G_CHAN, "Position capture control (homing trigger)"),
    VarDef("PowerBrick[{i}].Chan[{j}].CaptFlagSel", "Chan[{j}].CaptFlagSel", G_CHAN, "Capture flag select (0 home, 1 +lim, 2 -lim, 3 user)"),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcOffset[0]", "Chan[{j}].AdcOffset[0]", G_CHAN, "Sine ADC offset (sinusoidal encoder bias)"),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcOffset[1]", "Chan[{j}].AdcOffset[1]", G_CHAN, "Cosine ADC offset"),
    VarDef("PowerBrick[{i}].Chan[{j}].PfmWidth", "Chan[{j}].PfmWidth", G_CHAN, "PFM pulse width"),
    VarDef("PowerBrick[{i}].Chan[{j}].PfmFormat", "Chan[{j}].PfmFormat", G_CHAN, "0 = pulse/dir, 1 = quadrature"),
    VarDef("PowerBrick[{i}].Chan[{j}].SerialEncCmd", "Chan[{j}].SerialEncCmd", G_CHAN, "Serial encoder command word"),
    VarDef("PowerBrick[{i}].Chan[{j}].Status", "Chan[{j}].Status", G_CHAN, "Channel status/flag word (limits, home, amp fault, enc loss bit 28)", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].PlusLimit", "Chan[{j}].PlusLimit", G_CHAN, "Raw + limit input", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].MinusLimit", "Chan[{j}].MinusLimit", G_CHAN, "Raw - limit input", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].HomeFlag", "Chan[{j}].HomeFlag", G_CHAN, "Raw home flag", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].UserFlag", "Chan[{j}].UserFlag", G_CHAN, "Raw user flag", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AmpFault", "Chan[{j}].AmpFault", G_CHAN, "Raw amp fault input", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AmpEna", "Chan[{j}].AmpEna", G_CHAN, "Amp enable output", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].CountError", "Chan[{j}].CountError", G_CHAN, "Quadrature count error latched (lost counts!)", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].LossStatus", "Chan[{j}].LossStatus", G_CHAN, "Differential encoder loss latched", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].SosError", "Chan[{j}].SosError", G_CHAN, "Sine/cosine sum-of-squares too low (analog enc loss)", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].SumOfSquares", "Chan[{j}].SumOfSquares", G_CHAN, "Sine^2 + cosine^2 amplitude", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].UVW", "Chan[{j}].UVW", G_CHAN, "Hall sensor state (1..6 valid)", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].ServoCapt", "Chan[{j}].ServoCapt", G_CHAN, "Raw encoder counter at servo clock", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].PhaseCapt", "Chan[{j}].PhaseCapt", G_CHAN, "Raw encoder counter at phase clock", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcAmp[0]", "Chan[{j}].AdcAmp[0]", G_CHAN, "Phase A current ADC", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcAmp[1]", "Chan[{j}].AdcAmp[1]", G_CHAN, "Phase B current ADC", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcEnc[0]", "Chan[{j}].AdcEnc[0]", G_CHAN, "Sine ADC", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].AdcEnc[1]", "Chan[{j}].AdcEnc[1]", G_CHAN, "Cosine ADC", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].SerialEncDataA", "Chan[{j}].SerialEncDataA", G_CHAN, "Serial encoder data A", kind=STATUS),
    VarDef("PowerBrick[{i}].Chan[{j}].SerialEncDataB", "Chan[{j}].SerialEncDataB", G_CHAN, "Serial encoder data B (bit 31 = timeout/parity error)", kind=STATUS),
]

POWER_ENCTABLE: List[VarDef] = [
    VarDef("EncTable[{e}].type", "type", G_ENC, "Conversion type: 0 off, 1 single 32-bit, 3 1/T, 11 microstep...", default="0"),
    VarDef("EncTable[{e}].pEnc", "pEnc", G_ENC, "Source register"),
    VarDef("EncTable[{e}].pEnc1", "pEnc1", G_ENC, "Secondary source"),
    VarDef("EncTable[{e}].index1", "index1", G_ENC, "Shift/filter index 1"),
    VarDef("EncTable[{e}].index2", "index2", G_ENC, "Index 2"),
    VarDef("EncTable[{e}].index3", "index3", G_ENC, "Index 3"),
    VarDef("EncTable[{e}].index4", "index4", G_ENC, "Index 4"),
    VarDef("EncTable[{e}].index5", "index5", G_ENC, "Index 5"),
    VarDef("EncTable[{e}].index6", "index6", G_ENC, "Index 6"),
    VarDef("EncTable[{e}].ScaleFactor", "ScaleFactor", G_ENC, "Output scale (1/256 for raw counts with 1/T, 1/(256*2^index1) ...)", default="1"),
    VarDef("EncTable[{e}].MaxDelta", "MaxDelta", G_ENC, "Max change per cycle (0 = no check)"),
    VarDef("EncTable[{e}].PrevEnc", "PrevEnc", G_ENC, "Last processed value", kind=STATUS),
    VarDef("EncTable[{e}].DeltaPos", "DeltaPos", G_ENC, "Last delta (velocity)", kind=STATUS),
    VarDef("EncTable[{e}].PrevDelta", "PrevDelta", G_ENC, "Previous delta", kind=STATUS),
]

POWER_COORD: List[VarDef] = [
    VarDef("Coord[{c}].Ta", "Ta", G_CS, "Program accel time (ms)"),
    VarDef("Coord[{c}].Td", "Td", G_CS, "Program decel time (ms)"),
    VarDef("Coord[{c}].Ts", "Ts", G_CS, "Program S-curve time (ms)"),
    VarDef("Coord[{c}].FeedTime", "FeedTime", G_CS, "Feedrate time units (ms)"),
    VarDef("Coord[{c}].SegMoveTime", "SegMoveTime", G_CS, "Segmentation time (ms)"),
    VarDef("Coord[{c}].ProgRunning", "ProgRunning", G_CS, "Motion program running", kind=STATUS),
    VarDef("Coord[{c}].InPos", "InPos", G_CS, "All motors in position", kind=STATUS),
    VarDef("Coord[{c}].AmpEna", "AmpEna", G_CS, "All motors enabled", kind=STATUS),
    VarDef("Coord[{c}].ErrorStatus", "ErrorStatus", G_CS, "Program error code", kind=STATUS),
    VarDef("Coord[{c}].Status", "Status", G_CS, "CS status word", kind=STATUS),
]

# ======================================================================================
# TURBO PMAC - I variables
# ======================================================================================
TURBO_MOTOR: List[VarDef] = [
    VarDef("I{m}00", "Ixx00", G_ACT, "Motor activation", default="0 (I100=1)", equiv="Motor[x].ServoCtrl"),
    VarDef("I{m}01", "Ixx01", G_ACT, "Commutation enable (bit0) / buffered output (bit1)", default="0", equiv="Motor[x].PhaseCtrl"),
    VarDef("I{m}02", "Ixx02", G_ACT, "Command output address", equiv="Motor[x].pDac"),
    VarDef("I{m}03", "Ixx03", G_ACT, "Position-loop feedback address (ECT entry)", equiv="Motor[x].pEnc"),
    VarDef("I{m}04", "Ixx04", G_ACT, "Velocity-loop feedback address", equiv="Motor[x].pEnc2"),
    VarDef("I{m}05", "Ixx05", G_ACT, "Master position address"),
    VarDef("I{m}06", "Ixx06", G_ACT, "Position following enable", default="0", equiv="Motor[x].MasterCtrl"),
    VarDef("I{m}07", "Ixx07", G_SCALE, "Master scale factor", default="96"),
    VarDef("I{m}08", "Ixx08", G_SCALE, "Position scale factor (internal units = counts*Ixx08*32)", default="96", equiv="Motor[x].PosSf"),
    VarDef("I{m}09", "Ixx09", G_SCALE, "Velocity-loop scale factor", default="96", equiv="Motor[x].Pos2Sf"),
    VarDef("I{m}10", "Ixx10", G_ACT, "Power-on servo position address (absolute)"),
    VarDef("I{m}11", "Ixx11", G_SAFE, "Fatal following error limit (1/16 count). 0 disables!", default="32000", equiv="Motor[x].FatalFeLimit"),
    VarDef("I{m}12", "Ixx12", G_SAFE, "Warning following error limit (1/16 count)", default="16000", equiv="Motor[x].WarnFeLimit"),
    VarDef("I{m}13", "Ixx13", G_SAFE, "Positive software limit (counts, 0 = disabled)", default="0", equiv="Motor[x].MaxPos"),
    VarDef("I{m}14", "Ixx14", G_SAFE, "Negative software limit", default="0", equiv="Motor[x].MinPos"),
    VarDef("I{m}15", "Ixx15", G_SAFE, "Abort/limit decel rate (counts/ms^2)", default="0.25", equiv="Motor[x].AbortTa"),
    VarDef("I{m}16", "Ixx16", G_SAFE, "Max permitted program velocity (counts/ms)", default="32", equiv="Motor[x].MaxSpeed"),
    VarDef("I{m}17", "Ixx17", G_SAFE, "Max permitted program accel (counts/ms^2)", default="0.5", equiv="Motor[x].InvAmax"),
    VarDef("I{m}19", "Ixx19", G_SAFE, "Max permitted jog accel (counts/ms^2)", default="0.015625"),
    VarDef("I{m}20", "Ixx20", G_MOTION, "Jog/home accel time (ms)", default="0", equiv="Motor[x].JogTa"),
    VarDef("I{m}21", "Ixx21", G_MOTION, "Jog/home S-curve time (ms)", default="50", equiv="Motor[x].JogTs"),
    VarDef("I{m}22", "Ixx22", G_MOTION, "Jog speed (counts/ms)", default="32", equiv="Motor[x].JogSpeed"),
    VarDef("I{m}23", "Ixx23", G_MOTION, "Home speed and direction (counts/ms)", default="32", equiv="Motor[x].HomeVel"),
    VarDef("I{m}24", "Ixx24", G_ACT, "Flag mode control (bit 17 $20000 = overtravel limits DISABLED, bit 20 amp fault disable)", default="$0"),
    VarDef("I{m}25", "Ixx25", G_ACT, "Flag address (limits/home/amp fault)", equiv="Motor[x].pLimits"),
    VarDef("I{m}26", "Ixx26", G_MOTION, "Home offset (1/16 count)", default="0", equiv="Motor[x].HomeOffset"),
    VarDef("I{m}27", "Ixx27", G_MOTION, "Position rollover range", default="0"),
    VarDef("I{m}28", "Ixx28", G_MOTION, "In-position band (1/16 count)", default="160", equiv="Motor[x].InPosBand"),
    VarDef("I{m}29", "Ixx29", G_CUR, "Output / first phase offset (DAC bias)", default="0", equiv="Motor[x].DacBias"),
    VarDef("I{m}30", "Ixx30", G_SERVO, "PID proportional gain", default="2000", equiv="Motor[x].Servo.Kp"),
    VarDef("I{m}31", "Ixx31", G_SERVO, "PID derivative gain", default="1280", equiv="Motor[x].Servo.Kvfb"),
    VarDef("I{m}32", "Ixx32", G_SERVO, "PID velocity feedforward gain", default="1280", equiv="Motor[x].Servo.Kvff"),
    VarDef("I{m}33", "Ixx33", G_SERVO, "PID integral gain", default="0", equiv="Motor[x].Servo.Ki"),
    VarDef("I{m}34", "Ixx34", G_SERVO, "PID integration mode (1 = only when stopped)", default="1", equiv="Motor[x].Servo.SwZvInt"),
    VarDef("I{m}35", "Ixx35", G_SERVO, "PID acceleration feedforward gain", default="0", equiv="Motor[x].Servo.Kaff"),
    VarDef("I{m}36", "Ixx36", G_SERVO, "Notch filter coefficient N1", default="0"),
    VarDef("I{m}37", "Ixx37", G_SERVO, "Notch filter coefficient N2", default="0"),
    VarDef("I{m}38", "Ixx38", G_SERVO, "Notch filter coefficient D1", default="0"),
    VarDef("I{m}39", "Ixx39", G_SERVO, "Notch filter coefficient D2", default="0"),
    VarDef("I{m}40", "Ixx40", G_SERVO, "Net desired position filter gain", default="0"),
    VarDef("I{m}41", "Ixx41", G_SAFE, "Desired position limit band", default="0"),
    VarDef("I{m}42", "Ixx42", G_ACT, "Amplifier flag address", equiv="Motor[x].pAmpFault"),
    VarDef("I{m}43", "Ixx43", G_ACT, "Overtravel limit flag address"),
    VarDef("I{m}55", "Ixx55", G_SCALE, "Commutation table address offset", default="0"),
    VarDef("I{m}56", "Ixx56", G_SCALE, "Commutation delay compensation", default="0", equiv="Motor[x].AdvGain"),
    VarDef("I{m}57", "Ixx57", G_CUR, "Continuous current limit (16-bit units)", default="0", equiv="Motor[x].I2TSet"),
    VarDef("I{m}58", "Ixx58", G_CUR, "Integrated current limit (I2T trip). 0 disables", default="0", equiv="Motor[x].I2tTrip"),
    VarDef("I{m}59", "Ixx59", G_ACT, "User-written servo/phase enable", default="0"),
    VarDef("I{m}60", "Ixx60", G_SERVO, "Servo cycle period extension", default="0"),
    VarDef("I{m}61", "Ixx61", G_CUR, "Current-loop integral gain", default="0", equiv="Motor[x].IiGain"),
    VarDef("I{m}62", "Ixx62", G_CUR, "Current-loop forward-path proportional gain", default="0", equiv="Motor[x].IpfGain"),
    VarDef("I{m}63", "Ixx63", G_SERVO, "Integration limit", default="4194304", equiv="Motor[x].Servo.MaxInt"),
    VarDef("I{m}64", "Ixx64", G_SERVO, "Deadband gain factor", default="0", equiv="Motor[x].Servo.Kbreak"),
    VarDef("I{m}65", "Ixx65", G_SERVO, "Deadband size (1/16 count)", default="16", equiv="Motor[x].Servo.BreakPosErr"),
    VarDef("I{m}66", "Ixx66", G_CUR, "PWM scale factor", default="6527", equiv="Motor[x].PwmSf"),
    VarDef("I{m}67", "Ixx67", G_SERVO, "Position error limit (1/16 count)", default="4194176", equiv="Motor[x].Servo.MaxPosErr"),
    VarDef("I{m}68", "Ixx68", G_SERVO, "Friction feedforward", default="0", equiv="Motor[x].Servo.Kfff"),
    VarDef("I{m}69", "Ixx69", G_CUR, "Output command limit (16-bit units, peak current)", default="20480", equiv="Motor[x].MaxDac"),
    VarDef("I{m}70", "Ixx70", G_SCALE, "Number of commutation cycles (N)", default="1"),
    VarDef("I{m}71", "Ixx71", G_SCALE, "Counts per N commutation cycles", default="1000"),
    VarDef("I{m}72", "Ixx72", G_SCALE, "Commutation phase angle (683 = 120 deg, 512 = 90 deg)", default="683", equiv="Motor[x].PhaseOffset"),
    VarDef("I{m}73", "Ixx73", G_SCALE, "Phase finding output value", default="0", equiv="Motor[x].PhaseFindingDac"),
    VarDef("I{m}74", "Ixx74", G_SCALE, "Phase finding time", default="0", equiv="Motor[x].PhaseFindingTime"),
    VarDef("I{m}75", "Ixx75", G_SCALE, "Phase position offset", default="0", equiv="Motor[x].AbsPhasePosOffset"),
    VarDef("I{m}76", "Ixx76", G_CUR, "Current-loop back-path proportional gain", default="0", equiv="Motor[x].IpbGain"),
    VarDef("I{m}77", "Ixx77", G_CUR, "Magnetization current", default="0", equiv="Motor[x].IdCmd"),
    VarDef("I{m}78", "Ixx78", G_SCALE, "Slip gain", default="0", equiv="Motor[x].SlipGain"),
    VarDef("I{m}79", "Ixx79", G_CUR, "Second phase offset", default="0", equiv="Motor[x].IbBias"),
    VarDef("I{m}80", "Ixx80", G_ACT, "Power-up mode (bit0 phase at power-on, bit1 enable at power-on)", default="0", equiv="Motor[x].PowerOnMode"),
    VarDef("I{m}81", "Ixx81", G_SCALE, "Power-on phase position address", equiv="Motor[x].pAbsPhasePos"),
    VarDef("I{m}82", "Ixx82", G_CUR, "Current-loop feedback address"),
    VarDef("I{m}83", "Ixx83", G_SCALE, "Commutation position address", equiv="Motor[x].pPhaseEnc"),
    VarDef("I{m}84", "Ixx84", G_CUR, "Current-loop feedback mask word", default="$FFF000", equiv="Motor[x].AdcMask"),
    VarDef("I{m}85", "Ixx85", G_SAFE, "Backlash take-up rate", default="0", equiv="Motor[x].BlSlewRate"),
    VarDef("I{m}86", "Ixx86", G_SAFE, "Backlash size (1/16 count)", default="0", equiv="Motor[x].BlSize"),
    VarDef("I{m}87", "Ixx87", G_SAFE, "Backlash hysteresis", default="64", equiv="Motor[x].BlHysteresis"),
    VarDef("I{m}88", "Ixx88", G_MOTION, "In-position number of scans", default="0", equiv="Motor[x].InPosTime"),
    VarDef("I{m}90", "Ixx90", G_MOTION, "Rapid mode speed select", default="1"),
    VarDef("I{m}91", "Ixx91", G_SCALE, "Power-on phase position format", default="$0", equiv="Motor[x].AbsPhasePosFormat"),
    VarDef("I{m}92", "Ixx92", G_MOTION, "Jog move calculation time", default="10"),
    VarDef("I{m}95", "Ixx95", G_ACT, "Power-on servo position format", default="$0", equiv="Motor[x].AbsPosFormat"),
    VarDef("I{m}96", "Ixx96", G_CUR, "Command output neutral", default="0"),
    VarDef("I{m}97", "Ixx97", G_MOTION, "Position capture & trigger mode", default="0"),
]

TURBO_GLOBAL: List[VarDef] = [
    VarDef("I0", "I0", G_GLOBAL, "Serial card number"),
    VarDef("I1", "I1", G_GLOBAL, "Serial port mode"),
    VarDef("I2", "I2", G_GLOBAL, "Control panel port activation"),
    VarDef("I3", "I3", G_GLOBAL, "I/O handshake control (2 = ACK, 3 = ACK+LF; EPICS expects 2)", default="2"),
    VarDef("I4", "I4", G_GLOBAL, "Communications integrity mode", default="0"),
    VarDef("I5", "I5", G_GLOBAL, "PLC program control (0 none, 1 PLC0, 2 PLC1-31, 3 all)", default="0"),
    VarDef("I6", "I6", G_GLOBAL, "Error reporting mode (1 = BELL+ERRxxx)", default="1"),
    VarDef("I7", "I7", G_GLOBAL, "Phase cycle extension", default="0"),
    VarDef("I8", "I8", G_GLOBAL, "Real-time interrupt period (servo cycles - 1)", default="2"),
    VarDef("I9", "I9", G_GLOBAL, "Full/abbreviated listing control", default="2"),
    VarDef("I10", "I10", G_GLOBAL, "Servo interrupt time (1/8388608 ms units) -> servo frequency = 8388608000/I10", default="3713707"),
    VarDef("I11", "I11", G_GLOBAL, "Programmed move calculation time", default="0"),
    VarDef("I12", "I12", G_GLOBAL, "Lookahead spline time", default="0"),
    VarDef("I13", "I13", G_GLOBAL, "Foreground in-position check enable", default="0"),
    VarDef("I14", "I14", G_GLOBAL, "Temporary buffer save enable", default="0"),
    VarDef("I15", "I15", G_GLOBAL, "Degree/radian control", default="0"),
    VarDef("I19", "I19", G_GLOBAL, "Clock source I-variable number (e.g. 7007 = Servo IC 0)", default="7007"),
    VarDef("I37", "I37", G_GLOBAL, "Additional lookahead segments"),
    VarDef("I42", "I42", G_GLOBAL, "Spline/PVT time control", default="0"),
    VarDef("I51", "I51", G_GLOBAL, "Compensation table enable", default="0"),
    VarDef("I52", "I52", G_GLOBAL, "CPU frequency control"),
    VarDef("I59", "I59", G_GLOBAL, "Motor/CS group select", default="0"),
    VarDef("I60", "I60", G_GLOBAL, "Filtered velocity sample time", default="0"),
    VarDef("I61", "I61", G_GLOBAL, "Filtered velocity shift", default="0"),
    VarDef("I68", "I68", G_GLOBAL, "Coordinate system activation control", default="15"),
    VarDef("I80", "I80", G_GLOBAL, "MACRO ring check period"),
    VarDef("I81", "I81", G_GLOBAL, "MACRO max ring error count"),
    VarDef("I82", "I82", G_GLOBAL, "MACRO min sync packet count"),
]

TURBO_GATE: List[VarDef] = [
    VarDef("I7{i}00", "I7m00", G_CHAN, "Servo IC m MaxPhase frequency control", default="6527"),
    VarDef("I7{i}01", "I7m01", G_CHAN, "Phase clock frequency divider", default="0"),
    VarDef("I7{i}02", "I7m02", G_CHAN, "Servo clock frequency divider", default="3"),
    VarDef("I7{i}03", "I7m03", G_CHAN, "Hardware clock control (encoder/PFM/DAC/ADC clocks)", default="2258"),
    VarDef("I7{i}04", "I7m04", G_CHAN, "PWM deadtime / PFM pulse width", default="15"),
    VarDef("I7{i}05", "I7m05", G_CHAN, "DAC strobe word", default="$7FFFC0"),
    VarDef("I7{i}06", "I7m06", G_CHAN, "ADC strobe word", default="$FFFFFE"),
    VarDef("I7{i}{j}0", "I7mn0", G_CHAN, "Encoder/timer decode control (7 = x4 CW, 3 = x4 CCW, 8 = internal pulse/dir)", default="7"),
    VarDef("I7{i}{j}1", "I7mn1", G_CHAN, "Position compare channel select", default="0"),
    VarDef("I7{i}{j}2", "I7mn2", G_CHAN, "Capture control (homing trigger)", default="1"),
    VarDef("I7{i}{j}3", "I7mn3", G_CHAN, "Capture flag select", default="0"),
    VarDef("I7{i}{j}4", "I7mn4", G_CHAN, "Encoder gated index select", default="0"),
    VarDef("I7{i}{j}5", "I7mn5", G_CHAN, "Encoder index gate state", default="0"),
    VarDef("I7{i}{j}6", "I7mn6", G_CHAN, "Output mode select (0 DAC, 1 PFM, 2 PWM, 3 PWM/PFM)", default="0"),
    VarDef("I7{i}{j}7", "I7mn7", G_CHAN, "Output invert control", default="0"),
    VarDef("I7{i}{j}8", "I7mn8", G_CHAN, "PFM direction signal invert", default="0"),
    VarDef("I7{i}{j}9", "I7mn9", G_CHAN, "Hardware 1/T control", default="0"),
]


# ======================================================================================
# Lookup helpers
# ======================================================================================
_ALL_POWER = POWER_MOTOR + POWER_SYS + POWER_BRICKLV + POWER_CHANNEL + POWER_ENCTABLE + POWER_COORD
_ALL_TURBO = TURBO_MOTOR + TURBO_GLOBAL + TURBO_GATE


def _template_to_regex(template: str) -> re.Pattern:
    pat = re.escape(template)
    for ph in ("m", "i", "j", "c", "e"):
        pat = pat.replace(re.escape("{%s}" % ph), r"(?P<%s>\d+)" % ph)
    return re.compile("^" + pat + "$", re.IGNORECASE)


_POWER_INDEX = [(_template_to_regex(v.key), v) for v in _ALL_POWER]
_TURBO_INDEX = [(_template_to_regex(v.key), v) for v in _ALL_TURBO]


def describe(name: str, family: str = "power") -> Optional[VarDef]:
    """Return the VarDef matching a concrete variable name such as ``Motor[3].Servo.Kp``
    or ``I330`` (Turbo motor 3, Ixx30)."""
    name = name.strip()
    idx = _POWER_INDEX if family == "power" else _TURBO_INDEX
    for rx, v in idx:
        if rx.match(name):
            return v
    if family == "turbo":
        # Ixx variables are I<motor><00..99> with motor 1..32 -> I100 .. I3299
        m = re.match(r"^I(\d{1,2})(\d\d)$", name, re.I)
        if m and 1 <= int(m.group(1)) <= 32:
            for rx, v in _TURBO_INDEX:
                if v.key.startswith("I{m}") and v.key.endswith(m.group(2)):
                    return v
    return None


def power_motor_vars(m: int, kinds: Iterable[str] = (SETUP, STATUS)) -> List[str]:
    return [v.resolve(m=m) for v in POWER_MOTOR if v.kind in kinds]


def power_channel_vars(axis: int) -> List[str]:
    """Power Brick LV: motor n uses PowerBrick[(n-1)//4].Chan[(n-1)%4]."""
    i, j = (axis - 1) // 4, (axis - 1) % 4
    return [v.resolve(i=i, j=j) for v in POWER_CHANNEL]


def power_bricklv_vars(axis: Optional[int] = None) -> List[str]:
    out = []
    for v in POWER_BRICKLV:
        if "{j}" in v.key:
            if axis is not None:
                out.append(v.resolve(j=axis - 1))
            else:
                out.extend(v.resolve(j=j) for j in range(8))
        else:
            out.append(v.key)
    return out


def power_sys_vars() -> List[str]:
    return [v.key for v in POWER_SYS]


def power_enctable_vars(e: int) -> List[str]:
    return [v.resolve(e=e) for v in POWER_ENCTABLE]


def power_coord_vars(c: int) -> List[str]:
    return [v.resolve(c=c) for v in POWER_COORD]


def turbo_motor_vars(m: int) -> List[str]:
    return [v.resolve(m=m) for v in TURBO_MOTOR]


def turbo_global_vars() -> List[str]:
    return [v.key for v in TURBO_GLOBAL]


def turbo_gate_vars(axis: int) -> List[str]:
    """Geobrick/Turbo: channel n lives on Servo IC m = (n-1)//4 (0 or 1 for 8 axes), channel (n-1)%4+1."""
    i, j = (axis - 1) // 4, (axis - 1) % 4 + 1
    return [v.resolve(i=i, j=j) for v in TURBO_GATE]


GROUP_ORDER = [G_STAT, G_SAFE, G_SERVO, G_CUR, G_SCALE, G_ACT, G_MOTION, G_GLOBAL, G_BRICK, G_CHAN, G_ENC, G_CS]
