import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmacdoctor.comm.session import Session  # noqa: E402
from pmacdoctor.core import motor_model, tuning  # noqa: E402

s = Session()
s.connect_direct("simulator", scenario="healthy")
sim = s.direct
sim.realtime = False
print("default plant:", "\n  ".join(sim.motors[1].plant.notes))

sim.send("#1j/")
sim.step(100)
data = {"t": [], "des": [], "act": [], "out": []}
sim.send("#1j:2000")
for k in range(1200):
    sim.step(1)
    data["t"].append(k * 0.0005)
    data["des"].append(sim.motors[1].des_pos)
    data["act"].append(sim.motors[1].act_pos)
    data["out"].append(sim.motors[1].servo_out)
m = tuning.analyze_step(data["t"], data["des"], data["act"], data["out"], 8536)
print("default step:", m.verdict, "os=%.1f%%" % (m.overshoot_pct or 0), "rise=%s" % m.rise_time_ms,
      "settle=%s" % m.settling_time_ms, "sat=%s" % m.saturation_pct)

for name, spec in motor_model.presets().items():
    plant, applied = sim.apply_motor(2, spec, configure=True)
    print("\n==", name)
    for n in plant.notes:
        print("   ", n)
    print("    Kp=%s Kvfb=%s MaxDac=%s FatalFe=%s JogSpeed=%s" % (
        applied.get("Motor[2].Servo.Kp"), applied.get("Motor[2].Servo.Kvfb"),
        applied.get("Motor[2].MaxDac"), applied.get("Motor[2].FatalFeLimit"),
        applied.get("Motor[2].JogSpeed")))
    sim.send("#2$")
    sim.send("#2j/")
    sim.step(200)
    d = {"t": [], "des": [], "act": [], "out": []}
    fatal = float(applied.get("Motor[2].FatalFeLimit", 1000))
    dist = max(20.0, min(fatal * 0.4, float(applied.get("Motor[2].JogSpeed", 10)) * 20))
    sim.send("Motor[2].JogTa=-0.000001 Motor[2].JogTs=0 Motor[2].JogSpeed=%g" % (dist * 40))
    for k in range(-20, 0):
        sim.step(1)
        d["t"].append(k * 0.0005)
        d["des"].append(sim.motors[2].des_pos)
        d["act"].append(sim.motors[2].act_pos)
        d["out"].append(sim.motors[2].servo_out)
    sim.send("#2j:%g" % dist)
    for k in range(2400):
        sim.step(1)
        d["t"].append(k * 0.0005)
        d["des"].append(sim.motors[2].des_pos)
        d["act"].append(sim.motors[2].act_pos)
        d["out"].append(sim.motors[2].servo_out)
    mm = tuning.analyze_step(d["t"], d["des"], d["act"], d["out"], applied.get("Motor[2].MaxDac"))
    st = sim.motors[2]
    print("    step %g cts -> %s os=%.1f%% rise=%s settle=%s sat=%s | stalled=%s lost=%.1f fe=%.2f" % (
        dist, mm.verdict, mm.overshoot_pct or 0, mm.rise_time_ms, mm.settling_time_ms,
        mm.saturation_pct, st.stalled, st.lost_steps, st.des_pos - st.act_pos))
s.disconnect_all()
