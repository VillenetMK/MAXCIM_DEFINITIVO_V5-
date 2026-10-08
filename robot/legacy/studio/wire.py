"""Signed, bounded RPC shared by the private Jetson link and local AI tools."""
import hashlib
import hmac
import json
import secrets
import time
import urllib.request


def sign(key, data):
    return hmac.new(key.encode(), data, hashlib.sha256).hexdigest()


def encode(key, path, data):
    body = json.dumps(data, separators=(',', ':'), allow_nan=False).encode()
    stamp, nonce = str(time.time()), secrets.token_hex(16)
    value = b'POST\n' + path.encode() + b'\n' + stamp.encode() + b'\n' + nonce.encode() + b'\n' + body
    return body, nonce, {'Content-Type': 'application/json', 'X-Max-Time': stamp,
                         'X-Max-Nonce': nonce, 'X-Max-Signature': sign(key, value)}


def verify_response(key, nonce, body, signature):
    if not hmac.compare_digest(signature, sign(key, nonce.encode() + b'\n' + body)):
        raise ValueError('Respuesta de Jetson no verificada')
    return json.loads(body)


def rpc_local(key, path, data, timeout=2):
    body, nonce, headers = encode(key, path, data)
    request = urllib.request.Request('http://127.0.0.1:8092' + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('Respuesta demasiado grande')
        return verify_response(key, nonce, raw, response.headers.get('X-Max-Signature', ''))


async def exchange(client, config, data):
    if not config:
        raise ValueError('Jetson sin vincular')
    path = '/v2/exchange'
    body, nonce, headers = encode(config['key'], path, data)
    async with client.post(config['url'].rstrip('/') + path, data=body, headers=headers,
                           allow_redirects=False) as response:
        if response.status != 200:
            raise ValueError('El puente de Jetson no responde')
        raw = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            raw.extend(chunk)
            if len(raw) > 1048576:
                raise ValueError('Respuesta demasiado grande')
        return verify_response(config['key'], nonce, bytes(raw), response.headers.get('X-Max-Signature', ''))
