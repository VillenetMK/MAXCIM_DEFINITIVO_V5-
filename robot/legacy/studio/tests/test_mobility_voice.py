import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'studio'))
from mobility_voice import parse_command, TranscriptBuffer


class Commands(unittest.TestCase):
    def test_explicit_bounded_commands(self):
        self.assertEqual(parse_command('Maxim, avanza 25 centímetros.'),
                         {'kind': 'move', 'direction': 'adelante', 'amount': .25})
        self.assertEqual(parse_command('Maxin retrocede medio metro')['amount'], .5)
        self.assertEqual(parse_command('MAXCIM avanza 0,25 metros')['amount'], .25)
        self.assertEqual(parse_command('Maxim gira treinta grados a la izquierda')['amount'], 30)
        self.assertEqual(parse_command('Maxim detente'), {'kind': 'stop'})
        self.assertEqual(parse_command('Maxim ve al punto mesa')['target'], 'mesa')

    def test_preserves_education_and_arms_and_rejects_ambiguity(self):
        for phrase in ['saluda', 'Maxim saluda', 'Maxim cuenta un cuento',
                       'El cuento dice Maxim avanza', 'Maxim no avances',
                       'Maxim avanza 25 centímetros pero no lo hagas',
                       'Maxim avanza -0.5 metros', 'Maxim avanza 10 metros',
                       'Maxim gira 180 grados a la derecha',
                       'Maxim avanza y después gira', 'Maxim ven acá',
                       'Maxim ve junto a esa silla', '"Maxim avanza"']:
            with self.subTest(phrase=phrase):
                self.assertIsNone(parse_command(phrase))

    def test_fragments_require_silence_and_are_consumed_once(self):
        b = TranscriptBuffer()
        b.feed('Maxim avanza', 1., True)
        self.assertIsNone(b.take(2., True))  # No VAD, no movement.
        b.on_vad(False, 2.)
        b.feed('25 centímetros', 2.1, True)
        self.assertIsNone(b.take(2.4, True))
        b.on_vad(False, 3.)
        self.assertEqual(b.take(3., True)['amount'], .25)
        self.assertIsNone(b.take(3.1, True))

    def test_lost_session_expiry_correction_and_cumulative_transcript(self):
        b = TranscriptBuffer()
        b.on_vad(False, 1.)
        b.feed('Maxim avanza', 1., True)
        b.feed('Maxim avanza 25 centímetros', 1.1, True)
        b.feed('no lo hagas', 1.2, True)
        b.on_vad(False, 2.1)
        self.assertIsNone(b.take(2.1, True))
        b.feed('Maxim avanza', 3., True)
        self.assertIsNone(b.take(3.2, False))
        b.on_vad(False, 4.)
        self.assertIsNone(b.take(4., True))
        b.feed('Maxim avanza', 5., True)
        b.on_vad(False, 8.)
        self.assertIsNone(b.take(8., True))


class InstallationBoundary(unittest.TestCase):
    def test_installer_only_copies_separate_files(self):
        spec = importlib.util.spec_from_file_location('installer', ROOT/'ops/install_jetson_integration.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            original = home/'mciav2_ws/src/reasoning_pkg/reasoning_pkg'
            original.mkdir(parents=True)
            for name in ['tools.py', 'gemini_live_node.py']:
                (original/name).write_text('# Original user code\n')
            before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            destination = home/'.local/share/max-studio-jetson'
            installer.install(ROOT, destination)
            for name, value in before.items():
                self.assertEqual((home/name).read_bytes(), value)
            self.assertFalse((original/'studio_tools.py').exists())
            self.assertTrue((destination/'mobility_voice.py').is_file())
        # A regression here would make original arm actions depend on Studio again.
        self.assertNotIn("'/motion_command'", (ROOT/'studio/jetson_hub.py').read_text())


if __name__ == '__main__':
    unittest.main()
