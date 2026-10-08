from types import SimpleNamespace

import pytest

import app as app_module

PNG = b"\x89PNG\r\n\x1a\nfake-image"
PNG_EDITED = b"\x89PNG\r\n\x1a\nedited-image"
SENTENCE = {"texto": "El gato bebe leche.", "sustantivos": ["gato", "leche"]}


class NounModels:
    """Gemini falso: la generación manda un str; la edición, [prompt, imagen]."""

    def __init__(self):
        self.fail_edit = False
        self.edit_calls = []
        self.generate_calls = 0

    def generate_content(self, *, contents, config=None, **_kwargs):
        if getattr(config, "response_mime_type", None) == "application/json":
            # Consulta de texto (pregunta del robot por bit): no es imagen.
            return SimpleNamespace(text='{"preguntas": []}')
        is_edit = isinstance(contents, list)
        if is_edit:
            self.edit_calls.append(contents)
            if self.fail_edit:
                return SimpleNamespace(candidates=[])
        else:
            self.generate_calls += 1
        inline = SimpleNamespace(data=PNG_EDITED if is_edit else PNG)
        part = SimpleNamespace(inline_data=inline)
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


@pytest.fixture()
def models(monkeypatch):
    fake = NounModels()
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    return fake


def _prepared_sentences(client):
    body = client.post(
        "/api/material/image-sentences/prepare", json={"items": [SENTENCE]}
    ).get_json()
    return body["token"]


def _prepared_bits(client):
    body = client.post(
        "/api/bits/prepare", json={"items": [{"palabra": "mano"}, {"palabra": "mapa"}]}
    ).get_json()
    return body["token"]


# --- oraciones con imágenes -------------------------------------------------


def test_sentence_edit_replaces_only_that_noun_image(client, models):
    token = _prepared_sentences(client)

    response = client.post("/api/material/image-sentences/edit", json={
        "token": token, "sentence_index": 0, "noun_index": 1,
        "instruction": "  que sea   un vaso azul ",
    })

    assert response.status_code == 200
    body = response.get_json()
    assert "?v=" in body["imagen_url"]
    assert client.get(body["imagen_url"]).data == PNG_EDITED
    # La otra imagen del diseño no se toca.
    assert client.get(f"/api/material/image-sentences/preview/{token}/0/0").data == PNG
    prompt, image_part = models.edit_calls[0]
    assert "que sea un vaso azul" in prompt
    assert "Edit the attached educational illustration" in prompt
    assert image_part is not None


def test_sentence_edit_keeps_the_original_when_gemini_fails(client, models):
    token = _prepared_sentences(client)
    models.fail_edit = True

    response = client.post("/api/material/image-sentences/edit", json={
        "token": token, "sentence_index": 0, "noun_index": 0, "instruction": "hazlo rojo",
    })

    assert response.status_code == 502
    assert client.get(f"/api/material/image-sentences/preview/{token}/0/0").data == PNG


def test_sentence_edit_validates_its_input(client, models):
    token = _prepared_sentences(client)

    def edit(**overrides):
        payload = {
            "token": token, "sentence_index": 0, "noun_index": 0,
            "instruction": "hazlo rojo", **overrides,
        }
        return client.post("/api/material/image-sentences/edit", json=payload)

    assert edit(instruction="   ").status_code == 400
    assert edit(instruction=None).status_code == 400
    too_long = "x" * (app_module.NOUN_IMAGE_EDIT_MAX_CHARS + 1)
    assert edit(instruction=too_long).status_code == 413
    for bad in (-1, 1, "0", True, None):
        assert edit(sentence_index=bad).status_code == 400
    for bad in (-1, 2, "0", True, None):
        assert edit(noun_index=bad).status_code == 400
    assert edit(token="0" * 32).status_code == 404
    assert edit(token="no-es-un-token").status_code == 400
    # Nada de lo anterior llegó a Gemini.
    assert models.edit_calls == []


# --- bits -------------------------------------------------------------------


def test_bit_edit_replaces_only_that_bit_image(client, models):
    token = _prepared_bits(client)

    response = client.post("/api/bits/edit", json={
        "token": token, "item_index": 1, "instruction": "que tenga colores más vivos",
    })

    assert response.status_code == 200
    body = response.get_json()
    assert "?v=" in body["imagen_url"]
    assert client.get(body["imagen_url"]).data == PNG_EDITED
    assert client.get(f"/api/bits/preview/{token}/0").data == PNG
    assert "que tenga colores más vivos" in models.edit_calls[0][0]


def test_bit_edit_keeps_the_original_when_gemini_fails(client, models):
    token = _prepared_bits(client)
    models.fail_edit = True

    response = client.post("/api/bits/edit", json={
        "token": token, "item_index": 0, "instruction": "hazlo rojo",
    })

    assert response.status_code == 502
    assert client.get(f"/api/bits/preview/{token}/0").data == PNG


def test_bit_edit_validates_its_input(client, models):
    token = _prepared_bits(client)

    def edit(**overrides):
        payload = {"token": token, "item_index": 0, "instruction": "hazlo rojo", **overrides}
        return client.post("/api/bits/edit", json=payload)

    assert edit(instruction="").status_code == 400
    assert edit(instruction="x" * (app_module.NOUN_IMAGE_EDIT_MAX_CHARS + 1)).status_code == 413
    for bad in (-1, 2, "0", True, None):
        assert edit(item_index=bad).status_code == 400
    assert edit(token="0" * 32).status_code == 404
    assert models.edit_calls == []


def test_edits_work_in_demo_mode_without_gemini(app, client):
    app.config["DEMO_MODE"] = True
    token = _prepared_bits(client)

    response = client.post("/api/bits/edit", json={
        "token": token, "item_index": 0, "instruction": "que sea de otro color",
    })

    assert response.status_code == 200
    assert client.get(response.get_json()["imagen_url"]).status_code == 200


def test_edit_requires_gemini_when_not_in_demo(app, client, models, monkeypatch):
    token = _prepared_bits(client)  # se prepara con Gemini falso...
    monkeypatch.setattr(app_module, "gemini_client", None)  # ...y luego se cae
    app.config["DEMO_MODE"] = False

    response = client.post("/api/bits/edit", json={
        "token": token, "item_index": 0, "instruction": "hazlo rojo",
    })

    assert response.status_code == 503
