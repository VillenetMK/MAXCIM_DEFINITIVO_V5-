"""Framing of complete V3 motor telemetry without opening serial ports."""
import math


class TelemetryBuffer:
    """Keep partial lines between reads and return the latest valid sample."""

    def __init__(self, max_line=1024):
        self.pending = bytearray()
        self.max_line = max_line
        self.discarding = False

    def feed(self, chunk):
        latest = None
        for value in chunk:
            if value != 10:
                if not self.discarding:
                    self.pending.append(value)
                    if len(self.pending) > self.max_line:
                        self.pending.clear()
                        self.discarding = True
                continue
            if self.discarding:
                self.discarding = False
                self.pending.clear()
                continue
            raw = bytes(self.pending)
            self.pending.clear()
            try:
                line = raw.decode("ascii").strip()
                if not line.startswith("SP_I:"):
                    continue
                values = dict(part.split(":", 1) for part in line.split(","))
                left = float(values["Kal_I"])
                right = float(values["Kal_D"])
                if math.isfinite(left) and math.isfinite(right):
                    latest = (left, right)
            except (UnicodeDecodeError, ValueError, KeyError):
                continue
        return latest
