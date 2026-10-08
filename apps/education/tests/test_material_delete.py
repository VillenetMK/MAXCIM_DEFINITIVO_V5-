import os
import shutil
import uuid

from extensions import db
from models import Interaccion, Material, TIPO_CUENTO, TIPO_ORACION


TEST_TEACHER_ID = "DOC-TEST-1"
OTHER_TEACHER_ID = "DOC-OTHER-1"


def seed_material(**overrides):
    defaults = dict(
        nombre_material="El bosque que escucha",
        tipo_material=TIPO_CUENTO,
        path_audio="fixtures/audio.wav",
        path_texto="fixtures/texto.txt",
        path_texto_resumen="fixtures/resumen.txt",
        path_preguntas="fixtures/preguntas.json",
        fk_user=TEST_TEACHER_ID,
    )
    defaults.update(overrides)
    material = Material(**defaults)
    db.session.add(material)
    db.session.commit()
    return material


def register_interaction(material_id, **overrides):
    payload = dict(
        id_material=material_id,
        fk_alumno="ALU-TEST-1",
        pregunta="¿Qué hizo Luna para ayudar?",
        respuesta="La escuchó y esperó a que terminara.",
        path_audio_rpta="uploads/test/respuesta.wav",
        apreciacion_robot="Respuesta clara y completa.",
        rpta_correcta=True,
    )
    payload.update(overrides)
    interaction = Interaccion(**payload)
    db.session.add(interaction)
    db.session.commit()
    return interaction


def test_delete_removes_a_material_owned_by_the_teacher(app, client):
    with app.app_context():
        material_id = seed_material(
            nombre_material="Oraciones sueltas",
            tipo_material=TIPO_ORACION,
            path_audio=None,
            path_texto=None,
            path_texto_resumen=None,
            path_preguntas="Una oración.",
        ).id

    response = client.delete(f"/api/material/{material_id}")

    assert response.status_code == 200
    assert response.get_json() == {"deleted": True}
    with app.app_context():
        assert db.session.get(Material, material_id) is None


def test_delete_removes_the_cuento_upload_folder(app, client):
    # A diferencia de otros tests de este proyecto, este sí ejercita el
    # borrado real de archivos: crea una carpeta bajo UPLOADS_ROOT con un
    # nombre de UUID (el mismo formato que usa save_material) y verifica que
    # el endpoint la elimine.
    folder_name = uuid.uuid4().hex
    material_dir = os.path.join(app.config["UPLOADS_ROOT"], folder_name)
    os.makedirs(material_dir, exist_ok=True)
    for filename in ("texto.txt", "audio.wav"):
        with open(os.path.join(material_dir, filename), "w") as f:
            f.write("contenido de prueba")

    with app.app_context():
        material_id = seed_material(
            path_texto=f"uploads/{folder_name}/texto.txt",
            path_audio=f"uploads/{folder_name}/audio.wav",
            path_texto_resumen=f"uploads/{folder_name}/resumen.txt",
            path_preguntas=f"uploads/{folder_name}/preguntas.json",
        ).id

    try:
        response = client.delete(f"/api/material/{material_id}")
        assert response.status_code == 200
        assert not os.path.isdir(material_dir)
    finally:
        # Red de seguridad si la aserción fallara antes de limpiar.
        if os.path.isdir(material_dir):
            shutil.rmtree(material_dir, ignore_errors=True)


def test_delete_never_removes_the_uploads_root_from_a_crafted_anchor(app, client):
    # Un path_texto manipulado sin subcarpeta (dirname == UPLOADS_ROOT) no debe
    # provocar el borrado recursivo de todo el árbol de materiales.
    root = app.config["UPLOADS_ROOT"]
    other = os.path.join(root, "otro-material", "texto.txt")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    with open(other, "w") as f:
        f.write("no me borres")
    with app.app_context():
        material_id = seed_material(
            path_texto="uploads/texto.txt",
            path_audio=None,
            path_texto_resumen=None,
            path_preguntas="uploads/preguntas.json",
        ).id

    response = client.delete(f"/api/material/{material_id}")

    assert response.status_code == 200
    assert os.path.isdir(root)
    assert os.path.isfile(other)


def test_delete_is_blocked_when_the_material_has_interactions(app, client):
    with app.app_context():
        material_id = seed_material().id
        register_interaction(material_id)

    response = client.delete(f"/api/material/{material_id}")

    assert response.status_code == 409
    assert "interacci" in response.get_json()["error"].lower()
    with app.app_context():
        assert db.session.get(Material, material_id) is not None


def test_delete_rejects_a_material_owned_by_another_teacher(app, client):
    with app.app_context():
        material_id = seed_material(fk_user=OTHER_TEACHER_ID).id

    response = client.delete(f"/api/material/{material_id}")

    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(Material, material_id) is not None


def test_delete_rejects_an_unknown_material_id(client):
    response = client.delete("/api/material/999999")

    assert response.status_code == 404
