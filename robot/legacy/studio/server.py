#!/usr/bin/env python3
"""One authenticated browser session, existing base control and private V8 adapter."""
import argparse
import asyncio
import contextlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from jetson_link import fetch_status, load_config
from integration import Integration

STATIC = Path(__file__).parent / 'static'
BASE = 'http://127.0.0.1:8080'
ARMS = 'http://127.0.0.1:5000'


def create_app(token, hosts, observer=None, base_url=BASE, arms_url=ARMS, jetson_config=None, navigation=None):
    sessions = set()
    attempts = {}

    @web.middleware
    async def secure(request, handler):
        if request.host not in hosts:
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    app = web.Application(middlewares=[secure], client_max_size=4096)

    async def socket(request):
        if request.headers.get('Origin') != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden()
        peer = request.remote
        attempts[peer] = [t for t in attempts.get(peer, []) if time.monotonic()-t < 60]
        if len(attempts[peer]) >= 10:
            raise web.HTTPTooManyRequests()
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=4096)
        await ws.prepare(request)
        sessions.add(ws)
        tasks = []
        headers = {'X-Studio-Token': token, 'X-Studio-Owner': secrets.token_hex(16)}
        acquired = False
        upstream = None
        integration = None
        last_client = time.monotonic()
        last_arm_seq = -1
        challenges = {}
        arm_lock = asyncio.Lock()
        timeout = ClientTimeout(total=2)
        async with ClientSession(timeout=timeout) as client:
            async def arm_post(path, body=None):
                async with client.post(arms_url+path, json=body or {}, headers=headers) as response:
                    result = await response.json()
                    if response.status >= 400:
                        raise ValueError(result.get('message', 'Brazos no disponibles'))
                    return result

            async def stop_arms():
                if acquired:
                    with contextlib.suppress(Exception):
                        await arm_post('/studio/stop')

            async def arm_status():
                while not ws.closed:
                    result = {'available': False, 'message': 'Servicio de brazos desconectado'}
                    try:
                        await arm_post('/studio/lease')
                        async with client.get(arms_url+'/api/status') as response:
                            result = await response.json()
                        result['available'] = bool(result.get('serial_connected')) and not result.get('serial_error')
                    except Exception as error:
                        result['message'] = str(error) if isinstance(error, ValueError) else 'Servicio de brazos desconectado'
                    await ws.send_json({'type': 'arms_state', 'data': result})
                    await asyncio.sleep(.8)

            async def relay():
                async for message in upstream:
                    if message.type == WSMsgType.TEXT:
                        data = json.loads(message.data)
                        if integration and data.get('type') == 'state':
                            integration.challenge = data.get('challenge', '')
                            integration.challenge_at = time.monotonic()
                        await ws.send_json(data)
                await ws.close()

            async def extras():
                while not ws.closed:
                    nonlocal last_client
                    now = time.monotonic()
                    if now-last_client > .9:
                        await stop_arms()
                        await ws.close(code=1001, message=b'Control sin respuesta')
                        return
                    challenges_copy = {k:v for k,v in challenges.items() if v > now}
                    challenges.clear()
                    challenges.update(challenges_copy)
                    nonce = secrets.token_urlsafe(16)
                    challenges[nonce] = now + .6
                    await ws.send_json({'type':'arm_challenge', 'challenge':nonce})
                    await asyncio.sleep(.2)

            async def visual():
                while not ws.closed:
                    if observer:
                        await ws.send_json({'type':'visual', 'data':observer.snapshot()})
                    await asyncio.sleep(1)

            async def jetson_status():
                while not ws.closed:
                    result = await fetch_status(client, jetson_config)
                    await ws.send_json({'type': 'jetson_state', 'data': result})
                    await asyncio.sleep(3)

            try:
                auth = await asyncio.wait_for(ws.receive_json(), 5)
                supplied = auth.get('token') if isinstance(auth, dict) else None
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), token.encode()):
                    attempts[peer].append(time.monotonic())
                    await ws.send_json({'type':'error', 'message':'Código de acceso incorrecto'})
                    return ws
                upstream = await client.ws_connect(base_url+'/ws', origin=base_url, heartbeat=5)
                await upstream.send_json({'type':'auth', 'token':token})
                answer = await upstream.receive_json(timeout=4)
                await ws.send_json(answer)
                if answer.get('type') != 'connected':
                    return ws
                acquired = True
                integration = Integration(ws, upstream, client, jetson_config, navigation,
                                          arm_post, stop_arms, headers['X-Studio-Owner'], arms_url)
                tasks = [asyncio.create_task(c()) for c in (relay, arm_status, extras, visual, jetson_status)]
                tasks.extend(asyncio.create_task(c()) for c in (integration.poll, integration.tick))
                async for message in ws:
                    if message.type != WSMsgType.TEXT:
                        break
                    try:
                        data = json.loads(message.data)
                        if not isinstance(data, dict):
                            raise ValueError('Orden inválida')
                        kind = data.get('type')
                        if kind == 'heartbeat':
                            last_client = time.monotonic()
                        elif kind in ('drive', 'stop'):
                            await integration.manual(data)
                        elif kind == 'stop_all':
                            challenges.clear()
                            await integration.halt(disable=True)
                            await upstream.send_json({'type':'stop', 'request':data.get('request')})
                            await stop_arms()
                            await ws.send_json({'type':'notice', 'message':'Base detenida; pasos de brazos pendientes cancelados. El paso enviado puede terminar.'})
                        elif kind in ('voice_mode','save_point','media'):
                            nonce = data.get('challenge')
                            if not isinstance(nonce,str) or challenges.pop(nonce,0)<=time.monotonic():
                                raise ValueError('Orden vencida; vuelve a pulsar')
                            if kind == 'media':
                                integration.video = data.get('video') is True
                                integration.audio = data.get('audio') is True
                            elif kind == 'voice_mode':
                                await integration.halt(disable=True)
                                if data.get('enabled') is True:
                                    if time.monotonic()-integration.last_link>1:
                                        raise ValueError('Jetson sin conexión para recibir voz')
                                    integration.voice=True
                            else:
                                if not navigation:raise ValueError('Navegación no disponible')
                                await integration.halt(disable=True)
                                message=navigation.save_point(data.get('name'))
                                await ws.send_json({'type':'notice','message':message})
                        elif kind == 'arm':
                            seq, nonce = data.get('seq'), data.get('challenge')
                            if type(seq) is not int or seq <= last_arm_seq or not isinstance(nonce, str):
                                raise ValueError('Orden de brazos repetida o inválida')
                            last_arm_seq = seq
                            if challenges.pop(nonce, 0) <= time.monotonic():
                                raise ValueError('Orden vencida; vuelve a pulsar')
                            action = data.get('action')
                            routes = {'quick':'/api/quick_actions/run', 'movement':'/api/movements/run',
                                      'step':'/api/steps/run_single', 'home':'/api/home', 'joint':'/api/move'}
                            if action not in routes:
                                raise ValueError('Acción desconocida')
                            if action == 'joint':
                                ch, angle = data.get('channel'), data.get('angle')
                                if type(ch) is not int or type(angle) is not int:
                                    raise ValueError('Articulación inválida')
                                body = {'channel':ch, 'action':'angle', 'value':angle}
                            else:
                                name = data.get('name', '')
                                if not isinstance(name, str) or len(name)>160:
                                    raise ValueError('Nombre inválido')
                                body = {'name':name}
                            async with arm_lock:
                                await integration.halt(disable=True)
                                await arm_post('/studio/lease')
                                await upstream.send_json({'type':'stop', 'request':data.get('request')})
                                result = await arm_post(routes[action], body)
                            await ws.send_json({'type':'arm_result', 'message':result.get('message', 'Orden enviada al ESP32')})
                        else:
                            raise ValueError('Orden desconocida')
                    except (ValueError, TypeError, KeyError, ClientError, asyncio.TimeoutError) as error:
                        await ws.send_json({'type':'arm_error', 'message':str(error)})
            except (asyncio.TimeoutError, OSError, ValueError, ClientError) as error:
                if not ws.closed:
                    await ws.send_json({'type':'error', 'message':'No se pudo conectar con la base de MAX'})
            finally:
                if integration:
                    await integration.close()
                if upstream:
                    # Close the transport before awaiting cancelled relay tasks.
                    # This also releases the base owner's dead-man session immediately.
                    upstream._response.close()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await stop_arms()
                if acquired:
                    with contextlib.suppress(Exception):
                        await arm_post('/studio/release')
                sessions.discard(ws)
                await ws.close()
        return ws

    async def static(request):
        name = request.match_info.get('name') or 'index.html'
        if name not in ('index.html','app.js','studio.js','live.js','style.css','favicon.svg'):
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC/name)

    async def shutdown(app):
        for ws in list(sessions):
            await ws.close(code=1001)

    app.on_shutdown.append(shutdown)
    app.router.add_get('/ws', socket)
    app.router.add_get('/', static)
    app.router.add_get('/{name}', static)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', default=[])
    parser.add_argument('--port', type=int, default=8088)
    args = parser.parse_args()
    hosts = args.host or ['127.0.0.1']
    for host in hosts:
        ip = ipaddress.ip_address(host)
        if not any(ip in ipaddress.ip_network(n) for n in ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
            parser.error('Solo interfaces locales o de la red privada')
    token = (Path.home()/'.config/max-control/token').read_text().strip()
    from observer import Observer
    observer = Observer()
    from navigation import Navigation
    navigation = Navigation(observer.node)
    try:
        web.run_app(create_app(token, {f'{h}:{args.port}' for h in hosts}, observer, jetson_config=load_config(), navigation=navigation),
                    host=hosts, port=args.port, access_log=None, shutdown_timeout=2)
    finally:
        navigation.cancel('Servidor cerrado')
        observer.close()


if __name__ == '__main__':
    main()
