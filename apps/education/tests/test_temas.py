import json
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Material, Periodo, Tema, TIPO_ORACION


def _make_periodo(nombre="I BIMESTRE", anio=None, days_from_today=0):
    anio = anio or date.today().year
    periodo = Periodo(
        nombre=nombre,
        anio=anio,
        fecha_inicio=date.today() + timedelta(days=days_from_today - 1),
        fecha_fin=date.today() + timedelta(days=days_from_today + 1),
    )
    db.session.add(periodo)
    db.session.commit()
    return periodo


def test_temas_page_defaults_to_the_active_periodo_by_date(app, client):
    with app.app_context():
        active = _make_periodo(nombre="Activo")
        active_id = active.id
        other = _make_periodo(nombre="Otro", days_from_today=200)
        db.session.add(Tema(nombre="Del activo", fk_user="DOC-TEST-1", id_periodo=active_id))
        db.session.add(Tema(nombre="Del otro", fk_user="DOC-TEST-1", id_periodo=other.id))
        db.session.commit()

    response = client.get("/temas")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Del activo" in html
    assert "Del otro" not in html


def test_temas_page_periodo_query_param_overrides_the_default(app, client):
    with app.app_context():
        active_id = _make_periodo(nombre="Activo").id
        other = _make_periodo(nombre="Otro", days_from_today=200)
        db.session.add(Tema(nombre="Del activo", fk_user="DOC-TEST-1", id_periodo=active_id))
        db.session.add(Tema(nombre="Del otro", fk_user="DOC-TEST-1", id_periodo=other.id))
        db.session.commit()
        other_id = other.id

    response = client.get(f"/temas?periodo={other_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Del otro" in html
    assert "Del activo" not in html


def test_temas_page_empty_periodo_param_shows_all_periods(app, client):
    with app.app_context():
        active_id = _make_periodo(nombre="Activo").id
        other = _make_periodo(nombre="Otro", days_from_today=200)
        db.session.add(Tema(nombre="Del activo", fk_user="DOC-TEST-1", id_periodo=active_id))
        db.session.add(Tema(nombre="Del otro", fk_user="DOC-TEST-1", id_periodo=other.id))
        db.session.commit()

    response = client.get("/temas?periodo=")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Del activo" in html
    assert "Del otro" in html


def test_temas_page_never_shows_another_teachers_temas(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id
        db.session.add(Tema(nombre="Mío", fk_user="DOC-TEST-1", id_periodo=periodo_id))
        db.session.add(Tema(nombre="Ajeno", fk_user="OTRA-DOCENTE", id_periodo=periodo_id))
        db.session.commit()

    response = client.get("/temas?periodo=")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Mío" in html
    assert "Ajeno" not in html


def test_create_tema_persists_it_for_the_current_teacher(app, client):
    with app.app_context():
        periodo = _make_periodo()
        periodo_id = periodo.id

    response = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": str(periodo_id)})

    assert response.status_code == 200
    body = response.get_json()
    assert body["nombre"] == "Animales"
    with app.app_context():
        tema = db.session.get(Tema, body["id"])
        assert tema.fk_user == "DOC-TEST-1"
        assert tema.id_periodo == periodo_id


def test_create_tema_requires_a_valid_periodo(app, client):
    response = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": "999999"})

    assert response.status_code == 400


def test_create_tema_rejects_duplicate_name_in_the_same_periodo(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id

    first = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": str(periodo_id)})
    second = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": str(periodo_id)})

    assert first.status_code == 200
    assert second.status_code == 409


def test_create_tema_allows_same_name_in_a_different_periodo(app, client):
    with app.app_context():
        periodo_a = _make_periodo(nombre="I BIMESTRE").id
        periodo_b = _make_periodo(nombre="II BIMESTRE", days_from_today=100).id

    first = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": str(periodo_a)})
    second = client.post("/api/temas", data={"nombre": "Animales", "id_periodo": str(periodo_b)})

    assert first.status_code == 200
    assert second.status_code == 200


def test_rename_tema_updates_the_name(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_id)
        db.session.add(tema)
        db.session.commit()
        tema_id = tema.id

    response = client.patch(f"/api/temas/{tema_id}", data={"nombre": "Animales de granja"})

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Tema, tema_id).nombre == "Animales de granja"


def test_a_teacher_cannot_see_rename_or_delete_another_teachers_tema(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id
        ajeno = Tema(nombre="Ajeno", fk_user="OTRA-DOCENTE", id_periodo=periodo_id)
        db.session.add(ajeno)
        db.session.commit()
        ajeno_id = ajeno.id

    rename = client.patch(f"/api/temas/{ajeno_id}", data={"nombre": "Robado"})
    delete = client.delete(f"/api/temas/{ajeno_id}")

    assert rename.status_code == 404
    assert delete.status_code == 404
    with app.app_context():
        # Sigue intacto: ni el nombre cambió ni la fila desapareció.
        tema = db.session.get(Tema, ajeno_id)
        assert tema is not None
        assert tema.nombre == "Ajeno"


def test_delete_tema_without_material_removes_it(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_id)
        db.session.add(tema)
        db.session.commit()
        tema_id = tema.id

    response = client.delete(f"/api/temas/{tema_id}")

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Tema, tema_id) is None


def test_delete_tema_with_assigned_material_is_blocked(app, client):
    with app.app_context():
        periodo_id = _make_periodo().id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_id)
        db.session.add(tema)
        db.session.flush()
        material = Material(
            nombre_material="Cuento de prueba",
            tipo_material=TIPO_ORACION,
            path_preguntas="[]",
            fk_user="DOC-TEST-1",
            id_periodo=periodo_id,
            id_tema=tema.id,
        )
        db.session.add(material)
        db.session.commit()
        tema_id = tema.id

    response = client.delete(f"/api/temas/{tema_id}")

    assert response.status_code == 409
    with app.app_context():
        # No se pierde el tema ni el material queda huérfano.
        assert db.session.get(Tema, tema_id) is not None


def test_delete_tema_handles_a_material_assigned_after_the_count_check(app, client, monkeypatch):
    """Simula la ventana de carrera entre el `.count()` de delete_tema y su
    commit: otra petición asigna un material al tema justo después de esa
    lectura. La FK de material.id_tema (con FK enforcement activo en SQLite,
    ver extensions.py) debe convertir eso en un 409 legible, no en un 500 ni
    en un tema borrado con un material huérfano apuntándole."""
    with app.app_context():
        periodo_id = _make_periodo().id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_id)
        db.session.add(tema)
        db.session.flush()
        material = Material(
            nombre_material="Cuento de prueba",
            tipo_material=TIPO_ORACION,
            path_preguntas="[]",
            fk_user="DOC-TEST-1",
            id_periodo=periodo_id,
            id_tema=tema.id,
        )
        db.session.add(material)
        db.session.commit()
        tema_id = tema.id
        material_id = material.id

    # El material ya existe, pero se hace mentir al `.count()` de la ruta
    # para reproducir la lectura obsoleta ("0 materiales") que dispararía el
    # borrado si no hubiera una FK real detrás.
    monkeypatch.setattr(db.Query, "count", lambda self: 0)

    response = client.delete(f"/api/temas/{tema_id}")

    assert response.status_code == 409
    assert "no se puede eliminar" in response.get_json()["error"]
    with app.app_context():
        # Ni el tema desapareció ni el material quedó apuntando a un id que
        # ya no existe.
        assert db.session.get(Tema, tema_id) is not None
        assert db.session.get(Material, material_id).id_tema == tema_id


def test_sqlite_foreign_keys_are_actually_enforced(app):
    """Ancla el fix de extensions.py: sin `PRAGMA foreign_keys=ON`, SQLite
    deja pasar un id_tema huérfano en silencio (comportamiento que MySQL, la
    base de producción, sí rechazaría) y el resto de las protecciones de esta
    suite serían pura ilusión en las pruebas."""
    with app.app_context():
        material = Material(
            nombre_material="Huérfano",
            tipo_material=TIPO_ORACION,
            path_preguntas="[]",
            fk_user="DOC-TEST-1",
            id_tema=999999,
        )
        db.session.add(material)
        try:
            with pytest.raises(IntegrityError):
                db.session.commit()
        finally:
            db.session.rollback()


def test_saving_material_with_a_tema_from_another_teacher_is_rejected(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_id = _make_periodo().id
        ajeno = Tema(nombre="Ajeno", fk_user="OTRA-DOCENTE", id_periodo=periodo_id)
        db.session.add(ajeno)
        db.session.commit()
        ajeno_id = ajeno.id
        periodo = periodo_id

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Con tema ajeno",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": str(periodo),
            "id_tema": str(ajeno_id),
        },
    )

    assert response.status_code == 400


def test_saving_material_with_a_tema_from_a_different_periodo_is_rejected(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_a = _make_periodo(nombre="I BIMESTRE").id
        periodo_b = _make_periodo(nombre="II BIMESTRE", days_from_today=100).id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_a)
        db.session.add(tema)
        db.session.commit()
        tema_id = tema.id

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Tema de otro periodo",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": str(periodo_b),
            "id_tema": str(tema_id),
        },
    )

    assert response.status_code == 400


def test_saving_material_with_a_valid_matching_tema_succeeds(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_id = _make_periodo().id
        tema = Tema(nombre="Animales", fk_user="DOC-TEST-1", id_periodo=periodo_id)
        db.session.add(tema)
        db.session.commit()
        tema_id = tema.id
        periodo = periodo_id

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Con tema válido",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": str(periodo),
            "id_tema": str(tema_id),
        },
    )

    assert response.status_code == 200
    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        assert material.id_tema == tema_id
