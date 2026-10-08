from __future__ import annotations

from datetime import date

from models import Periodo


def periodo_for_date(fecha: date) -> Periodo | None:
    return Periodo.query.filter(
        Periodo.fecha_inicio <= fecha,
        Periodo.fecha_fin >= fecha,
    ).first()


def current_periodo() -> Periodo | None:
    # Centraliza la fecha actual para que todos los consumidores resuelvan el mismo bimestre.
    return periodo_for_date(date.today())
