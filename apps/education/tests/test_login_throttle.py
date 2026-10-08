import pytest
from cryptography.fernet import Fernet

import app as app_module
from extensions import db
from services.institutional import (
    AuthenticatedTeacher,
    InstitutionalAPIError,
    InstitutionalAuthenticationError,
)
from services.login_throttle import LoginThrottle


class PasswordInstitutionalClient:
    login_ready = True
    google_login_ready = False

    def __init__(self):
        self.calls = 0
        self.outage = False

    def authenticate(self, institutional_id, credential):
        self.calls += 1
        if self.outage:
            raise InstitutionalAPIError("No se pudo contactar la API institucional.", 503)
        if credential != "correcta":
            raise InstitutionalAuthenticationError()
        return AuthenticatedTeacher(
            institutional_id="DOC-1",
            display_name="Docente Uno",
            role="DOCENTE",
            access_token="institutional-access-token",
            expires_in_seconds=3600,
        )


@pytest.fixture
def throttled_app():
    institutional = PasswordInstitutionalClient()
    application = app_module.create_app({
        "TESTING": True,
        "DEMO_MODE": False,
        "SECRET_KEY": "test-throttle-secret",
        "SESSION_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SESSION_COOKIE_SECURE": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "INSTITUTIONAL_CLIENT": institutional,
        "LOGIN_MAX_ATTEMPTS": 3,
        "LOGIN_IP_MAX_ATTEMPTS": 6,
        "LOGIN_LOCKOUT_SECONDS": 900,
    })
    with application.app_context():
        db.create_all()
    yield application, institutional
    with application.app_context():
        db.session.remove()
        db.drop_all()


def attempt(client, user="docente1", password="mala"):
    return client.post(
        "/login", data={"institutional_id": user, "credential": password}
    )


def test_third_failed_attempt_locks_the_login(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()

    first = attempt(client)
    assert first.status_code == 401
    assert "Te quedan 2 intentos" in first.get_data(as_text=True)

    second = attempt(client)
    assert second.status_code == 401
    assert "Te quedan 1 intento." in second.get_data(as_text=True)

    third = attempt(client)
    assert third.status_code == 429
    assert "Demasiados intentos fallidos" in third.get_data(as_text=True)
    assert institutional.calls == 3


def test_locked_login_rejects_even_the_right_password_without_calling_cima(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()
    for _ in range(3):
        attempt(client)

    blocked = attempt(client, password="correcta")

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert institutional.calls == 3
    with client.session_transaction() as session:
        assert "teacher_id" not in session


def test_successful_login_resets_the_counter(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()
    attempt(client)
    attempt(client)

    ok = attempt(client, password="correcta")
    assert ok.status_code == 302

    client.post("/logout")
    assert attempt(client).status_code == 401
    assert attempt(client).status_code == 401


def test_lock_is_scoped_to_the_user_id(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()
    for _ in range(3):
        attempt(client, user="docente1")

    other = attempt(client, user="docente2", password="correcta")

    assert other.status_code == 302


def test_cima_outage_does_not_count_as_a_failed_attempt(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()
    institutional.outage = True
    for _ in range(5):
        assert attempt(client).status_code == 503

    institutional.outage = False
    assert attempt(client, password="correcta").status_code == 302


def test_empty_fields_do_not_count(throttled_app):
    application, _ = throttled_app
    client = application.test_client()
    for _ in range(5):
        assert client.post("/login", data={"institutional_id": "", "credential": ""}).status_code == 400

    assert attempt(client, password="correcta").status_code == 302


def test_throttle_lockout_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("services.login_throttle.time.monotonic", lambda: now[0])
    throttle = LoginThrottle(max_attempts=3, lockout_seconds=60)

    assert throttle.record_failure("1.1.1.1", "ana") == 2
    assert throttle.record_failure("1.1.1.1", "ana") == 1
    assert throttle.record_failure("1.1.1.1", "ana") == 0
    assert throttle.retry_after("1.1.1.1", "ana") == 60

    now[0] += 61
    assert throttle.retry_after("1.1.1.1", "ana") == 0
    # Ventana nueva: vuelven los 3 intentos completos.
    assert throttle.record_failure("1.1.1.1", "ana") == 2


def test_throttle_is_case_insensitive_and_per_ip():
    throttle = LoginThrottle(max_attempts=3, lockout_seconds=60)
    for _ in range(3):
        throttle.record_failure("1.1.1.1", "Ana")

    assert throttle.retry_after("1.1.1.1", "ana") > 0
    assert throttle.retry_after("2.2.2.2", "ana") == 0


def test_one_ip_cannot_spray_many_accounts(throttled_app):
    application, institutional = throttled_app
    client = application.test_client()

    statuses = [attempt(client, user=f"docente{i}").status_code for i in range(6)]
    blocked = attempt(client, user="otra-docente", password="correcta")

    assert statuses == [401] * 6
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    calls_after = institutional.calls
    attempt(client, user="otra-docente", password="correcta")
    assert institutional.calls == calls_after


@pytest.mark.parametrize("field", ["institutional_id", "credential"])
def test_oversized_login_fields_are_rejected_without_calling_cima(throttled_app, field):
    application, institutional = throttled_app
    client = application.test_client()
    data = {"institutional_id": "docente1", "credential": "mala", field: "x" * 1000}

    response = client.post("/login", data=data)

    assert response.status_code == 400
    assert institutional.calls == 0
