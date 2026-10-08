from io import BytesIO
from urllib.parse import quote

import pytest
from PIL import Image

import app as app_module
from extensions import db


def make_demo_app():
    application = app_module.create_app({
        "TESTING": True,
        "DEMO_MODE": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
    })
    with application.app_context():
        db.create_all()
    return application


@pytest.mark.parametrize("target", [
    "/\\evil.com", "//evil.com", "/\t/evil.com", "/\n/evil.com", "https://evil.com",
])
def test_login_next_parameter_cannot_redirect_off_site(target):
    response = make_demo_app().test_client().get(f"/login/google?next={quote(target)}")

    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"


def test_login_next_parameter_keeps_a_local_path():
    response = make_demo_app().test_client().get("/login/google?next=/material")

    assert response.headers["Location"] == "/material"


def test_pages_send_a_script_restricting_content_security_policy(client):
    policy = client.get("/health").headers["Content-Security-Policy"]

    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'self'" in policy
    # form-action rompería el botón de Google (redirige a accounts.google.com).
    assert "form-action" not in policy


def test_uploaded_image_with_huge_declared_dimensions_is_rejected():
    class Upload:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    buffer = BytesIO()
    Image.new("1", (6000, 6000)).save(buffer, format="PNG")

    with pytest.raises(ValueError, match="demasiado grande"):
        app_module.normalize_uploaded_noun_image(Upload(buffer.getvalue()))


def test_robot_interaction_rejects_an_oversized_appreciation(client):
    response = client.post(
        "/api/interacciones",
        data={
            "fk_alumno": "ALU-TEST-1",
            "pregunta": "¿Qué es?",
            "respuesta": "Un gato",
            "apreciacion_robot": "x" * (app_module.MAX_TRANSCRIPT_CHARS + 1),
            "rpta_correcta": "true",
            "audio_rpta": (BytesIO(b"RIFF"), "a.wav"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 413


def production_config(**overrides):
    return {
        "TESTING": False,
        "DEMO_MODE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "MAXCIM_WEBHOOK_SECRET": "robot-secret-0123456789abcdef0123456789abcdef",
        "SECRET_KEY": "k" * 40,
        "SESSION_TOKEN_ENCRYPTION_KEY": "x" * 40,
        **overrides,
    }


@pytest.mark.parametrize("overrides", [
    {"SECRET_KEY": ""},
    {"SECRET_KEY": "corta"},
    {"INSTITUTIONAL_API_BASE_URL": "http://apicima.example.pe"},
    {"INSTITUTIONAL_API_VERIFY_TLS": False},
])
def test_production_refuses_to_start_with_weak_or_insecure_settings(monkeypatch, overrides):
    monkeypatch.setattr(app_module, "gemini_client", None)
    monkeypatch.delenv("ALLOW_INSECURE_SESSION_COOKIE", raising=False)

    with pytest.raises(RuntimeError):
        app_module.create_app(production_config(**overrides))


def test_production_starts_with_https_institutional_api(monkeypatch):
    monkeypatch.setattr(app_module, "gemini_client", None)
    monkeypatch.delenv("ALLOW_INSECURE_SESSION_COOKIE", raising=False)

    application = app_module.create_app(production_config(
        INSTITUTIONAL_API_BASE_URL="https://apicima.example.pe"
    ))

    assert application.config["SESSION_COOKIE_SECURE"] is True
