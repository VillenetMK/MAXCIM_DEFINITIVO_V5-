import io
import json
import os
import wave
from types import SimpleNamespace

import pytest

import app as app_module
from extensions import db
from services.fish_audio import FishAudioError
from models import Material

# 24 kHz, 16 bits: 1 s de silencio por cada 48 000 bytes.
PCM_ONE_SECOND = b"\0" * (24_000 * 2)
PNG = b"\x89PNG\r\n\x1a\nfake-image"
STORY = "Luna salió al bosque. Encontró a un búho. Juntos regresaron a casa."
TEACHER_ID = "DOC-TEST-1"


class ScenesAndSpeechModels:
    """Falsos clientes de IA: Gemini (plan en JSON e imágenes) y Fish Audio
    (narración, vía `synthesize_pcm`). Un mismo objeto hace de los dos."""

    sample_rate = 24_000

    def __init__(self, plan=None, fail_audio_at=None):
        self.plan = plan if plan is not None else {
            "personaje": "a small orange fox",
            "escenas": [
                {"desde": 1, "descripcion": "Fox enters a forest."},
                {"desde": 2, "descripcion": "Fox meets an owl."},
                {"desde": 3, "descripcion": "Fox and owl walk home."},
            ],
        }
        self.fail_audio_at = fail_audio_at
        self.spoken = []

    def generate_content(self, *, contents, config, **_kwargs):
        if config.response_mime_type == "application/json":
            return SimpleNamespace(text=json.dumps(self.plan))
        part = SimpleNamespace(inline_data=SimpleNamespace(data=PNG))
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])

    def synthesize_pcm(self, text):
        self.spoken.append(text)
        if self.fail_audio_at == len(self.spoken) - 1:
            raise FishAudioError("tts caído")
        return PCM_ONE_SECOND


def install(monkeypatch, fake):
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    monkeypatch.setattr(app_module, "fish_client", fake)


@pytest.fixture()
def models(monkeypatch):
    fake = ScenesAndSpeechModels()
    install(monkeypatch, fake)
    return fake


def plan(client, story=STORY):
    return client.post("/api/story/scenes/plan", json={"story": story}).get_json()


def make_all_scenes(client, token, count=3):
    for index in range(count):
        image = client.post("/api/story/scenes/image", json={"token": token, "index": index})
        audio = client.post("/api/story/scenes/audio", json={"token": token, "index": index})
        assert image.status_code == 200 and audio.status_code == 200


def material_directory(app, material_id):
    with app.app_context():
        material = db.session.get(Material, material_id)
        # path_texto = "uploads/<uuid>/texto.txt"
        return os.path.join(app.config["UPLOADS_ROOT"], material.path_texto.split("/")[1])


def wav_bytes(seconds, rate=24_000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\0" * (rate * 2 * seconds))
    return buffer.getvalue()


def save_story(client, periodo_tema, *, full_audio=None, **extra):
    """Guarda un cuento. Con `scenes_token` no se envía audio completo (el
    servidor lo arma con las escenas); sin él sí, como el flujo anterior."""
    periodo_id, tema_id = periodo_tema(teacher_id=TEACHER_ID)
    data = {
        "tipo_material": "cuento",
        "title": "Luna y el bosque",
        "transcribed_text": STORY,
        "summary_text": "Luna y el búho.",
        "questions_json": json.dumps([{"pregunta": "¿Quién?", "respuesta_esperada": "Luna"}]),
        "id_periodo": str(periodo_id),
        "id_tema": str(tema_id),
        **extra,
    }
    if full_audio is None:
        full_audio = "scenes_token" not in extra
    if full_audio:
        data["audio_full"] = (io.BytesIO(wav_bytes(1)), "audio.wav")
    return client.post("/api/material/save", data=data, content_type="multipart/form-data")


# --- plan: las escenas cubren el cuento completo ------------------------------

def test_scene_texts_are_verbatim_consecutive_and_cover_the_whole_story(client, models):
    body = plan(client)
    assert " ".join(scene["texto"] for scene in body["scenes"]) == STORY


def test_plan_prompt_gives_the_ai_the_numbered_sentences(client, models):
    captured = []
    original = models.generate_content

    def spy(*, contents, config, **kwargs):
        if config.response_mime_type == "application/json":
            captured.append(contents)
        return original(contents=contents, config=config, **kwargs)

    models.generate_content = spy
    plan(client)
    assert "1. Luna salió al bosque." in captured[0]
    assert "3. Juntos regresaron a casa." in captured[0]


@pytest.mark.parametrize("escenas, expected", [
    # Sin ordenar, con duplicados y con números fuera de rango.
    ([{"desde": 3, "descripcion": "c"}, {"desde": 1, "descripcion": "a"},
      {"desde": 3, "descripcion": "otra c"}, {"desde": 99, "descripcion": "d"}],
     ["Luna salió al bosque. Encontró a un búho.", "Juntos regresaron a casa."]),
    # La IA no ilustró el inicio: se suma a la primera escena.
    ([{"desde": 2, "descripcion": "b"}, {"desde": 3, "descripcion": "c"}],
     ["Luna salió al bosque. Encontró a un búho.", "Juntos regresaron a casa."]),
    # Números como texto; entradas sin descripción o mal formadas se ignoran.
    ([{"desde": "1", "descripcion": "a"}, {"desde": "3", "descripcion": "c"},
      {"desde": 2, "descripcion": ""}, "basura", {"desde": True, "descripcion": "x"}],
     ["Luna salió al bosque. Encontró a un búho.", "Juntos regresaron a casa."]),
])
def test_scene_plan_is_repaired_when_the_ai_numbers_are_off(client, monkeypatch, escenas, expected):
    fake = ScenesAndSpeechModels(plan={"personaje": "a fox", "escenas": escenas})
    install(monkeypatch, fake)

    body = plan(client)
    assert [scene["texto"] for scene in body["scenes"]] == expected
    assert " ".join(scene["texto"] for scene in body["scenes"]) == STORY


def test_plan_normalizes_whitespace_and_paragraphs(client, models):
    body = plan(client, "Luna salió al bosque.\n\nEncontró   a un búho.\nJuntos regresaron a casa.")
    assert " ".join(scene["texto"] for scene in body["scenes"]) == STORY


def test_story_with_a_single_sentence_cannot_be_split_into_scenes(client, models):
    response = client.post("/api/story/scenes/plan", json={"story": "Luna salió al bosque."})
    assert response.status_code == 400
    assert "al menos" in response.get_json()["error"]


# --- audio por escena ----------------------------------------------------------

def test_each_scene_is_narrated_with_its_own_text(client, models):
    token = plan(client)["token"]
    make_all_scenes(client, token)

    assert len(models.spoken) == 3
    for spoken, texto in zip(models.spoken, [
        "Luna salió al bosque.", "Encontró a un búho.", "Juntos regresaron a casa.",
    ]):
        assert texto in spoken


def test_scene_audio_reports_its_duration_and_can_be_played(client, models):
    token = plan(client)["token"]
    response = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})

    assert response.status_code == 200
    body = response.get_json()
    assert body["index"] == 0
    assert body["duration_seconds"] == 1.0
    preview = client.get(body["audio_url"])
    assert preview.status_code == 200
    assert preview.mimetype == "audio/wav"
    assert preview.headers["Cache-Control"] == "no-store"
    with wave.open(io.BytesIO(preview.data), "rb") as f:
        assert f.getnframes() == 24_000


def test_scene_audio_can_be_regenerated(client, models):
    token = plan(client)["token"]
    for _ in range(2):
        assert client.post("/api/story/scenes/audio", json={"token": token, "index": 1}).status_code == 200
    assert len(models.spoken) == 2


def test_failed_scene_audio_can_be_retried(client, monkeypatch):
    fake = ScenesAndSpeechModels(fail_audio_at=0)
    install(monkeypatch, fake)
    token = plan(client)["token"]

    failed = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})
    assert failed.status_code == 502
    assert client.get(f"/api/story/scenes/audio-preview/{token}/0").status_code == 404
    assert client.post("/api/story/scenes/audio", json={"token": token, "index": 0}).status_code == 200


def test_failed_regeneration_keeps_the_previous_scene_audio(client, monkeypatch):
    fake = ScenesAndSpeechModels(fail_audio_at=1)
    install(monkeypatch, fake)
    token = plan(client)["token"]

    first = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})
    assert first.status_code == 200
    failed = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})
    assert failed.status_code == 502

    preview = client.get(f"/api/story/scenes/audio-preview/{token}/0")
    assert preview.status_code == 200
    with wave.open(io.BytesIO(preview.data), "rb") as f:
        assert f.getnframes() == 24_000


@pytest.mark.parametrize("index", [-1, 3, "0", True, None])
def test_scene_audio_rejects_invalid_index(client, models, index):
    token = plan(client)["token"]
    response = client.post("/api/story/scenes/audio", json={"token": token, "index": index})
    assert response.status_code == 400
    assert models.spoken == []


def test_scene_audio_rejects_unknown_token(client, models):
    for token in ("no-es-un-token", "0" * 32):
        assert client.post("/api/story/scenes/audio", json={"token": token, "index": 0}).status_code == 404
    assert client.get(f"/api/story/scenes/audio-preview/{'0' * 32}/0").status_code == 404


def test_scene_audio_works_in_demo_mode(app, client):
    app.config["DEMO_MODE"] = True
    token = plan(client)["token"]
    response = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})
    assert response.status_code == 200
    assert client.get(response.get_json()["audio_url"]).mimetype == "audio/wav"


def test_scene_audio_without_fish_or_demo_is_unavailable(app, client, monkeypatch):
    app.config["DEMO_MODE"] = True
    token = plan(client)["token"]
    app.config["DEMO_MODE"] = False
    response = client.post("/api/story/scenes/audio", json={"token": token, "index": 0})
    assert response.status_code == 503
    assert "FISH_API_KEY" in response.get_json()["error"]


# --- guardado con las escenas --------------------------------------------------

def test_story_is_saved_with_every_scene_image_and_audio(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)

    response = save_story(client, periodo_tema, scenes_token=token)
    assert response.status_code == 200, response.get_json()

    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        material_dir = os.path.dirname(app_module.uploads_abspath_for_tests(material.path_texto)) \
            if hasattr(app_module, "uploads_abspath_for_tests") \
            else os.path.join(app.config["UPLOADS_ROOT"], material.path_texto.split("/")[1])
    with open(os.path.join(material_dir, "escenas.json"), encoding="utf-8") as f:
        rows = json.load(f)
    assert [row["texto"] for row in rows] == [
        "Luna salió al bosque.", "Encontró a un búho.", "Juntos regresaron a casa.",
    ]
    assert [row["duracion_s"] for row in rows] == [1.0, 1.0, 1.0]
    # El archivo indica qué imagen va con qué audio, escena por escena.
    assert [(row["indice"], row["imagen"], row["audio"]) for row in rows] == [
        (0, "escenas/escena_0.png", "escenas/escena_0.wav"),
        (1, "escenas/escena_1.png", "escenas/escena_1.wav"),
        (2, "escenas/escena_2.png", "escenas/escena_2.wav"),
    ]
    for row in rows:
        assert open(os.path.join(material_dir, row["imagen"]), "rb").read() == PNG
        with wave.open(os.path.join(material_dir, row["audio"]), "rb") as f:
            assert f.getnframes() == 24_000
    # La carpeta de previsualización se limpia al guardar.
    assert client.get(f"/api/story/scenes/audio-preview/{token}/0").status_code == 404


def test_full_audio_is_built_by_joining_the_scene_audios(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)

    response = save_story(client, periodo_tema, scenes_token=token)
    assert response.status_code == 200, response.get_json()

    directory = material_directory(app, response.get_json()["material_id"])
    with wave.open(os.path.join(directory, "audio.wav"), "rb") as f:
        # Tres escenas de 1 s cada una.
        assert f.getframerate() == 24_000
        assert f.getnframes() == 3 * 24_000


def test_uploaded_full_audio_is_ignored_when_there_are_scenes(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)

    response = save_story(client, periodo_tema, scenes_token=token, full_audio=False,
                          audio_full=(io.BytesIO(wav_bytes(5)), "audio.wav"))
    assert response.status_code == 200

    directory = material_directory(app, response.get_json()["material_id"])
    with wave.open(os.path.join(directory, "audio.wav"), "rb") as f:
        assert f.getnframes() == 3 * 24_000  # no los 5 s subidos


def test_robot_can_download_the_full_audio_built_from_scenes(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)
    material_id = save_story(client, periodo_tema, scenes_token=token).get_json()["material_id"]

    audio = client.get(f"/api/materials/{material_id}/audio?teacher_id={TEACHER_ID}")
    assert audio.status_code == 200
    with wave.open(io.BytesIO(audio.data), "rb") as f:
        assert f.getnframes() == 3 * 24_000


def test_save_requires_scenes_or_a_full_audio(app, client, periodo_tema):
    response = save_story(client, periodo_tema, full_audio=False)
    assert response.status_code == 400
    assert "escenas" in response.get_json()["error"]
    with app.app_context():
        assert Material.query.count() == 0


def test_save_rejects_scene_audios_with_different_formats(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)
    # Un audio con otra frecuencia (p. ej. uno del modo demo mezclado con reales).
    staged = os.path.join(app.config["UPLOADS_ROOT"], "_previews", token, "escena-1.wav")
    with open(staged, "wb") as f:
        f.write(wav_bytes(1, rate=8_000))

    response = save_story(client, periodo_tema, scenes_token=token)

    assert response.status_code == 400
    assert "no son compatibles" in response.get_json()["error"]
    with app.app_context():
        assert Material.query.count() == 0
    # No queda una carpeta de material a medias (solo las previsualizaciones).
    assert os.listdir(app.config["UPLOADS_ROOT"]) == ["_previews"]


def test_story_without_scenes_is_saved_as_before(app, client, periodo_tema):
    response = save_story(client, periodo_tema)
    assert response.status_code == 200
    directory = material_directory(app, response.get_json()["material_id"])
    assert not os.path.exists(os.path.join(directory, "escenas.json"))


def test_save_rejects_scenes_when_the_story_text_changed(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)

    response = save_story(client, periodo_tema, scenes_token=token, transcribed_text=STORY + " Fin.")
    assert response.status_code == 400
    assert "cambió" in response.get_json()["error"]
    with app.app_context():
        assert Material.query.count() == 0


@pytest.mark.parametrize("missing, message", [
    ("image", "la imagen de la escena 2"),
    ("audio", "el audio de la escena 2"),
])
def test_save_rejects_incomplete_scenes(app, client, models, periodo_tema, missing, message):
    token = plan(client)["token"]
    make_all_scenes(client, token)
    name = "escena-1.png" if missing == "image" else "escena-1.wav"
    os.remove(os.path.join(app.config["UPLOADS_ROOT"], "_previews", token, name))

    response = save_story(client, periodo_tema, scenes_token=token)
    assert response.status_code == 400
    assert message in response.get_json()["error"]
    with app.app_context():
        assert Material.query.count() == 0
    # Las escenas siguen en la previsualización para poder completarlas.
    assert client.get(f"/api/story/scenes/audio-preview/{token}/0").status_code == 200


def test_save_rejects_an_unknown_scenes_token(client, periodo_tema):
    for token in ("no-es-un-token", "0" * 32):
        response = save_story(client, periodo_tema, scenes_token=token)
        assert response.status_code == 400


# --- API del robot -------------------------------------------------------------

def saved_story(app, client, models, periodo_tema):
    token = plan(client)["token"]
    make_all_scenes(client, token)
    return save_story(client, periodo_tema, scenes_token=token).get_json()["material_id"]


def test_robot_gets_the_scenes_in_reading_order(app, client, models, periodo_tema):
    material_id = saved_story(app, client, models, periodo_tema)

    body = client.get(f"/api/materials/{material_id}?teacher_id={TEACHER_ID}").get_json()

    assert [scene["indice"] for scene in body["escenas"]] == [0, 1, 2]
    assert [scene["texto"] for scene in body["escenas"]] == [
        "Luna salió al bosque.", "Encontró a un búho.", "Juntos regresaron a casa.",
    ]
    assert all(scene["duracion_s"] == 1.0 for scene in body["escenas"])
    assert body["escenas"][1]["imagen_url"].endswith(f"/api/materials/{material_id}/escena-imagen/1")
    assert body["escenas"][1]["audio_url"].endswith(f"/api/materials/{material_id}/escena-audio/1")
    # El audio completo de siempre no cambia.
    assert body["audio_completo_url"]


def test_robot_list_includes_the_scenes(app, client, models, periodo_tema):
    saved_story(app, client, models, periodo_tema)
    listing = client.get(f"/api/materials?teacher_id={TEACHER_ID}&tipo=cuento").get_json()
    assert len(listing[0]["escenas"]) == 3


def test_robot_downloads_each_scene_image_and_audio(app, client, models, periodo_tema):
    material_id = saved_story(app, client, models, periodo_tema)
    query = f"teacher_id={TEACHER_ID}"

    image = client.get(f"/api/materials/{material_id}/escena-imagen/2?{query}")
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    assert image.data == PNG
    assert "escena_2.png" in image.headers["Content-Disposition"]

    audio = client.get(f"/api/materials/{material_id}/escena-audio/2?{query}")
    assert audio.status_code == 200
    assert audio.mimetype == "audio/wav"
    with wave.open(io.BytesIO(audio.data), "rb") as f:
        assert f.getnframes() == 24_000
    assert "escena_2.wav" in audio.headers["Content-Disposition"]


def test_scene_downloads_check_range_owner_and_authorization(app, client, models, periodo_tema):
    material_id = saved_story(app, client, models, periodo_tema)

    for kind in ("escena-imagen", "escena-audio"):
        url = f"/api/materials/{material_id}/{kind}"
        assert client.get(f"{url}/3?teacher_id={TEACHER_ID}").status_code == 404
        assert client.get(f"{url}/0").status_code == 400
        assert client.get(f"{url}/0?teacher_id=OTRA-DOCENTE").status_code == 403
        app.config["TEST_WEBHOOK_AUTHORIZED"] = False
        try:
            assert client.get(f"{url}/0?teacher_id={TEACHER_ID}").status_code == 401
        finally:
            app.config["TEST_WEBHOOK_AUTHORIZED"] = True


def test_story_without_scenes_exposes_an_empty_list_and_no_scene_files(app, client, periodo_tema):
    material_id = save_story(client, periodo_tema).get_json()["material_id"]

    body = client.get(f"/api/materials/{material_id}?teacher_id={TEACHER_ID}").get_json()
    assert body["escenas"] == []
    assert client.get(
        f"/api/materials/{material_id}/escena-audio/0?teacher_id={TEACHER_ID}"
    ).status_code == 404


def test_deleting_the_story_removes_its_scene_files(app, client, models, periodo_tema):
    material_id = saved_story(app, client, models, periodo_tema)
    directory = material_directory(app, material_id)
    assert os.path.isdir(os.path.join(directory, "escenas"))

    assert client.delete(f"/api/material/{material_id}").status_code == 200
    assert not os.path.exists(directory)


# --- modal de revisión: asistente de tres pantallas ------------------------------

def test_story_review_is_a_three_step_wizard(client):
    html = client.get("/material").get_data(as_text=True)

    # Las tres secciones, en este orden: texto, escenas y preguntas.
    positions = [html.index(f'data-review-step="{key}"') for key in ("text", "scenes", "questions")]
    assert positions == sorted(positions)
    for number, title in ((1, "Revisar texto"), (2, "Ilustrar y narrar escenas"), (3, "Generar preguntas")):
        assert f"{number}. {title}" in html

    # Indicador de pasos y botón para volver; el audio resumen ya no existe.
    assert 'id="reviewStepper"' in html and 'id="reviewStepHeading"' in html
    assert 'id="resultBackBtn"' in html
    assert "resultAudioColumn" not in html
