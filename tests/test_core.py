"""Unit tests for the core (run: python -m pytest tests -q)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from pmacdoctor.comm.base import DirectLink, PmacError, to_float  # noqa: E402
from pmacdoctor.comm.ethernet_turbo import VR_DOWNLOAD, VR_PMAC_GETRESPONSE, build_packet  # noqa: E402
from pmacdoctor.comm.simulator import SimulatedPmac  # noqa: E402
from pmacdoctor.comm.session import Session  # noqa: E402
from pmacdoctor.core import knowledge as kb  # noqa: E402
from pmacdoctor.core import rules, tuning, units  # noqa: E402
from pmacdoctor.core.models import AxisSnapshot, ControllerSnapshot, VariableSet  # noqa: E402
from pmacdoctor.core.status_bits import (parse_power_axis_status, parse_power_global_status,  # noqa: E402
                                         parse_turbo_axis_status, decode_msta)


# ---------------------------------------------------------------- status words
def test_power_status_parse():
    # AmpEna(12) ClosedLoop(13) DesVelZero(14) InPos(11) PhaseFound(9) -> 0x7A00
    st = parse_power_axis_status(1, "$00007A0000000001")
    assert st.amp_enabled and st.closed_loop and st.in_position and st.phase_found
    assert not st.any_fault and st.summary == "In position"
    st = parse_power_axis_status(2, "$%08X%08X" % ((1 << 24) | (1 << 21), 0))
    assert st.amp_fault and st.i2t_fault and st.severity == "fault"
    assert "AMP FAULT" in st.summary


def test_turbo_status_parse():
    # word1: activated(23) ampena(19) desvelzero(13); word2: inpos(0) assigned CS(15) cs=2 (bits20-23 = 1)
    w1 = (1 << 23) | (1 << 19) | (1 << 13)
    w2 = 1 | (1 << 15) | (1 << 20)
    st = parse_turbo_axis_status(3, "%06X%06X" % (w1, w2))
    assert st.amp_enabled and st.closed_loop and st.in_position and st.cs_number == 2
    w2 = (1 << 2) | (1 << 3)
    st = parse_turbo_axis_status(3, "%06X%06X" % (w1, w2))
    assert st.fe_fatal and st.amp_fault


def test_global_and_msta():
    w, bits = parse_power_global_status("$00000001")
    assert w == 1 and any(b.name == "WDTFault_b0" and b.value for b in bits)
    m = {b.name: b.value for b in decode_msta((1 << 1) | (1 << 9))}
    assert m["DONE"] and m["PROBLEM"] and not m["MOVING"]


# ---------------------------------------------------------------- base link
def test_clean_and_errors():
    assert DirectLink._clean("40\r15\r\x06") == "40\n15"
    try:
        DirectLink._clean("\x07ERR003\r")
        assert False
    except PmacError:
        pass
    assert DirectLink._strip_name("Motor[1].Servo.Kp=40") == "40"
    assert to_float("$FF") == 255.0


def test_ethernet_packet():
    p = build_packet(VR_DOWNLOAD, VR_PMAC_GETRESPONSE, data=b"I10")
    assert p[:2] == bytes([0x40, 0xBF]) and p[6:8] == b"\x00\x03" and p[8:] == b"I10"


# ---------------------------------------------------------------- knowledge
def test_knowledge_lookup():
    d = kb.describe("Motor[3].Servo.Kp")
    assert d is not None and d.equiv == "Ixx30"
    d = kb.describe("I330", "turbo")
    assert d is not None and "proportional" in d.desc.lower()
    assert kb.describe("PowerBrick[1].Chan[2].EncCtrl") is not None
    assert len(kb.power_motor_vars(1)) > 100


# ---------------------------------------------------------------- units
def test_i2t_formulas():
    cs = units.compute_i2t(7.2, 3.6, 1.0, 33.85, "brushless", rms=True)
    # manual: MaxDac = 7.2*32768*sqrt2*cos30/33.85
    assert abs(cs.max_dac - 7.2 * 32768 * math.sqrt(2) * math.cos(math.radians(30)) / 33.85) < 1e-6
    assert abs(cs.i2t_trip - (cs.max_dac ** 2 - cs.i2t_set ** 2)) < 1e-6
    assert abs(units.pwm_sf(24, 48) - 0.95 * 16384 * 0.5) < 1e-6
    assert units.microstep_counts_per_rev(1.8) == 102400


# ---------------------------------------------------------------- tuning math
def _second_order_step(wn, zeta, T, n, size):
    t = np.arange(n) * T
    wd = wn * math.sqrt(max(1e-9, 1 - zeta ** 2))
    y = size * (1 - np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta / math.sqrt(1 - zeta ** 2) * np.sin(wd * t)))
    return t, y


def test_analyze_step_overshoot():
    T = 0.0005
    t, y = _second_order_step(2 * math.pi * 20, 0.3, T, 2000, 100.0)
    t = np.concatenate([[-0.01, -0.005], t])
    y = np.concatenate([[0, 0], y])
    des = np.concatenate([[0, 0], np.full(2000, 100.0)])
    m = tuning.analyze_step(t, des, y)
    assert m.ok
    expected_os = 100 * math.exp(-math.pi * 0.3 / math.sqrt(1 - 0.09))
    assert abs(m.overshoot_pct - expected_os) < 3
    assert m.verdict != "Good response"
    assert any("Overshoot" in r for r in m.recommendations)


def test_analyze_step_good():
    T = 0.0005
    t, y = _second_order_step(2 * math.pi * 20, 0.9, T, 2000, 100.0)
    des = np.full(2000, 100.0)
    t = np.concatenate([[-0.01], t])
    y = np.concatenate([[0], y])
    des = np.concatenate([[0], des])
    m = tuning.analyze_step(t, des, y)
    assert m.ok and m.overshoot_pct < 3 and m.verdict == "Good response"


def test_identify_and_design():
    T = 0.0005
    ka = 2e-4
    mag = 1000.0
    n = 400
    t = np.arange(n) * T
    out = np.where(t < 0.1, mag, -mag)
    vel = np.where(t < 0.1, ka * mag * t / T, ka * mag * 0.1 / T - ka * mag * (t - 0.1) / T)
    pm = tuning.identify_plant(t, vel, out, T, 0.1)
    assert pm.ok and abs(pm.ka - ka) / ka < 0.05
    g = tuning.design_gains(pm, 20.0, 0.8)
    wn = 2 * math.pi * 20 * T
    assert abs(g.kp - wn * wn / pm.ka) < 1e-9 and abs(g.kvfb - 2 * 0.8 * wn / pm.ka) < 1e-9
    an = tuning.analyze_gains(g.kp, g.kvfb, pm.ka, T)
    assert abs(an["fn_hz"] - 20) < 0.01 and abs(an["zeta"] - 0.8) < 0.01
    inv = tuning.identify_plant(t, -vel, out, T, 0.1)
    assert not inv.ok and "INVERTED" in inv.message


# ---------------------------------------------------------------- simulator + session + rules
def test_simulator_scenarios_and_rules():
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    try:
        sim: SimulatedPmac = s.direct  # type: ignore
        sim.realtime = False
        snap = s.poll()
        assert len(snap.axes) == 8 and snap.family == "power"
        for n in range(1, 9):
            s.dump_axis(n, include_global=(n == 1))
        g = s.dump_global()
        f = rules.evaluate(snap, s.dumps, g)
        assert not [x for x in f if x.severity == "fault"]
        # limits not wired -> both limits fault on axis 4
        sim.send("sim.scenario=limits_not_wired")
        snap = s.poll()
        f = rules.evaluate(snap, s.dumps, g)
        assert any(x.rule_id == "both-limits" and x.axis == 4 for x in f)
        # not phased
        sim.send("sim.scenario=not_phased")
        s.dump_axis(6)
        snap = s.poll()
        f = rules.evaluate(snap, s.dumps, g)
        assert any(x.rule_id == "not-phased" and x.axis == 6 for x in f)
        # watchdog
        sim.send("sim.scenario=watchdog")
        snap = s.poll()
        f = rules.evaluate(snap, s.dumps, g)
        assert any("WDT" in x.rule_id or x.rule_id == "wdt" for x in f)
        # encoder loss -> kill
        sim.send("sim.scenario=encoder_loss")
        sim.send("#3j/")
        sim.step(50)
        snap = s.poll()
        assert snap.axes[3].status.enc_loss
        f = rules.evaluate(snap, s.dumps, g)
        assert any(x.rule_id == "enc-loss" and x.axis == 3 for x in f)
    finally:
        s.disconnect_all()


def test_rules_epics_mismatch():
    snap = ControllerSnapshot(family="power", epics_ok=True)
    ax = snap.axis(1)
    ax.values["ActPos"] = 1000.0
    ax.epics.update({"RBV": 5.0, "MRES": 0.001, "OFF": 0.0, "DIR": 0, "MSTA": 1 << 9})
    f = rules.evaluate(snap)
    ids = {x.rule_id for x in f}
    assert "rbv-mismatch" in ids and "msta-problem" in ids


def test_turbo_simulator_poll():
    s = Session()
    s.connect_direct("simulator", scenario="healthy", family="turbo")
    try:
        snap = s.poll()
        assert snap.family == "turbo" and snap.axes[1].status is not None
        vs = s.dump_axis(1)
        assert vs.f("I130") == 2000.0
        rng = s.read_range("I", 100, 110)
        assert rng["I100"] == "1"
    finally:
        s.disconnect_all()
