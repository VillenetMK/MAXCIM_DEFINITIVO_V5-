"""Estado de habilitación y frescura, independiente de ROS y del puerto serie."""

from math import isfinite


class DriveGate:
    def __init__(self, feedback_timeout=0.25, command_timeout=0.30):
        if not all(isfinite(v) and 0 < v <= 0.30
                   for v in (feedback_timeout, command_timeout)):
            raise ValueError('Los plazos deben estar entre 0 y 0.30 segundos.')
        self.feedback_timeout = feedback_timeout
        self.command_timeout = command_timeout
        self.enabled = False
        self.feedback_at = None
        self.command_at = None
        self.enabled_at = None
        self.flags = 1
        self.goal = (0.0, 0.0)
        self.reason = 'Esperando Nano y calibración'

    def stop(self, reason):
        self.enabled = False
        self.command_at = None
        self.enabled_at = None
        self.goal = (0.0, 0.0)
        self.reason = reason

    def feedback(self, now, flags):
        self.feedback_at = now
        self.flags = flags
        if flags:
            self.stop('Nano detenido: flags=' + str(flags))

    def enable(self, now, calibrated):
        if not calibrated:
            self.stop('Faltan calibración o sentido de avance confirmado')
            return False
        if self.feedback_at is None or not 0 <= now - self.feedback_at < self.feedback_timeout:
            self.stop('Telemetría ausente o caducada')
            return False
        if self.flags:
            self.stop('Nano no preparado: flags=' + str(self.flags))
            return False
        # Un mando recibido antes de habilitar nunca se ejecuta después.
        self.goal = (0.0, 0.0)
        self.command_at = None
        self.enabled_at = now
        self.enabled = True
        self.reason = 'Habilitado; esperando cmd_vel nuevo'
        return True

    def command(self, now, goal):
        if not all(isfinite(v) and v >= 0 for v in goal):
            self.stop('Consigna inválida')
            return False
        if not self.enabled:
            return False
        self.goal = tuple(goal)
        self.command_at = now
        self.reason = 'Control activo'
        return True

    def output(self, now):
        if not self.enabled:
            return None
        if self.feedback_at is None or not 0 <= now - self.feedback_at < self.feedback_timeout:
            self.stop('Se perdió la telemetría')
            return None
        if self.command_at is None:
            return (0.0, 0.0)
        if not 0 <= now - self.command_at < self.command_timeout:
            self.stop('cmd_vel caducado; requiere habilitar otra vez')
            return None
        return self.goal


class FeedbackClock:
    """Descarta repetidos, reinicios y colas viejas; reconoce wrap de millis()."""
    def __init__(self, max_gap=0.25):
        self.max_gap = max_gap
        self.seq = None
        self.uptime = None
        self.elapsed = 0.0
        self.anchor = None

    def accept(self, seq, uptime, now):
        if self.seq is None:
            self.seq, self.uptime, self.anchor = seq, uptime, now
            return 0.0
        ds = (seq - self.seq) & 0xffffffff
        dt = ((uptime - self.uptime) & 0xffffffff) / 1000.0
        if not 0 < ds < 0x80000000 or not 0 < dt <= self.max_gap:
            raise ValueError('Telemetría repetida, reinicio o intervalo perdido')
        self.elapsed += dt
        predicted = self.anchor + self.elapsed
        age = now - predicted
        if age > self.max_gap or age < -0.1:
            raise ValueError('Telemetría retrasada o reloj inconsistente')
        self.seq, self.uptime = seq, uptime
        return max(0.0, age)
