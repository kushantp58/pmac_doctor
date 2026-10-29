"""Quick end-to-end exercise of the core against the simulator (no GUI). Run: python tests/smoke_core.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pmacdoctor.comm.session import Session  # noqa: E402
from pmacdoctor.core import rules, tuning  # noqa: E402
from pmacdoctor.core.autotuner import AutoTuner  # noqa: E402


def main():
    s = Session()
    s.connect_direct("simulator", scenario="healthy")
    snap = s.poll()
    print("poll ms", round(snap.comm_ms, 1), [(n, a.status.summary) for n, a in snap.axes.items()])
    d = s.dump_axis(1)
    unknown = [k for k, v in d.values.items() if v is None]
    print("dump", len(d.values), "values;", len(unknown), "unknown:", unknown[:10])
    g = s.dump_global()
    fs = rules.evaluate(snap, s.dumps, g)
    print("findings healthy:", [(f.severity, f.title) for f in fs][:8])
    s.direct.send("sim.scenario=i2t_trip")
    s.direct.send("#2j+")
    time.sleep(1.5)
    s.direct.send("sim.scenario=limits_not_wired")
    snap = s.poll()
    s.dump_axis(2)
    s.dump_axis(4)
    fs = rules.evaluate(snap, s.dumps, g)
    print("findings faults:", [(f.severity, f.title) for f in fs if f.severity != "info"][:10])
    s.direct.send("sim.scenario=healthy")
    s.direct.send("#1j/")
    time.sleep(0.2)
    t0 = time.time()
    data = s.acquire_step(1, 500, 0.5, 0.2, progress=print)
    print("acq", len(data["t"]), "samples in", round(time.time() - t0, 2), "s")
    m = tuning.analyze_step(data["t"], data["des"], data["act"], data["out"], 28000)
    print(m.verdict, m.rise_time_ms, m.overshoot_pct, m.settling_time_ms, m.recommendations[:2])
    at = AutoTuner(s, 1, progress=print)
    r = at.run(step_size=300, bandwidth_hz=25, iterations=2)
    print(r.ok, r.message, r.plant.ka if r.plant else None, r.applied)
    s.disconnect_all()


if __name__ == "__main__":
    main()
