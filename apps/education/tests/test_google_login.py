import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet

import app as app_module
from extensions import db
from services.google_oauth import (
    GoogleIdentity,
    GoogleOIDCAuthenticationError,
    GoogleOIDCClient,
)
from services.institutional import AuthenticatedTeacher, Classroom, InstitutionalClient


class FakeGoogleOIDCClient:
    ready = True

    def __init__(self, error=None):
        self.error = error
        self.authorization_request = None
        self.exchange_request = None

    def create_pkce_pair(self):
        return "test-code-verifier", "test-code-challenge"

    def authorization_url(self, **kwargs):
        self.authorization_request = kwargs
        return f"https://accounts.google.test/authorize?state={kwargs['state']}"

    def exchange_and_verify(self, **kwargs):
        self.exchange_request = kwargs
        if self.error:
            raise self.error
        return GoogleIdentity(
            id_token="verified-google-id-token",
            subject="google-subject-1",
            email="docente@cima.edu",
            display_name="Docente Google",
            hosted_domain="cima.edu",
        )


class GoogleInstitutionalClient:
    login_ready = True
    google_login_ready = True

    def __init__(self):
        self.received_google_email = None

    def authenticate_google(self, verified_email):
        self.received_google_email = verified_email
        return AuthenticatedTeacher(
            institutional_id="DOC-GOOGLE-1",
            display_name="Docente Google",
            role="DOCENTE",
            access_token="institutional-access-token",
            expires_in_seconds=3600,
        )

    def list_teacher_classrooms(self, access_token, teacher_id):
        assert access_token == "institutional-access-token"
        assert teacher_id == "DOC-GOOGLE-1"
        return [Classroom(
            institutional_id="AULA-1",
            name="Aula real",
            grade="Primaria",
            course="Tutoría",
            period="2026",
        )]


def make_google_app(google_client=None, institutional_client=None):
    google_client = google_client or FakeGoogleOIDCClient()
    institutional_client = institutional_client or GoogleInstitutionalClient()
    application = app_module.create_app({
        "TESTING": True,
        "DEMO_MODE": False,
        "SECRET_KEY": "test-google-secret",
        "SESSION_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SESSION_COOKIE_SECURE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "INSTITUTIONAL_CLIENT": institutional_client,
        "GOOGLE_OIDC_CLIENT": google_client,
        # Evita que un GOOGLE_OAUTH_REDIRECT_URI del .env local altere la URI derivada.
        "GOOGLE_OAUTH_REDIRECT_URI": "",
    })
    with application.app_context():
        db.create_all()
    return application, google_client, institutional_client


def destroy_app(application):
    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_google_login_creates_only_an_institutional_teacher_session():
    application, google_client, institutional_client = make_google_app()
    client = application.test_client()
    try:
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert b"Continuar con Google" in login_page.data
        login_text = login_page.get_data(as_text=True)
        assert "cada lectura se convierte en una nueva aventura" in login_text
        assert "Materiales, narración oral" not in login_text
        assert "Los alumnos no crean cuentas" not in login_text
        assert "El administrador todavía debe configurar" not in login_text
        assert "MAXCIM no almacena contraseñas" not in login_text

        start = client.get("/login/google?next=/material")
        assert start.status_code == 302
        assert start.location.startswith("https://accounts.google.test/authorize")
        with client.session_transaction() as session:
            state = session["google_oauth_state"]

        callback = client.get(
            f"/auth/google/callback?state={state}&code=authorization-code"
        )
        assert callback.status_code == 302
        assert callback.location == "/material"
        assert institutional_client.received_google_email == "docente@cima.edu"
        assert google_client.authorization_request["redirect_uri"] == (
            "http://localhost/auth/google/callback"
        )
        assert google_client.exchange_request["code_verifier"] == "test-code-verifier"

        # No hay tabla de sesiones: la sesión de la docente vive solo en la
        # cookie firmada. Verificamos ahí que se guardó el ID institucional
        # y que el token de Google (no el institucional) nunca llega a
        # persistirse en ningún lado.
        with client.session_transaction() as session:
            assert session["teacher_id"] == "DOC-GOOGLE-1"
            assert "verified-google-id-token" not in session["teacher_token"]

        assert client.get("/material").status_code == 200
    finally:
        destroy_app(application)


def test_google_callback_rejects_a_mismatched_state():
    application, _, _ = make_google_app()
    client = application.test_client()
    try:
        client.get("/login/google")
        response = client.get(
            "/auth/google/callback?state=attacker-state&code=authorization-code",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "no corresponde a esta sesión" in response.get_data(as_text=True)
        with client.session_transaction() as session:
            assert "teacher_token" not in session
    finally:
        destroy_app(application)


def test_google_identity_error_is_shown_without_creating_a_user():
    error = GoogleOIDCAuthenticationError(
        "Utiliza una cuenta institucional autorizada del colegio."
    )
    application, _, _ = make_google_app(FakeGoogleOIDCClient(error=error))
    client = application.test_client()
    try:
        client.get("/login/google")
        with client.session_transaction() as session:
            state = session["google_oauth_state"]
        response = client.get(
            f"/auth/google/callback?state={state}&code=authorization-code",
            follow_redirects=True,
        )
        assert "cuenta institucional autorizada" in response.get_data(as_text=True)
        with client.session_transaction() as session:
            assert "teacher_token" not in session
    finally:
        destroy_app(application)


def test_google_oidc_builds_workspace_scoped_pkce_authorization_url():
    client = GoogleOIDCClient(
        client_id="client-id",
        client_secret="client-secret",
        allowed_domains=("cima.edu",),
    )
    verifier, challenge = client.create_pkce_pair()
    assert len(verifier) >= 43
    url = client.authorization_url(
        redirect_uri="https://maxcim.example/auth/google/callback",
        state="state-value",
        nonce="nonce-value",
        code_challenge=challenge,
    )
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == ["state-value"]
    assert query["nonce"] == ["nonce-value"]
    assert query["hd"] == ["cima.edu"]
    assert query["code_challenge_method"] == ["S256"]


def test_google_oidc_rejects_an_account_outside_the_allowed_workspace(monkeypatch):
    class TokenResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": "signed-google-id-token"}

    monkeypatch.setattr("services.google_oauth.requests.post", lambda *args, **kwargs: TokenResponse())
    monkeypatch.setattr(
        "services.google_oauth.google_id_token.verify_oauth2_token",
        lambda *args, **kwargs: {
            "sub": "subject",
            "email": "personal@gmail.com",
            "email_verified": True,
            "hd": "gmail.com",
            "nonce": "expected-nonce",
        },
    )
    client = GoogleOIDCClient(
        client_id="client-id",
        client_secret="client-secret",
        allowed_domains=("cima.edu",),
    )
    with pytest.raises(GoogleOIDCAuthenticationError, match="cuenta institucional"):
        client.exchange_and_verify(
            code="code",
            redirect_uri="https://maxcim.example/auth/google/callback",
            nonce="expected-nonce",
            code_verifier="verifier",
        )


def test_institutional_api_maps_google_email_to_an_active_teacher(monkeypatch):
    client = InstitutionalClient(
        base_url="https://api.cima.example",
        login_path="/v1/auth/login",
        google_login_path="/v1/auth/google",
        classrooms_path="/v1/teachers/{teacher_id}/classrooms",
        student_path="/v1/students/{person_id}",
        service_token="service-token",
        id_system=21,
    )
    captured = {}
    now = int(time.time())
    # CIMA no firma con una clave que MAXCIM conozca: se decodifica sin
    # verificar la firma, así que un JWT firmado con cualquier clave alcanza
    # para probar el parseo de claims.
    fake_jwt = jwt.encode(
        {
            "idPersona": "1000001",
            "nombres": "DOCENTE DEMO UNO",
            "grupoPersonal": "DOCENTE COLEGIO",
            "iat": now,
            "exp": now + 3600,
        },
        key="unused-test-key-padded-to-32-bytes!!",
        algorithm="HS256",
    )

    def fake_request(method, path, *, token=None, payload=None):
        captured.update(method=method, path=path, token=token, payload=payload)
        return {"content": {"token": f"Bearer {fake_jwt}"}}

    monkeypatch.setattr(client, "_request", fake_request)
    teacher = client.authenticate_google("docente@cima.edu")
    assert captured == {
        "method": "POST",
        "path": "/v1/auth/google",
        "token": None,
        "payload": {
            "email": "docente@cima.edu",
            "password": "email",
            "idSystem": 21,
            "identifier": "Sin IP",
        },
    }
    assert teacher.institutional_id == "1000001"
    assert teacher.display_name == "Docente Demo Uno"
    assert teacher.role == "DOCENTE"
    assert teacher.access_token == fake_jwt
