import io
import json


import app as app_module

TEACHER_ID = "DOC-TEST-1"


def _tiny_png(color) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


def save_bits(client, periodo_tema, palabras):
    """Guarda un material `bits` por el "modo editor" (sin IA): una imagen
    subida por palabra, como en test_bits_manual_editor_saves_uploaded_images_
    without_staging."""
    periodo_id, tema_id = periodo_tema()
    items = [{"palabra": palabra, "staging_index": index} for index, palabra in enumerate(palabras)]
    data = {
        "tipo_material": "bits",
        "title": "Bits",
        "bits_json": json.dumps(items),
        "id_periodo": str(periodo_id),
        "id_tema": str(tema_id),
    }
    for index, _ in enumerate(palabras):
        data[f"imagen_{index}"] = (io.BytesIO(_tiny_png((10 * index, 0, 0))), f"palabra_{index}.png")
    response = client.post("/api/material/save", data=data, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_json()
    return response.get_json()["material_id"]


# --- unidad: bits_shared_consonant ---------------------------------------------

def test_shared_consonant_when_every_word_starts_with_the_same_letter():
    items = [{"palabra": "mano"}, {"palabra": "mapa"}, {"palabra": "Mesa"}]
    assert app_module.bits_shared_consonant(items) == "M"


def test_no_shared_consonant_when_words_start_differently():
    items = [{"palabra": "mano"}, {"palabra": "sapo"}]
    assert app_module.bits_shared_consonant(items) is None


def test_no_shared_consonant_when_the_shared_initial_is_a_vowel():
    items = [{"palabra": "oso"}, {"palabra": "oveja"}]
    assert app_module.bits_shared_consonant(items) is None


def test_no_shared_consonant_for_an_empty_list():
    assert app_module.bits_shared_consonant([]) is None


def test_shared_consonant_matches_regardless_of_accents_or_case():
    decomposed_enye = "ñandú"  # "ñandú" con la ñ descompuesta
    items = [{"palabra": decomposed_enye}, {"palabra": "ñoño"}]
    assert app_module.bits_shared_consonant(items) == "Ñ"


# --- integración: el material y el recurso del robot exponen "consonante" -----

def test_material_and_robot_resource_expose_the_shared_consonant(app, client, periodo_tema):
    material_id = save_bits(client, periodo_tema, ["mano", "mapa"])

    material = client.get(f"/api/materials/{material_id}?teacher_id={TEACHER_ID}").get_json()
    assert material["consonante"] == "M"

    resource = client.get(f"/api/materials/{material_id}/bits?teacher_id={TEACHER_ID}").get_json()
    assert resource["consonante"] == "M"
    assert [b["palabra"] for b in resource["bits"]] == ["mano", "mapa"]


def test_consonante_is_null_when_words_do_not_share_one(app, client, periodo_tema):
    material_id = save_bits(client, periodo_tema, ["mano", "sapo"])

    material = client.get(f"/api/materials/{material_id}?teacher_id={TEACHER_ID}").get_json()
    assert material["consonante"] is None

    resource = client.get(f"/api/materials/{material_id}/bits?teacher_id={TEACHER_ID}").get_json()
    assert resource["consonante"] is None


def test_consonante_appears_in_the_materials_listing_too(app, client, periodo_tema):
    save_bits(client, periodo_tema, ["taza", "tomate"])

    listing = client.get(f"/api/materials?teacher_id={TEACHER_ID}&tipo=bits").get_json()

    assert listing[0]["consonante"] == "T"
