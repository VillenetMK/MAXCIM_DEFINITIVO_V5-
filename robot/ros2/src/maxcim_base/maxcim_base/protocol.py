"""Tramas MAXCIM_BASE v1; módulo independiente de ROS y del puerto serie."""

from dataclasses import dataclass
import math
import re


MAX_LINE_BYTES = 180  # Incluye el salto de línea de la trama completa.
UINT32_MAX = 0xFFFFFFFF
KNOWN_STATUS_BITS = 0x0F


class ProtocolError(ValueError):
    """Trama incompleta, corrupta o incompatible con el protocolo."""


@dataclass(frozen=True)
class Identity:
    session: int
    flags: int


@dataclass(frozen=True)
class Telemetry:
    session: int
    sequence: int
    uptime_ms: int
    left_ticks: int
    right_ticks: int
    left_pps: float
    right_pps: float
    left_pwm_permille: int
    right_pwm_permille: int
    status: int
    command_ack: int


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def _payload_bytes(payload: str) -> bytes:
    if not isinstance(payload, str):
        raise ProtocolError("El payload debe ser texto ASCII.")
    try:
        data = payload.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProtocolError("El payload contiene caracteres no ASCII.") from exc
    if (not data or len(data) + 6 > MAX_LINE_BYTES
            or any(byte < 32 or byte > 126 or byte == 42 for byte in data)):
        raise ProtocolError("Payload vacío, demasiado largo o con separadores inválidos.")
    return data


def encode(payload: str) -> bytes:
    """Añade CRC16-CCITT-FALSE y LF; nunca acepta comandos multilínea."""
    data = _payload_bytes(payload)
    return data + f"*{_crc16(data):04X}\n".encode("ascii")


def decode(line: bytes) -> str:
    """Verifica una trama completa, con o sin su LF final."""
    if not isinstance(line, bytes):
        raise ProtocolError("La trama debe ser bytes.")
    body = line[:-1] if line.endswith(b"\n") else line
    if len(body) + 1 > MAX_LINE_BYTES or len(body) < 6:
        raise ProtocolError("Longitud de trama inválida.")
    if body[-5:-4] != b"*" or not re.fullmatch(rb"[0-9A-Fa-f]{4}", body[-4:]):
        raise ProtocolError("Falta el CRC hexadecimal de cuatro dígitos.")
    try:
        payload = body[:-5].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("La trama contiene caracteres no ASCII.") from exc
    data = _payload_bytes(payload)
    if int(body[-4:], 16) != _crc16(data):
        raise ProtocolError("CRC incorrecto.")
    return payload


def _tokens(payload: str, count: int, kind: str):
    _payload_bytes(payload)
    tokens = payload.split(" ")
    if len(tokens) != count or any(not token for token in tokens) or tokens[0] != kind:
        raise ProtocolError(f"Formato de mensaje {kind} inválido.")
    return tokens


def _uint(token: str, maximum: int = UINT32_MAX, minimum: int = 0) -> int:
    if not re.fullmatch(r"[0-9]+", token):
        raise ProtocolError("Se esperaba un entero decimal sin signo.")
    result = int(token)
    if not minimum <= result <= maximum:
        raise ProtocolError("Entero fuera de rango.")
    return result


def _pps(token: str) -> float:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", token):
        raise ProtocolError("PPS debe ser un número finito no negativo.")
    result = float(token)
    if not math.isfinite(result) or result < 0:
        raise ProtocolError("PPS fuera de rango.")
    return result


def parse_identity(payload: str) -> Identity:
    tokens = _tokens(payload, 5, "I")
    if tokens[2:4] != ["MAXCIM_BASE", "1"]:
        raise ProtocolError("Dispositivo o versión de protocolo incompatible.")
    return Identity(_uint(tokens[1], minimum=1), _uint(tokens[4], KNOWN_STATUS_BITS))


def parse_telemetry(payload: str) -> Telemetry:
    tokens = _tokens(payload, 12, "T")
    return Telemetry(
        session=_uint(tokens[1], minimum=1),
        sequence=_uint(tokens[2]),
        uptime_ms=_uint(tokens[3]),
        left_ticks=_uint(tokens[4]),
        right_ticks=_uint(tokens[5]),
        left_pps=_pps(tokens[6]),
        right_pps=_pps(tokens[7]),
        left_pwm_permille=_uint(tokens[8], 1000),
        right_pwm_permille=_uint(tokens[9], 1000),
        status=_uint(tokens[10], KNOWN_STATUS_BITS),
        command_ack=_uint(tokens[11]),
    )


class LineBuffer:
    """Reconstruye líneas fragmentadas y descarta una línea larga completa."""

    def __init__(self):
        self._pending = bytearray()
        self._discarding = False

    def feed(self, data: bytes) -> list:
        lines = []
        for byte in data:
            if self._discarding:
                if byte == 10:
                    self._discarding = False
                continue
            if byte == 10:
                lines.append(bytes(self._pending) + b"\n")
                self._pending.clear()
            elif len(self._pending) >= MAX_LINE_BYTES - 1:
                self._pending.clear()
                self._discarding = True
            else:
                self._pending.append(byte)
        return lines
