import json
from types import SimpleNamespace

import pytest

import app as app_module

PNG = b"\x89PNG\r\n\x1a\nfake-image"
PNG_EDITED = b"\x89PNG\r\n\x1a\nedited-image"
STORY = "Luna salió al bosque. Encontró a un búho. Juntos regresaron a casa."


class SceneModels:
    def __init__(self, plan=None, fail_image_at=None):
        self.plan = plan if plan is not None else {
            "personaje": "a small orange fox with a blue scarf",
            "estilo": "photorealistic",
            "escenas": [
                {"desde": 1, "descripcion": "Fox enters a forest."},
                {"desde": 2, "descripcion": "Fox meets an owl."},
                {"desde": 3, "descripcion": "Fox and owl walk home."},
            ],
        }
        self.fail_image_at = fail_image_at
        self.fail_edit = False
        self.image_contents = []
        self.plan_prompts = []

    def generate_content(self, *, contents, config, **_kwargs):
        if config.response_mime_type == "application/json":
            self.plan_prompts.append(contents)
            return SimpleNamespace(text=json.dumps(self.plan))
        self.image_contents.append(contents)
        is_edit = "Edit the attached illustration" in contents[0]
        if is_edit and self.fail_edit:
            return SimpleNamespace(candidates=[])
        if not is_edit and self.fail_image_at == len(self.image_contents) - 1:
            return SimpleNamespace(candidates=[])
        inline = SimpleNamespace(data=PNG_EDITED if is_edit else PNG)
        part = SimpleNamespace(inline_data=inline)
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


@pytest.fixture()
def models(monkeypatch):
    fake = SceneModels()
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    return fake


def test_ai_decides_scenes_and_images_keep_first_scene_as_reference(client, models):
    plan = client.post("/api/story/scenes/plan", json={"story": STORY})
    assert plan.status_code == 200
    body = plan.get_json()
    assert [s["texto"] for s in body["scenes"]] == [
        "Luna salió al bosque.", "Encontró a un búho.", "Juntos regresaron a casa.",
    ]

    for index in range(3):
        image = client.post(
            "/api/story/scenes/image", json={"token": body["token"], "index": index},
        )
        assert image.status_code == 200
        preview = client.get(image.get_json()["imagen_url"])
        assert preview.status_code == 200
        assert preview.data == PNG

    # La primera escena no tiene referencia; las siguientes reciben la primera.
    assert [len(contents) for contents in models.image_contents] == [1, 3, 3]
    assert "small orange fox" in models.image_contents[0][0]


def test_scene_images_are_always_2d_cartoon(client, models):
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]
    for index in range(3):
        client.post("/api/story/scenes/image", json={"token": token, "index": index})

    for contents in models.image_contents:
        prompt = contents[0]
        assert "2D cartoon animation style" in prompt
        assert "not photographic" in prompt
        # El estilo lo fija el servidor: el que sugiera el plan de la IA se ignora.
        assert "photorealistic" not in prompt
    assert "2D cartoon" in models.image_contents[1][1]


def _draw_all(client, token, count=3):
    for index in range(count):
        client.post("/api/story/scenes/image", json={"token": token, "index": index})


@pytest.mark.parametrize("tone", list(app_module.STORY_SCENE_TONES))
def test_teacher_tone_reaches_plan_and_every_image(client, models, tone):
    body = client.post(
        "/api/story/scenes/plan", json={"story": STORY, "tone": tone},
    ).get_json()
    expected = app_module.STORY_SCENE_TONES[tone]

    assert body["tone"] == tone
    assert body["tone_label"] == expected["label"]
    assert expected["label"] in models.plan_prompts[0]

    _draw_all(client, body["token"])
    for contents in models.image_contents:
        assert expected["prompt"] in contents[0]
        # El tono se suma al estilo cartoon 2D, no lo reemplaza.
        assert "2D cartoon animation style" in contents[0]


def test_auto_tone_uses_the_one_the_ai_picks(client, monkeypatch):
    fake = SceneModels()
    fake.plan["tono"] = "tierno"
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    body = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()
    assert body["tone"] == "tierno"
    for key in app_module.STORY_SCENE_TONES:
        assert key in fake.plan_prompts[0]

    _draw_all(client, body["token"])
    assert app_module.STORY_SCENE_TONES["tierno"]["prompt"] in fake.image_contents[0][0]


@pytest.mark.parametrize("suggested", [None, "", "inventado", 7])
def test_auto_tone_falls_back_when_ai_answer_is_invalid(client, monkeypatch, suggested):
    fake = SceneModels()
    if suggested is not None:
        fake.plan["tono"] = suggested
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    body = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()
    assert body["tone"] == app_module.STORY_SCENE_TONE_FALLBACK


def test_teacher_tone_wins_over_the_ai_suggestion(client, monkeypatch):
    fake = SceneModels()
    fake.plan["tono"] = "misterioso"
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    body = client.post(
        "/api/story/scenes/plan", json={"story": STORY, "tone": "divertido"},
    ).get_json()
    assert body["tone"] == "divertido"


@pytest.mark.parametrize("tone", ["oscuro", "REALISTA", "<script>", 3])
def test_invalid_tone_is_rejected(client, models, tone):
    response = client.post("/api/story/scenes/plan", json={"story": STORY, "tone": tone})
    assert response.status_code == 400
    assert models.plan_prompts == []


def test_tone_selector_lists_every_tone(client):
    html = client.get("/material").get_data(as_text=True)
    assert 'id="scenesTone"' in html
    assert 'value="auto"' in html
    for key, tone in app_module.STORY_SCENE_TONES.items():
        assert f'value="{key}"' in html
        assert tone["label"] in html


def _scene_with_image(client, index=1):
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]
    client.post("/api/story/scenes/image", json={"token": token, "index": index})
    return token


def test_edit_replaces_the_image_with_the_teachers_change(client, models):
    token = _scene_with_image(client, index=1)
    before = len(models.image_contents)

    response = client.post("/api/story/scenes/edit", json={
        "token": token, "index": 1, "instruction": "  que lleve   una bufanda roja ",
    })

    assert response.status_code == 200
    assert client.get(response.get_json()["imagen_url"]).data == PNG_EDITED
    contents = models.image_contents[before]
    # Se manda la instrucción (con espacios normalizados) y la imagen actual.
    assert "que lleve una bufanda roja" in contents[0]
    assert "2D cartoon art style" in contents[0]
    assert len(contents) == 2


def test_edit_keeps_the_original_image_when_gemini_fails(client, models):
    token = _scene_with_image(client, index=0)
    models.fail_edit = True

    response = client.post("/api/story/scenes/edit", json={
        "token": token, "index": 0, "instruction": "cambia el cielo",
    })

    assert response.status_code == 502
    assert client.get(f"/api/story/scenes/preview/{token}/0").data == PNG


def test_edit_validates_its_input(client, models):
    token = _scene_with_image(client, index=0)
    edit = lambda **kw: client.post("/api/story/scenes/edit", json={"token": token, "index": 0, **kw})

    assert edit(instruction="   ").status_code == 400
    assert edit().status_code == 400
    too_long = "x" * (app_module.STORY_SCENE_EDIT_MAX_CHARS + 1)
    assert edit(instruction=too_long).status_code == 413
    for bad_index in (-1, 3, "0", True, None):
        response = client.post("/api/story/scenes/edit", json={
            "token": token, "index": bad_index, "instruction": "cambia algo",
        })
        assert response.status_code == 400
    assert client.post("/api/story/scenes/edit", json={
        "token": "0" * 32, "index": 0, "instruction": "cambia algo",
    }).status_code == 404
    # Nada de lo anterior llegó a Gemini más allá de la generación inicial.
    assert len(models.image_contents) == 1


def test_edit_requires_an_existing_image(client, models):
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]
    response = client.post("/api/story/scenes/edit", json={
        "token": token, "index": 2, "instruction": "cambia algo",
    })
    assert response.status_code == 409
    assert models.image_contents == []


def test_edit_works_in_demo_mode(app, client):
    app.config["DEMO_MODE"] = True
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]
    client.post("/api/story/scenes/image", json={"token": token, "index": 0})
    response = client.post("/api/story/scenes/edit", json={
        "token": token, "index": 0, "instruction": "cambia el cielo",
    })
    assert response.status_code == 200
    assert client.get(response.get_json()["imagen_url"]).status_code == 200


def test_failed_scene_can_be_retried(client, monkeypatch):
    fake = SceneModels(fail_image_at=1)
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]

    assert client.post("/api/story/scenes/image", json={"token": token, "index": 0}).status_code == 200
    failed = client.post("/api/story/scenes/image", json={"token": token, "index": 1})
    assert failed.status_code == 502
    assert client.get(f"/api/story/scenes/preview/{token}/1").status_code == 404
    retry = client.post("/api/story/scenes/image", json={"token": token, "index": 1})
    assert retry.status_code == 200


def test_plan_requires_story_and_enough_scenes(client, monkeypatch):
    assert client.post("/api/story/scenes/plan", json={"story": "  "}).status_code == 400

    fake = SceneModels(plan={
        "personaje": "a fox",
        "escenas": [{"desde": 1, "descripcion": "One scene."}],
    })
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    assert client.post("/api/story/scenes/plan", json={"story": STORY}).status_code == 502


def test_scene_count_is_capped(client, monkeypatch):
    long_story = " ".join(f"Oración número {i}." for i in range(1, 26))
    many = [{"desde": i, "descripcion": f"Scene {i}."} for i in range(1, 26)]
    fake = SceneModels(plan={"personaje": "a fox", "escenas": many})
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    body = client.post("/api/story/scenes/plan", json={"story": long_story}).get_json()
    assert len(body["scenes"]) == app_module.STORY_SCENES_MAX
    # Al recortar, la última escena conserva el resto del cuento.
    assert " ".join(scene["texto"] for scene in body["scenes"]) == long_story


@pytest.mark.parametrize("payload", [
    {"token": "no-es-un-token", "index": 0},
    {"token": "0" * 32, "index": 0},
])
def test_image_rejects_unknown_token(client, models, payload):
    assert client.post("/api/story/scenes/image", json=payload).status_code == 404


@pytest.mark.parametrize("index", [-1, 3, "0", True, None])
def test_image_rejects_invalid_index(client, models, index):
    token = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()["token"]
    response = client.post("/api/story/scenes/image", json={"token": token, "index": index})
    assert response.status_code == 400


def test_scenes_work_in_demo_mode(app, client):
    app.config["DEMO_MODE"] = True
    body = client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()
    assert len(body["scenes"]) >= 2
    image = client.post("/api/story/scenes/image", json={"token": body["token"], "index": 0})
    assert image.status_code == 200
    assert client.get(image.get_json()["imagen_url"]).status_code == 200
