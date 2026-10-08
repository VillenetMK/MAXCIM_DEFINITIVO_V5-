import json
import unicodedata
from types import SimpleNamespace

import pytest

import app as app_module


class BitsModels:
    def __init__(self, words):
        self.words = words
        self.prompts = []

    def generate_content(self, *, contents, **_kwargs):
        self.prompts.append(contents)
        return SimpleNamespace(text=json.dumps({"palabras": self.words}))


@pytest.fixture()
def fake_bits(monkeypatch):
    def install(words):
        models = BitsModels(words)
        monkeypatch.setattr(app_module, "gemini_client", SimpleNamespace(models=models))
        return models
    return install


def generate(client, silabas, **extra):
    payload = {"silabas": silabas, "count": 10, **extra}
    return client.post("/api/bits/generate", json=payload)


def test_words_that_do_not_start_with_any_chosen_syllable_are_dropped(client, fake_bits):
    fake_bits(["mano", "mesa", "pelota", "sol", "oso", "Mapa", "luna"])

    body = generate(client, "ma").get_json()

    assert [item["palabra"] for item in body["items"]] == ["mano", "Mapa"]


def test_a_word_matching_any_of_several_syllables_is_kept(client, fake_bits):
    fake_bits(["mano", "sapo", "luna", "oso"])

    body = generate(client, "ma, sa").get_json()

    assert [item["palabra"] for item in body["items"]] == ["mano", "sapo"]


def test_prompt_names_all_the_chosen_syllables(client, fake_bits):
    models = fake_bits(["mano"])

    generate(client, "ma, sa")

    prompt = models.prompts[0]
    assert "primera sílaba" in prompt
    assert ": ma, sa. Reparte las 10 palabras" in prompt


@pytest.mark.parametrize("sent, expected", [
    ("m", "m"), (" MA ", "ma"), ("ma,  se", "ma, se"),
])
def test_title_shows_the_normalized_syllables(client, fake_bits, sent, expected):
    fake_bits(["mano"])

    body = generate(client, sent).get_json()

    assert body["title"] == f"Bits: sílabas {expected}"


def test_syllables_match_even_when_the_model_sends_a_decomposed_accent(client, fake_bits):
    decomposed = unicodedata.normalize("NFD", "ñoño")
    fake_bits([decomposed, "nube"])

    only_enye = generate(client, "ño").get_json()["items"]
    only_n = generate(client, "nu").get_json()["items"]

    assert [item["palabra"] for item in only_enye] == [decomposed]
    assert [item["palabra"] for item in only_n] == ["nube"]


def test_error_when_no_word_starts_with_any_syllable(client, fake_bits):
    fake_bits(["oso", "ala"])

    response = generate(client, "ma")

    assert response.status_code == 502
    assert "palabras" in response.get_json()["error"]


@pytest.mark.parametrize("invalid", [None, 3, {}, "", "   ", [], ["", "  "]])
def test_at_least_one_syllable_is_required(client, fake_bits, invalid):
    models = fake_bits(["mano"])

    response = generate(client, invalid)

    assert response.status_code == 400
    assert "sílaba" in response.get_json()["error"]
    assert models.prompts == []


def test_syllable_list_is_capped_deduped_and_lowercased(client, fake_bits):
    models = fake_bits(["mano"])
    many = ", ".join(["ma", "MA", " ma ", "me", "mi", "mo", "mu", "pa", "pe"])

    generate(client, many)

    prompt = models.prompts[0]
    assert prompt.count(": ma, me, mi, mo, mu, pa.") == 1


def test_audience_is_fixed_but_the_teacher_chooses_how_many_words(client, fake_bits):
    models = fake_bits(["mano"])

    generate(client, "ma", count=6, grade_level="quinto de secundaria")

    prompt = models.prompts[0]
    assert "niños de inicial de 5 años" in prompt
    assert "quinto de secundaria" not in prompt
    assert "Escribe exactamente 6 palabras" in prompt


def test_prompt_includes_the_teachers_own_words_about_syllable_count(client, fake_bits):
    models = fake_bits(["mano"])

    generate(client, "ma", syllable_count="  de tres   o cuatro sílabas ")

    assert "según indicó la docente: de tres o cuatro sílabas." in models.prompts[0]


@pytest.mark.parametrize("empty", [None, "", "   ", 0])
def test_empty_syllable_count_falls_back_to_the_default(client, fake_bits, empty):
    models = fake_bits(["mano"])

    generate(client, "ma", syllable_count=empty)

    default = app_module.BITS_DEFAULT_SYLLABLE_COUNT
    assert f"según indicó la docente: {default}." in models.prompts[0]


def test_syllable_count_absent_falls_back_to_the_default(client, fake_bits):
    models = fake_bits(["mano"])

    generate(client, "ma")

    default = app_module.BITS_DEFAULT_SYLLABLE_COUNT
    assert f"según indicó la docente: {default}." in models.prompts[0]


def test_overlong_syllable_count_is_rejected(client, fake_bits):
    models = fake_bits(["mano"])
    too_long = "x" * (app_module.MAX_BITS_SYLLABLE_COUNT_CHARS + 1)

    response = generate(client, "ma", syllable_count=too_long)

    assert response.status_code == 413
    assert "sílabas por palabra" in response.get_json()["error"]
    assert models.prompts == []


@pytest.mark.parametrize("raw, expected", [
    ("ma, me  mi", ["ma", "me", "mi"]),
    (["MA", " Me ", "ma"], ["ma", "me"]),
    ("a" * 20, ["a" * 10]),
])
def test_normalize_bits_syllables_splits_dedupes_and_caps(raw, expected):
    assert app_module.normalize_bits_syllables(raw) == expected


def test_normalize_bits_syllables_caps_the_list_length():
    many = ", ".join(f"s{i}" for i in range(20))
    assert len(app_module.normalize_bits_syllables(many)) == app_module.MAX_BITS_SYLLABLES


def test_bits_form_has_a_free_text_syllables_field_instead_of_a_consonant_picker(client):
    html = client.get("/material").get_data(as_text=True)

    assert 'id="bitsSyllables"' in html
    assert "consonant-picker" not in html
    assert 'data-consonant' not in html


def test_bits_form_asks_for_syllables_word_count_and_optional_details(client):
    html = client.get("/material").get_data(as_text=True)

    assert 'id="bitsGrade"' not in html
    default = app_module.BITS_DEFAULT_WORDS
    assert f'id="bitsCount" required min="1" max="{app_module.MAX_BITS_PER_REQUEST}"' in html
    assert f'value="{default}"' in html
    assert 'id="bitsDetails"' in html


def test_bits_form_has_a_free_text_syllable_count_field(client):
    html = client.get("/material").get_data(as_text=True)

    limit = app_module.MAX_BITS_SYLLABLE_COUNT_CHARS
    assert f'<input type="text" id="bitsSyllableCount" maxlength="{limit}"' in html
    assert "syllable-picker" not in html
