"""Current / scaling helpers for Power Brick LV channels and Power PMAC motors.

All formulas come from the Power Brick LV ARM User Manual (MN-000143 rev F),
"Step 6: Motor Setup" -> "I2T Protection", "PWM Scale Factor" and "Maximum
Command Output".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

# Channel rating (continuous A / peak A)  ->  Max ADC full-scale reading [A peak]
# (Power Brick LV manual, I2T Protection section)
MAX_ADC_BY_RATING: Dict[str, float] = {
    "0.25 A / 0.75 A": 1.6925,
    "1 A / 3 A": 6.770,
    "5 A / 15 A": 33.85,
}
DEFAULT_MAX_ADC = 33.85  # 5/15 A channel (most common Power Brick LV)

# Power Brick LV channel ratings (continuous, peak) in A for the display / rules
CHANNEL_RATINGS = {
    "0.25 A / 0.75 A": (0.25, 0.75),
    "1 A / 3 A": (1.0, 3.0),
    "5 A / 15 A": (5.0, 15.0),
}

COSD30 = math.cos(math.radians(30.0))  # 0.866 - brushless 3-phase factor


@dataclass
class CurrentSettings:
    """Result of computing MaxDac / I2TSet / I2tTrip from motor + channel data."""

    max_dac: float
    i2t_set: float
    i2t_trip: float
    notes: str = ""


def current_to_dac(amps: float, max_adc: float, brushless: bool, rms: bool) -> float:
    """Convert a current spec to 16-bit command units (32768 == Max ADC).

    * ``rms``: the datasheet gives RMS values -> multiply by sqrt(2)
    * ``brushless``: 3-phase brushless -> multiply by cos(30 deg) (manual formula)
    """
    v = amps * 32768.0 / max_adc
    if rms:
        v *= math.sqrt(2.0)
    if brushless:
        v *= COSD30
    return v


def dac_to_amps(dac: float, max_adc: float, brushless: bool = False, rms: bool = False) -> float:
    a = dac * max_adc / 32768.0
    if brushless:
        a /= COSD30
    if rms:
        a /= math.sqrt(2.0)
    return a


def compute_i2t(peak_a: float, cont_a: float, time_at_peak_s: float, max_adc: float,
                motor_type: str = "brushless", rms: bool = True) -> CurrentSettings:
    """Compute Motor[x].MaxDac, I2TSet and I2tTrip per the Power Brick LV manual.

    motor_type: brushless | stepper | brushed | microstep
    """
    brushless = motor_type == "brushless"
    max_dac = current_to_dac(peak_a, max_adc, brushless, rms)
    i2t_set = current_to_dac(cont_a, max_adc, brushless, rms)
    i2t_trip = (max_dac ** 2 - i2t_set ** 2) * time_at_peak_s
    notes = ""
    if max_dac > 32767:
        notes = ("MaxDac exceeds 32767 (16-bit full scale): the motor peak current is above the "
                 "channel Max ADC. Clamp to the drive rating.")
    return CurrentSettings(max_dac=max_dac, i2t_set=i2t_set, i2t_trip=i2t_trip, notes=notes)


def pwm_sf(motor_rated_v: Optional[float], bus_v: Optional[float]) -> float:
    """Motor[x].PwmSf per the manual: 0.95*16384, scaled down if bus voltage > motor voltage."""
    base = 0.95 * 16384.0
    if motor_rated_v and bus_v and bus_v > motor_rated_v:
        return base * motor_rated_v / bus_v
    return base


def microstep_counts_per_rev(step_angle_deg: float) -> float:
    """Direct micro-stepping: Counts/Rev = 360 * 512 / step angle."""
    return 360.0 * 512.0 / step_angle_deg


def phase_pos_sf_quadrature(pole_pairs: float, counts_per_rev: float) -> float:
    """Motor[x].PhasePosSf for a rotary motor with quadrature encoder."""
    return 2048.0 * pole_pairs / (256.0 * counts_per_rev)


def phase_pos_sf_linear_quadrature(res_mm: float, ecl_mm: float) -> float:
    return 2048.0 * res_mm / (256.0 * ecl_mm)


def phase_finding_time(total_ms: float, servo_period_ms: float, rt_int_period: int) -> float:
    """Motor[x].PhaseFindingTime (in RTI periods) for the automatic stepper phasing."""
    return total_ms * 0.5 / (servo_period_ms * (rt_int_period + 1))


def turbo_natural_kp(ixx30: float, ixx08: float = 96.0) -> float:
    """Approximate Turbo PMAC proportional gain in 16-bit DAC bits per count.

    Turbo: DAC = 2^-19 * Ixx30 * (Ixx08*32) * FE[counts]  =>  Ixx30*Ixx08/16384.
    (Estimate - see Turbo SRM Ixx30 description.)
    """
    return ixx30 * ixx08 / 16384.0


def turbo_ixx30_from_natural(kp_nat: float, ixx08: float = 96.0) -> float:
    return kp_nat * 16384.0 / ixx08


def turbo_natural_kvfb(ixx30: float, ixx31: float, ixx09: float = 96.0) -> float:
    """Approximate Turbo derivative gain in DAC bits per (count/servo cycle)."""
    return ixx30 * ixx31 * ixx09 / (128.0 * 16384.0)


def turbo_ixx31_from_natural(kvfb_nat: float, ixx30: float, ixx09: float = 96.0) -> float:
    if ixx30 == 0:
        return 0.0
    return kvfb_nat * 128.0 * 16384.0 / (ixx30 * ixx09)
