"""Pure wheel-count odometry; no ROS, motor command or speed integration.

The robot supports physically confirmed forward rotation on each fixed wheel.
FG is unsigned: external reverse motion cannot be reconstructed from one FG.
"""

from dataclasses import dataclass
import math
from typing import Optional, Set


UINT32_MASK = (1 << 32) - 1
HALF_UINT32 = 1 << 31


@dataclass(frozen=True)
class Calibration:
    left_wheel_radius_m: float
    right_wheel_radius_m: float
    wheel_separation_m: float
    left_pulses_per_revolution: float
    right_pulses_per_revolution: float
    max_wheel_speed_mps: float = 1.0
    max_sample_gap_s: float = 0.25
    max_feedback_age_s: float = 0.25

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.max_sample_gap_s >= HALF_UINT32 / 1000.0:
            raise ValueError("max_sample_gap_s is ambiguous across MCU uptime wrap")
        distances_per_tick = (
            2.0 * math.pi * self.left_wheel_radius_m / self.left_pulses_per_revolution,
            2.0 * math.pi * self.right_wheel_radius_m / self.right_pulses_per_revolution,
        )
        if any(not math.isfinite(value) or value <= 0 for value in distances_per_tick):
            raise ValueError("Wheel calibration must produce finite positive distance per pulse")
        interval_bound = (
            self.max_wheel_speed_mps * self.max_sample_gap_s + max(distances_per_tick)
        )
        if (not math.isfinite(interval_bound)
                or not math.isfinite(2.0 * interval_bound / self.wheel_separation_m)):
            raise ValueError("Wheel calibration exceeds finite integration limits")


@dataclass(frozen=True)
class Sample:
    session: int
    sequence: int
    uptime_ms: int
    left_ticks: int
    right_ticks: int
    stamp_ns: int
    direction_valid: bool = True
    status: int = 0


@dataclass(frozen=True)
class Estimate:
    stamp_ns: int
    x: float
    y: float
    yaw: float
    linear_velocity: float
    angular_velocity: float
    dt: float
    left_distance: float
    right_distance: float


class WheelOdometry:
    """Integrate consecutive, timely, physically plausible MCU count samples.

    A sample gap or impossible count jump becomes a new baseline: the unknown
    interval is not integrated. An invalid direction clears the baseline until
    direction is valid again. Retired session IDs are rejected for this process.
    No method extrapolates pose or emits periodic odometry on its own.
    """

    def __init__(self, calibration: Calibration):
        self.calibration = calibration
        self.x = self.y = self.yaw = 0.0
        self.last_reason = "waiting_for_feedback"
        self._session: Optional[int] = None
        self._retired_sessions: Set[int] = set()
        self._last_seen: Optional[Sample] = None
        self._baseline: Optional[Sample] = None
        self._left_m_per_tick = (
            2.0 * math.pi * calibration.left_wheel_radius_m
            / calibration.left_pulses_per_revolution
        )
        self._right_m_per_tick = (
            2.0 * math.pi * calibration.right_wheel_radius_m
            / calibration.right_pulses_per_revolution
        )

    def _drop(self, reason: str, clear_baseline: bool = False):
        self.last_reason = reason
        if clear_baseline:
            self._baseline = None
        return None

    def invalidate(self, reason: str):
        """Discard interval continuity without changing pose or session ordering."""
        self._drop(reason, clear_baseline=True)

    def accept(self, sample: Sample, now_ns: int) -> Optional[Estimate]:
        """Return one estimate, or None when a sample cannot define an interval."""
        for name in ("session", "sequence", "uptime_ms", "left_ticks", "right_ticks"):
            value = getattr(sample, name)
            if type(value) is not int or not 0 <= value <= UINT32_MASK:
                return self._drop("malformed_counter", clear_baseline=True)
        if sample.session == 0:
            return self._drop("invalid_session", clear_baseline=True)
        if type(sample.stamp_ns) is not int or sample.stamp_ns <= 0:
            return self._drop("invalid_stamp", clear_baseline=True)
        if type(sample.direction_valid) is not bool:
            return self._drop("invalid_direction_flag", clear_baseline=True)
        if type(sample.status) is not int or not 0 <= sample.status <= 65535:
            return self._drop("invalid_status", clear_baseline=True)
        if sample.session in self._retired_sessions:
            return self._drop("retired_session")

        age_s = (now_ns - sample.stamp_ns) / 1e9
        if age_s < 0:
            return self._drop("future_stamp", clear_baseline=True)
        if age_s > self.calibration.max_feedback_age_s:
            return self._drop("stale_feedback", clear_baseline=True)

        previous = self._last_seen
        session_changed = sample.session != self._session
        if previous is not None and sample.stamp_ns <= previous.stamp_ns:
            return self._drop("non_increasing_stamp")
        if session_changed:
            if self._session is not None:
                self._retired_sessions.add(self._session)
            self._session = sample.session
            self._baseline = None
        elif previous is not None:
            sequence_delta = (sample.sequence - previous.sequence) & UINT32_MASK
            if sequence_delta == 0 or sequence_delta >= HALF_UINT32:
                return self._drop("old_sequence")

        self._last_seen = sample
        if not sample.direction_valid or sample.status & (1 | 4):
            return self._drop("direction_or_hardware_invalid", clear_baseline=True)

        baseline = self._baseline
        self._baseline = sample
        if baseline is None:
            return self._drop("session_baseline" if session_changed else "baseline")

        sequence_delta = (sample.sequence - baseline.sequence) & UINT32_MASK
        if sequence_delta != 1:
            return self._drop("sequence_gap")
        dt_ms = (sample.uptime_ms - baseline.uptime_ms) & UINT32_MASK
        if dt_ms == 0 or dt_ms >= HALF_UINT32:
            return self._drop("invalid_mcu_interval")
        dt = dt_ms / 1000.0
        host_dt = (sample.stamp_ns - baseline.stamp_ns) / 1e9
        if dt > self.calibration.max_sample_gap_s or host_dt > self.calibration.max_sample_gap_s:
            return self._drop("sample_gap")

        left_ticks = (sample.left_ticks - baseline.left_ticks) & UINT32_MASK
        right_ticks = (sample.right_ticks - baseline.right_ticks) & UINT32_MASK
        left_distance = left_ticks * self._left_m_per_tick
        right_distance = right_ticks * self._right_m_per_tick
        # An edge can fall on either side of the sampling boundary. Allow one
        # pulse of quantization beyond the maximum physical travel per interval.
        maximum_travel = self.calibration.max_wheel_speed_mps * dt
        if (left_ticks >= HALF_UINT32 or right_ticks >= HALF_UINT32
                or left_distance > maximum_travel + self._left_m_per_tick
                or right_distance > maximum_travel + self._right_m_per_tick):
            return self._drop("implausible_counts")

        distance = (left_distance + right_distance) / 2.0
        turn = (right_distance - left_distance) / self.calibration.wheel_separation_m
        half_turn = turn / 2.0
        sinc = math.sin(half_turn) / half_turn if abs(half_turn) > 1e-12 else 1.0
        self.x += distance * sinc * math.cos(self.yaw + half_turn)
        self.y += distance * sinc * math.sin(self.yaw + half_turn)
        self.yaw = math.atan2(math.sin(self.yaw + turn), math.cos(self.yaw + turn))
        self.last_reason = "accepted"
        return Estimate(
            sample.stamp_ns, self.x, self.y, self.yaw,
            distance / dt, turn / dt, dt, left_distance, right_distance,
        )
