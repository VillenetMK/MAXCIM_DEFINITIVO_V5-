import json
from types import SimpleNamespace

import app as app_module


class StoryModels:
    def __init__(self):
        self.prompts = []

    def generate_content(self, *, contents, **_kwargs):
        self.prompts.append(contents)
        if len(self.prompts) == 1:
            story = "Luna escuchó y ayudó felizmente."
        else:
            story = " ".join(["palabra"] * 125)
        return SimpleNamespace(text=json.dumps({
            "titulo": "Luna y el bosque",
            "cuento": story,
            "resumen": "Luna aprendió a escuchar.",
        }))


class FishSpeech:
    """Falso cliente de Fish Audio: dos segundos de silencio por petición."""

    sample_rate = 24_000

    def __init__(self):
        self.texts = []

    def synthesize_pcm(self, text):
        self.texts.append(text)
        return b"\0" * (self.sample_rate * 2 * 2)


def test_story_is_corrected_toward_selected_duration(client, monkeypatch):
    models = StoryModels()
    monkeypatch.setattr(
        app_module,
        "gemini_client",
        SimpleNamespace(models=models),
    )

    response = client.post("/api/story/generate", json={
        "character": "Luna",
        "setting": "el bosque",
        "grade_level": "tercero de primaria",
        "objective": "escucha activa",
        "duration_minutes": 1,
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["target_duration_minutes"] == 1
    assert payload["word_count"] == 125
    assert payload["estimated_duration_seconds"] == 60
    assert len(models.prompts) == 2


def test_tts_reports_measured_wav_duration(client, monkeypatch):
    fish = FishSpeech()
    monkeypatch.setattr(app_module, "fish_client", fish)
    story = " ".join(["palabra"] * 125)

    response = client.post("/api/material/tts", json={"text": story})

    assert response.status_code == 200
    assert response.mimetype == "audio/wav"
    assert response.headers["X-MAXCIM-Audio-Duration-Seconds"] == "2.00"
    # El texto llega tal cual, sin instrucciones de estilo ni de ritmo.
    assert fish.texts == [story]


def test_tts_splits_very_long_text_and_joins_the_audio(client, monkeypatch):
    fish = FishSpeech()
    monkeypatch.setattr(app_module, "fish_client", fish)
    monkeypatch.setattr(app_module, "FISH_CHUNK_MAX_CHARS", 40)

    response = client.post("/api/material/tts", json={
        "text": "Primera oración corta. Segunda oración corta. Tercera oración corta.",
    })

    assert response.status_code == 200
    assert len(fish.texts) == 3
    assert response.headers["X-MAXCIM-Audio-Duration-Seconds"] == "6.00"


def test_tts_without_fish_key_is_unavailable(client):
    response = client.post("/api/material/tts", json={"text": "Un cuento breve."})
    assert response.status_code == 503
    assert "FISH_API_KEY" in response.get_json()["error"]


def test_tts_reports_a_fish_failure_as_bad_gateway(client, monkeypatch):
    from services.fish_audio import FishAudioError

    class Broken:
        sample_rate = 24_000

        def synthesize_pcm(self, text):
            raise FishAudioError("caído")

    monkeypatch.setattr(app_module, "fish_client", Broken())
    response = client.post("/api/material/tts", json={"text": "Un cuento breve."})
    assert response.status_code == 502
    assert "Fish Audio" in response.get_json()["error"]
