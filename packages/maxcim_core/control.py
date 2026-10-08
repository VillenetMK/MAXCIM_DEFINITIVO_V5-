"""Autoridad única de comandos, parada enclavada y vencimiento monotónico."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Protocol


class Rejected(ValueError):
    pass


class Actuators(Protocol):
    def drive(self, linear: float, angular: float) -> None: ...
    def arm(self, action: str) -> None: ...
    def stop(self) -> None: ...
    def arm_busy(self) -> bool: ...
    def ready(self) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class Limits:
    max_linear: float = 0.20
    max_angular: float = 0.40
    drive_timeout: float = 0.35
    lease_timeout: float = 2.0
    sensor_timeout: float = 0.5
    min_clearance: float = 0.45
    require_scan: bool = True

    def __post_init__(self):
        for value in self.__dict__.values():
            if type(value) is bool:
                continue
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Límites inválidos")


class Controller:
    def __init__(self, actuators: Actuators, limits: Limits | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.actuators = actuators
        self.limits = limits or Limits()
        self.clock = clock
        self.owner: str | None = None
        self.lease_until = 0.0
        self.drive_until = 0.0
        self.sequence = -1
        self.linear = self.angular = 0.0
        self.estop = False
        self.reason = "Sin operador"
        self.scan_at = float("-inf")
        self.clearance = 0.0
        self.voice_enabled = False

    def scan(self, clearance: float):
        if type(clearance) not in (int, float) or not math.isfinite(clearance) or clearance < 0:
            return
        self.clearance, self.scan_at = clearance, self.clock()

    def _stop(self, reason: str):
        # Limpia el estado incluso si el transporte ha fallado. El gateway físico
        # y el controlador de la base tienen además sus propios watchdogs.
        self.linear = self.angular = 0.0
        self.drive_until = 0.0
        self.reason = reason
        self.actuators.stop()

    def acquire(self, owner: str):
        self.tick()
        if self.estop:
            raise Rejected("Parada enclavada; requiere rearme de un ingeniero")
        if not owner:
            raise Rejected("Operador inválido")
        if self.owner not in (None, owner):
            raise Rejected("Otro operador tiene el control")
        if self.owner is None:
            self._stop("Control adquirido")
            self.sequence = -1
        self.owner, self.lease_until = owner, self.clock() + self.limits.lease_timeout

    def heartbeat(self, owner: str):
        self._require_owner(owner)
        self.lease_until = self.clock() + self.limits.lease_timeout

    def _require_owner(self, owner: str):
        self.tick()
        if not owner or self.estop or self.owner != owner:
            raise Rejected("No tienes el control del robot")

    def drive(self, owner: str, seq: int, linear: float, angular: float):
        self._require_owner(owner)
        if type(seq) is not int or seq <= self.sequence:
            raise Rejected("Orden duplicada o fuera de secuencia")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (linear, angular)):
            raise Rejected("Velocidad inválida")
        if abs(linear) > self.limits.max_linear or abs(angular) > self.limits.max_angular:
            raise Rejected("Velocidad fuera de los límites configurados")
        ready, reason = self.actuators.ready()
        if not ready:
            raise Rejected(reason)
        if self.actuators.arm_busy():
            raise Rejected("Espera a que termine la secuencia de brazos")
        if (linear or angular) and self.limits.require_scan:
            if self.clock() - self.scan_at > self.limits.sensor_timeout:
                raise Rejected("LiDAR sin datos recientes")
            if self.clearance < self.limits.min_clearance:
                raise Rejected("Obstáculo dentro del margen de parada")
        self.actuators.drive(float(linear), float(angular))
        self.sequence, self.linear, self.angular = seq, float(linear), float(angular)
        self.drive_until = self.clock() + self.limits.drive_timeout
        self.lease_until = self.clock() + self.limits.lease_timeout
        self.reason = "Movimiento autorizado" if linear or angular else "Detenido"

    def arm(self, owner: str, action: str):
        self._require_owner(owner)
        if self.linear or self.angular:
            raise Rejected("Detén la base antes de mover los brazos")
        if self.actuators.arm_busy():
            raise Rejected("Brazos ocupados")
        ready, reason = self.actuators.ready()
        if not ready:
            raise Rejected(reason)
        if not isinstance(action, str) or not action or len(action) > 80:
            raise Rejected("Acción inválida")
        self.actuators.arm(action)
        self.lease_until = self.clock() + self.limits.lease_timeout

    def release(self, owner: str):
        if self.owner == owner:
            self.owner = None
            self.voice_enabled = False
            self._stop("Control liberado")

    def stop(self):
        self.owner = None  # Las órdenes que lleguen después ya no tienen concesión.
        self.voice_enabled = False
        self._stop("Parada solicitada")

    def brake(self, owner: str, seq: int):
        self._require_owner(owner)
        if type(seq) is not int or seq <= self.sequence:
            raise Rejected("Orden duplicada o fuera de secuencia")
        self.sequence = seq
        self._stop("Detenido por el operador")

    def emergency(self):
        self.estop, self.owner, self.voice_enabled = True, None, False
        self._stop("Parada de emergencia enclavada")

    def reset(self):
        self._stop("Rearme; adquiere el control para continuar")
        self.estop, self.owner, self.voice_enabled = False, None, False

    def tick(self):
        now = self.clock()
        if self.owner and now >= self.lease_until:
            self.owner, self.voice_enabled = None, False
            self._stop("Operador desconectado")
        elif (self.linear or self.angular) and (
            now >= self.drive_until or
            (self.limits.require_scan and (now - self.scan_at > self.limits.sensor_timeout or
                                         self.clearance < self.limits.min_clearance))
        ):
            self._stop("Orden vencida o percepción no disponible")

    def state(self):
        return {"owner": self.owner, "estop": self.estop, "reason": self.reason,
                "linear": self.linear, "angular": self.angular,
                "voice_enabled": self.voice_enabled,
                "sequence": self.sequence,
                "arm_busy": self.actuators.arm_busy(),
                "scan_recent": self.clock() - self.scan_at <= self.limits.sensor_timeout,
                "clearance": self.clearance}
