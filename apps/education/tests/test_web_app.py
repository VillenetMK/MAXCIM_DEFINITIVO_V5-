def test_teacher_pages_and_pwa_assets_render(client, urls):
    for path in ("/dashboard", "/material", urls.classroom("AULA-REAL-1")):
        response = client.get(path)
        assert response.status_code == 200
        assert b"MAXCIM" in response.data

    assert client.get("/sesiones").status_code == 404

    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.get_json()["short_name"] == "MAXCIM"

    service_worker = client.get("/service-worker.js")
    assert service_worker.status_code == 200
    assert service_worker.headers["Service-Worker-Allowed"] == "/"
    assert service_worker.headers["Cache-Control"] == "no-cache"

    material_page = client.get("/material")
    assert b'id="storyDuration"' in material_page.data
    assert b'min="1" max="15"' in material_page.data

    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}


def test_story_validation_runs_before_gemini(client):
    missing_fields = client.post("/api/story/generate", json={"character": "Luna"})
    assert missing_fields.status_code == 400
    assert "lugar de la historia" in missing_fields.get_json()["error"]

    configured_story = client.post("/api/story/generate", json={
        "character": "Luna",
        "setting": "el bosque",
        "grade_level": "tercero de primaria",
        "objective": "escucha activa",
        "duration_minutes": 5,
    })
    assert configured_story.status_code == 503
    assert "GOOGLE_API_KEY" in configured_story.get_json()["error"]


def test_sentences_generate_validation_runs_before_gemini(client):
    missing_topic = client.post("/api/sentences/generate", json={"count": 5})
    assert missing_topic.status_code == 400
    assert "tema" in missing_topic.get_json()["error"]

    bad_count = client.post("/api/sentences/generate", json={
        "topic": "los animales",
        "count": 99,
    })
    assert bad_count.status_code == 400
    assert "entre 1 y 40" in bad_count.get_json()["error"]

    configured = client.post("/api/sentences/generate", json={
        "topic": "los animales",
        "grade_level": "tercero de primaria",
        "count": 10,
    })
    assert configured.status_code == 503
    assert "GOOGLE_API_KEY" in configured.get_json()["error"]

    # "Generar 5 más" dentro de la revisión: sin tema pero con oraciones previas.
    with_existing = client.post("/api/sentences/generate", json={
        "count": 5,
        "existing": ["El perro corre en el parque."],
    })
    assert with_existing.status_code == 503
    assert "GOOGLE_API_KEY" in with_existing.get_json()["error"]


def test_image_sentences_generate_validation_runs_before_gemini(client):
    missing_topic = client.post("/api/sentences/generate-images", json={"count": 5})
    assert missing_topic.status_code == 400
    assert "tema" in missing_topic.get_json()["error"]

    bad_count = client.post("/api/sentences/generate-images", json={
        "topic": "las profesiones",
        "count": 99,
    })
    assert bad_count.status_code == 400
    assert "entre 1 y 20" in bad_count.get_json()["error"]

    configured = client.post("/api/sentences/generate-images", json={
        "topic": "las profesiones",
        "grade_level": "segundo de primaria",
        "count": 8,
    })
    assert configured.status_code == 503
    assert "GOOGLE_API_KEY" in configured.get_json()["error"]


def test_bits_generate_validation_runs_before_gemini(client):
    missing_syllables = client.post("/api/bits/generate", json={"count": 5})
    assert missing_syllables.status_code == 400
    assert "sílaba" in missing_syllables.get_json()["error"]

    # Sin sílabas que sobrevivan a la normalización, se rechaza antes de
    # llegar a Gemini.
    for invalid in ("", "  ", None, [], ["", " "], {}, 3):
        response = client.post("/api/bits/generate", json={"silabas": invalid, "count": 5})
        assert response.status_code == 400, invalid

    for bad_count in (None, 0, -1, 99, "muchas"):
        response = client.post("/api/bits/generate", json={"silabas": "ma", "count": bad_count})
        assert response.status_code == 400, bad_count
        assert "entre 1 y 20" in response.get_json()["error"]

    too_long = client.post("/api/bits/generate", json={
        "silabas": "ma", "count": 5, "extra_details": "x" * 1001,
    })
    assert too_long.status_code == 413

    configured = client.post("/api/bits/generate", json={"silabas": "ma", "count": 8})
    assert configured.status_code == 503
    assert "GOOGLE_API_KEY" in configured.get_json()["error"]


def test_story_duration_validation_runs_before_gemini(client):
    invalid_story = client.post("/api/story/generate", json={
        "character": "Luna",
        "setting": "el bosque",
        "grade_level": "tercero de primaria",
        "objective": "escucha activa",
        "duration_minutes": 16,
    })
    assert invalid_story.status_code == 400
    assert "entre 1 y 15 minutos" in invalid_story.get_json()["error"]
