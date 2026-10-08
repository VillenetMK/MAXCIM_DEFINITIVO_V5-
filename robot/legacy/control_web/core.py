"""Dead-man control, independent of ROS and HTTP; all times are monotonic."""
import math
import secrets
import threading
import time


class ControlError(ValueError):
    pass


class Controller:
    MAX_LINEAR = 0.15
    MAX_ANGULAR = 0.40
    COMMAND_TTL = 0.30
    CHALLENGE_TTL = 0.35

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.owner = None
        self.sequence = -1
        self.challenges = {}
        self.target = (0.0, 0.0)
        self.deadline = 0.0
        self.odom_at = -math.inf
        self.scan_at = -math.inf
        self.other_controller = False
        self.stop_pulses = 0
        self.measured = (0.0, 0.0)

    def reason(self):
        now = self.clock()
        if self.other_controller:
            return 'Otro controlador está enviando órdenes'
        if now - self.odom_at > 0.5:
            return 'Esperando odometría de las ruedas'
        if now - self.scan_at > 1.0:
            return 'Esperando lecturas del LiDAR'
        return ''

    def acquire(self, owner):
        with self.lock:
            if self.owner is not None:
                raise ControlError('El control está ocupado en otra ventana')
            self.owner = owner
            self.sequence = -1
            self.challenges.clear()
            self.target = (0.0, 0.0)

    def release(self, owner):
        with self.lock:
            if owner == self.owner:
                self.stop()
                self.owner = None

    def stop(self):
        with self.lock:
            self.target = (0.0, 0.0)
            self.deadline = 0.0
            self.challenges.clear()
            self.stop_pulses = 3

    def challenge(self, owner):
        with self.lock:
            if owner != self.owner:
                raise ControlError('Sesión inactiva')
            now = self.clock()
            self.challenges = {k: v for k, v in self.challenges.items() if v > now}
            token = secrets.token_urlsafe(12)
            self.challenges[token] = now + self.CHALLENGE_TTL
            return token

    def command(self, owner, seq, challenge, linear, angular):
        with self.lock:
            if owner != self.owner:
                raise ControlError('Sesión inactiva')
            try:
                if type(seq) is not int or seq <= self.sequence:
                    raise ControlError('Orden duplicada o fuera de secuencia')
                self.sequence = seq
                if not isinstance(challenge, str) or self.challenges.pop(challenge, 0) <= self.clock():
                    raise ControlError('Orden vencida; comprueba la conexión')
                if any(type(v) not in (float, int) or not math.isfinite(v) for v in (linear, angular)):
                    raise ControlError('Velocidad inválida')
                if abs(linear) > self.MAX_LINEAR or abs(angular) > self.MAX_ANGULAR:
                    raise ControlError('Velocidad fuera del límite')
                reason = self.reason()
                if reason:
                    raise ControlError(reason)
                self.target = (float(linear), float(angular))
                self.deadline = self.clock() + self.COMMAND_TTL
            except ControlError:
                self.stop()
                raise

    def tick(self):
        """None = don't compete with other controllers; zero = explicit stop."""
        with self.lock:
            if self.target != (0.0, 0.0) and (self.clock() >= self.deadline or self.reason()):
                self.stop()
            if self.stop_pulses:
                self.stop_pulses -= 1
                return (0.0, 0.0)
            return self.target if self.target != (0.0, 0.0) else None

    def status(self):
        with self.lock:
            now = self.clock()
            return {'ready': not bool(self.reason()), 'reason': self.reason(),
                    'odom_ok': now - self.odom_at <= 0.5,
                    'scan_ok': now - self.scan_at <= 1.0,
                    'linear': self.measured[0], 'angular': self.measured[1],
                    'limits': {'linear': self.MAX_LINEAR, 'angular': self.MAX_ANGULAR}}
