"""Tests for the physical motor model and its use by the simulator."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pmacdoctor.comm.session import Session  # noqa: E402
from pmacdoctor.comm.simulator import SimulatedPmac  # noqa: E402
from pmacdoctor.core import motor_model, tuning  # noqa: E402
from pmacdoctor.core.motor_model import MotorSpec, STEPPER_MICRO  # noqa: E402

T = 0.0005


def test_ka_matches_hand_calculation():
    s = MotorSpec(torque_constant=0.35, kt_is_per_rms_amp=True, channel_rating="5 A / 15 A",
                  rotor_inertia=4e-5, load_inertia=6e-5, counts_per_rev=4000, lead_mm=0,
                  payload_mass_kg=0)
    p = motor_model.derive(s, T)
    kt_peak = 0.35 / math.sqrt(2)
    torque_per_out = kt_peak * 33.85 / 32768.0
    expect = torque_per_out / 1e-4 * (4000 / (2 * math.pi)) * T * T
    assert abs(p.ka - expect) / expect < 1e-9
    assert abs(p.torque_per_out - torque_per_out) < 1e-12
    # twice the inertia, half the acceleration constant
    s2 = MotorSpec(**dict(s.to_dict(), load_inertia=1.6e-4))   # total 2e-4, i.e. doubled
    assert abs(motor_model.derive(s2, T).ka - p.ka / 2.0) / p.ka < 1e-9


def test_reflected_inertia_through_screw_and_gearbox():
    s = MotorSpec(rotor_inertia=3e-5, load_inertia=2e-5, payload_mass_kg=8.0, lead_mm=2.0,
                  gear_ratio=5.0)
    expect = 3e-5 + (2e-5 + 8.0 * (0.002 / (2 * math.pi)) ** 2) / 25.0
    assert abs(s.total_inertia() - expect) < 1e-15
    # travel converts through the same chain
    assert abs(motor_model.travel_counts(MotorSpec(travel_mm=50, lead_mm=2.0, gear_ratio=5.0,
                                                   counts_per_rev=2000)) - 250000.0) < 1e-6


def test_available_current_falls_with_speed():
    s = MotorSpec()
    p = motor_model.derive(s, T)
    assert p.available_current(0.0) > p.available_current(p.max_vel_cps * 0.5) > 0
    # at the back-EMF ceiling there is nothing left
    assert p.available_current(p.max_vel_cps * 1.01) == 0.0


def test_resonance_above_servo_bandwidth_is_ignored():
    stiff = MotorSpec(coupling_stiffness=1e5, rotor_inertia=3e-5, load_inertia=3e-5)
    p = motor_model.derive(stiff, T)
    assert p.resonance_hz == 0.0
    assert any("above the servo bandwidth" in n for n in p.notes)
    soft = MotorSpec(coupling_stiffness=80.0, rotor_inertia=1.7e-4, load_inertia=2.0e-4)
    assert 100 < motor_model.derive(soft, T).resonance_hz < 250


def test_suggested_settings_are_sane():
    for name, spec in motor_model.presets().items():
        p = motor_model.derive(spec, T)
        out = motor_model.suggest_settings(spec, p, 1, "power")
        assert 0 < out["Motor[1].MaxDac"] <= 32767, name
        assert out["Motor[1].FatalFeLimit"] > out["Motor[1].WarnFeLimit"] > 0, name
        assert out["Motor[1].Servo.Kp"] > 0, name
        assert out["Motor[1].JogSpeed"] > 0, name
        turbo = motor_model.suggest_settings(spec, p, 1, "turbo")
        assert turbo["I169"] > 0 and turbo["I130"] > 0, name


def test_presets_run_in_the_simulator():
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    try:
        sim = s.direct
        sim.realtime = False
        for name, spec in motor_model.presets().items():
            plant, applied = sim.apply_motor(1, spec, configure=True)
            assert plant.ka > 0, name
            assert s.direct.read_float("Motor[1].MaxDac") == applied["Motor[1].MaxDac"], name
            sim.send("#1$")
            sim.send("#1j/")
            sim.step(200)
            mot = sim.motors[1]
            assert mot.amp_ena and mot.closed_loop, name
            fatal = applied["Motor[1].FatalFeLimit"]
            dist = max(20.0, min(fatal * 0.3, applied["Motor[1].JogSpeed"] * 10))
            sim.send("#1j:%g" % dist)
            sim.step(5000)   # 2.5 s: long enough for the slowest preset to pull in
            assert not mot.fe_fatal, "%s tripped a fatal following error" % name
            assert abs(mot.act_pos - dist) < max(8.0, 0.05 * dist), \
                "%s did not reach the target (%.1f of %.1f)" % (name, mot.act_pos, dist)
    finally:
        s.disconnect_all()


def test_microstepping_loses_steps_without_showing_following_error():
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    try:
        sim = s.direct
        sim.realtime = False
        spec = motor_model.presets()["Stepper 1.8 deg, direct micro-stepping"]
        assert spec.motor_type == STEPPER_MICRO
        sim.apply_motor(1, spec, configure=True)
        mot = sim.motors[1]
        sim.send("#1j/")
        sim.step(50)
        mot.extra_friction = 40.0          # heavily overloaded / jammed
        sim.send("#1j+")
        sim.step(600)
        assert mot.stalled, "the overloaded micro-stepper should lose synchronism"
        assert abs(mot.lost_steps) > 1.0, "steps should be lost"
        # the controller still sees no following error: this is the point of the test
        assert abs(mot.des_pos - mot.act_pos) < 1e-6
        assert not mot.fe_fatal and not mot.fe_warn
        # clearing the overload stops the slip
        mot.extra_friction = 1.0
        before = mot.lost_steps
        sim.step(300)
        assert abs(mot.lost_steps - before) < 1e-6
        assert not mot.stalled
    finally:
        s.disconnect_all()


def test_fault_injection_round_trip():
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    try:
        sim = s.direct
        sim.realtime = False
        sim.send("#1j/")
        sim.step(20)
        sim.set_fault(1, "enc_loss", True)
        sim.step(10)
        assert sim.motors[1].enc_loss and not sim.motors[1].amp_ena
        sim.set_fault(1, "enc_loss", False)
        sim.step(10)
        assert not sim.motors[1].enc_loss
        sim.set_fault(1, "reverse_feedback", True)
        assert sim.motors[1].feedback_sign < 0
        sim.set_fault(1, "limits_unwired", True)
        assert not sim.motors[1].limits_wired
        sim.set_fault(1, "jam", True)
        assert sim.motors[1].extra_friction > 1
        # applying a motor model clears the injected faults
        sim.apply_motor(1, MotorSpec(), configure=False)
        m = sim.motors[1]
        assert m.feedback_sign > 0 and m.limits_wired and m.extra_friction == 1.0
    finally:
        s.disconnect_all()


def test_scope_ring_buffer():
    # built directly so no background thread advances the model: the sample count is exact
    sim = SimulatedPmac("power", realtime=False)
    sim.connect()
    try:
        sim.scope_start(1, decimate=2)
        assert abs(sim.scope_rate_hz() - 1000.0) < 1e-6
        sim.send("#1j/")
        sim.step(200)
        sim.send("#1j:200")
        sim.step(400)
        rows = sim.scope_drain()
        assert len(rows) == 300, len(rows)          # 600 cycles, decimated by 2
        assert sim.scope_drain() == []              # draining empties the buffer
        t, des, act, fe, out, amps, vel, flags = rows[-1]
        assert t > 0 and abs(fe - (des - act)) < 1e-9
        assert int(flags) & sim.SCOPE_ENABLED
        assert abs(amps) < 50
        # a fatal following error shows up in the flags
        sim.send("Motor[1].FatalFeLimit=2")
        sim.send("#1j/")
        sim.step(20)
        sim.send("#1j:5000")
        sim.step(60)
        assert any(int(r[7]) & sim.SCOPE_FATAL for r in sim.scope_drain())
        sim.scope_stop()
        sim.step(50)
        assert sim.scope_drain() == []
    finally:
        sim.disconnect()


def test_step_larger_than_fatal_limit_is_refused():
    from pmacdoctor.core.autotuner import AutoTuner
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    try:
        sim = s.direct
        sim.realtime = False
        sim.send("Motor[1].FatalFeLimit=500")
        at = AutoTuner(s, 1)
        assert at.check_step(100) is None
        msg = at.check_step(900)
        assert msg and "fatal limit" in msg
    finally:
        s.disconnect_all()
