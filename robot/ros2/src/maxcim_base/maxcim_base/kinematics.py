"""Cinemática diferencial de dos ruedas que solo avanzan."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Geometry:
    left_radius_m: float
    right_radius_m: float
    wheel_separation_m: float
    left_ticks_per_revolution: float
    right_ticks_per_revolution: float
    max_wheel_speed_m_s: float

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} debe ser positivo y finito.")


def wheel_targets(linear: float, angular: float, geometry: Geometry) -> tuple:
    """Devuelve PPS izquierda/derecha; rechaza maniobras con marcha atrás.

    angular positivo significa giro hacia la izquierda (convención ROS).
    Al superar la velocidad máxima se reducen ambas ruedas por igual.
    """
    if not math.isfinite(linear) or not math.isfinite(angular):
        raise ValueError("Las velocidades deben ser finitas.")
    half_rotation = angular * geometry.wheel_separation_m / 2.0
    left = linear - half_rotation
    right = linear + half_rotation
    if not math.isfinite(left) or not math.isfinite(right):
        raise ValueError("La consigna excede el rango numérico.")
    tolerance = 1e-12
    if left < -tolerance or right < -tolerance:
        raise ValueError("La maniobra requiere invertir una rueda; solo se admite avance.")
    left, right = max(0.0, left), max(0.0, right)
    peak = max(left, right)
    if peak > geometry.max_wheel_speed_m_s:
        scale = geometry.max_wheel_speed_m_s / peak
        left, right = left * scale, right * scale
    left_pps = left / (2.0 * math.pi * geometry.left_radius_m) * geometry.left_ticks_per_revolution
    right_pps = right / (2.0 * math.pi * geometry.right_radius_m) * geometry.right_ticks_per_revolution
    if not math.isfinite(left_pps) or not math.isfinite(right_pps):
        raise ValueError("La calibración produce una frecuencia fuera de rango.")
    return left_pps, right_pps
