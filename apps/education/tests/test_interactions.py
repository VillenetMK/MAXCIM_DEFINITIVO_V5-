import io
import json
import wave

import pytest

import app as app_module
from extensions import db
from models import Interaccion, Material, TIPO_CUENTO, TIPO_ORACION


TEST_TEACHER_ID = "DOC-TEST-1"


def make_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 1600)  # 0.1s de silencio
    return buffer.getvalue()


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


def register_interaction(
    client, material_id, include_audio=True, audio_bytes=None, **overrides
):
    data = {
        "id_material": material_id,
        "fk_alumno": "ALU-TEST-1",
        "pregunta": "¿Qué hizo Luna para ayudar?",
        "respuesta": "La escuchó y esperó a que terminara.",
        "apreciacion_robot": "Respuesta clara y completa.",
        "rpta_correcta": True,
    }
    data.update(overrides)
    # Sin `id_material` (en vez de null: multipart no tiene ese concepto), el
    # turno queda como conversación libre.
    if data.get("id_material") is None:
        data.pop("id_material", None)
    else:
        data["id_material"] = str(data["id_material"])
    data["rpta_correcta"] = str(data["rpta_correcta"])
    if include_audio:
        data["audio_rpta"] = (io.BytesIO(audio_bytes or make_wav_bytes()), "respuesta.wav")
    return client.post("/api/interacciones", data=data, content_type="multipart/form-data")


def test_registrar_interaccion_creates_a_record(app, client):
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(client, material_id)
    assert response.status_code == 201
    data = response.get_json()
    assert data["id_material"] == material_id
    assert data["fk_alumno"] == "ALU-TEST-1"
    assert data["rpta_correcta"] is True

    with app.app_context():
        assert Interaccion.query.count() == 1


def test_registrar_interaccion_without_material_is_a_conversation(app, client):
    response = register_interaction(client, None)
    assert response.status_code == 201
    data = response.get_json()
    assert data["id_material"] is None

    with app.app_context():
        interaccion = Interaccion.query.one()
        assert interaccion.id_material is None
        assert interaccion.material is None


def test_registrar_interaccion_still_rejects_a_non_numeric_material(client):
    response = register_interaction(client, "abc")
    assert response.status_code == 400
    assert "id_material" in response.get_json()["error"]


def test_registrar_interaccion_requires_all_fields(app, client):
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(client, material_id, pregunta="")
    assert response.status_code == 400
    assert "pregunta" in response.get_json()["error"]


def test_registrar_interaccion_rejects_an_invalid_bool(app, client):
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(client, material_id, rpta_correcta="tal vez")
    assert response.status_code == 400


def test_registrar_interaccion_rejects_unknown_material(client):
    response = register_interaction(client, 999999)
    assert response.status_code == 404


def test_registrar_interaccion_requires_the_audio_file(app, client):
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(client, material_id, include_audio=False)
    assert response.status_code == 400
    assert "audio_rpta" in response.get_json()["error"]

    with app.app_context():
        assert Interaccion.query.count() == 0


def test_registrar_interaccion_rejects_a_non_wav_audio_file(app, client):
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(
        client, material_id, audio_bytes=b"esto no es un wav"
    )
    assert response.status_code == 400
    assert "WAV" in response.get_json()["error"]

    with app.app_context():
        assert Interaccion.query.count() == 0


def test_registrar_interaccion_stores_the_audio_and_exposes_a_download_url(
    app, client, tmp_path, monkeypatch
):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        material_id = seed_material().id

    response = register_interaction(client, material_id)
    assert response.status_code == 201
    body = response.get_json()
    assert "path_audio_rpta" not in body
    audio_url = body["audio_rpta_url"]
    assert audio_url == f"http://localhost/api/interacciones/{body['id']}/audio"

    with app.app_context():
        interaccion = db.session.get(Interaccion, body["id"])
        stored_path = interaccion.path_audio_rpta
        assert stored_path.startswith("uploads/")
        assert (tmp_path / stored_path.removeprefix("uploads/")).is_file()

    audio_path = audio_url.removeprefix("http://localhost")
    # Sin identificar a la docente, 400 (hallazgo A-03).
    assert client.get(audio_path).status_code == 400
    download = client.get(f"{audio_path}?teacher_id={TEST_TEACHER_ID}")
    assert download.status_code == 200
    assert download.mimetype == "audio/wav"


@pytest.mark.parametrize(
    "bad_secret",
    ["", "cambia-este-secreto-compartido", "CAMBIA-ESTE-SECRETO-COMPARTIDO", "corto"],
)
def test_create_app_rejects_weak_webhook_secret_in_production(monkeypatch, bad_secret):
    monkeypatch.setattr(app_module, "gemini_client", None)
    with pytest.raises(RuntimeError):
        app_module.create_app({
            "TESTING": False,
            "DEMO_MODE": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "MAXCIM_WEBHOOK_SECRET": bad_secret,
        })


def test_create_app_refuses_demo_mode_on_a_remote_database(monkeypatch):
    monkeypatch.setattr(app_module, "gemini_client", None)
    with pytest.raises(RuntimeError):
        app_module.create_app({
            "TESTING": False,
            "DEMO_MODE": True,
            "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://root:x@db:3306/maxcim_app",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
        })


def test_create_app_forces_https_session_in_production(monkeypatch):
    from werkzeug.middleware.proxy_fix import ProxyFix

    monkeypatch.setattr(app_module, "gemini_client", None)
    monkeypatch.delenv("ALLOW_INSECURE_SESSION_COOKIE", raising=False)
    application = app_module.create_app({
        "TESTING": False,
        "DEMO_MODE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "MAXCIM_WEBHOOK_SECRET": "robot-secret-0123456789abcdef0123456789abcdef",
        "SECRET_KEY": "x" * 40,
        "SESSION_TOKEN_ENCRYPTION_KEY": "x" * 40,
        # Aunque el .env diga false, no-demo lo fuerza a true.
        "SESSION_COOKIE_SECURE": False,
    })
    assert application.config["SESSION_COOKIE_SECURE"] is True
    assert application.config["PREFERRED_URL_SCHEME"] == "https"
    assert isinstance(application.wsgi_app, ProxyFix)


def test_allow_insecure_session_cookie_escape_hatch(monkeypatch):
    """ALLOW_INSECURE_SESSION_COOKIE=true respeta SESSION_COOKIE_SECURE=false
    y no fuerza el esquema https (solo para desarrollo local sobre http://)."""
    monkeypatch.setattr(app_module, "gemini_client", None)
    monkeypatch.setenv("ALLOW_INSECURE_SESSION_COOKIE", "true")
    application = app_module.create_app({
        "TESTING": False,
        "DEMO_MODE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "MAXCIM_WEBHOOK_SECRET": "robot-secret-0123456789abcdef0123456789abcdef",
        "SECRET_KEY": "x" * 40,
        "SESSION_TOKEN_ENCRYPTION_KEY": "x" * 40,
        "SESSION_COOKIE_SECURE": False,
    })
    assert application.config["SESSION_COOKIE_SECURE"] is False
    assert application.config.get("PREFERRED_URL_SCHEME") != "https"
    health = application.test_client().get("/health")
    assert "Strict-Transport-Security" not in health.headers

    # Con el flag activo pero SESSION_COOKIE_SECURE=true, sigue siendo Secure.
    monkeypatch.setattr(app_module, "gemini_client", None)
    secure = app_module.create_app({
        "TESTING": False,
        "DEMO_MODE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "MAXCIM_WEBHOOK_SECRET": "robot-secret-0123456789abcdef0123456789abcdef",
        "SECRET_KEY": "x" * 40,
        "SESSION_TOKEN_ENCRYPTION_KEY": "x" * 40,
        "SESSION_COOKIE_SECURE": True,
    })
    assert secure.config["SESSION_COOKIE_SECURE"] is True


def test_registrar_interaccion_requires_webhook_secret_in_production(monkeypatch):
    monkeypatch.setattr(app_module, "gemini_client", None)
    application = app_module.create_app({
        "TESTING": False,
        "DEMO_MODE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "MAXCIM_WEBHOOK_SECRET": "robot-secret-0123456789abcdef0123456789abcdef",
    })
    with application.app_context():
        db.create_all()
        material_id = seed_material().id

    test_client = application.test_client()
    data = {
        "id_material": str(material_id),
        "fk_alumno": "ALU-TEST-1",
        "pregunta": "¿Qué aprendiste?",
        "respuesta": "A escuchar antes de responder.",
        "apreciacion_robot": "Bien hecho.",
        "rpta_correcta": "true",
        "audio_rpta": (io.BytesIO(make_wav_bytes()), "respuesta.wav"),
    }
    assert test_client.post("/api/interacciones", data=data).status_code == 401
    data["audio_rpta"] = (io.BytesIO(make_wav_bytes()), "respuesta.wav")
    accepted = test_client.post(
        "/api/interacciones",
        data=data,
        headers={"X-MAXCIM-Webhook-Secret": "robot-secret-0123456789abcdef0123456789abcdef"},
    )
    assert accepted.status_code == 201

    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_list_interacciones_filters_by_material_and_alumno(app, client):
    with app.app_context():
        material_id = seed_material().id
        other_material_id = seed_material(nombre_material="Otro cuento").id

    register_interaction(client, material_id, fk_alumno="ALU-A")
    register_interaction(client, material_id, fk_alumno="ALU-B")
    register_interaction(client, other_material_id, fk_alumno="ALU-A")

    tid = f"teacher_id={TEST_TEACHER_ID}"

    by_material = client.get(f"/api/interacciones?{tid}&id_material={material_id}")
    assert by_material.status_code == 200
    assert len(by_material.get_json()) == 2

    by_alumno = client.get(f"/api/interacciones?{tid}&fk_alumno=ALU-A")
    assert by_alumno.status_code == 200
    assert len(by_alumno.get_json()) == 2

    both = client.get(
        f"/api/interacciones?{tid}&id_material={material_id}&fk_alumno=ALU-A"
    )
    assert both.status_code == 200
    assert len(both.get_json()) == 1

    # Sin identificar a la docente, 400 (hallazgo A-03).
    assert client.get(
        f"/api/interacciones?id_material={material_id}"
    ).status_code == 400


def test_get_cuento_material_round_trips_through_robot_api(
    app, client, tmp_path, monkeypatch
):
    material_dir = tmp_path / "material"
    material_dir.mkdir()
    questions = [{"pregunta": "¿Quién es el personaje?", "respuesta_esperada": "Luna"}]
    (material_dir / "preguntas.json").write_text(
        json.dumps(questions, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))

    with app.app_context():
        material = seed_material(
            tipo_material=TIPO_CUENTO,
            path_texto="material/texto.txt",
            path_texto_resumen="material/resumen.txt",
            path_audio="material/audio.wav",
            path_preguntas="material/preguntas.json",
        )
        material_id = material.id
        fecha_subido = material.fecha_subido.isoformat()

    response = client.get(f"/api/materials/{material_id}?teacher_id={TEST_TEACHER_ID}")
    assert response.status_code == 200
    base = f"http://localhost/api/materials/{material_id}"
    assert response.get_json() == {
        "id": material_id,
        "titulo": "El bosque que escucha",
        "tipo_material": TIPO_CUENTO,
        "fecha_subido": fecha_subido,
        "fk_user": TEST_TEACHER_ID,
        "docente": None,
        "id_periodo": None,
        "id_tema": None,
        "periodo": None,
        "tema": None,
        # Los *_url apuntan al endpoint autenticado, no a /static/.
        "texto_completo_url": f"{base}/texto",
        "texto_resumen_url": f"{base}/resumen",
        "audio_completo_url": f"{base}/audio",
        "preguntas_url": f"{base}/preguntas",
        "preguntas": questions,
        # Un cuento sin escenas ilustradas/narradas las expone vacías.
        "escenas": [],
    }


def test_get_oracion_material_round_trips_through_robot_api(app, client):
    # A legacy row stores plain text in path_preguntas; the robot API still
    # serves it as a list, split into individual sentences on the fly.
    with app.app_context():
        material = seed_material(
            nombre_material="Oraciones de práctica",
            tipo_material=TIPO_ORACION,
            path_preguntas="La luna brilla.\nEl río canta.",
            path_texto=None,
            path_texto_resumen=None,
            path_audio=None,
        )
        material_id = material.id
        fecha_subido = material.fecha_subido.isoformat()

    response = client.get(f"/api/materials/{material_id}?teacher_id={TEST_TEACHER_ID}")
    assert response.status_code == 200
    assert response.get_json() == {
        "id": material_id,
        "titulo": "Oraciones de práctica",
        "tipo_material": TIPO_ORACION,
        "fecha_subido": fecha_subido,
        "fk_user": TEST_TEACHER_ID,
        "docente": None,
        "id_periodo": None,
        "id_tema": None,
        "periodo": None,
        "tema": None,
        "oraciones": ["La luna brilla.", "El río canta."],
        "oraciones_url": None,
        "texto_completo_url": None,
        "texto_resumen_url": None,
        "audio_completo_url": None,
        "preguntas_url": None,
        "preguntas": [],
    }


def test_get_oracion_material_serves_the_json_list_written_on_save(
    app, client, tmp_path, monkeypatch, periodo_tema
):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    periodo_id, tema_id = periodo_tema()
    save = client.post(
        "/api/material/save",
        data={
            "tipo_material": TIPO_ORACION,
            "title": "Oraciones nuevas",
            "sentences_json": json.dumps(["Hoy llueve.", "Mañana saldrá el sol."]),
            "id_periodo": str(periodo_id),
            "id_tema": str(tema_id),
        },
    )
    assert save.status_code == 200
    material_id = save.get_json()["material_id"]

    body = client.get(
        f"/api/materials/{material_id}?teacher_id={TEST_TEACHER_ID}"
    ).get_json()
    assert body["oraciones"] == ["Hoy llueve.", "Mañana saldrá el sol."]
    assert body["oraciones_url"] == f"http://localhost/api/materials/{material_id}/oraciones"


def test_material_save_requires_reviewed_expected_answers(client, periodo_tema):
    periodo_id, tema_id = periodo_tema()
    response = client.post(
        "/api/material/save",
        data={
            "title": "Cuento",
            "transcribed_text": "Texto del cuento",
            "summary_text": "Resumen",
            "id_periodo": str(periodo_id),
            "id_tema": str(tema_id),
            "questions_json": json.dumps([{
                "tipo": "literal",
                "pregunta": "¿Quién es el personaje?",
                "respuesta_esperada": "",
            }]),
        },
    )
    assert response.status_code == 400
    assert "respuesta esperada" in response.get_json()["error"]
