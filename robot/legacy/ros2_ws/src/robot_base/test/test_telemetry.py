"""Regression tests for fragmented V3 telemetry; no hardware access."""
from robot_base.telemetry import TelemetryBuffer

FRAME = b"SP_I:0.10,Kal_I:0.100,SP_D:0.20,Kal_D:0.200,Ticks_I:12,Ticks_D:24\r\n"


def test_frame_survives_every_serial_split():
    for split in range(1, len(FRAME)):
        parser = TelemetryBuffer()
        assert parser.feed(FRAME[:split]) is None
        assert parser.feed(FRAME[split:]) == (0.1, 0.2)


def test_partial_next_frame_does_not_discard_current_measurement():
    parser = TelemetryBuffer()
    next_frame = FRAME.replace(b"0.100", b"-0.300")
    assert parser.feed(FRAME + next_frame[:20]) == (0.1, 0.2)
    assert parser.feed(next_frame[20:]) == (-0.3, 0.2)


def test_corrupt_or_infinite_values_are_ignored():
    parser = TelemetryBuffer()
    assert parser.feed(b"noise\nSP_I:0,Kal_I:nan,Kal_D:0\n") is None
    assert parser.feed(b"SP_I:0,Kal_I:1\n") is None
    assert parser.feed(FRAME) == (0.1, 0.2)


def test_oversized_line_recovers_at_next_boundary():
    parser = TelemetryBuffer(max_line=128)
    assert parser.feed(b"x" * 4096 + b"\n" + FRAME) == (0.1, 0.2)
