"""API y frontend en el mismo origen; sesiones revocables y acceso por rol."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from aiohttp import web

from .bridge import RosBridge, SimulationBridge
from .store import Store

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
OPERATORS = {"admin", "engineer"}
SAFE_COMMANDS = {"acquire", "heartbeat", "release", "drive", "brake", "arm", "voice", "stop", "estop", "reset"}
STORE = web.AppKey("store", Store)
BRIDGE = web.AppKey("bridge", object)
CONFIG = web.AppKey("config", dict)
CLIENTS = web.AppKey("clients", set)
FAILURES = web.AppKey("failures", dict)
ACTOR = web.RequestKey("actor", dict)


@web.middleware
async def middleware(request, handler):
    try:
        if request.path.startswith("/api/") and request.path not in {"/api/login", "/api/health"}:
            actor = request.app[STORE].session(request.cookies.get("maxcim_session"))
            if not actor:
                raise web.HTTPUnauthorized(text="Inicia sesión")
            request[ACTOR] = actor
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), actor["csrf"]):
                    raise web.HTTPForbidden(text="Solicitud sin protección CSRF válida")
        if request.method not in {"GET", "HEAD", "OPTIONS"} or request.path == "/api/events":
            origin = request.headers.get("Origin")
            if origin and origin not in request.app[CONFIG]["origins"]:
                raise web.HTTPForbidden(text="Origen no permitido")
        response = await handler(request)
    except web.HTTPException as error:
        response = web.json_response({"error": error.text}, status=error.status)
    except (ValueError, TypeError, KeyError) as error:
        response = web.json_response({"error": str(error)}, status=400)
    except sqlite3.IntegrityError:
        response = web.json_response({"error": "El registro ya existe o viola una restricción"}, status=409)
    except Exception:
        LOG.exception("Error de la API")
        response = web.json_response({"error": "Error interno; consulta los registros del servicio"}, status=500)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"})
    return response


def require_role(request, roles):
    if request[ACTOR]["role"] not in roles:
        raise web.HTTPForbidden(text="Tu rol no permite esta operación")


def control_owner(request):
    client = request.headers.get("X-Control-Client", request.query.get("client", ""))
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", client):
        raise ValueError("Identificador de panel de control inválido")
    return request[ACTOR]["token"] + ":" + client


async def object_body(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Se requiere application/json")
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Se requiere un objeto JSON")
    return data


async def login(request):
    data = await object_body(request)
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 256:
        raise ValueError("Credenciales inválidas")
    key = request.remote or "unknown"
    now = time.monotonic()
    failures = request.app[FAILURES]
    if key not in failures and len(failures) >= 2048:
        for remote in list(failures):
            if all(now-t >= 300 for t in failures[remote]):
                del failures[remote]
        if len(failures) >= 2048:
            raise web.HTTPTooManyRequests(text="Intenta de nuevo más tarde")
    recent = [t for t in failures.get(key, []) if now-t < 300]
    if len(recent) >= 8:
        raise web.HTTPTooManyRequests(text="Demasiados intentos; espera cinco minutos")
    result = request.app[STORE].login(username, password)
    if not result:
        failures[key] = recent + [now]
        raise web.HTTPUnauthorized(text="Usuario o contraseña incorrectos")
    failures.pop(key, None)
    token, csrf = result
    request.app[STORE].audit(username, "login")
    response = web.json_response({"csrf": csrf})
    response.set_cookie("maxcim_session", token, httponly=True, samesite="Strict", max_age=8*3600,
                        secure=request.app[CONFIG]["secure"], path="/")
    return response


async def me(request):
    actor = request[ACTOR]
    return web.json_response({key: actor[key] for key in ("username", "role", "csrf")})


async def logout(request):
    actor = request[ACTOR]
    try:
        await request.app[BRIDGE].execute(control_owner(request), "release", {})
    except ValueError:
        pass  # El watchdog del gateway vence la concesión si está desconectado.
    finally:
        request.app[STORE].logout(actor["token"])
    response = web.json_response({"ok": True})
    response.del_cookie("maxcim_session", path="/")
    return response


def public_state(app):
    state = app[BRIDGE].state()
    state.pop("owner", None)  # No revelar el hash del identificador de sesión.
    return state


async def state(request):
    result = public_state(request.app)
    result["owns_control"] = request.app[BRIDGE].state().get("owner") == control_owner(request)
    return web.json_response(result)


async def camera(request):
    image = request.app[BRIDGE].camera()
    if image is None:
        raise web.HTTPServiceUnavailable(text="No hay una imagen JPEG reciente en ROS 2")
    return web.Response(body=image, content_type="image/jpeg")


async def command(request):
    actor = request[ACTOR]
    data = await object_body(request)
    kind = data.get("command")
    if kind not in SAFE_COMMANDS:
        raise ValueError("Comando inválido")
    if kind not in {"stop", "estop"}:
        require_role(request, OPERATORS)
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido")
    try:
        result = await request.app[BRIDGE].execute(control_owner(request), kind, payload)
    except ValueError as error:
        request.app[STORE].audit(actor["username"], "command_rejected", {"command": kind, "reason": str(error)})
        raise web.HTTPConflict(text=str(error))
    if kind != "heartbeat":
        request.app[STORE].audit(actor["username"], kind, payload)
    result.pop("owner", None)
    return web.json_response(result)


async def tasks(request):
    store, actor = request.app[STORE], request[ACTOR]
    if request.method == "GET":
        return web.json_response(store.tasks())
    require_role(request, {"admin", "engineer", "teacher"})
    data = await object_body(request)
    if request.method == "POST":
        task_id = store.create_task(actor["username"], data.get("title"), data.get("owner", ""))
        store.audit(actor["username"], "task_created", {"id": task_id})
        return web.json_response({"id": task_id}, status=201)
    task_id = int(request.match_info["id"])
    if not store.update_task(task_id, data.get("state"), data.get("version")):
        raise web.HTTPConflict(text="La tarea cambió; actualiza antes de editar")
    store.audit(actor["username"], "task_updated", {"id": task_id, "state": data["state"]})
    return web.json_response({"ok": True})


async def notes(request):
    store = request.app[STORE]
    if request.method == "GET":
        return web.json_response(store.notes())
    require_role(request, {"admin", "engineer", "teacher"})
    data = await object_body(request)
    store.add_note(request[ACTOR]["username"], data.get("body"))
    return web.json_response({"ok": True}, status=201)


async def users(request):
    require_role(request, {"admin"})
    data = await object_body(request)
    request.app[STORE].create_user(data.get("username"), data.get("password"), data.get("role"))
    request.app[STORE].audit(request[ACTOR]["username"], "user_created", {"username": data["username"], "role": data["role"]})
    return web.json_response({"ok": True}, status=201)


async def audit(request):
    require_role(request, OPERATORS)
    return web.json_response(request.app[STORE].audit_rows())


async def events(request):
    if len(request.app[CLIENTS]) >= 64:
        raise web.HTTPServiceUnavailable(text="Demasiadas conexiones de estado")
    owner = control_owner(request)
    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=4096)
    await ws.prepare(request)
    client = (ws, request[ACTOR]["token"], owner)
    request.app[CLIENTS].add(client)
    try:
        async for _ in ws:
            pass  # Solo estado: los comandos pasan por API/CSRF y el gateway.
    finally:
        request.app[CLIENTS].discard(client)
    return ws


async def life(app):
    async def run():
        last = 0.0
        while True:
            app[BRIDGE].tick()
            now = time.monotonic()
            if now-last >= .2:
                last = now
                for ws, token, owner in tuple(app[CLIENTS]):
                    actor = app[STORE].db.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1", (token, time.time())).fetchone()
                    if not actor:
                        await ws.close(code=1008, message=b"Session expired")
                        app[CLIENTS].discard((ws, token, owner))
                        continue
                    payload = public_state(app)
                    payload["owns_control"] = app[BRIDGE].state().get("owner") == owner
                    try:
                        await asyncio.wait_for(ws.send_json({"type": "state", "data": payload}), .1)
                    except (TimeoutError, ConnectionError, RuntimeError):
                        app[CLIENTS].discard((ws, token, owner))
                        await ws.close()
            await asyncio.sleep(.05)
    task = asyncio.create_task(run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for ws, _, _ in tuple(app[CLIENTS]):
            await ws.close(code=1001, message=b"Server shutdown")
        with contextlib.suppress(Exception):
            await app[BRIDGE].execute("server_shutdown", "estop", {})
        app[BRIDGE].close()
        app[STORE].close()


def create_app(*, database=None, mode=None, origins=None, secure=None):
    app = web.Application(middlewares=[middleware], client_max_size=16384)
    mode = mode or os.getenv("MAXCIM_MODE", "simulation")
    if mode not in {"simulation", "ros2"}:
        raise ValueError("MAXCIM_MODE debe ser simulation o ros2")
    app[STORE] = Store(database or os.getenv("MAXCIM_DATABASE", str(ROOT/"var/team.db")))
    app[BRIDGE] = SimulationBridge() if mode == "simulation" else RosBridge()
    app[CONFIG] = {"origins": set(origins or os.getenv("MAXCIM_ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080").split(",")),
                   "secure": secure if secure is not None else os.getenv("MAXCIM_COOKIE_SECURE", "false").lower() == "true"}
    app[CLIENTS], app[FAILURES] = set(), {}
    app.cleanup_ctx.append(life)
    async def health(_):
        return web.json_response({"ok": True, "mode": mode})
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/robot", state)
    app.router.add_get("/api/camera", camera)
    app.router.add_post("/api/robot/command", command)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/tasks", tasks)
    app.router.add_post("/api/tasks", tasks)
    app.router.add_patch("/api/tasks/{id}", tasks)
    app.router.add_get("/api/notes", notes)
    app.router.add_post("/api/notes", notes)
    app.router.add_post("/api/users", users)
    app.router.add_get("/api/audit", audit)
    async def index(_):
        return web.FileResponse(ROOT/"frontend/index.html")
    app.router.add_get("/", index)
    app.router.add_static("/assets/", ROOT/"frontend/assets", show_index=False)
    return app


def main():
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host=os.getenv("MAXCIM_HOST", "127.0.0.1"), port=int(os.getenv("MAXCIM_PORT", "8080")))


if __name__ == "__main__":
    main()
