"""Abstract direct-link backend.

A *direct link* talks the PMAC on-line command language (``#1j+``, ``Motor[1].Servo.Kp``,
``I130`` ...). Three implementations exist: :mod:`ssh_power` (Power PMAC via gpascii),
:mod:`ethernet_turbo` (Turbo PMAC / Geobrick via Ethernet port 1025) and
:mod:`simulator` (in-process model used for demos and tests).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

ACK = "\x06"
BELL = "\x07"


class PmacError(Exception):
    """Controller returned an error (BELL / ERRxxx / error #nn)."""


class PmacCommError(Exception):
    """Transport problem (timeout, disconnected)."""


@dataclass
class LinkInfo:
    family: str = "power"  # power | turbo
    description: str = ""
    cid: int = 0
    cpu: str = ""
    firmware: str = ""
    servo_period_ms: float = 0.0
    phase_over_servo: float = 0.0
    max_motors: int = 8
    extras: Dict[str, str] = field(default_factory=dict)


class DirectLink:
    """Base class. Subclasses implement :meth:`_open`, :meth:`_close`, :meth:`_transact`."""

    family: str = "power"
    name: str = "direct"

    def __init__(self):
        self._lock = threading.RLock()
        self.connected = False
        self.info = LinkInfo()
        self.log: List[str] = []
        self.max_log = 2000
        self.listeners = []  # callables(cmd, reply, dt)

    # ---------------------------------------------------------------- lifecycle
    def connect(self) -> LinkInfo:
        with self._lock:
            self._open()
            self.connected = True
            try:
                self._identify()
            except Exception:
                self.connected = False
                self._close()
                raise
            return self.info

    def disconnect(self):
        with self._lock:
            try:
                self._close()
            finally:
                self.connected = False

    def _open(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def _close(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def _transact(self, cmd: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---------------------------------------------------------------- commands
    def send(self, cmd: str) -> str:
        """Send one on-line command and return the cleaned reply (no ACK, lines joined by \\n)."""
        if not self.connected:
            raise PmacCommError("not connected")
        with self._lock:
            t0 = time.perf_counter()
            try:
                raw = self._transact(cmd)
            except PmacError:
                raise
            except Exception as exc:  # transport
                self.connected = False
                raise PmacCommError(str(exc)) from exc
            dt = time.perf_counter() - t0
            reply = self._clean(raw)
            self._record(cmd, reply, dt)
            return reply

    def _record(self, cmd: str, reply: str, dt: float):
        line = "%s  ->  %s  (%.1f ms)" % (cmd, reply.replace("\n", " | ")[:200], dt * 1e3)
        self.log.append(line)
        if len(self.log) > self.max_log:
            del self.log[: len(self.log) - self.max_log]
        for cb in list(self.listeners):
            try:
                cb(cmd, reply, dt)
            except Exception:
                pass

    @staticmethod
    def _clean(raw: str) -> str:
        if raw is None:
            return ""
        s = raw.replace(ACK, "")
        if BELL in s:
            msg = s.replace(BELL, "").strip()
            raise PmacError(msg or "ERR")
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln.rstrip() for ln in s.split("\n")]
        lines = [ln for ln in lines if ln != ""]
        for ln in lines:
            low = ln.lower()
            if low.startswith("err") and re.match(r"^err\d{3}", low):
                raise PmacError(ln)
            if "error #" in low:
                raise PmacError(ln)
        return "\n".join(lines)

    # ---------------------------------------------------------------- helpers
    def read_values(self, names: Sequence[str], chunk: int = 20) -> Dict[str, Optional[str]]:
        """Read many variables. Returns raw strings (None when the controller rejected one).

        Variables are grouped into one command line per ``chunk`` names; a failing chunk is
        retried one variable at a time so a single bad name doesn't poison the group.
        """
        out: Dict[str, Optional[str]] = {}
        names = list(names)
        for k in range(0, len(names), chunk):
            grp = names[k:k + chunk]
            try:
                reply = self.send(" ".join(grp))
                vals = self._split_values(reply, len(grp))
                if vals is None:
                    raise PmacError("count mismatch")
                for n, v in zip(grp, vals):
                    out[n] = v
            except PmacError:
                for n in grp:
                    try:
                        out[n] = self._strip_name(self.send(n))
                    except PmacError:
                        out[n] = None
        return out

    def _split_values(self, reply: str, expected: int) -> Optional[List[str]]:
        lines = [self._strip_name(ln) for ln in reply.split("\n") if ln.strip() != ""]
        if len(lines) != expected:
            # Turbo may return values separated by CR which _clean converted to \n; if a
            # single line contains spaces try splitting on whitespace.
            if len(lines) == 1 and len(lines[0].split()) == expected:
                return lines[0].split()
            return None
        return lines

    @staticmethod
    def _strip_name(v: str) -> str:
        # Power PMAC without 'echo 7' returns "Motor[1].Servo.Kp=40"
        if "=" in v and not v.strip().startswith("$") and not v.strip().startswith("-"):
            left, right = v.split("=", 1)
            if re.match(r"^[A-Za-z_][\w\.\[\]]*$", left.strip()):
                return right.strip()
        return v.strip()

    def read_float(self, name: str) -> Optional[float]:
        try:
            return to_float(self._strip_name(self.send(name)))
        except (PmacError, ValueError):
            return None

    def write_value(self, name: str, value) -> None:
        self.send("%s=%s" % (name, format_value(value)))

    def read_many_floats(self, names: Sequence[str]) -> Dict[str, Optional[float]]:
        raw = self.read_values(names)
        return {k: (to_float(v) if v is not None else None) for k, v in raw.items()}

    # ---------------------------------------------------------------- identity
    def _identify(self):
        """Fill self.info. Subclasses may extend."""
        pass

    # ---------------------------------------------------------------- gather (optional)
    def supports_gather(self) -> bool:
        return False

    def gather(self, axis: int, items: Sequence[str], period: int, samples: int,
               trigger) -> Optional[Dict[str, List[float]]]:  # pragma: no cover
        return None


def to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return None
    if s.startswith("$"):
        return float(int(s[1:], 16))
    if s.lower() in ("nan", "inf", "-inf"):
        return float(s)
    try:
        return float(s)
    except ValueError:
        # 'Motor[1].pEnc' style addresses -> not numeric
        m = re.match(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?", s)
        if m:
            return float(m.group(0))
        raise


def format_value(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return ("%.10g" % v)
    return str(v)
