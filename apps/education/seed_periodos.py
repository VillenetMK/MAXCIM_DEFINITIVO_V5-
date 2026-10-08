"""Siembra el calendario académico estándar (I-IV BIMESTRE) para uno o
varios años, para no tener que llamar a `manage_periodos.py agregar` cuatro
veces por año al preparar un entorno de pruebas.

No duplica su lógica: reutiliza `manage_periodos.do_agregar`/`do_borrar`
-misma validación (fechas cruzadas, solapes, nombre+año duplicado, y el
borrado se niega si el periodo ya tiene material o interacciones
asociadas)-, así que ambos scripts no pueden desincronizarse.

Uso:
    # Sembrar el calendario estándar de un año:
    python seed_periodos.py --anio 2027

    # Varios años de una sola vez:
    python seed_periodos.py --anio 2027 --anio 2028
    python seed_periodos.py --desde 2025 --hasta 2028

    # Borrar lo sembrado de un año (por periodo, se niega si ya tiene
    # material o interacciones asociadas -igual que `manage_periodos.py
    # borrar`; sin --yes pregunta una vez por periodo):
    python seed_periodos.py --undo --anio 2027

IMPORTANTE: las fechas de este calendario son una aproximación razonable
(marzo-diciembre, ~10 semanas por bimestre, calcada del ejemplo en
manage_periodos.py), no el calendario oficial del colegio. Si un año ya
tiene fechas reales publicadas, usa `manage_periodos.py editar` para
corregirlas en vez de volver a sembrar.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import manage_periodos as mp
from app import app
from models import Periodo

# (mes, día) de inicio/fin de cada bimestre, calcado del calendario de
# ejemplo en manage_periodos.py (docs/integration-contract.md, sección 4).
STANDARD_BIMESTERS = [
    ("I BIMESTRE", (3, 1), (5, 7)),
    ("II BIMESTRE", (5, 10), (7, 23)),
    ("III BIMESTRE", (8, 2), (10, 8)),
    ("IV BIMESTRE", (10, 11), (12, 17)),
]
STANDARD_NAMES = {nombre for nombre, _, _ in STANDARD_BIMESTERS}


class _Args:
    """Namespace mínimo con los atributos que esperan do_agregar/do_borrar
    de manage_periodos.py, sin pasar por argparse."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def do_seed(anios: list[int]) -> int:
    creados = 0
    for anio in anios:
        for nombre, inicio, fin in STANDARD_BIMESTERS:
            ns = _Args(
                nombre=nombre,
                anio=anio,
                fecha_inicio=date(anio, *inicio),
                fecha_fin=date(anio, *fin),
            )
            if mp.do_agregar(ns) == 0:
                creados += 1
    print(f"Listo: {creados} periodo(s) creado(s) (los que ya existían se dejaron intactos).")
    return 0


def do_undo(anios: list[int], yes: bool) -> int:
    periodos = (
        Periodo.query.filter(Periodo.anio.in_(anios), Periodo.nombre.in_(STANDARD_NAMES))
        .order_by(Periodo.anio, Periodo.fecha_inicio)
        .all()
    )
    if not periodos:
        print("No hay periodos sembrados que coincidan.")
        return 0
    borrados = 0
    for p in periodos:
        if mp.do_borrar(_Args(id=p.id, yes=yes)) == 0:
            borrados += 1
    print(f"Borrados {borrados}/{len(periodos)} periodo(s).")
    return 0


def resolve_years(args, parser: argparse.ArgumentParser | None = None) -> list[int]:
    """Combina --anio (repetible) y --desde/--hasta en una lista ordenada
    de años. Compartida con seed_periodos_remote.py para no duplicar esta
    validación. `parser` solo hace falta si quieres que los errores salgan
    como uso de argparse (`parser.error`); si no se pasa, se levanta
    SystemExit(2) con el mismo mensaje."""
    def fail(msg: str) -> None:
        if parser is not None:
            parser.error(msg)
        print(msg, file=sys.stderr)
        raise SystemExit(2)

    if bool(args.desde) != bool(args.hasta):
        fail("--desde y --hasta van juntos.")
    if args.desde and args.hasta and args.hasta < args.desde:
        fail("--hasta no puede ser anterior a --desde.")

    anios = sorted(set(args.anio or []) | (set(range(args.desde, args.hasta + 1)) if args.desde else set()))
    if not anios:
        fail("indica al menos --anio, o --desde/--hasta.")
    return anios


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Siembra o borra el calendario estándar de periodos (I-IV bimestre) para pruebas.",
    )
    parser.add_argument("--anio", type=int, action="append", help="Año a sembrar; repetible.")
    parser.add_argument("--desde", type=int, help="Primer año de un rango (junto con --hasta).")
    parser.add_argument("--hasta", type=int, help="Último año de un rango (junto con --desde).")
    parser.add_argument("--undo", action="store_true",
                        help="Borra el calendario estándar sembrado de esos años.")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="No pedir confirmación (al sembrar, o por cada periodo al borrar).")
    args = parser.parse_args()

    anios = resolve_years(args, parser)

    with app.app_context():
        if args.undo:
            return do_undo(anios, args.yes)

        print(f"Se sembrará el calendario estándar (I-IV bimestre) para: {', '.join(map(str, anios))}")
        if not args.yes:
            if input("¿Continuar? [s/N] ").strip().lower() not in ("s", "si", "sí", "y"):
                print("Cancelado.")
                return 1
        return do_seed(anios)


if __name__ == "__main__":
    raise SystemExit(main())
