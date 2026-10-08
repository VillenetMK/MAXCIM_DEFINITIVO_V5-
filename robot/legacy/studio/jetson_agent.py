#!/usr/bin/env python3
"""Read-only Jetson telemetry. No camera capture, microphone capture or actuators."""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import threading
import time

NODES = {
    'camera': 'orbbec_camera_node', 'faces': 'orbbec_face_recognition_node',
    'proximity': 'orbbec_proximity_node', 'microphone': 'mic_node',
    'voice': 'gemini_live_node', 'memory': 'memory_node', 'screen': 'web_display_node',
}


def signature(key, value):
    return hmac.new(key.encode(), value, hashlib.sha256).hexdigest()


def collect():
    memory = {k: int(v.split()[0]) for k, v in
              (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    devices = set()
    for item in Path('/sys/bus/usb/devices').glob('*'):
        try:
            devices.add((item.joinpath('idVendor').read_text().strip(),
                         item.joinpath('idProduct').read_text().strip()))
        except OSError:
            pass
    processes = set()
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = proc.joinpath('cmdline').read_bytes().split(b'\0')[:2]
            processes.update(Path(arg.decode(errors='replace')).name for arg in args if arg)
        except OSError:
            pass
    temperatures = []
    for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
        try:
            if any(word in zone.joinpath('type').read_text().lower() for word in ('cpu', 'gpu', 'soc')):
                value = int(zone.joinpath('temp').read_text()) / 1000
                if -20 < value < 130:
                    temperatures.append(value)
        except (OSError, ValueError):
            pass
    disk = shutil.disk_usage('/')
    return {'label': 'Jetson Orin Nano Super', 'sampled_at': time.time(),
            'load': round(os.getloadavg()[0], 2), 'cores': os.cpu_count(),
            'ram_used_gb': round((memory['MemTotal'] - memory['MemAvailable']) / 1048576, 2),
            'ram_total_gb': round(memory['MemTotal'] / 1048576, 2),
            'disk_free_gb': round(disk.free / 1e9, 1),
            'temperature_c': round(max(temperatures), 1) if temperatures else None,
            'camera_usb': any(v == '2bc5' for v, _ in devices),
            'microphone_usb': ('2886', '0018') in devices,
            'processes': {name: executable in processes for name, executable in NODES.items()}}


class Monitor:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def run(self):
        while True:
            try:
                data = collect()
                with self.lock:
                    self.data = data
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(3)

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
        result['available'] = bool(result) and time.time() - result.get('sampled_at', 0) < 10
        return result


def handler_class(key, monitor, hub=None):
    seen, nonce_lock = {}, threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if hub is None or self.path not in ('/v2/exchange', '/v2/intent', '/v2/result'):
                self.send_error(501)
                return
            # Tool submission stays on Jetson. Only the paired Raspberry polls over LAN.
            if self.path != '/v2/exchange' and self.client_address[0] != '127.0.0.1':
                self.send_error(403)
                return
            self.connection.settimeout(2)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    raise ValueError('Tamaño inválido')
                raw = self.rfile.read(length)
                stamp, nonce = self.headers.get('X-Max-Time', ''), self.headers.get('X-Max-Nonce', '')
                value = b'POST\n'+self.path.encode()+b'\n'+stamp.encode()+b'\n'+nonce.encode()+b'\n'+raw
                if (len(nonce) != 32 or len(stamp) > 24 or abs(time.time()-float(stamp)) > 3
                        or not hmac.compare_digest(self.headers.get('X-Max-Signature',''), signature(key, value))):
                    raise ValueError('Solicitud no válida')
                with nonce_lock:
                    for n in list(seen):
                        if seen[n] < time.monotonic():
                            del seen[n]
                    if nonce in seen or len(seen) > 1024:
                        raise ValueError('Solicitud repetida')
                    seen[nonce] = time.monotonic()+7
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('Formato inválido')
            except (ValueError, TypeError, OSError):
                self.send_error(403)
                return
            try:
                result = hub.rpc(self.path, data)
            except (ValueError, TypeError, KeyError, IndexError) as error:
                result = {'ok':False, 'accepted':False, 'error':str(error)}
            body = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Max-Signature', signature(key, nonce.encode()+b'\n'+body))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path != '/v1/status':
                self.send_error(404)
                return
            stamp = self.headers.get('X-Max-Time', '')
            nonce = self.headers.get('X-Max-Nonce', '')
            signed = self.headers.get('X-Max-Signature', '')
            valid = False
            try:
                valid = (len(stamp) < 20 and abs(time.time() - float(stamp)) < 15
                         and len(nonce) == 32 and all(c in '0123456789abcdef' for c in nonce)
                         and hmac.compare_digest(signed, signature(key, f'{stamp}\n{nonce}'.encode())))
            except (ValueError, TypeError):
                pass
            if not valid:
                self.send_error(403)
                return
            body = json.dumps(monitor.snapshot(), separators=(',', ':')).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Max-Signature', signature(key, nonce.encode() + b'\n' + body))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', default=[])
    parser.add_argument('--port', type=int, default=8092)
    args = parser.parse_args()
    hosts = args.host or ['127.0.0.1']
    allowed = [ipaddress.ip_network(n) for n in ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    if any(not any(ipaddress.ip_address(h) in n for n in allowed) for h in hosts):
        parser.error('Solo interfaces privadas o loopback')
    key = Path.home().joinpath('.config/max-studio/jetson-key').read_text().strip()
    monitor = Monitor()
    from jetson_hub import Hub
    hub = Hub()
    hub.start_ros()
    threading.Thread(target=monitor.run, daemon=True).start()
    handler = handler_class(key, monitor, hub)
    servers = [ThreadingHTTPServer((host, args.port), handler) for host in hosts]
    for server in servers[:-1]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[-1].serve_forever()


if __name__ == '__main__':
    main()
