"""Pruebas sin hardware de framing, límites y recepción fragmentada."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "ros2" / "src" / "maxcim_base"))
from maxcim_base.protocol import (  # noqa: E402
    LineBuffer, MAX_LINE_BYTES, ProtocolError, decode, encode,
    parse_identity, parse_telemetry,
)


VALID_SAMPLE = "T 27 15 1000 100 120 12.5 1e2 80 90 8 3"


class ProtocolTests(unittest.TestCase):
    def test_crc_standard_check_vector(self):
        self.assertEqual(encode("123456789"), b"123456789*29B1\n")

    def test_round_trip_and_optional_lf(self):
        frame = encode(VALID_SAMPLE)
        self.assertEqual(decode(frame), VALID_SAMPLE)
        self.assertEqual(decode(frame[:-1]), VALID_SAMPLE)

    def test_bit_corruption_is_rejected(self):
        frame = encode(VALID_SAMPLE)
        for index in range(len(frame) - 1):
            corrupt = bytearray(frame)
            corrupt[index] ^= 1
            with self.subTest(index=index), self.assertRaises(ProtocolError):
                decode(bytes(corrupt))

    def test_payload_rejects_injection_and_non_ascii(self):
        for payload in ("", "H 1\nD 1 1 150 150", "H 1\r", "H\t1", "á", "H 1*AAAA", "H 1\x7f"):
            with self.subTest(payload=repr(payload)), self.assertRaises(ProtocolError):
                encode(payload)

    def test_exact_wire_size_limit(self):
        frame = encode("x" * (MAX_LINE_BYTES - 6))
        self.assertEqual(len(frame), MAX_LINE_BYTES)
        self.assertEqual(len(decode(frame)), MAX_LINE_BYTES - 6)
        with self.assertRaises(ProtocolError):
            encode("x" * (MAX_LINE_BYTES - 5))

    def test_invalid_crc_syntax(self):
        for frame in (b"H 1*FFFFF\n", b"H 1*ZZZZ\n", b"H 1\n", b"*FFFF\n", b"H 1*ffff\r\n", b"\xff*0000\n"):
            with self.subTest(frame=frame), self.assertRaises(ProtocolError):
                decode(frame)

    def test_identity_device_version_session_flags(self):
        identity = parse_identity("I 4294967295 MAXCIM_BASE 1 15")
        self.assertEqual(identity.session, 4294967295)
        self.assertEqual(identity.flags, 15)
        for payload in ("I 0 MAXCIM_BASE 1 0", "I 1 OTHER 1 0", "I 1 MAXCIM_BASE 2 0", "I 1 MAXCIM_BASE 1 16", "I 1 MAXCIM_BASE 1 0 extra"):
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                parse_identity(payload)

    def test_telemetry_fields_and_limits(self):
        sample = parse_telemetry(VALID_SAMPLE)
        self.assertEqual((sample.session, sample.sequence, sample.uptime_ms), (27, 15, 1000))
        self.assertEqual((sample.left_pps, sample.right_pps), (12.5, 100.0))
        self.assertEqual((sample.left_pwm_permille, sample.right_pwm_permille, sample.status, sample.command_ack), (80, 90, 8, 3))
        boundary = "T 1 4294967295 4294967295 4294967295 4294967295 0 0 0 1000 15 4294967295"
        self.assertEqual(parse_telemetry(boundary).right_ticks, 4294967295)

    def test_uint_sign_overflow_fraction_and_hex_rejected(self):
        tokens = VALID_SAMPLE.split()
        for index in (1, 2, 3, 4, 5, 11):
            for value in ("-1", "+1", "4294967296", "1.0", "0x01"):
                corrupted = tokens[:]
                corrupted[index] = value
                with self.subTest(index=index, value=value), self.assertRaises(ProtocolError):
                    parse_telemetry(" ".join(corrupted))

    def test_nonfinite_negative_pps_rejected(self):
        for index in (6, 7):
            for value in ("nan", "NaN", "inf", "-inf", "1e999", "-0.1", "-0", "1.2.3"):
                tokens = VALID_SAMPLE.split()
                tokens[index] = value
                with self.subTest(index=index, value=value), self.assertRaises(ProtocolError):
                    parse_telemetry(" ".join(tokens))

    def test_unknown_flags_and_out_of_range_pwm_rejected(self):
        for index, value in ((8, "1001"), (9, "-1"), (10, "16"), (10, "255")):
            tokens = VALID_SAMPLE.split()
            tokens[index] = value
            with self.subTest(index=index), self.assertRaises(ProtocolError):
                parse_telemetry(" ".join(tokens))

    def test_token_count_and_whitespace_are_strict(self):
        for value in (VALID_SAMPLE + " 9", VALID_SAMPLE.rsplit(" ", 1)[0], VALID_SAMPLE.replace(" ", "  ", 1), " " + VALID_SAMPLE, VALID_SAMPLE + " ", VALID_SAMPLE.replace(" ", "\t", 1)):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_telemetry(value)

    def test_all_split_positions_reassemble(self):
        frame = encode(VALID_SAMPLE)
        for split in range(len(frame) + 1):
            buf = LineBuffer()
            self.assertEqual(buf.feed(frame[:split]) + buf.feed(frame[split:]), [frame])

    def test_multiple_lines_and_partial_tail(self):
        buf = LineBuffer()
        first, second = encode("H 2"), encode("S 2 1")
        self.assertEqual(buf.feed(first + second + first[:3]), [first, second])
        self.assertEqual(buf.feed(first[3:]), [first])

    def test_overflow_discards_until_newline_without_suffix_recovery(self):
        buf = LineBuffer()
        good = encode("H 2")
        self.assertEqual(buf.feed(b"x" * MAX_LINE_BYTES), [])
        self.assertEqual(buf.feed(good), [])
        self.assertEqual(buf.feed(good), [good])

    def test_extremely_long_line_does_not_grow_memory(self):
        buf = LineBuffer()
        self.assertEqual(buf.feed(b"x" * 100000), [])
        self.assertLessEqual(len(buf._pending), MAX_LINE_BYTES - 1)
        self.assertEqual(buf.feed(b"\n" + encode("H 1")), [encode("H 1")])

    def test_exact_limit_line_is_preserved(self):
        frame = encode("x" * (MAX_LINE_BYTES - 6))
        self.assertEqual(LineBuffer().feed(frame), [frame])


spec = importlib.util.spec_from_file_location("nano_tool_under_test", ROOT / "scripts" / "nano_tool.py")
nano_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nano_tool)


class FakeSerial:
    def __init__(self):
        self.incoming = bytearray()
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, count):
        chunk = bytes(self.incoming[:count])
        del self.incoming[:count]
        return chunk

    def write(self, data):
        self.writes.append(data)


class NanoLinkTests(unittest.TestCase):
    def setUp(self):
        self.port = FakeSerial()
        self.link = nano_tool.NanoLink(self.port)
        self.link.session = 27
        self.link.command_sequence = 3
        self.link.identity = parse_identity("I 27 MAXCIM_BASE 1 8")

    def feed(self, payload):
        self.port.incoming.extend(encode(payload))
        return self.link.poll()

    def test_wrong_session_does_not_refresh_stream(self):
        self.assertEqual(self.feed(VALID_SAMPLE.replace("T 27", "T 28")), [])
        self.assertIsNone(self.link.last_sample_at)

    def test_telemetry_before_identity_does_not_refresh_stream(self):
        self.link.identity = None
        self.assertEqual(self.feed(VALID_SAMPLE), [])
        self.assertIsNone(self.link.last_sample_at)

    def test_delayed_increasing_queue_is_rejected(self):
        with patch.object(nano_tool.time, "monotonic", return_value=100.0):
            self.feed(VALID_SAMPLE)
        with patch.object(nano_tool.time, "monotonic", return_value=100.4):
            with self.assertRaises(nano_tool.NanoError):
                self.feed("T 27 16 1050 101 121 12.5 100 80 90 8 3")

    def test_moderate_queue_delay_is_subtracted_from_freshness(self):
        with patch.object(nano_tool.time, "monotonic", return_value=100.0):
            self.feed(VALID_SAMPLE)
        with patch.object(nano_tool.time, "monotonic", return_value=100.2):
            self.feed("T 27 16 1050 101 121 12.5 100 80 90 8 3")
        self.assertAlmostEqual(self.link.last_sample_at, 100.05)

    def test_oversized_input_queue_is_rejected(self):
        self.port.incoming.extend(b"x" * 4097)
        with self.assertRaises(nano_tool.NanoError):
            self.link.poll()

    def test_duplicate_sample_does_not_refresh_stream(self):
        self.feed(VALID_SAMPLE)
        baseline = self.link.last_sample_at
        self.assertEqual(self.feed(VALID_SAMPLE), [])
        self.assertEqual(self.link.last_sample_at, baseline)

    def test_ack_of_unsent_command_is_rejected(self):
        with self.assertRaises(nano_tool.NanoError):
            self.feed(VALID_SAMPLE[:-1] + "4")

    def test_reboot_and_out_of_order_are_rejected(self):
        self.feed(VALID_SAMPLE)
        with self.assertRaises(nano_tool.NanoError):
            self.feed("T 27 1 50 0 0 0 0 0 0 8 0")

    def test_modular_rollover_is_accepted(self):
        self.feed("T 27 4294967295 4294967290 4294967295 4294967295 0 0 0 0 8 3")
        self.assertEqual(len(self.feed("T 27 0 40 1 1 0 0 0 0 8 3")), 1)

    def test_corrupt_frame_is_discarded(self):
        self.port.incoming.extend(b"T 27 1*FFFF\n")
        self.assertEqual(self.link.poll(), [])
        self.assertEqual(self.link.bad_frames, 1)
        self.assertIsNone(self.link.last_sample_at)

    def test_missing_samples_require_stop(self):
        with self.assertRaises(nano_tool.NanoError):
            self.link.ensure_fresh()

    def test_stop_command_is_session_bound(self):
        self.link.stop()
        self.assertEqual(decode(self.port.writes[-1]), "S 27 4")

    def test_stop_collects_braking_ticks_and_waits_for_settled_count(self):
        class TrialLink:
            command_sequence = 3

            def __init__(self):
                self.samples = iter([
                    "T 27 1 100 10 0 20 0 0 0 8 3",  # S todavía no confirmado.
                    "T 27 2 150 11 0 20 0 0 0 8 4",
                    "T 27 3 200 12 0 20 0 0 0 8 4",  # Pulsos durante el frenado.
                    "T 27 4 250 13 0 20 0 0 0 8 4",
                    "T 27 5 300 13 0 0 0 0 0 8 4",
                    "T 27 6 400 13 0 0 0 0 0 8 4",
                ])
                self.commands = []

            def send(self, kind):
                self.commands.append(kind)
                self.command_sequence += 1

            def stop(self):
                self.send("S")

            def poll(self):
                return [parse_telemetry(next(self.samples))]

            def ensure_fresh(self):
                pass

        link = TrialLink()
        end = nano_tool.stop_and_settle(link)
        self.assertEqual((end.left_ticks, end.uptime_ms), (13, 400))
        self.assertEqual(set(link.commands), {"S"})


if __name__ == "__main__":
    unittest.main()
