"""Session = the combination of an optional direct link and an optional EPICS client.

It produces :class:`ControllerSnapshot` objects on demand (the UI runs :meth:`poll` from a
worker thread), full variable dumps for the rule engine and the variable browser, and the
step-response acquisition used by the tuning tools.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

from ..core import knowledge as kb
from ..core.models import AxisSnapshot, ControllerSnapshot, VariableSet
from ..core.status_bits import (parse_power_axis_status, parse_power_global_status,
                                parse_turbo_axis_status, parse_turbo_global_status,
                                power_status_from_words, turbo_status_from_words)
from .base import DirectLink, PmacCommError, PmacError, to_float
from .epics_client import EpicsClient, EpicsConfig, HAVE_EPICS, MOTOR_FIELDS
from .ethernet_turbo import TurboEthernetLink
from .simulator import SimulatedPmac
from .ssh_power import PowerSshLink

POWER_GLOBAL_POLL = ["Sys.WDTFault", "Sys.FgCpuUsage", "Sys.BgCpuUsage", "Sys.CpuTemp",
                     "BrickLV.Monitor", "BrickLV.BusUnderVoltage", "BrickLV.BusOverVoltage",
                     "BrickLV.OverTemp", "Sys.MotorsRunning", "Sys.pAbortAll"]
TURBO_GLOBAL_POLL = ["I3", "I5", "I6", "I10", "I8"]


class Session:
    def __init__(self):
        self.direct: Optional[DirectLink] = None
        self.epics: Optional[EpicsClient] = None
        self.epics_cfg = EpicsConfig()
        self.naxes = 8
        self.lock = threading.RLock()
        self.last_snapshot: Optional[ControllerSnapshot] = None
        self.dumps: Dict[int, VariableSet] = {}
        self.global_dump: Optional[VariableSet] = None
        self.status_listeners: List[Callable[[str], None]] = []
        self.axis_descriptions: Dict[int, str] = {}

    # ------------------------------------------------------------------ connections
    @property
    def family(self) -> str:
        if self.direct is not None and self.direct.connected:
            return self.direct.family
        if self.epics is not None:
            f = self.epics.family()
            if f:
                return f
        return "power"

    def connect_direct(self, kind: str, host: str = "", port: int = 0, username: str = "root",
                       password: str = "deltatau", scenario: str = "healthy", family: str = "power"):
        self.disconnect_direct()
        if kind == "ssh":
            link: DirectLink = PowerSshLink(host, port or 22, username, password)
        elif kind == "ethernet":
            link = TurboEthernetLink(host, port or 1025)
        elif kind == "simulator":
            link = SimulatedPmac(family=family, scenario=scenario)
        else:
            raise ValueError("unknown link kind %r" % kind)
        info = link.connect()
        with self.lock:
            self.direct = link
            self.naxes = max(1, min(32, info.max_motors or 8))
            if kind == "simulator":
                self.naxes = link.n_motors  # type: ignore[attr-defined]
            self.dumps.clear()
            self.global_dump = None
        self._emit("direct connected: %s" % info.description)
        return info

    def disconnect_direct(self):
        with self.lock:
            if self.direct is not None:
                try:
                    self.direct.disconnect()
                except Exception:
                    pass
                self.direct = None
        self._emit("direct link closed")

    def connect_epics(self, cfg: EpicsConfig):
        self.disconnect_epics()
        if not HAVE_EPICS:
            raise RuntimeError("pyepics is not installed")
        client = EpicsClient(cfg)
        client.start()
        with self.lock:
            self.epics = client
            self.epics_cfg = cfg
            if self.direct is None:
                self.naxes = cfg.naxes
        self._emit("EPICS monitors started for %s" % cfg.prefix)

    def disconnect_epics(self):
        with self.lock:
            if self.epics is not None:
                try:
                    self.epics.stop()
                except Exception:
                    pass
                self.epics = None
        self._emit("EPICS monitors stopped")

    def disconnect_all(self):
        self.disconnect_direct()
        self.disconnect_epics()

    def _emit(self, msg: str):
        for cb in list(self.status_listeners):
            try:
                cb(msg)
            except Exception:
                pass

    @property
    def direct_ok(self) -> bool:
        return self.direct is not None and self.direct.connected

    @property
    def epics_ok(self) -> bool:
        return self.epics is not None and (self.epics.controller_connected() or
                                           any(self.epics.motor_connected(n) for n in range(1, self.naxes + 1)))

    # ------------------------------------------------------------------ polling
    def poll(self) -> ControllerSnapshot:
        snap = ControllerSnapshot(family=self.family)
        t0 = time.perf_counter()
        with self.lock:
            direct = self.direct
            ep = self.epics
        if direct is not None and direct.connected:
            try:
                self._poll_direct(direct, snap)
                snap.direct_ok = True
            except (PmacCommError, PmacError) as exc:
                snap.errors.append("direct: %s" % exc)
        if ep is not None:
            try:
                self._poll_epics(ep, snap)
            except Exception as exc:  # pragma: no cover
                snap.errors.append("epics: %s" % exc)
        snap.comm_ms = (time.perf_counter() - t0) * 1e3
        for n, d in self.axis_descriptions.items():
            if n in snap.axes and not snap.axes[n].desc:
                snap.axes[n].desc = d
        self.last_snapshot = snap
        return snap

    def _poll_direct(self, link: DirectLink, snap: ControllerSnapshot):
        info = link.info
        snap.family = link.family
        snap.description = info.description
        snap.firmware = info.firmware
        snap.cpu = info.cpu
        snap.servo_period_ms = info.servo_period_ms
        n = self.naxes
        if link.family == "power":
            # axis status words in one command
            reply = link.send(" ".join("#%d?" % k for k in range(1, n + 1)))
            lines = [ln for ln in reply.split("\n") if ln.strip()]
            for k, ln in enumerate(lines[:n], start=1):
                try:
                    snap.axis(k).status = parse_power_axis_status(k, ln)
                except ValueError:
                    pass
            names = []
            for k in range(1, n + 1):
                names += ["Motor[%d].%s" % (k, e) for e in kb.POWER_MOTOR_SNAPSHOT]
            vals = link.read_values(names, chunk=24)
            for k in range(1, n + 1):
                ax = snap.axis(k)
                for e in kb.POWER_MOTOR_SNAPSHOT:
                    raw = vals.get("Motor[%d].%s" % (k, e))
                    try:
                        ax.values[e] = to_float(raw)
                    except ValueError:
                        ax.values[e] = None
                if ax.status is not None:
                    ax.status.position = ax.values.get("ActPos")
                    ax.status.following_error = ax.values.get("PosError")
                    ax.status.velocity = ax.values.get("ActVel")
                    c = ax.values.get("Coord")
                    if c is not None:
                        ax.status.cs_number = int(c)
            g = link.send("?")
            snap.global_word, snap.global_bits = parse_power_global_status(g)
            snap.sys_values = link.read_values(POWER_GLOBAL_POLL, chunk=12)
        else:
            reply = link.send(" ".join("#%d?" % k for k in range(1, n + 1)))
            lines = [ln for ln in reply.split("\n") if ln.strip()]
            for k, ln in enumerate(lines[:n], start=1):
                try:
                    snap.axis(k).status = parse_turbo_axis_status(k, ln)
                except ValueError:
                    pass
            cmds = []
            for k in range(1, n + 1):
                cmds += ["#%dP" % k, "#%dF" % k, "#%dV" % k]
            reply = link.send(" ".join(cmds))
            lines = [ln for ln in reply.split("\n") if ln.strip()]
            for k in range(1, n + 1):
                ax = snap.axis(k)
                base = (k - 1) * 3
                try:
                    ax.values["ActPos"] = float(lines[base])
                    ax.values["PosError"] = float(lines[base + 1])
                    ax.values["ActVel"] = float(lines[base + 2])
                    ax.values["DesPos"] = ax.values["ActPos"] + ax.values["PosError"]
                except (IndexError, ValueError):
                    pass
                if ax.status is not None:
                    ax.status.position = ax.values.get("ActPos")
                    ax.status.following_error = ax.values.get("PosError")
                    ax.status.velocity = ax.values.get("ActVel")
            g = link.send("???")
            snap.global_word, snap.global_bits = parse_turbo_global_status(g)
            snap.sys_values = link.read_values(TURBO_GLOBAL_POLL, chunk=12)

    def _poll_epics(self, ep: EpicsClient, snap: ControllerSnapshot):
        P = ep.cfg.prefix
        snap.epics_ok = ep.controller_connected() or any(ep.motor_connected(k) for k in range(1, self.naxes + 1))
        if not snap.direct_ok:
            fam = ep.family()
            if fam:
                snap.family = fam
            sf = ep.ctrl("SERVO_FREQ")
            if sf:
                try:
                    snap.servo_period_ms = 1.0 / float(sf)
                except Exception:
                    pass
            snap.description = "EPICS IOC %s" % P
        for key in ("PMACTYPE", "CPULOAD_0", "CPULOAD_1", "CPULOAD_2", "CPULOAD_3", "SERVO_FREQ",
                    "READ_CTRLSTAT1", "READ_CTRLSTAT2", "READ_CTRLSTAT3", "READ_PLCBITS00",
                    "READ_PLCBITS01", "READ_PROGBITS", "READ_GPIO_INP", "READ_GPIO_OUT",
                    "GLOBAL_PROBLEM_RBV", "FEEDRATE_RBV", "FEEDRATE_PROBLEM_RBV", "FEEDRATE_LIMIT",
                    "STAT_MSG_TIME_RBV", "STAT_MAX_TIME_RBV", "STAT_AVE_TIME_RBV", "STAT_MSG_NO_RBV",
                    "FAST_SIZE_RBV", "MEDIUM_SIZE_RBV", "SLOW_SIZE_RBV", "I_VARIABLES_RBV",
                    "P_VARIABLES_RBV", "M_VARIABLES_RBV", "MACROERRS", "NAXES", "COORDINATE_SYS_GROUP_RBV",
                    "IO_HANDSHAKE", "PLC_CONTROL", "ERRREPMODE", "I10", "SERVO_PERIOD"):
            v = ep.ctrl(key)
            if v is not None:
                snap.epics[key] = v
        # global status from EPICS when no direct link
        if not snap.direct_ok and "READ_CTRLSTAT1" in snap.epics:
            try:
                w1 = int(snap.epics.get("READ_CTRLSTAT1", 0))
                w2 = int(snap.epics.get("READ_CTRLSTAT2", 0))
                w3 = int(snap.epics.get("READ_CTRLSTAT3", 0))
                if snap.family == "power":
                    word = ((w1 & 0xFFFF) << 16) | (w2 & 0xFFFF)
                    snap.global_word, snap.global_bits = parse_power_global_status("$%08X" % word)
                else:
                    word = ((w1 & 0xFF) << 16) | ((w2 & 0xFFFF))
                    snap.global_word, snap.global_bits = parse_turbo_global_status("%06X%06X" % (((w1 & 0xFFFF) << 8) | (w2 >> 8), ((w2 & 0xFF) << 16) | (w3 & 0xFFFF)))
            except Exception:
                pass
        for k in range(1, self.naxes + 1):
            ax = snap.axis(k)
            for s in ("POSITION", "FOLL_ERROR", "VELOCITY", "READ_STAT1", "READ_STAT2", "READ_STAT3", "DESC"):
                v = ep.axis(k, s)
                if v is not None:
                    ax.epics["AXIS:" + s] = v
            if ep.motor_connected(k):
                for f in MOTOR_FIELDS:
                    v = ep.motor(k, f)
                    if v is not None:
                        ax.epics[f] = v
                for s in ("FERROR", "FERRORMAX", "ELOSS", "HOMED", "SCALE", "DirectMotorNumber", "Mres", "Offset"):
                    v = ep.motor(k, ":" + s)
                    if v is not None:
                        ax.epics[":" + s] = v
                ax.epics["PV"] = ep.cfg.motor_pv(k)
            d = ax.epics.get("AXIS:DESC") or ax.epics.get("DESC")
            if d:
                ax.desc = str(d)
            # synthesize status from EPICS words when no direct link
            if ax.status is None and "AXIS:READ_STAT1" in ax.epics:
                try:
                    s1 = int(ax.epics.get("AXIS:READ_STAT1", 0)) & 0xFFFF
                    s2 = int(ax.epics.get("AXIS:READ_STAT2", 0)) & 0xFFFF
                    s3 = int(ax.epics.get("AXIS:READ_STAT3", 0)) & 0xFFFF
                    if snap.family == "power":
                        ax.status = power_status_from_words(k, (s1 << 16) | s2, s3 << 16)
                    else:
                        w1 = (s1 << 8) | (s2 >> 8)
                        w2 = ((s2 & 0xFF) << 16) | s3
                        ax.status = turbo_status_from_words(k, w1, w2)
                    ax.status.position = _f(ax.epics.get("AXIS:POSITION"))
                    ax.status.following_error = _f(ax.epics.get("AXIS:FOLL_ERROR"))
                    ax.status.velocity = _f(ax.epics.get("AXIS:VELOCITY"))
                    ax.values.setdefault("ActPos", ax.status.position)
                    ax.values.setdefault("PosError", ax.status.following_error)
                    ax.values.setdefault("ActVel", ax.status.velocity)
                except Exception:
                    pass

    # ------------------------------------------------------------------ dumps
    def dump_axis(self, axis: int, include_global: bool = True) -> VariableSet:
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required for a variable dump")
        names: List[str] = []
        if link.family == "power":
            names += kb.power_motor_vars(axis)
            names += kb.power_channel_vars(axis)
            names += kb.power_bricklv_vars(axis)
            names += kb.power_enctable_vars(axis)
            if include_global:
                names += kb.power_sys_vars()
        else:
            names += kb.turbo_motor_vars(axis)
            names += kb.turbo_gate_vars(axis)
            if include_global:
                names += kb.turbo_global_vars()
        vals = link.read_values(names, chunk=24)
        vs = VariableSet(axis=axis, family=link.family, values=vals)
        with self.lock:
            self.dumps[axis] = vs
        return vs

    def dump_global(self) -> VariableSet:
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required")
        if link.family == "power":
            names = kb.power_sys_vars() + kb.power_bricklv_vars(None)
            for c in range(1, 3):
                names += kb.power_coord_vars(c)
        else:
            names = kb.turbo_global_vars()
            for i in range(2):
                names += ["I7%d0%d" % (i, k) for k in range(0, 7)]
        vals = link.read_values(names, chunk=24)
        vs = VariableSet(axis=0, family=link.family, values=vals)
        with self.lock:
            self.global_dump = vs
        return vs

    def read_range(self, prefix: str, start: int, end: int) -> Dict[str, Optional[str]]:
        """Read a numbered range such as P0..P1023 / I100..I199 / M0..M1023 (also 'M->' definitions)."""
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required")
        out: Dict[str, Optional[str]] = {}
        step = 50
        for a in range(start, end + 1, step):
            b = min(end, a + step - 1)
            cmd = "%s%d..%d" % (prefix, a, b)
            try:
                reply = link.send(cmd)
                lines = [ln for ln in reply.split("\n") if ln.strip()]
                if len(lines) == b - a + 1:
                    for k, ln in enumerate(lines):
                        out["%s%d" % (prefix, a + k)] = link._strip_name(ln)
                    continue
            except PmacError:
                pass
            names = ["%s%d" % (prefix, k) for k in range(a, b + 1)]
            out.update(link.read_values(names, chunk=25))
        return out

    # ------------------------------------------------------------------ commands
    def send(self, cmd: str) -> str:
        link = self.direct
        if link is None or not link.connected:
            if self.epics is not None:
                self.epics.send_cmd(cmd)
                return "(sent via EPICS SendCmd - no reply available)"
            raise PmacCommError("not connected")
        return link.send(cmd)

    def write_var(self, name: str, value) -> None:
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required to write variables")
        link.write_value(name, value)

    # ------------------------------------------------------------------ acquisition
    def acquire_step(self, axis: int, size: float, duration_s: float = 1.0,
                     settle_s: float = 0.3, use_gather: bool = True,
                     progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
        """Command a positioning step of ``size`` motor units and record DesPos/ActPos/ServoOut.

        Returns dict with keys t (seconds), des, act, out (out may be empty for Turbo).
        Temporarily sets JogTa/JogTs to tiny values so the command is a true step.
        """
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required")
        fam = link.family
        servo_s = (link.info.servo_period_ms or 0.5) / 1000.0
        saved: Dict[str, Optional[str]] = {}
        try:
            if fam == "power":
                saved = link.read_values(["Motor[%d].JogTa" % axis, "Motor[%d].JogTs" % axis,
                                          "Motor[%d].JogSpeed" % axis])
                link.send("Motor[%d].JogTa=-0.000001 Motor[%d].JogTs=0 Motor[%d].JogSpeed=%g" % (
                    axis, axis, axis, max(abs(size) / servo_s / 1000.0 * 4, 1.0)))
            else:
                saved = link.read_values(["I%d20" % axis, "I%d21" % axis, "I%d22" % axis, "I%d19" % axis])
                link.send("I%d20=1 I%d21=0 I%d22=%g I%d19=1000000" % (axis, axis, axis, max(abs(size) * 4, 1.0), axis))
            trigger = lambda: link.send("#%dj:%g" % (axis, size))  # noqa: E731
            data: Optional[Dict[str, List[float]]] = None
            if use_gather and link.supports_gather() and fam == "power":
                if progress:
                    progress("gathering with the controller's data gather...")
                period = max(1, int(round((duration_s + settle_s) / servo_s / 4000)))
                samples = int((duration_s + settle_s) / (servo_s * period))
                items = ["Motor[%d].DesPos" % axis, "Motor[%d].ActPos" % axis, "Motor[%d].ServoOut" % axis]
                g = link.gather(axis, items, period, samples, trigger)
                if g and g.get(items[1]):
                    nsamp = len(g[items[1]])
                    data = {"t": [k * period * servo_s for k in range(nsamp)],
                            "des": g[items[0]], "act": g[items[1]], "out": g[items[2]]}
            if data is None:
                if progress:
                    progress("gather unavailable - polling the controller over the link")
                data = self._poll_acquire(link, axis, trigger, duration_s + settle_s, fam)
            return data
        finally:
            for k, v in saved.items():
                if v is not None:
                    try:
                        link.send("%s=%s" % (k, v))
                    except PmacError:
                        pass

    def _poll_acquire(self, link: DirectLink, axis: int, trigger, duration: float, fam: str):
        t: List[float] = []
        des: List[float] = []
        act: List[float] = []
        out: List[float] = []
        if fam == "power":
            cmd = "Motor[%d].DesPos Motor[%d].ActPos Motor[%d].ServoOut" % (axis, axis, axis)
        else:
            cmd = "#%dP #%dF" % (axis, axis)
        # pre-trigger samples
        t0 = time.perf_counter()
        for _ in range(5):
            self._sample(link, cmd, fam, t0, t, des, act, out)
        trigger()
        deadline = t0 + duration + (time.perf_counter() - t0)
        while time.perf_counter() < deadline:
            self._sample(link, cmd, fam, t0, t, des, act, out)
        return {"t": t, "des": des, "act": act, "out": out}

    @staticmethod
    def _sample(link, cmd, fam, t0, t, des, act, out):
        try:
            r = link.send(cmd)
        except PmacError:
            return
        now = time.perf_counter() - t0
        lines = [ln for ln in r.split("\n") if ln.strip()]
        try:
            if fam == "power" and len(lines) >= 3:
                d, a, o = (to_float(link._strip_name(x)) for x in lines[:3])
                t.append(now); des.append(d); act.append(a); out.append(o)
            elif fam == "turbo" and len(lines) >= 2:
                p, f = float(lines[0]), float(lines[1])
                t.append(now); des.append(p + f); act.append(p)
        except (ValueError, TypeError):
            pass

    def acquire_open_loop(self, axis: int, magnitude_pct: float, pulse_s: float = 0.1,
                          progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
        """Open-loop test: +mag for pulse_s, -mag for pulse_s, 0. Records ActPos/ActVel/ServoOut.

        Requires the motor to be phased (brushless). Leaves the motor killed afterwards.
        """
        link = self.direct
        if link is None or not link.connected:
            raise PmacCommError("direct link required")
        fam = link.family
        t: List[float] = []
        pos: List[float] = []
        vel: List[float] = []
        out: List[float] = []
        if fam == "power":
            cmd = "Motor[%d].ActPos Motor[%d].ActVel Motor[%d].ServoOut" % (axis, axis, axis)
        else:
            cmd = "#%dP #%dV" % (axis, axis)
        t0 = time.perf_counter()

        def sample():
            try:
                r = link.send(cmd)
            except PmacError:
                return
            now = time.perf_counter() - t0
            lines = [ln for ln in r.split("\n") if ln.strip()]
            try:
                if fam == "power" and len(lines) >= 3:
                    p, v, o = (to_float(link._strip_name(x)) for x in lines[:3])
                    t.append(now); pos.append(p); vel.append(v); out.append(o)
                elif fam == "turbo" and len(lines) >= 2:
                    p, v = float(lines[0]), float(lines[1])
                    t.append(now); pos.append(p); vel.append(v); out.append(0.0)
            except (ValueError, TypeError):
                pass

        try:
            for _ in range(5):
                sample()
            link.send("#%dout%g" % (axis, magnitude_pct))
            end = time.perf_counter() + pulse_s
            while time.perf_counter() < end:
                sample()
            link.send("#%dout%g" % (axis, -magnitude_pct))
            end = time.perf_counter() + pulse_s
            while time.perf_counter() < end:
                sample()
            link.send("#%dout0" % axis)
            end = time.perf_counter() + pulse_s * 0.5
            while time.perf_counter() < end:
                sample()
        finally:
            try:
                link.send("#%dk" % axis)
            except PmacError:
                pass
        return {"t": t, "pos": pos, "vel": vel, "out": out, "mag_pct": [magnitude_pct]}


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
