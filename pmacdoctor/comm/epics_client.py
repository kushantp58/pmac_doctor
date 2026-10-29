"""EPICS Channel Access client for IOCs built on the DiamondLightSource ``pmac`` module.

PV layout (from pmacController.template / pmacStatusAxis.template / dls_pmac_asyn_motor):

* ``$(P):PMACTYPE``           mbbi, PMAC type (cid)
* ``$(P):CPULOAD_0..3``       CPU usage per core
* ``$(P):SERVO_FREQ``         kHz (Turbo: from I10 via pmacStatus.template, Power: Sys.ServoPeriod)
* ``$(P):READ_CTRLSTAT1..3``  global status words (``???`` / ``?``)
* ``$(P):READ_PLCBITS00/01``  PLC *disabled* bits
* ``$(P):READ_PROGBITS``      motion program running bits
* ``$(P):READ_GPIO_INP/OUT``
* ``$(P):GLOBAL_PROBLEM_RBV`` bi, ``$(P):FEEDRATE_RBV`` / ``FEEDRATE_PROBLEM_RBV``
* ``$(P):I_VARIABLES_RBV``  ``P_VARIABLES_RBV``  ``M_VARIABLES_RBV`` waveforms (slow-polled list)
* ``$(P):STAT_*``             comms statistics
* ``$(P):SendCmd``            waveform / write-only arbitrary command (no reply)
* ``$(P):AXIS<n>:POSITION / FOLL_ERROR / VELOCITY / READ_STAT1..3 / DESC``
* motor records: ``$(P):M<n>`` aliases (dls_pmac_asyn_motor) or user supplied names

Everything is monitored (camonitor) so the UI never blocks on CA.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# pyepics uses PYTHONIOENCODING verbatim as a codec name; values such as
# "utf-8:surrogateescape" (error-handler suffix) make every PV creation fail. Sanitise.
os.environ.setdefault("PYEPICS_ENCODING",
                      ((os.environ.get("PYTHONIOENCODING") or "utf-8").split(":")[0] or "utf-8"))

try:
    import epics  # type: ignore
    from epics import PV  # type: ignore
    HAVE_EPICS = True
except Exception:  # pragma: no cover
    epics = None
    PV = None
    HAVE_EPICS = False


MOTOR_FIELDS = ["RBV", "VAL", "DMOV", "MOVN", "MSTA", "HLS", "LLS", "LVIO", "MRES", "EGU", "VELO",
                "VMAX", "ACCL", "HLM", "LLM", "DHLM", "DLLM", "PREC", "DESC", "RDBD", "OFF", "DIR",
                "SPMG", "CNEN", "STOP", "MISS", "RTRY", "RCNT", "UEIP", "ERES", "HOMF", "HOMR", "JOGF",
                "JOGR", "TWV", "HVEL", "BDST", "DLY", "STAT", "SEVR", "DIFF"]

CONTROLLER_PVS = [
    "PMACTYPE", "CPUNUMCORES", "CPULOAD_0", "CPULOAD_1", "CPULOAD_2", "CPULOAD_3", "SERVO_FREQ",
    "READ_CTRLSTAT1", "READ_CTRLSTAT2", "READ_CTRLSTAT3", "READ_PLCBITS00", "READ_PLCBITS01",
    "READ_GPIO_INP", "READ_GPIO_OUT", "READ_PROGBITS", "MACROERRS", "GLOBAL_PROBLEM_RBV",
    "FEEDRATE_RBV", "FEEDRATE_LIMIT", "FEEDRATE_PROBLEM_RBV", "STAT_MSG_NO_RBV",
    "STAT_MSG_TIME_RBV", "STAT_MAX_TIME_RBV", "STAT_AVE_TIME_RBV", "STAT_TOTAL_WRITE_RBV",
    "STAT_TOTAL_READ_RBV", "FAST_SIZE_RBV", "MEDIUM_SIZE_RBV", "SLOW_SIZE_RBV", "NAXES",
    "I_VARIABLES_RBV", "P_VARIABLES_RBV", "M_VARIABLES_RBV", "AxesReadonly_RBV",
    "COORDINATE_SYS_GROUP_RBV", "Port", "SERVO_PERIOD", "I10", "IO_HANDSHAKE", "PLC_CONTROL",
    "ERRREPMODE", "READ_ECHO",
]

AXIS_PVS = ["POSITION", "FOLL_ERROR", "VELOCITY", "READ_STAT1", "READ_STAT2", "READ_STAT3", "DESC"]

# PMACTYPE enum from pmacController.template (cid values)
CID_POWER = 604020
CID_GEOBRICK = 603382
CID_PMAC = 602413
CID_CLIPPER = 602404


@dataclass
class PvValue:
    name: str
    value: object = None
    connected: bool = False
    timestamp: float = 0.0
    severity: int = 0
    units: str = ""


@dataclass
class EpicsConfig:
    prefix: str = "BL99P-MO-BRICK-01"
    naxes: int = 8
    motor_pv_template: str = "{P}:M{n}"  # dls default alias; e.g. "XF:31ID-ES{Mtr:{n}}" style can be typed
    motor_pvs: Dict[int, str] = field(default_factory=dict)  # explicit overrides per axis
    timeout: float = 2.0

    def motor_pv(self, n: int) -> str:
        if n in self.motor_pvs and self.motor_pvs[n]:
            return self.motor_pvs[n]
        return self.motor_pv_template.format(P=self.prefix, n=n, nn="%02d" % n)


class EpicsClient:
    """Monitors a Diamond-pmac IOC. Thread-safe; values are cached."""

    def __init__(self, config: EpicsConfig):
        self.cfg = config
        self._pvs: Dict[str, object] = {}
        self._values: Dict[str, PvValue] = {}
        self._lock = threading.RLock()
        self.connected_count = 0
        self.listeners: List[Callable[[str, PvValue], None]] = []
        self.started = False

    # ------------------------------------------------------------------ control
    def start(self):
        if not HAVE_EPICS:
            raise RuntimeError("pyepics is not available (pip install pyepics)")
        P = self.cfg.prefix
        names = ["%s:%s" % (P, s) for s in CONTROLLER_PVS]
        for n in range(1, self.cfg.naxes + 1):
            names += ["%s:AXIS%d:%s" % (P, n, s) for s in AXIS_PVS]
            mp = self.cfg.motor_pv(n)
            names.append(mp)
            names += ["%s.%s" % (mp, f) for f in MOTOR_FIELDS]
            names += ["%s:%s" % (mp, s) for s in ("FERROR", "FERRORMAX", "ELOSS", "HOMED", "SCALE",
                                                 "DirectDemand", "Mres", "Offset", "DirectMotorNumber",
                                                 "MASTER_CTRL_RBV", "SDIS", "KILL", "HOMING")]
        for nm in names:
            self._subscribe(nm)
        self.started = True

    def stop(self):
        with self._lock:
            for pv in self._pvs.values():
                try:
                    pv.clear_callbacks()
                    pv.disconnect()
                except Exception:
                    pass
            self._pvs.clear()
        self.started = False

    def _subscribe(self, name: str):
        if name in self._pvs:
            return
        self._values[name] = PvValue(name=name)
        pv = PV(name, callback=self._on_value, connection_callback=self._on_conn, auto_monitor=True,
                form="time")
        self._pvs[name] = pv

    def add_pv(self, name: str):
        with self._lock:
            self._subscribe(name)

    # ------------------------------------------------------------------ callbacks
    def _on_conn(self, pvname=None, conn=None, **kw):
        with self._lock:
            v = self._values.setdefault(pvname, PvValue(name=pvname))
            v.connected = bool(conn)
            self.connected_count = sum(1 for x in self._values.values() if x.connected)

    def _on_value(self, pvname=None, value=None, timestamp=None, severity=None, units=None, char_value=None, **kw):
        with self._lock:
            v = self._values.setdefault(pvname, PvValue(name=pvname))
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("latin-1", "replace")
            # waveform of chars -> string
            if hasattr(value, "dtype") and getattr(value, "dtype", None) is not None and \
                    str(value.dtype).startswith(("uint8", "int8")):
                try:
                    value = bytes(int(c) for c in value if int(c) != 0).decode("latin-1", "replace")
                except Exception:
                    pass
            v.value = value
            v.connected = True
            v.timestamp = timestamp or time.time()
            v.severity = severity or 0
            v.units = units or ""
        for cb in list(self.listeners):
            try:
                cb(pvname, v)
            except Exception:
                pass

    # ------------------------------------------------------------------ access
    def get(self, name: str, default=None):
        with self._lock:
            v = self._values.get(name)
            if v is None or not v.connected or v.value is None:
                return default
            return v.value

    def ctrl(self, suffix: str, default=None):
        return self.get("%s:%s" % (self.cfg.prefix, suffix), default)

    def axis(self, n: int, suffix: str, default=None):
        return self.get("%s:AXIS%d:%s" % (self.cfg.prefix, n, suffix), default)

    def motor(self, n: int, fld: str = "", default=None):
        mp = self.cfg.motor_pv(n)
        name = mp if fld == "" else ("%s.%s" % (mp, fld) if not fld.startswith(":") else mp + fld)
        return self.get(name, default)

    def motor_connected(self, n: int) -> bool:
        with self._lock:
            v = self._values.get(self.cfg.motor_pv(n) + ".RBV")
            return bool(v and v.connected)

    def controller_connected(self) -> bool:
        with self._lock:
            v = self._values.get("%s:READ_CTRLSTAT1" % self.cfg.prefix)
            return bool(v and v.connected)

    def all_values(self) -> Dict[str, PvValue]:
        with self._lock:
            return dict(self._values)

    def family(self) -> Optional[str]:
        t = self.ctrl("PMACTYPE")
        if t is None:
            return None
        try:
            ti = int(t)
        except Exception:
            return None
        # mbbi returns the state index; template maps cid values to states - handle both
        if ti == CID_POWER or ti == 3:
            return "power"
        if ti in (CID_GEOBRICK, CID_PMAC, CID_CLIPPER, 0, 1, 2):
            return "turbo"
        return None

    # ------------------------------------------------------------------ writes
    def put(self, name: str, value, wait: bool = False, timeout: float = 2.0):
        with self._lock:
            pv = self._pvs.get(name)
        if pv is None:
            pv = PV(name)
            with self._lock:
                self._pvs[name] = pv
        return pv.put(value, wait=wait, timeout=timeout)

    def send_cmd(self, cmd: str):
        """Write to $(P):SendCmd (waveform, asynOctetWrite PMAC_C_WRITE_CMD). No reply available."""
        name = "%s:SendCmd" % self.cfg.prefix
        with self._lock:
            pv = self._pvs.get(name)
        if pv is None:
            pv = PV(name)
            with self._lock:
                self._pvs[name] = pv
        pv.put(cmd, wait=True, timeout=self.cfg.timeout)

    def motor_move(self, n: int, pos: float):
        self.put(self.cfg.motor_pv(n) + ".VAL", pos)

    def motor_stop(self, n: int):
        self.put(self.cfg.motor_pv(n) + ".STOP", 1)
