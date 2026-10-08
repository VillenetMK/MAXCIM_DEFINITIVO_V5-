"""Simulación explícita. Nunca selecciona puertos ni publica en el robot físico."""
import time


class SimulatedActuators:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.busy_until = 0.0
        self.linear = self.angular = 0.0
        self.last_action = None
        self.actions = {"saludar", "levantar_manos", "dar_mano", "dar_mano_izquierda",
                        "bailar", "home", "brazo_derecho_arriba", "brazo_izquierdo_arriba"}

    def drive(self, linear, angular):
        self.linear, self.angular = linear, angular

    def arm(self, action):
        if action not in self.actions:
            raise ValueError("Acción desconocida")
        self.last_action = action
        self.busy_until = self.clock() + 1.5

    def stop(self):
        self.linear = self.angular = 0.0
        self.busy_until = 0.0

    def arm_busy(self):
        return self.clock() < self.busy_until

    def ready(self):
        return True, "Simulación"
