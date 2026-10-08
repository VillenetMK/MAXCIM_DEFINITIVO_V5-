import argparse
import getpass
import os

from .store import ROLES, Store


def main():
    parser = argparse.ArgumentParser(description="Crear una cuenta MAXCIM sin contraseña por defecto")
    parser.add_argument("username")
    parser.add_argument("--role", choices=sorted(ROLES), default="admin")
    parser.add_argument("--database", default=os.getenv("MAXCIM_DATABASE", "var/team.db"))
    args = parser.parse_args()
    password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
    if password != getpass.getpass("Repetir contraseña: "):
        parser.error("Las contraseñas no coinciden")
    store = Store(args.database)
    try:
        store.create_user(args.username, password, args.role)
    finally:
        store.close()
    print("Cuenta creada.")


if __name__ == "__main__":
    main()
