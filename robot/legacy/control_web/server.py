#!/usr/bin/env python3
"""Private-network bridge for MAX's static control panel. Never opens serial ports."""
import argparse
import asyncio
import contextlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from core import Controller, ControlError

STATIC = Path(__file__).parent / 'static'


class RosBridge:
    def __init__(self, control):
        import rclpy
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import LaserScan
        from rclpy.qos import qos_profile_sensor_data
        self.rclpy, self.Twist, self.control = rclpy, Twist, control
        rclpy.init()
        self.node = rclpy.create_node('max_web_control')
        self.publisher = self.node.create_publisher(Twist, '/cmd_vel', 1)
        self.node.create_subscription(Odometry, '/odom', self.odom, 1)
        self.node.create_subscription(LaserScan, '/scan', self.scan, qos_profile_sensor_data)
        self.node.create_timer(0.05, self.tick)
        self.node.create_timer(0.25, self.check_controllers)
        self.thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True)
        self.thread.start()

    def odom(self, msg):
        import math
        values = (msg.twist.twist.linear.x, msg.twist.twist.angular.z)
        age = (self.node.get_clock().now().nanoseconds -
               (msg.header.stamp.sec * 10**9 + msg.header.stamp.nanosec)) / 1e9
        if all(math.isfinite(v) for v in values) and -0.2 <= age < 0.5:
            with self.control.lock:
                self.control.odom_at = time.monotonic()
                self.control.measured = values

    def scan(self, msg):
        import math
        age = (self.node.get_clock().now().nanoseconds -
               (msg.header.stamp.sec * 10**9 + msg.header.stamp.nanosec)) / 1e9
        if -0.2 <= age < 1.0 and any(math.isfinite(v) and msg.range_min <= v <= msg.range_max for v in msg.ranges):
            with self.control.lock:
                self.control.scan_at = time.monotonic()

    def check_controllers(self):
        peers = self.node.get_publishers_info_by_topic('/cmd_vel')
        with self.control.lock:
            self.control.other_controller = any(
                p.node_name != self.node.get_name() or p.node_namespace != self.node.get_namespace()
                for p in peers)

    def tick(self):
        output = self.control.tick()
        if output is not None:
            msg = self.Twist()
            msg.linear.x, msg.angular.z = output
            self.publisher.publish(msg)

    def close(self):
        self.control.stop()
        for _ in range(3):
            self.publisher.publish(self.Twist())
            time.sleep(0.05)
        self.rclpy.try_shutdown()
        self.thread.join(timeout=2)


def load_token(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            out.write(secrets.token_urlsafe(24))
    os.chmod(path, 0o600)
    token = path.read_text().strip()
    if len(token) < 24:
        raise RuntimeError('El código de acceso debe tener al menos 24 caracteres')
    return token


def create_app(control, token, origins=(), hosts=('127.0.0.1:8080',), demo=False):
    allowed_hosts, allowed_origins = set(hosts), set(origins)

    @web.middleware
    async def secure(request, handler):
        if request.host not in allowed_hosts:
            raise web.HTTPForbidden(text='Host no permitido')
        response = await handler(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'"})
        return response

    app = web.Application(middlewares=[secure], client_max_size=4096)
    sockets, attempts = set(), {}

    async def websocket(request):
        origin = request.headers.get('Origin', '')
        same_origin = origin == f'{request.scheme}://{request.host}'
        if not same_origin and origin not in allowed_origins:
            raise web.HTTPForbidden(text='Origen no permitido')
        now = time.monotonic()
        peer = request.remote
        attempts[peer] = [t for t in attempts.get(peer, []) if now - t < 60]
        if len(attempts[peer]) >= 10:
            raise web.HTTPTooManyRequests(text='Espera un minuto antes de reconectar')
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=2048, compress=False)
        await ws.prepare(request)
        sockets.add(ws)
        owner = secrets.token_hex(12)
        acquired = False
        sender = None
        try:
            auth = await asyncio.wait_for(ws.receive_json(), timeout=5)
            supplied = auth.get('token') if isinstance(auth, dict) else None
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), token.encode()):
                attempts[peer].append(now)
                await ws.send_json({'type': 'error', 'message': 'Código de acceso incorrecto'})
                return ws
            control.acquire(owner)
            acquired = True
            await ws.send_json({'type': 'connected', 'demo': demo})

            async def updates():
                while not ws.closed:
                    if demo:
                        with control.lock:
                            control.odom_at = control.scan_at = time.monotonic()
                    state = control.status()
                    state.update(type='state', challenge=control.challenge(owner))
                    await asyncio.wait_for(ws.send_json(state), timeout=0.2)
                    await asyncio.sleep(0.1)

            sender = asyncio.create_task(updates())
            async for message in ws:
                if sender.done():
                    break
                if message.type != WSMsgType.TEXT:
                    break
                try:
                    data = json.loads(message.data)
                    if not isinstance(data, dict):
                        raise ControlError('Orden inválida')
                    if data.get('type') == 'stop':
                        control.stop()
                        await ws.send_json({'type': 'stopped', 'request': data.get('request')})
                    elif data.get('type') == 'drive':
                        control.command(owner, data.get('seq'), data.get('challenge'), data.get('linear'), data.get('angular'))
                    else:
                        raise ControlError('Orden desconocida')
                except (ValueError, TypeError) as error:
                    control.stop()
                    await ws.send_json({'type': 'error', 'message': str(error)})
        except ControlError as error:
            await ws.send_json({'type': 'error', 'message': str(error)})
        except (asyncio.TimeoutError, ValueError, TypeError, ConnectionError):
            pass
        finally:
            if acquired:
                control.release(owner)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError, asyncio.TimeoutError):
                    await sender
            sockets.discard(ws)
            await ws.close()
        return ws

    async def static(request):
        name = request.match_info.get('name') or 'index.html'
        if name not in {'index.html', 'app.js', 'style.css', 'favicon.svg'}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    async def shutdown(app):
        control.stop()
        for ws in list(sockets):
            await ws.close(code=1001, message=b'Servidor detenido')

    app.on_shutdown.append(shutdown)
    app.router.add_get('/ws', websocket)
    app.router.add_get('/', static)
    app.router.add_get('/{name}', static)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', default=[])
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--origin', action='append', default=[])
    parser.add_argument('--token-file', default='~/.config/max-control/token')
    parser.add_argument('--demo', action='store_true', help='No carga ROS ni envía órdenes al robot')
    args = parser.parse_args()
    hosts = args.host or ['127.0.0.1']
    # Bind only explicit loopback/private IPv4 interfaces. No public robot endpoint.
    import ipaddress
    for host in hosts:
        ip = ipaddress.ip_address(host)
        private = any(ip in net for net in map(ipaddress.ip_network, ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8']))
        if not private:
            parser.error('Usa la dirección IPv4 de la Raspberry en la red local')
    control = Controller()
    token = load_token(args.token_file)
    bridge = None if args.demo else RosBridge(control)
    app = create_app(control, token, args.origin, [f'{h}:{args.port}' for h in hosts], args.demo)
    try:
        web.run_app(app, host=hosts, port=args.port, access_log=None, shutdown_timeout=2)
    finally:
        if bridge:
            bridge.close()


if __name__ == '__main__':
    main()
