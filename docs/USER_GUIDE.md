# PMAC Doctor - User Guide

PMAC Doctor attaches to an EPICS IOC (DiamondLightSource `pmac` driver) and/or directly to a
Delta Tau / Omron PMAC controller and helps you find motor and configuration problems and tune
servo loops. This guide walks through each page. Everything can be tried without hardware using
the built-in simulator.

---

## 1. Connecting (Settings page)

### EPICS IOC
* **PV prefix** - the `$(P)` of the controller template, e.g. `BL99P-MO-BRICK-01` or
  `XF:31IDA-CT{MC:01}`. The app monitors `<P>:PMACTYPE`, `<P>:READ_CTRLSTAT1..3`,
  `<P>:AXISn:POSITION/FOLL_ERROR/VELOCITY/READ_STAT1..3`, CPU load, PLC bits, feedrate, comms
  statistics and the `I_/P_/M_VARIABLES_RBV` waveforms.
* **Motor record** - template for the motor record names. Default `{P}:M{n}` matches the
  Diamond aliases; use `{n}` for the axis number, `{nn}` for zero-padded, e.g.
  `XF:31IDA-OP{Mir:1-Ax:{n}}Mtr`.
* **CA address list** - optional `EPICS_CA_ADDR_LIST` if the IOC is on another subnet.

### Direct link
* **Power PMAC via SSH** (Power Brick LV/AC, CK3E, UMAC): IP, port 22, user `root`, password
  `deltatau` unless changed. The app runs `gpascii -2` like the IOC does and uses the
  controller's data gather for tuning captures.
* **Turbo PMAC / Geobrick via Ethernet**: IP, port 1025 (Delta Tau Ethernet protocol).
* **Built-in simulator**: choose family (Power / Turbo) and a fault scenario.

Both links can be used together. With EPICS only, status and motor records are analysed but
variable dumps and tuning tests are unavailable because the IOC's `SendCmd` PV is write-only.

**Preferences**: poll interval, confirmation prompts before anything that moves or kills a motor,
auto-reconnect at start-up.

---

## 2. Dashboard

* **Controller card** - type, firmware, CPU, servo rate, poll time and the decoded global status
  word (`?` on Power PMAC = `Sys.Status`; `???` on Turbo).
* **Health card** - live result of the status-level rules (faults / warnings / notes).
* **Links card** - EPICS / direct state and the Power Brick LV amplifier summary
  (`BrickLV.BusUnderVoltage`, `BusOverVoltage`, `OverTemp`, `Monitor`).
* **Motor cards** - one per axis: status badge (In position / Moving / Killed / AMP FAULT /
  FATAL FE / ...), position (EPICS `RBV` in EGU if available, otherwise controller units),
  following error and key flags. Click a card to open the axis.

---

## 3. Axes

Live values (position, EPICS position, following error, velocity, servo output as % of
`MaxDac`, I2T integrator as % of trip), the full decoded `#n?` status word, the EPICS motor record
fields with decoded `MSTA`, a trend of following error / velocity / output, the findings for this
axis and **actions**: enable (`#nj/`), kill, stop, jog +/- by a distance, home, phase (`#n$`),
clear faults (`BrickLV.Reset=1` + re-enable on Power Brick LV).

---

## 4. Variables

Requires a direct link. **Read from controller** reads, for the selected axis, every element of
the knowledge base:

| Power PMAC | Turbo PMAC |
|---|---|
| `Motor[x].*` activation/pointers, scaling & commutation, current loop & I2T, servo gains, safety limits, jog/home/in-position, live status | `Ixx00-Ixx99` |
| `PowerBrick[i].Chan[j].*` (encoder decode, capture, PFM, raw flags, count/loss errors) | `I7mn0-I7mn9`, `I7m00-I7m06` |
| `BrickLV.*` amplifier elements | `I0-I99` communication / clock / PLC settings |
| `EncTable[n].*`, `Sys.*`, `Coord[c].*` | |

Each row shows value, documented default (non-default values in amber), description and the
Turbo/Power equivalent. Filter by group or text, show only non-default values, **double-click a
value to write it**, read numbered ranges (`P0..1023`, `Q`, `M`, `I`, `M->` definitions), export
to CSV / JSON / PMAC script, **save a baseline** and **compare** a motor against it (differences
highlighted) - ideal for "motor 3 used to work, what changed?" or "make axis 5 like axis 4".

Nothing is written to flash; type `save` in the Terminal when you want to keep changes.

---

## 5. Diagnostics

**Run diagnosis** polls the controller, (with a direct link) reads the full configuration of
every axis, and runs the rule engine. Findings are ranked (fault > warning > note), each with a
plain-language explanation, **possible causes**, **suggested actions**, the evidence used and a
reference to the Power Brick LV manual section. Filter by severity, restrict to one axis, and
**export** an HTML or Markdown report.

Examples of what is checked:

* amplifier fault -> I2T (`I2tFault`, `I2tSum`), hardware over-current (`BrickLV.Chan[j].OverCurrent`),
  bus under/over-voltage, over-temperature, STO, amp-fault polarity (`AmpFaultLevel`), `pAmpFault`
* fatal / warning following error -> phasing, encoder direction, jam/brake, no bus power, gains,
  saturation (`ServoOut` vs `MaxDac`), limit too tight, encoder loss, count errors
* both limits active -> limits not wired but `pLimits` set; polarity; `LimitBits`
* not phased, phasing search error, open-loop amp enabled, deactivated motor, not homed
* configuration: `FatalFeLimit=0`, `MaxDac` range / vs motor rating, `I2tTrip=0`, `I2TSet > MaxDac`,
  zero gains, no damping, missing feedforward, `MaxPosErr`, current-loop gains, `PwmSf`, `IbBias`,
  `PhaseTableBias`, `AdcMask`, `PhaseFindingTime`, software limits, `TwoPhaseMode` vs `PhaseMode`,
  `pEncLoss` disabled, `EncClockDiv`, Turbo `Ixx24` limits-disabled bit
* global: watchdog, project/config load errors, no clocks, abort input, CPU load, `BrickLV.Monitor`
  off, EPICS feedrate problem, slow comms, PLCs disabled, MACRO errors, I3/I6 handshake settings
* EPICS vs controller: `RBV` != `ActPos*MRES*SCALE+OFF`, soft-limit mismatch, following-error
  alarm near the fatal limit, `MSTA` PROBLEM/COMM_ERR, `LVIO`, `MISS`, `DMOV` stuck, ELOSS flag

Enter the motor's current ratings on the Tuning -> *Current & I2T calculator* tab and press
**Remember for diagnostics**: the engine then compares `MaxDac` / `I2TSet` with the motor.

---

## 6. Tuning

> Tuning tests move the motor. Make sure it can travel the test distance in both directions,
> limits work and `FatalFeLimit` is set. Nothing is saved to flash.

A step larger than about 70 % of `FatalFeLimit` is refused: a true position step creates that
much following error instantly and would kill the motor before it could respond.

### Step response
Commands a position step (`#nj:size` with accel time temporarily minimised), captures commanded /
actual position and servo output (Power PMAC: data gather, otherwise polling) and reports rise
time, overshoot, settling time, steady-state error, peak error, ringing frequency, output
saturation and noise, with recommendations. The **Servo gains** card reads/edits/applies gains
and can remember and restore a set.

### Open-loop test
Applies +M % then -M % of `MaxDac` as a raw output (`#noutM`), records velocity, then kills the
motor. A healthy axis shows a velocity saw-tooth with positive command -> positive velocity
(manual p.179-180). An **inverted** result means the encoder decode (`EncCtrl` 3<->7) or the
commutation direction (`PwmSf`/`PhaseOffset` sign, motor leads) is wrong - do not close the loop.
The slope gives the plant acceleration constant *Ka* and friction.

### Auto-tune
1. optional open-loop identification (Ka, friction)
2. model-based design for the requested bandwidth *fn* and damping *zeta*:
   `Kp = wn^2/Ka`, `Kvfb = 2*zeta*wn/Ka`, `Ki = Kp*wn*ratio`, `Kvff = Kvfb`, `Kaff = 1/Ka`
   (wn in rad/servo-cycle; Turbo gains converted to Ixx30-35 approximately)
3. verification step with the PD(I) part; the integrator is softened if it overshoots, the
   bandwidth is reduced on ringing/saturation and increased when sluggish, for up to N iterations
4. the best iteration is applied together with feedforward; original gains can be restored.

### Current & I2T calculator
Implements the manual's formulas:
`MaxDac = Ipeak*32768*(sqrt2 if RMS)*(cos30 if brushless)/MaxADC`, same for `I2TSet` with the
continuous current, `I2tTrip = (MaxDac^2 - I2TSet^2)*t_peak`, `PwmSf = 0.95*16384*(Vmotor/Vbus if
Vbus > Vmotor)`. Max ADC per channel rating: 0.25/0.75 A -> 1.6925 A, 1/3 A -> 6.77 A,
5/15 A -> 33.85 A. Values can be written to the motor and remembered for the diagnostics.

---

## 7. Virtual motor (simulator only)

Describe a real stepper or servo from its datasheet and the simulator becomes that motor, so
you can practise, demonstrate faults or sanity-check a tuning recipe before touching hardware.

**What you enter**

* **Motor**: type (brushless, DC brush, stepper with encoder, stepper direct micro-stepping,
  linear), torque constant, peak and continuous current, winding resistance and inductance,
  pole pairs or step angle and holding torque, bus and motor voltage, drive channel rating.
* **Mechanics**: rotor inertia, load inertia, payload mass, screw lead, gear ratio, Coulomb and
  viscous friction, coupling stiffness and damping, travel.
* **Feedback**: encoder type, resolution, noise amplitude and whether to quantise to whole counts.

Inertias are entered in g·cm² (kg for a linear motor) and reflected to the motor shaft through
the gearbox and screw automatically.

**What it computes.** The acceleration constant Ka in counts per servo cycle squared per output
unit, torque per output unit, the back-EMF and inductance limited speed/torque droop, the
Coulomb friction expressed in output units, the two-mass coupling resonance (ignored and
flagged when it lands above the servo bandwidth) and, for steppers, the full-step size and
pull-out current.

**Configure the controller too.** With the checkbox ticked, the simulated PMAC is given the
matching settings: MaxDac, I2TSet, I2tTrip and PwmSf from the current ratings, commutation
setup for the motor type, following-error limits, in-position band, jog and maximum speed, and
a starting PID set designed for the bandwidth and damping you choose. Direct micro-stepping
follows the manual's own recipe instead (Kp = Kvff = Kaff = 1, no damping or integral term,
SlipGain and AdvGain computed, MaxDac used as a speed limit, IdCmd set to half I2TSet).

**Fault injection.** Tick a box to inject a fault on the selected axis and untick to clear it:
encoder unplugged, limits unwired, both limits asserted, reversed encoder direction, bus off,
mechanical jam, excited resonance, lost phase reference, amplifier fault. The Diagnostics page
should find each one.

**Live panel.** For steppers it shows lost steps, rotor load angle and a stall indicator. A
direct micro-stepping motor deliberately reports zero following error while it loses steps,
which is exactly why lost motion goes unnoticed on real machines.

Motors can be saved to and loaded from JSON, and the current ratings are remembered per axis so
the Diagnostics page can compare the controller's limits against the motor.

## 8. Live scope

Watch the motor move while you change things. Three rolling traces share a time axis:

* **Position** - commanded against actual, so overshoot, ringing and lag are obvious.
* **Following error** - with dashed lines at WarnFeLimit (amber) and FatalFeLimit (red).
* **Motor current** - in amps peak, with dashed lines at I2TSet (continuous) and MaxDac (peak).

Above them, a travel bar shows the commanded position as a blue line, the actual position as a
block, the end stops, and a gauge of current as a percentage of MaxDac. It turns red and says
what happened when the axis is killed.

**Motion.** The move generator repeats a move so there is always something to watch. Step mode
commands a true step, which is what makes overshoot visible. Profiled mode uses the configured
acceleration. This is deliberately **simulator only**: the app will not drive real hardware in a
loop. A step amplitude close to FatalFeLimit is reduced automatically, because a true step
creates its whole amplitude as following error in one servo cycle.

**Live servo gains.** Kp, Kvfb, Kvff, Kaff and Ki each have a slider and a box, written to the
controller as you drag. Raise Kp and watch the overshoot grow and the rise time shorten; add
Kvfb to damp it again. "Back to the values I started with" restores what the page read on entry.

**Protection.** FatalFeLimit, WarnFeLimit and MaxDac are editable here so you can make a trip
happen on purpose: tighten the fatal limit until the axis is killed, or cut MaxDac until the
output saturates and the error grows on every acceleration. **Re-enable** clears the fault and
closes the loop again.

**Last completed move** reports overshoot, rise time, settling time, peak following error, peak
current and output saturation, recomputed after every move.

On the simulator the samples come from a ring buffer filled at the servo rate, so the traces
have full resolution (1000 samples per second by default). On real hardware the page follows the
normal polling rate, which is far coarser; use the Tuning page's gather-based step capture when
you need detail there.

## 9. Terminal

Send any on-line command (`Motor[1].Servo.Kp`, `#1j+`, `I130`, `?`, `list plc 1`, ...) with
history; quick buttons for common queries; optional display of the background polling traffic.
`save` / `$$$***` ask for confirmation. With EPICS only, commands go to `<P>:SendCmd` (no reply).

---

## 10. Simulator scenarios (for training / demos)

| Scenario | Symptom |
|---|---|
| `healthy` | all motors tuned reasonably |
| `i2t_trip` | motor 2 trips I2T shortly after moving |
| `encoder_loss` | motor 3 encoder-loss latch, killed when enabled |
| `limits_not_wired` | motor 4 shows both limits active |
| `wrong_feedback_sign` | motor 5 runs away into fatal following error |
| `not_phased` | motor 6 `PhaseFound=0`, refuses to enable |
| `oscillating` | motor 7 huge Kp / no damping |
| `sluggish` | motor 8 tiny gains, large following error |
| `bus_undervoltage` | DC bus off - enables but no torque |
| `watchdog` | `Sys.WDTFault=1` |
| `saturating` | motor 1 `MaxDac` too low |
| `resonance` | motor 1 120 Hz mechanical resonance |

Change scenario at run time from the Terminal with `sim.scenario=<name>`, or start the app with
`PmacDoctor.exe --simulate <name>`. Other start-up shortcuts: `--epics <prefix>`, `--ssh <host>`.

---

## 11. Troubleshooting the app itself

* **EPICS "waiting for PVs"** - wrong prefix, or `EPICS_CA_ADDR_LIST` not set (enter it in
  Settings). Try `caget <P>:PMACTYPE` from the same PC.
* **SSH connect failed** - check IP/credentials; the Power PMAC must allow password login for
  `root`. Only one gpascii session per user is fine; the IOC uses its own.
* **Step capture shows "commanded position never changed"** - the axis refused the move: killed,
  on a limit, not closed loop, or `JogSpeed`/limits prevent motion. Check the Axes page.
* **Gather unavailable** - the app falls back to polling over the link (lower sample rate).

Settings are stored per user in the registry (`HKCU\Software\NSLS-II\PMAC Doctor`).
