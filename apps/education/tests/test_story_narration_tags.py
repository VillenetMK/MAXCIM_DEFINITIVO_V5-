import json
from types import SimpleNamespace


import app as app_module

PLAN = {
    "personaje": "a small orange fox",
    "escenas": [
        {"desde": 1, "descripcion": "Fox enters a forest."},
        {"desde": 2, "descripcion": "Fox meets an owl."},
        {"desde": 3, "descripcion": "Fox and owl walk home."},
    ],
}
PNG = b"\x89PNG\r\n\x1a\nfake-image"
PCM_ONE_SECOND = b"\0" * (24_000 * 2)
STORY = "Luna salió al bosque. Encontró a un búho. Juntos regresaron a casa."


class TaggingModels:
    """Fake Gemini (plan de escenas + etiquetado de narración) y Fish Audio
    (narración) en un solo objeto, como en test_story_scene_audio.py."""

    sample_rate = 24_000

    def __init__(self, tagged_text=None, raise_on_tag=False):
        self.tagged_text = tagged_text
        self.raise_on_tag = raise_on_tag
        self.spoken = []
        self.tag_prompts = []

    def generate_content(self, *, contents, config, **_kwargs):
        if config.response_mime_type == "application/json":
            if "texto_narrado" in contents and "Fragmento a narrar" in contents:
                self.tag_prompts.append(contents)
                if self.raise_on_tag:
                    raise RuntimeError("Gemini no respondió")
                text = self.tagged_text if self.tagged_text is not None else ""
                return SimpleNamespace(text=json.dumps({"texto_narrado": text}))
            return SimpleNamespace(text=json.dumps(PLAN))
        part = SimpleNamespace(inline_data=SimpleNamespace(data=PNG))
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])

    def synthesize_pcm(self, text):
        self.spoken.append(text)
        return PCM_ONE_SECOND


def install(monkeypatch, fake):
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))
    monkeypatch.setattr(app_module, "fish_client", fake)


def plan(client):
    return client.post("/api/story/scenes/plan", json={"story": STORY}).get_json()


def narrate(client, token, index=0):
    return client.post("/api/story/scenes/audio", json={"token": token, "index": index})


# --- integración: /api/story/scenes/audio pide las etiquetas antes de narrar ---

def test_tagged_text_is_sent_to_fish_when_gemini_returns_valid_tags(client, monkeypatch):
    fake = TaggingModels(tagged_text="[happy] Luna salió al bosque.")
    install(monkeypatch, fake)
    token = plan(client)["token"]

    response = narrate(client, token, 0)

    assert response.status_code == 200
    assert fake.spoken == ["[happy] Luna salió al bosque."]
    assert fake.tag_prompts


def test_tag_prompt_lists_only_the_allowed_tags_and_the_character(client, monkeypatch):
    fake = TaggingModels(tagged_text="Luna salió al bosque.")
    install(monkeypatch, fake)
    token = plan(client)["token"]

    narrate(client, token, 0)

    prompt = fake.tag_prompts[0]
    for tag in app_module.FISH_NARRATION_TAGS:
        assert f"[{tag}]" in prompt
    assert "a small orange fox" in prompt
    assert "Luna salió al bosque." in prompt


def test_invented_tag_is_stripped_before_reaching_fish(client, monkeypatch):
    fake = TaggingModels(tagged_text="[mysterious-vibe] Luna salió al bosque.")
    install(monkeypatch, fake)
    token = plan(client)["token"]

    narrate(client, token, 0)

    assert fake.spoken == ["Luna salió al bosque."]


def test_reworded_narration_falls_back_to_the_original_plain_text(client, monkeypatch):
    fake = TaggingModels(tagged_text="[happy] Luna corrió al bosque.")
    install(monkeypatch, fake)
    token = plan(client)["token"]

    response = narrate(client, token, 0)

    assert response.status_code == 200
    assert fake.spoken == ["Luna salió al bosque."]


def test_empty_tagging_response_falls_back_to_the_original_plain_text(client, monkeypatch):
    fake = TaggingModels(tagged_text="")
    install(monkeypatch, fake)
    token = plan(client)["token"]

    narrate(client, token, 0)

    assert fake.spoken == ["Luna salió al bosque."]


def test_gemini_error_during_tagging_falls_back_to_plain_text(client, monkeypatch):
    fake = TaggingModels(raise_on_tag=True)
    install(monkeypatch, fake)
    token = plan(client)["token"]

    response = narrate(client, token, 0)

    assert response.status_code == 200
    assert fake.spoken == ["Luna salió al bosque."]


def test_narration_tags_can_be_disabled_via_flag(client, monkeypatch):
    fake = TaggingModels(tagged_text="[happy] Luna salió al bosque.")
    install(monkeypatch, fake)
    monkeypatch.setattr(app_module, "FISH_NARRATION_TAGS_ENABLED", False)
    token = plan(client)["token"]

    narrate(client, token, 0)

    assert fake.spoken == ["Luna salió al bosque."]
    assert fake.tag_prompts == []


# --- unidad: saneo de etiquetas ------------------------------------------------

def test_sanitize_keeps_allowed_tags_case_insensitively():
    original = "Luna salió al bosque."
    annotated = "[Happy] Luna salió al bosque. [SIGHING]"
    assert app_module._sanitize_narration_tags(annotated, original) == annotated


def test_sanitize_drops_tags_outside_the_allowed_list():
    original = "Luna salió al bosque."
    annotated = "[mysterious] Luna salió al bosque."
    assert app_module._sanitize_narration_tags(annotated, original) == "Luna salió al bosque."


def test_sanitize_falls_back_when_a_word_was_changed():
    original = "Luna salió al bosque."
    assert app_module._sanitize_narration_tags(
        "[happy] Luna corrió al bosque.", original
    ) == original


def test_sanitize_falls_back_when_a_word_was_removed():
    original = "Luna salió al bosque muy contenta."
    assert app_module._sanitize_narration_tags(
        "[happy] Luna salió al bosque.", original
    ) == original


# --- unidad: annotate_narration_for_tts ----------------------------------------

class FakeGeminiJSON:
    def __init__(self, payload=None, raise_error=False):
        self.payload = payload
        self.raise_error = raise_error
        self.calls = []

    def generate_content(self, *, contents, **_kwargs):
        self.calls.append(contents)
        if self.raise_error:
            raise RuntimeError("boom")
        return SimpleNamespace(text=json.dumps(self.payload))


def test_annotate_returns_the_tagged_text_when_gemini_cooperates(monkeypatch):
    fake = FakeGeminiJSON({"texto_narrado": "[happy] Hola mundo."})
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    result = app_module.annotate_narration_for_tts("Hola mundo.", "un zorro")

    assert result == "[happy] Hola mundo."
    assert "un zorro" in fake.calls[0]
    assert "Hola mundo." in fake.calls[0]


def test_annotate_falls_back_when_gemini_raises(monkeypatch):
    fake = FakeGeminiJSON(raise_error=True)
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    assert app_module.annotate_narration_for_tts("Hola mundo.") == "Hola mundo."


def test_annotate_falls_back_when_the_response_is_not_a_json_object(monkeypatch):
    fake = FakeGeminiJSON(["not", "a", "dict"])
    monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=fake))

    assert app_module.annotate_narration_for_tts("Hola mundo.") == "Hola mundo."
