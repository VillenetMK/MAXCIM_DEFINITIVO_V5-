#!/usr/bin/env python3
"""Optional mobility node reading existing Gemini transcripts, with no AI edits.

No USB capture, cloud calls, speech files or publications on original action
topics. Start explicitly; requires a visible Studio session and fresh VAD.
Educational and arm commands are ignored.
"""
import argparse
import json
from pathlib import Path
import re
import time
import unicodedata

from wire import rpc_local


def normalize(text):
    value = unicodedata.normalize('NFKD', text.casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r'(?<=\d),(?=\d)', '.', value)
    # Preserve minus signs, quotes and other syntax: removing them could turn a
    # rejected/quoted/negative command into a valid positive movement.
    return ' '.join(re.sub(r'[¿?¡!,;:]', ' ', value).strip(' .').split())


def parse_command(text):
    """Accept complete addressed commands, never substrings from a conversation."""
    if not isinstance(text, str) or len(text) > 256:
        return None
    match = re.fullmatch(r'(?:maxim|maxin|maxcim|max) (?:movilidad )?(.+)', normalize(text))
    if not match:
        return None
    command = match[1]
    if command in ('alto', 'para', 'detente', 'quieto', 'cancela'):
        return {'kind': 'stop'}
    if command in ('estado', 'estado de movimiento'):
        return {'kind': 'status'}
    match = re.fullmatch(r'(avanza|retrocede)(?: (.+))?', command)
    if match:
        amount = .25
        if match[2]:
            quantities = {'medio metro': .5, 'un metro': 1., 'un cuarto de metro': .25,
                          'diez centimetros': .1, 'veinte centimetros': .2,
                          'veinticinco centimetros': .25, 'cincuenta centimetros': .5}
            amount = quantities.get(match[2])
            if amount is None:
                number = re.fullmatch(r'(\d+(?:\.\d+)?) (metros?|centimetros?)', match[2])
                if not number:
                    return None
                amount = float(number[1]) / (100 if number[2].startswith('centimetro') else 1)
        if not .05 <= amount <= 1:
            return None
        return {'kind': 'move', 'direction': 'adelante' if match[1] == 'avanza' else 'atras', 'amount': amount}
    match = re.fullmatch(r'gira(?: (\d+|quince|treinta|cuarenta y cinco|noventa) grados)? (?:a la |hacia la )?(izquierda|derecha)', command)
    if match:
        degrees = {'quince': 15, 'treinta': 30, 'cuarenta y cinco': 45, 'noventa': 90}.get(match[1])
        if degrees is None:
            degrees = int(match[1] or 30)
        if not 5 <= degrees <= 90:
            return None
        return {'kind': 'turn', 'direction': match[2], 'amount': degrees}
    match = re.fullmatch(r've al punto ([a-z0-9 ]{1,40})', command)
    if match and not re.search(r'\b(y|luego|despues|pero|no)\b', match[1]):
        return {'kind': 'goto', 'target': match[1]}
    match = re.fullmatch(r'acercate a ([a-z ]{1,40})', command)
    if match and not re.search(r'\b(aca|aqui|ahi|mi|el|la|ese|esa|y|luego|despues|no)\b', match[1]):
        return {'kind': 'person', 'target': match[1]}
    return None


class TranscriptBuffer:
    """Short-lived fragments requiring fresh silence and a stable full command."""
    def __init__(self):
        self.clear()
        self.vad_at = self.quiet_since = None
        self.speaking = True

    def clear(self):
        self.text = ''
        self.first = self.last = 0.

    def on_vad(self, active, now):
        if active:
            self.quiet_since = None
        elif self.speaking or self.quiet_since is None:
            self.quiet_since = now
        self.speaking = active
        self.vad_at = now

    def feed(self, text, now, enabled):
        if not enabled:
            self.clear()
            return
        text = text.strip()
        if not text or len(text) > 256:
            self.clear()
            return
        if self.text and now-self.first > 4:
            self.clear()
        if not self.text:
            self.first = now
        if text.startswith(self.text):
            self.text = text
        elif not self.text.endswith(text):
            self.text += ' '+text
        self.last = now
        if len(self.text) > 256:
            self.clear()

    def take(self, now, enabled):
        if not enabled or (self.text and now-self.last > 2):
            self.clear()
            return None
        if (not self.text or self.vad_at is None or now-self.vad_at > .5
                or self.speaking or self.quiet_since is None
                or now-self.quiet_since < .8 or now-self.last < .8):
            return None
        result = parse_command(self.text)
        self.clear()
        return result


def listen():
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from std_msgs.msg import String, Bool

    key = (Path.home()/'.config/max-studio/jetson-key').read_text().strip()
    rclpy.init(args=['--ros-args', '--disable-external-lib-logs', '--disable-stdout-logs', '--disable-rosout-logs'])
    node = rclpy.create_node('max_mobility_voice')
    buffer = TranscriptBuffer()
    state = {'enabled': False, 'checked': 0., 'sent': None, 'sent_at': 0.}
    publisher = node.create_publisher(String, '/max/mobility/result', 1)

    def transcript(msg):
        now = time.monotonic()
        buffer.feed(msg.data, now, state['enabled'] and now-state['checked'] < .8)

    def tick():
        now = time.monotonic()
        if now-state['checked'] > .4:
            try:
                status = rpc_local(key, '/v2/intent', {'kind': 'status'}, timeout=.25)
                enabled = status.get('voice') is True
            except Exception:
                enabled = False
            if enabled != state['enabled']:
                buffer.clear()
            state.update(enabled=enabled, checked=time.monotonic())
        intent = buffer.take(time.monotonic(), state['enabled'])
        if intent is None:
            return
        # Repeated ASR fragments do not repeat an action. No retry on rejection.
        if intent == state['sent'] and now-state['sent_at'] < 3:
            return
        state.update(sent=intent, sent_at=now)
        try:
            result = rpc_local(key, '/v2/intent', intent, timeout=.25)
        except Exception:
            result = {'accepted': False, 'error': 'Sin confirmación del puente; no se repite la orden.'}
        msg = String()
        msg.data = json.dumps(result, ensure_ascii=False)
        publisher.publish(msg)

    node.create_subscription(String, '/gemini/input_transcript', transcript, 10)
    node.create_subscription(Bool, '/audio/vad', lambda m: buffer.on_vad(m.data, time.monotonic()), qos_profile_sensor_data)
    node.create_timer(.05, tick)
    print('Movilidad separada: esperando transcripciones existentes y sesión Studio habilitada.', flush=True)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if state['enabled']:
            try:
                rpc_local(key, '/v2/intent', {'kind': 'stop'}, timeout=.25)
            except Exception:
                pass
        node.destroy_node()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--parse', metavar='FRASE', help='Analiza sin conectar ni mover hardware')
    mode.add_argument('--listen', action='store_true', help='Escucha transcripciones ROS existentes')
    args = parser.parse_args()
    if args.parse is not None:
        print(json.dumps(parse_command(args.parse), ensure_ascii=False))
    else:
        listen()


if __name__ == '__main__':
    main()
