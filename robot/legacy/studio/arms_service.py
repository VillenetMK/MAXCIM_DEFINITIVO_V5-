"""Run the unchanged V8 panel locally, with a lease and cancellable queued steps."""
import hmac
import math
import os
from pathlib import Path
import runpy
import subprocess
import threading
import time
from types import SimpleNamespace

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[1]
PORT = '/dev/serial/by-path/platform-xhci-hcd.1-usb-0:1:1.0-port0'


def main():
    port = os.environ.get('MAX_ARMS_PORT', PORT)
    if not Path(port).exists():
        raise RuntimeError('El puerto físico del ESP32 no está disponible')
    if subprocess.run(['fuser', os.path.realpath(port)], capture_output=True).returncode == 0:
        raise RuntimeError('El puerto de brazos está ocupado; cierra el controlador anterior')
    os.environ['ESP32_PORT'] = port
    loaded = runpy.run_path(str(ROOT / 'BRAZOS_MAXICM_v8/SERV.py'), run_name='max_arms_local')
    ns = loaded['enviar_comando'].__globals__
    app = ns['app']
    # Voice is routed through Studio's exclusive lease, never directly from DDS.
    ns['publisher'].destroy_subscription(ns['publisher'].motion_subscription)
    token = (Path.home() / '.config/max-control/token').read_text().strip()
    guard = threading.RLock()
    cancel = threading.Event()
    lease = {'owner': None, 'until': 0.0}
    diagnostics = {'last_ack': '', 'ack_at': 0.0, 'error': ''}
    original_send = ns['enviar_comando']
    original_start = ns['iniciar_secuencia_background']

    class Cancelled(RuntimeError):
        pass

    def cancellable_sleep(seconds):
        if cancel.wait(max(0, float(seconds))):
            raise Cancelled('Pasos pendientes cancelados; el paso enviado puede terminar')

    ns['time'] = SimpleNamespace(sleep=cancellable_sleep, strftime=time.strftime)

    def send(command):
        with guard:
            if cancel.is_set():
                raise Cancelled('Secuencia cancelada')
            if not ns['esp32'] or not ns['esp32'].is_open:
                raise RuntimeError('ESP32 desconectado')
            original_send(command)

    def start(sequence):
        with guard:
            if ns['sequence_running']:
                return False
            cancel.clear()
            return original_start(sequence)

    ns['enviar_comando'] = send
    ns['iniciar_secuencia_background'] = start

    def expire():
        if lease['owner'] and time.monotonic() >= lease['until']:
            cancel.set()
            lease.update(owner=None, until=0.0)

    def reader():
        while ns['esp32'] and ns['esp32'].is_open:
            try:
                line = ns['esp32'].readline().decode('utf-8', errors='replace').strip()
                if line.startswith(('ACK', 'OK', 'ERR')):
                    diagnostics.update(last_ack=line[:160], ack_at=time.monotonic())
            except Exception as error:
                diagnostics['error'] = str(error)[:160]
                cancel.set()
                break

    def watchdog():
        while True:
            with guard:
                expire()
            time.sleep(.1)

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

    @app.before_request
    def protect():
        if request.host not in ('127.0.0.1:5000', 'localhost:5000'):
            return jsonify(message='Host no permitido'), 403
        if request.method != 'POST':
            return None
        with guard:
            expire()
            internal = hmac.compare_digest(request.headers.get('X-Studio-Token', ''), token)
            owner = request.headers.get('X-Studio-Owner', '')
            if request.path.startswith('/studio/'):
                if not internal or not owner:
                    return jsonify(message='Sesión no válida'), 403
            elif (internal and owner != lease['owner']) or (lease['owner'] and not internal):
                return jsonify(message='MAX Studio tiene el control de brazos'), 409
            if request.path.startswith('/api/'):
                if ns['sequence_running']:
                    return jsonify(message='Espera a que termine la secuencia'), 409
                if not ns['esp32'] or not ns['esp32'].is_open or diagnostics['error']:
                    return jsonify(message='ESP32 desconectado'), 503
                if request.path == '/api/move':
                    data = request.get_json(silent=True) or {}
                    ch, value = data.get('channel'), data.get('value')
                    if type(ch) is not int or ch not in ns['SERVOS_CONFIG'] or data.get('action') not in ('angle', 'step_up', 'step_down'):
                        return jsonify(message='Articulación o ángulo inválidos'), 400
                    config = ns['SERVOS_CONFIG'][ch]
                    if data.get('action') == 'angle' and (type(value) is not int or not config['min'] <= value <= config['max']):
                        return jsonify(message='Ángulo fuera del rango calibrado'), 400
                cancel.clear()

    @app.post('/studio/lease')
    def acquire():
        with guard:
            owner = request.headers['X-Studio-Owner']
            if lease['owner'] not in (None, owner):
                return jsonify(message='Brazos ocupados'), 409
            if lease['owner'] is None and ns['sequence_running']:
                return jsonify(message='El panel V8 está ejecutando una secuencia'), 409
            lease.update(owner=owner, until=time.monotonic() + 3)
            return jsonify(ok=True)

    @app.post('/studio/stop')
    def stop():
        with guard:
            if lease['owner'] != request.headers['X-Studio-Owner']:
                return jsonify(message='Sesión no válida'), 403
            cancel.set()
            return jsonify(ok=True, message='Pasos pendientes cancelados. El paso enviado puede terminar.')

    @app.post('/studio/voice')
    def voice():
        with guard:
            if lease['owner'] != request.headers['X-Studio-Owner']:
                return jsonify(message='Sesión no válida'), 403
            if ns['sequence_running']:
                return jsonify(message='Los brazos están ocupados'), 409
            if not ns['esp32'] or not ns['esp32'].is_open or diagnostics['error']:
                return jsonify(message='ESP32 no disponible'), 503
            action = (request.get_json(silent=True) or {}).get('action')
            if not isinstance(action, str):
                return jsonify(message='Orden inválida'), 400
            config = ns['load_json_file'](ns['VOICE_COMMANDS_FILE']).get(action)
            if not isinstance(config, dict):
                return jsonify(message='Comando de voz desconocido'), 400
            movement, home = config.get('movimiento',''), config.get('volver_a_home','')
            catalog = ns['load_quick_actions']()
            if not movement or any(name and name not in catalog for name in (movement,home)):
                return jsonify(message='No existe la expresión calibrada'), 400
            try:
                delay = float(config.get('esperar_segundos',0))
                if not math.isfinite(delay) or not 0 <= delay <= 30: raise ValueError()
            except (ValueError,TypeError):
                return jsonify(message='Pausa de voz inválida'), 400
            cancel.clear()
            started = ns['iniciar_comando_voz_background'](movement,delay,home)
            return jsonify(ok=started,message='Expresión iniciada' if started else 'Brazos ocupados'), 200 if started else 409

    @app.post('/studio/release')
    def release():
        with guard:
            if lease['owner'] == request.headers['X-Studio-Owner']:
                cancel.set()
                lease.update(owner=None, until=0)
            return jsonify(ok=True)

    original_status = app.view_functions['get_status']

    def status():
        response = original_status()
        data = response.get_json()
        data.update(position_feedback=False, cancel_requested=cancel.is_set(),
                    studio_owner=bool(lease['owner']), serial_error=diagnostics['error'],
                    last_ack=diagnostics['last_ack'])
        return jsonify(data)

    app.view_functions['get_status'] = status
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
