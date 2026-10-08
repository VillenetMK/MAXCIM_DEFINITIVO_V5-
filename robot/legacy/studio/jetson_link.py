"""Fetch signed, read-only status from the paired Jetson over the private LAN."""
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit


def load_config(path=None):
    path = path or Path.home() / '.config/max-studio/jetson.json'
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    url = urlsplit(config['url'])
    networks = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8')]
    if (url.scheme != 'http' or not any(ipaddress.ip_address(url.hostname) in n for n in networks)
            or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment
            or len(config['key']) < 32):
        raise ValueError('Configuración de Jetson inválida')
    return config


async def fetch_status(client, config):
    if not config:
        return {'available': False, 'message': 'Jetson sin vincular'}
    key = config['key'].encode()
    stamp, nonce = str(time.time()), secrets.token_hex(16)
    sign = lambda body: hmac.new(key, body, hashlib.sha256).hexdigest()
    headers = {'X-Max-Time': stamp, 'X-Max-Nonce': nonce,
               'X-Max-Signature': sign(f'{stamp}\n{nonce}'.encode())}
    try:
        async with client.get(config['url'].rstrip('/') + '/v1/status', headers=headers,
                              allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('Respuesta no válida')
            body = b''
            async for chunk in response.content.iter_chunked(2048):
                body += chunk
                if len(body) > 8192:
                    raise ValueError('Respuesta demasiado grande')
            if not hmac.compare_digest(
                    response.headers.get('X-Max-Signature', ''), sign(nonce.encode() + b'\n' + body)):
                raise ValueError('Respuesta no verificada')
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Formato no válido')
            return data
    except Exception:
        return {'available': False, 'message': 'Jetson sin respuesta'}
