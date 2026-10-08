import json
from types import SimpleNamespace

import pytest

import app as app_module

PNG = b"\x89PNG\r\n\x1a\nfake-image"


class BitModels:
    """Gemini falso: imágenes por defecto; JSON con preguntas si se pide JSON."""

    def __init__(self, questions=None):
        self.questions = questions if questions is not None else {
            1: "El sol está…", 2: "Esto es una…",
        }
        self.fail_questions = False
        self.question_calls = []

    def generate_content(self, *, contents, config=None, **_kwargs):
        if getattr(config, "response_mime_type", None) == "application/json":
            self.question_calls.append(contents)
            if self.fail_questions:
                raise RuntimeError("Gemini no disponible")
            rows = [
                {"tarjeta": number, "pregunta": question}
                for number, question in self.questions.items()
            ]
            return SimpleNamespace(text=json.dumps({"preguntas": rows}))
        inline = SimpleNamespace(data=PNG)
        part = SimpleNamespace(inline_data=inline)
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


@pytest.fixture()
def models(monkeypatch):
    fake = BitModels()
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    return fake


WORDS = [{"palabra": "feliz"}, {"palabra": "familia"}]


def test_prepare_suggests_one_question_per_bit_from_a_single_call(client, models):
    body = client.post("/api/bits/prepare", json={"items": WORDS}).get_json()

    assert [item["pregunta"] for item in body["items"]] == ["El sol está…", "Esto es una…"]
    # Una sola consulta con las dos tarjetas (prompt + 2 × [texto, imagen]).
    assert len(models.question_calls) == 1
    contents = models.question_calls[0]
    assert len(contents) == 1 + 2 * 2
    assert "«feliz»" in contents[1]
    assert "«familia»" in contents[3]


def test_prepare_still_works_when_the_ai_cannot_suggest_questions(client, models):
    models.fail_questions = True

    response = client.post("/api/bits/prepare", json={"items": WORDS})

    assert response.status_code == 200
    assert [item["pregunta"] for item in response.get_json()["items"]] == ["", ""]


def test_a_question_that_gives_away_the_answer_is_discarded(client, models):
    models.questions = {1: "Esto es feliz…", 2: "Esto es una…"}

    body = client.post("/api/bits/prepare", json={"items": WORDS}).get_json()

    # «feliz» es la respuesta: la pregunta no puede contenerla.
    assert [item["pregunta"] for item in body["items"]] == ["", "Esto es una…"]


def test_the_answer_check_matches_whole_words_only():
    assert app_module.normalize_bit_question("Aquí hay un solo…", "sol") == "Aquí hay un solo…"
    assert app_module.normalize_bit_question("El Sol está…", "sol") == ""
    assert app_module.normalize_bit_question("  Esto   es   una… ", "familia") == "Esto es una…"
    assert app_module.normalize_bit_question("", "sol") == ""


def test_suggest_question_uses_the_current_image_and_typed_word(client, models):
    token = client.post("/api/bits/prepare", json={"items": WORDS}).get_json()["token"]
    models.questions = {1: "La niña se siente…"}

    response = client.post("/api/bits/suggest-question", json={
        "token": token, "item_index": 0, "palabra": "  contenta ",
    })

    assert response.status_code == 200
    assert response.get_json() == {"pregunta": "La niña se siente…"}
    contents = models.question_calls[-1]
    assert "«contenta»" in contents[1]  # la palabra escrita, normalizada
    assert len(contents) == 3  # prompt + texto + imagen


def test_suggest_question_validates_its_input(client, models):
    token = client.post("/api/bits/prepare", json={"items": WORDS}).get_json()["token"]
    calls_before = len(models.question_calls)

    def suggest(**overrides):
        payload = {"token": token, "item_index": 0, **overrides}
        return client.post("/api/bits/suggest-question", json=payload)

    for bad in (-1, 2, "0", True, None):
        assert suggest(item_index=bad).status_code == 400
    assert suggest(token="0" * 32).status_code == 404
    assert suggest(token="no-es-un-token").status_code == 400
    assert len(models.question_calls) == calls_before


def test_suggest_question_reports_ai_failures_without_a_500(client, models):
    token = client.post("/api/bits/prepare", json={"items": WORDS}).get_json()["token"]
    models.fail_questions = True
    assert client.post("/api/bits/suggest-question", json={
        "token": token, "item_index": 0,
    }).status_code == 502

    models.fail_questions = False
    models.questions = {1: "Esto es feliz…"}  # regala la respuesta -> descartada
    assert client.post("/api/bits/suggest-question", json={
        "token": token, "item_index": 0,
    }).status_code == 502
