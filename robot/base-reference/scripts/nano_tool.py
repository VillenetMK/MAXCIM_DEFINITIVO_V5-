#!/usr/bin/env python3
"""Identificar el Nano y medir FG en banco sin instalar ROS 2."""

import argparse
import math
from pathlib import Path
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ros2" / "src" / "maxcim_base"))
from maxcim_base.protocol import (  # noqa: E402
    LineBuffer, ProtocolError, UINT32_MAX, decode, encode,
    parse_identity, parse_telemetry,
)
from maxcim_base.safety import FeedbackClock  # noqa: E402


class NanoError(RuntimeError):
    """Fallo de comunicación o permiso de movimiento ausente."""


class NanoLink:
    def __init__(self, port):
        self.port = port
        self.session = secrets.randbelow(UINT32_MAX) + 1
        self.command_sequence = 0
        self.buffer = LineBuffer()
        self.identity = None
        self.latest = None
        self.last_sample_at = None
        self.last_ack_at = None
        self.bad_frames = 0
        self.feedback_clock = FeedbackClock()

    def send(self, kind, *values):
        if self.command_sequence >= UINT32_MAX:
            raise NanoError("Se agotó la secuencia de mando; vuelva a conectar.")
        self.command_sequence += 1
        payload = " ".join(str(value) for value in (kind, self.session, self.command_sequence, *values))
        self.port.write(encode(payload))

    def poll(self):
        samples = []
        pending = self.port.in_waiting
        if pending > 4096:
            raise NanoError("Cola serie demasiado grande: la telemetría puede estar retrasada.")
        data = self.port.read(max(1, pending))
        for line in self.buffer.feed(data):
            try:
                payload = decode(line)
                if payload.startswith("I "):
                    identity = parse_identity(payload)
                    if identity.session == self.session:
                        self.identity = identity
                    continue
                sample = parse_telemetry(payload)
            except ProtocolError:
                self.bad_frames += 1
                continue  # Una trama corrupta nunca actualiza la frescura.
            if self.identity is None or sample.session != self.session:
                continue
            if sample.command_ack > self.command_sequence:
                raise NanoError("El Nano confirmó una orden que no pertenece a esta sesión.")
            if self.latest is not None:
                delta_seq = (sample.sequence - self.latest.sequence) & UINT32_MAX
                if delta_seq == 0:
                    continue
                if delta_seq > 0x7FFFFFFF:
                    raise NanoError("Telemetría fuera de orden o Nano reiniciado.")
                for field in ("uptime_ms", "left_ticks", "right_ticks"):
                    if (getattr(sample, field) - getattr(self.latest, field)) & UINT32_MAX > 0x7FFFFFFF:
                        raise NanoError("Reloj/contador retrocedió; posible reinicio del Nano.")
                if sample.command_ack < self.latest.command_ack:
                    raise NanoError("La confirmación de mandos retrocedió.")
            now = time.monotonic()
            try:
                age = self.feedback_clock.accept(sample.sequence, sample.uptime_ms, now)
            except ValueError as exc:
                raise NanoError(str(exc)) from exc
            measured_at = now - age
            if self.latest is None or sample.command_ack > self.latest.command_ack:
                self.last_ack_at = measured_at
            self.latest = sample
            self.last_sample_at = measured_at
            samples.append(sample)
        return samples

    def connect(self):
        # Abrir USB puede reiniciar el Nano. Durante el arranque permanece parado.
        boot_deadline = time.monotonic() + 2.0
        while time.monotonic() < boot_deadline:
            self.port.read(256)
        self.port.reset_input_buffer()
        self.port.write(encode(f"H {self.session}"))
        deadline = time.monotonic() + 3.0
        while self.identity is None and time.monotonic() < deadline:
            self.poll()
        if self.identity is None:
            raise NanoError("No se recibió identidad MAXCIM_BASE v1 con CRC válido.")
        self.send("S")
        target_ack = self.command_sequence
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self.poll()
            if self.latest is not None and self.latest.command_ack >= target_ack:
                return
        raise NanoError("El Nano no confirmó la parada inicial.")

    def ensure_fresh(self):
        now = time.monotonic()
        if self.last_sample_at is None or now - self.last_sample_at > 0.25:
            raise NanoError("Telemetría ausente durante más de 250 ms: parada.")
        if (self.latest.command_ack < self.command_sequence
                and now - self.last_ack_at > 0.25):
            raise NanoError("El Nano dejó de confirmar los mandos: parada.")

    def stop(self):
        self.send("S")


def show_sample(sample):
    print(
        f"muestra={sample.sequence} FG izq={sample.left_ticks} der={sample.right_ticks} "
        f"pps izq={sample.left_pps:.2f} der={sample.right_pps:.2f} "
        f"PWM‰ izq={sample.left_pwm_permille} der={sample.right_pwm_permille} "
        f"flags={sample.status} ack={sample.command_ack}", flush=True,
    )


def info(link, count):
    print(f"Nano MAXCIM_BASE v1 identificado; flags={link.identity.flags}.")
    print("Flags: 1=cableado pendiente, 2=watchdog, 4=fallo FG, 8=PI pendiente.")
    seen = 0
    next_command = 0.0
    deadline = time.monotonic() + count / 20.0 + 3.0
    while seen < count and time.monotonic() < deadline:
        if time.monotonic() >= next_command:
            link.send("S")
            next_command = time.monotonic() + 0.05
        for sample in link.poll():
            show_sample(sample)
            seen += 1
        link.ensure_fresh()
    if seen < count:
        raise NanoError("No se recibieron todas las muestras solicitadas.")


def bench(link, wheel, duty, seconds):
    link.ensure_fresh()
    if link.latest.status & (1 | 2 | 4):
        raise NanoError(f"Prueba bloqueada por el firmware (flags={link.latest.status}).")
    baseline = link.latest
    permille = round(duty * 1000)
    left, right = (permille, 0) if wheel == "left" else (0, permille)
    print(f"Prueba rueda {wheel}: {permille}/1000 durante {seconds:.2f} s.", flush=True)
    start = time.monotonic()
    deadline = start + seconds
    next_command = start
    while time.monotonic() < deadline:
        link.ensure_fresh()
        if link.latest.status & (1 | 2 | 4):
            raise NanoError(f"El Nano notificó un fallo (flags={link.latest.status}).")
        if time.monotonic() >= next_command:
            link.send("D", left, right)
            next_command = time.monotonic() + 0.05
        for sample in link.poll():
            show_sample(sample)
    end = stop_and_settle(link)
    elapsed_ms = (end.uptime_ms - baseline.uptime_ms) & UINT32_MAX
    if elapsed_ms == 0:
        raise NanoError("Sin intervalo de medición válido.")
    for name, field in (("izquierda", "left_ticks"), ("derecha", "right_ticks")):
        ticks = (getattr(end, field) - getattr(baseline, field)) & UINT32_MAX
        print(
            f"Rueda {name}: FG inicial={getattr(baseline, field)}, final={getattr(end, field)}, "
            f"delta FG={ticks}; media de toda la prueba={ticks * 1000 / elapsed_ms:.2f} pulsos/s."
        )
    print(f"Intervalo medido completo, incluido frenado: {elapsed_ms / 1000:.3f} s.")
    print("La media incluye arranque y frenado: no es la velocidad estable para ajustar PI.")
    print("Para el ajuste estable use las muestras individuales de pps y PWM anteriores.")
    print("Los pulsos/s no determinan por sí solos pulsos/vuelta: cuente vueltas físicas conocidas.")


def stop_and_settle(link):
    """Confirma S y recoge los últimos pulsos hasta 150 ms sin cambios."""
    link.stop()
    stop_ack = link.command_sequence
    deadline = time.monotonic() + 1.0
    next_command = time.monotonic() + 0.05
    last_ticks = None
    unchanged_since_ms = None
    while time.monotonic() < deadline:
        if time.monotonic() >= next_command:
            link.send("S")
            next_command = time.monotonic() + 0.05
        for sample in link.poll():
            if sample.status & (1 | 4):
                raise NanoError(f"Fallo al detener la prueba (flags={sample.status}).")
            if sample.command_ack < stop_ack:
                continue
            ticks = (sample.left_ticks, sample.right_ticks)
            if ticks != last_ticks:
                last_ticks = ticks
                unchanged_since_ms = sample.uptime_ms
            elif (sample.uptime_ms - unchanged_since_ms) & UINT32_MAX >= 150:
                link.ensure_fresh()
                return sample
        link.ensure_fresh()
    raise NanoError("No se confirmó parada y 150 ms sin pulsos dentro de un segundo.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Puerto concreto, preferible /dev/serial/by-id/…")
    commands = parser.add_subparsers(dest="command", required=True)
    info_parser = commands.add_parser("info", help="Identificar, ordenar parada y leer pulsos reales.")
    info_parser.add_argument("--samples", type=int, default=20, help="Muestras a mostrar, 1..200.")
    bench_parser = commands.add_parser("bench", help="Prueba breve de una rueda levantada del suelo.")
    bench_parser.add_argument("--wheel", choices=("left", "right"), required=True)
    bench_parser.add_argument("--duty", type=float, default=0.08, help="Fracción de potencia: >0 y <=0.15.")
    bench_parser.add_argument("--seconds", type=float, default=1.0, help="Duración: >0 y <=2 segundos.")
    bench_parser.add_argument("--wiring-confirmed", action="store_true", help="Confirma revisión física de cableado y rueda elevada.")
    args = parser.parse_args()
    if args.command == "info" and not 1 <= args.samples <= 200:
        parser.error("--samples debe estar entre 1 y 200.")
    if args.command == "bench":
        if not args.wiring_confirmed:
            parser.error("La prueba requiere --wiring-confirmed y el permiso independiente del firmware.")
        if not math.isfinite(args.duty) or not 0 < args.duty <= 0.15 or round(args.duty * 1000) == 0:
            parser.error("--duty debe representar al menos 1/1000 y no superar 0.15.")
        if not math.isfinite(args.seconds) or not 0 < args.seconds <= 2:
            parser.error("--seconds debe ser positivo y no superar 2 segundos.")
    try:
        import serial
    except ImportError:
        print("Falta pyserial. Instale: sudo apt install python3-serial", file=sys.stderr)
        return 2
    port = None
    link = None
    try:
        port = serial.Serial(args.port, 115200, timeout=0.02, write_timeout=0.2, exclusive=True)
        link = NanoLink(port)
        link.connect()
        if args.command == "info":
            info(link, args.samples)
        else:
            bench(link, args.wheel, args.duty, args.seconds)
        return 0
    except (NanoError, OSError, serial.SerialException, KeyboardInterrupt) as exc:
        print(f"Interrumpido: {exc}", file=sys.stderr)
        return 1
    finally:
        if link is not None:
            try:
                link.stop()
            except (OSError, serial.SerialException, NanoError):
                pass  # Si USB se perdió, actúa además el watchdog local de 350 ms.
        if port is not None:
            port.close()


if __name__ == "__main__":
    sys.exit(main())
