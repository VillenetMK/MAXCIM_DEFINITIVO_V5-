"""Helper compartido por los scripts `seed_*_remote.py`: arma la URL de un
servidor MySQL *remoto* explícito y construye una app de Flask aislada
apuntando a él, sin tocar la base local configurada en `.env`
(`MYSQL_*`/`DATABASE_URL`).

Usa a propósito el prefijo de variables de entorno REMOTE_, distinto del
MYSQL_*/DATABASE_URL que usa app.py: si no le das un destino explícito a
través de las banderas o de REMOTE_*, estos scripts fallan en vez de caer
a los valores por defecto de `app.py` (localhost/root) -así nunca puedes
confundir por accidente la base local con la remota.

Conexión remota (en orden de prioridad):
    1. --database-url (URL completa de SQLAlchemy)
    2. --host/--port/--user/--password/--database
    3. Variables de entorno REMOTE_DATABASE_URL o
       REMOTE_MYSQL_{HOST,PORT,USER,PASSWORD,DATABASE}
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote_plus

import app as app_module


def add_remote_args(parser) -> None:
    """Agrega las banderas de conexión remota a un ArgumentParser (o a un
    subparser). En un CLI con subcomandos, agrégalas al parser PRINCIPAL:
    argparse solo reconoce las opciones del parser en el que aparecen, así
    que deben escribirse ANTES del nombre del subcomando en la línea de
    comandos."""
    group = parser.add_argument_group("conexión remota")
    group.add_argument("--database-url", help="URL completa de SQLAlchemy hacia el MySQL remoto.")
    group.add_argument("--host", help="Host del MySQL remoto.")
    group.add_argument("--port", default="3306", help="Puerto del MySQL remoto (default 3306).")
    group.add_argument("--user", help="Usuario del MySQL remoto.")
    group.add_argument("--password", default="", help="Contraseña del MySQL remoto.")
    group.add_argument("--database", help="Nombre de la base de datos remota.")


def resolve_database_url(args) -> str:
    if args.database_url:
        url = args.database_url
    elif args.host or args.user or args.password or args.database:
        missing = [
            name for name, value in (
                ("--host", args.host), ("--user", args.user), ("--database", args.database),
            ) if not value
        ]
        if missing:
            print(f"Faltan {', '.join(missing)} para armar la conexión remota.", file=sys.stderr)
            raise SystemExit(2)
        url = (
            f"mysql+pymysql://{quote_plus(args.user)}:{quote_plus(args.password or '')}"
            f"@{args.host}:{args.port}/{args.database}?charset=utf8mb4"
        )
    else:
        env_url = os.environ.get("REMOTE_DATABASE_URL", "")
        if env_url:
            url = env_url
        else:
            host = os.environ.get("REMOTE_MYSQL_HOST", "")
            user = os.environ.get("REMOTE_MYSQL_USER", "")
            database = os.environ.get("REMOTE_MYSQL_DATABASE", "")
            if not (host and user and database):
                print(
                    "Falta indicar el servidor remoto: pasa --database-url, o "
                    "--host/--user/--database, o exporta REMOTE_DATABASE_URL / "
                    "REMOTE_MYSQL_HOST+REMOTE_MYSQL_USER+REMOTE_MYSQL_DATABASE.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            port = os.environ.get("REMOTE_MYSQL_PORT", "3306")
            password = os.environ.get("REMOTE_MYSQL_PASSWORD", "")
            url = (
                f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
                f"@{host}:{port}/{database}?charset=utf8mb4"
            )

    if url.startswith("mysql://"):
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def describe_target(url: str) -> str:
    """Host/puerto/base para mostrar en el banner de confirmación, sin la
    credencial -evita que la contraseña quede en la terminal/logs."""
    try:
        from sqlalchemy.engine import make_url
        u = make_url(url)
        return f"{u.host}:{u.port or 3306}/{u.database} (usuario: {u.username})"
    except Exception:
        return "(no se pudo interpretar la URL para mostrarla; revisa --database-url)"


def confirm_target(url: str, *, yes: bool) -> bool:
    """Muestra a qué servidor se va a conectar y pide confirmación (salvo
    --yes). Devuelve False si el usuario canceló."""
    print(f"Servidor remoto: {describe_target(url)}")
    if yes:
        return True
    return input("¿Es este el destino correcto? [s/N] ").strip().lower() in ("s", "si", "sí", "y")


def build_remote_app(database_url: str):
    """App de Flask aislada, apuntando solo a `database_url`. TESTING=True
    salta las exigencias de producción (secretos fuertes, cookies https,
    etc.) que no aplican a un script de línea de comandos que nunca sirve
    HTTP."""
    return app_module.create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "UPLOADS_ROOT": app_module.app.config["UPLOADS_ROOT"],
    })
