"""Shared data structures passed between the session (I/O), the rule engine and the UI."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .status_bits import AxisStatus, DecodedBit


@dataclass
class AxisSnapshot:
    axis: int
    status: Optional[AxisStatus] = None
    values: Dict[str, Optional[float]] = field(default_factory=dict)  # ActPos, PosError, ServoOut ...
    epics: Dict[str, object] = field(default_factory=dict)            # motor record fields, AXISn PVs
    desc: str = ""

    def v(self, key: str, default=None):
        x = self.values.get(key)
        return default if x is None else x

    def e(self, key: str, default=None):
        x = self.epics.get(key)
        return default if x is None else x


@dataclass
class ControllerSnapshot:
    family: str = "power"
    timestamp: float = field(default_factory=time.time)
    direct_ok: bool = False
    epics_ok: bool = False
    description: str = ""
    firmware: str = ""
    cpu: str = ""
    servo_period_ms: float = 0.0
    global_word: int = 0
    global_bits: List[DecodedBit] = field(default_factory=list)
    sys_values: Dict[str, Optional[str]] = field(default_factory=dict)   # Sys.* / BrickLV.* / I0..
    epics: Dict[str, object] = field(default_factory=dict)              # controller-level PVs
    axes: Dict[int, AxisSnapshot] = field(default_factory=dict)
    comm_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    def axis(self, n: int) -> AxisSnapshot:
        return self.axes.setdefault(n, AxisSnapshot(axis=n))

    def sysf(self, key: str) -> Optional[float]:
        v = self.sys_values.get(key)
        if v is None:
            return None
        try:
            if isinstance(v, str) and v.startswith("$"):
                return float(int(v[1:], 16))
            return float(v)
        except (TypeError, ValueError):
            return None


@dataclass
class VariableSet:
    """A dump of variables for one axis (plus global/channel context) as raw strings."""

    axis: int
    family: str
    timestamp: float = field(default_factory=time.time)
    values: Dict[str, Optional[str]] = field(default_factory=dict)

    def f(self, name: str) -> Optional[float]:
        v = self.values.get(name)
        if v is None:
            return None
        try:
            s = v.strip()
            if s.startswith("$"):
                return float(int(s[1:], 16))
            return float(s)
        except (TypeError, ValueError):
            return None

    def s(self, name: str) -> Optional[str]:
        return self.values.get(name)


@dataclass
class Finding:
    rule_id: str
    severity: str           # fault | warning | info | ok
    title: str
    detail: str
    axis: Optional[int] = None
    causes: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    evidence: Dict[str, object] = field(default_factory=dict)
    reference: str = ""

    @property
    def rank(self) -> int:
        return {"fault": 0, "warning": 1, "info": 2, "ok": 3}.get(self.severity, 4)
