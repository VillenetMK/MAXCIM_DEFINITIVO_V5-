"""Administra los periodos académicos (bimestres) de la tabla `periodo`.

Los periodos no se crean desde la consola de la docente: hoy son 4 fechas
fijas que el colegio publica una vez al año (ver migrations/004_periodo.sql
y docs/integration-contract.md, sección 4). Este script es la forma prevista
de agregar el calendario del año siguiente, o de corregir una fecha, sin
escribir SQL a mano.

Uso:
    # Listar los periodos existentes
    python manage_periodos.py listar

    # Agregar el calendario de un año nuevo (una llamada por bimestre)
    python manage_periodos.py agregar "I BIMESTRE" 2027 2027-03-01 2027-05-07
    python manage_periodos.py agregar "II BIMESTRE" 2027 2027-05-10 2027-07-23
    python manage_periodos.py agregar "III BIMESTRE" 2027 2027-08-02 2027-10-08
    python manage_periodos.py agregar "IV BIMESTRE" 2027 2027-10-11 2027-12-17

    # Corregir fechas o nombre de uno existente (por id; ver "listar")
    python manage_periodos.py editar 5 --fecha-inicio 2027-03-02 --fecha-fin 2027-05-08
    python manage_periodos.py editar 5 --nombre "I BIMESTRE"

    # Borrar uno (se niega si ya tiene material o interacciones asociadas)
    python manage_periodos.py borrar 5

Dentro de Docker Compose:
    docker compose exec app python manage_periodos.py listar
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from app import app
from extensions import db
from models import Periodo


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"fecha inválida: '{value}' (usa AAAA-MM-DD, ej. 2027-03-01)"
        )


def do_listar(args) -> int:
    periodos = Periodo.query.order_by(Periodo.anio, Periodo.fecha_inicio).all()
    if not periodos:
        print("No hay periodos registrados todavía.")
        return 0
    for p in periodos:
        materiales = len(p.materiales)
        interacciones = len(p.interacciones)
        print(
            f"{p.id:>3}  {p.anio}  {p.nombre:<15} "
            f"{p.fecha_inicio.isoformat()} -> {p.fecha_fin.isoformat()}  "
            f"({materiales} material(es), {interacciones} interacción(es))"
        )
    return 0


def do_agregar(args) -> int:
    if args.fecha_fin < args.fecha_inicio:
        print("La fecha de fin no puede ser anterior a la de inicio.", file=sys.stderr)
        return 2

    existente = Periodo.query.filter_by(nombre=args.nombre, anio=args.anio).first()
    if existente:
        print(
            f"Ya existe '{args.nombre}' {args.anio} (id={existente.id}, "
            f"{existente.fecha_inicio} -> {existente.fecha_fin}). Usa 'editar' si quieres corregirlo.",
            file=sys.stderr,
        )
        return 2

    # Aviso, no bloqueo: dos periodos con fechas cruzadas no rompe nada a
    # nivel de datos, pero probablemente sea un error de tipeo.
    solapado = Periodo.query.filter(
        Periodo.fecha_inicio <= args.fecha_fin,
        Periodo.fecha_fin >= args.fecha_inicio,
    ).first()
    if solapado:
        print(
            f"Aviso: se solapa con '{solapado.nombre}' {solapado.anio} "
            f"({solapado.fecha_inicio} -> {solapado.fecha_fin})."
        )

    periodo = Periodo(
        nombre=args.nombre,
        anio=args.anio,
        fecha_inicio=args.fecha_inicio,
        fecha_fin=args.fecha_fin,
    )
    db.session.add(periodo)
    db.session.commit()
    print(
        f"Creado id={periodo.id}: {periodo.nombre} {periodo.anio} "
        f"({periodo.fecha_inicio} -> {periodo.fecha_fin})"
    )
    return 0


def do_editar(args) -> int:
    periodo = db.session.get(Periodo, args.id)
    if not periodo:
        print(f"No existe un periodo con id={args.id}. Usa 'listar' para ver los ids.", file=sys.stderr)
        return 2

    nueva_inicio = args.fecha_inicio if args.fecha_inicio is not None else periodo.fecha_inicio
    nueva_fin = args.fecha_fin if args.fecha_fin is not None else periodo.fecha_fin
    if nueva_fin < nueva_inicio:
        print("La fecha de fin no puede ser anterior a la de inicio.", file=sys.stderr)
        return 2

    if not any([args.nombre, args.anio, args.fecha_inicio, args.fecha_fin]):
        print("Indica al menos un cambio: --nombre, --anio, --fecha-inicio o --fecha-fin.", file=sys.stderr)
        return 2

    if args.nombre is not None:
        periodo.nombre = args.nombre
    if args.anio is not None:
        periodo.anio = args.anio
    periodo.fecha_inicio = nueva_inicio
    periodo.fecha_fin = nueva_fin
    db.session.commit()
    print(
        f"Actualizado id={periodo.id}: {periodo.nombre} {periodo.anio} "
        f"({periodo.fecha_inicio} -> {periodo.fecha_fin})"
    )
    return 0


def do_borrar(args) -> int:
    periodo = db.session.get(Periodo, args.id)
    if not periodo:
        print(f"No existe un periodo con id={args.id}. Usa 'listar' para ver los ids.", file=sys.stderr)
        return 2

    materiales = len(periodo.materiales)
    interacciones = len(periodo.interacciones)
    if materiales or interacciones:
        print(
            f"No se puede borrar: tiene {materiales} material(es) y "
            f"{interacciones} interacción(es) asociadas. Reasígnalas antes "
            "(quedarían con id_periodo NULL, 'sin periodo').",
            file=sys.stderr,
        )
        return 2

    if not args.yes:
        respuesta = input(
            f"Se borrará '{periodo.nombre}' {periodo.anio} "
            f"({periodo.fecha_inicio} -> {periodo.fecha_fin}). ¿Continuar? [s/N] "
        )
        if respuesta.strip().lower() not in ("s", "si", "sí", "y"):
            print("Cancelado.")
            return 1

    db.session.delete(periodo)
    db.session.commit()
    print(f"Borrado id={args.id}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Administra los periodos académicos (bimestres).")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("listar", help="Lista los periodos registrados.")

    p_agregar = sub.add_parser("agregar", help="Crea un periodo nuevo.")
    p_agregar.add_argument("nombre", help='Ej. "I BIMESTRE".')
    p_agregar.add_argument("anio", type=int, help="Ej. 2027.")
    p_agregar.add_argument("fecha_inicio", type=_parse_date, help="AAAA-MM-DD.")
    p_agregar.add_argument("fecha_fin", type=_parse_date, help="AAAA-MM-DD.")

    p_editar = sub.add_parser("editar", help="Corrige nombre/año/fechas de un periodo existente.")
    p_editar.add_argument("id", type=int, help="Id del periodo (ver 'listar').")
    p_editar.add_argument("--nombre")
    p_editar.add_argument("--anio", type=int)
    p_editar.add_argument("--fecha-inicio", dest="fecha_inicio", type=_parse_date)
    p_editar.add_argument("--fecha-fin", dest="fecha_fin", type=_parse_date)

    p_borrar = sub.add_parser("borrar", help="Borra un periodo sin material ni interacciones asociadas.")
    p_borrar.add_argument("id", type=int, help="Id del periodo (ver 'listar').")
    p_borrar.add_argument("-y", "--yes", action="store_true", help="No pedir confirmación.")

    args = parser.parse_args()
    handlers = {
        "listar": do_listar,
        "agregar": do_agregar,
        "editar": do_editar,
        "borrar": do_borrar,
    }

    with app.app_context():
        return handlers[args.comando](args)


if __name__ == "__main__":
    raise SystemExit(main())
