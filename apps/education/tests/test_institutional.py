import time

import jwt
import pytest

from services.institutional import (
    InstitutionalAPIError,
    InstitutionalAuthenticationError,
    InstitutionalClient,
)


def make_client(**overrides):
    settings = {
        "base_url": "https://api.cima.example",
        "login_path": "/v1/auth/login",
        "google_login_path": "/v1/auth/google",
        "classrooms_path": "/v1/teachers/{login_id}/classrooms",
        "students_path": "/v1/classrooms/{classroom_id}/students",
        "student_path": "/v1/students/{person_id}",
        "service_token": "service-token",
        "id_system": 21,
    }
    settings.update(overrides)
    return InstitutionalClient(**settings)


# Todos los datos de identidad de estas pruebas son ficticios a propósito
# (idPersona 1000001, "DOCENTE DEMO UNO", usuario docente.demo, idLogueo
# 500001). Antes existían credenciales y nombres reales versionados aquí; se
# sustituyeron en la remediación de C-01 (ver docs/security-remediation.md).
# Nunca commitear usuario/contraseña ni idPersona/nombres reales de CIMA.
def make_cima_jwt(**claim_overrides):
    now = int(time.time())
    claims = {
        "idPersona": "1000001",
        "nombres": "DOCENTE DEMO UNO",
        "grupoPersonal": "DOCENTE COLEGIO",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    # CIMA no firma con una clave que MAXCIM conozca; se decodifica sin
    # verificar la firma (ver InstitutionalClient._parse_jwt_teacher), así
    # que cualquier clave de prueba alcanza para producir un JWT válido.
    return jwt.encode(claims, key="unused-test-key-padded-to-32-bytes!!", algorithm="HS256")


def test_authenticate_sends_the_cima_login_contract(monkeypatch):
    client = make_client()
    captured = {}
    fake_jwt = make_cima_jwt()

    def fake_request(method, path, *, token=None, payload=None):
        captured.update(method=method, path=path, token=token, payload=payload)
        return {"content": {"token": f"Bearer {fake_jwt}"}}

    monkeypatch.setattr(client, "_request", fake_request)
    teacher = client.authenticate("docente.demo", "clave-demo-no-real")

    assert captured == {
        "method": "POST",
        "path": "/v1/auth/login",
        "token": None,
        "payload": {
            "username": "docente.demo",
            "password": "clave-demo-no-real",
            "idSystem": 21,
            "identifier": "Sin IP",
        },
    }
    assert teacher.institutional_id == "1000001"
    assert teacher.display_name == "Docente Demo Uno"
    # `raw_name` conserva el valor tal como lo envía CIMA (sin el
    # `.capitalize()` de `display_name`): es lo que se guarda en
    # `material.fk_user_name`.
    assert teacher.raw_name == "DOCENTE DEMO UNO"
    assert teacher.role == "DOCENTE"
    assert teacher.access_token == fake_jwt
    assert teacher.expires_in_seconds == 3600
    assert teacher.photo_url == ""


def test_authenticate_rewrites_the_drive_photo_into_an_embeddable_url(monkeypatch):
    client = make_client()
    fake_jwt = make_cima_jwt(
        rutaFoto="https://drive.google.com/file/d/ABC123xyz/view?usp=drivesdk"
    )
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": f"Bearer {fake_jwt}"}}
    )

    teacher = client.authenticate("docente.demo", "clave-demo-no-real")

    assert teacher.photo_url == (
        "https://drive.google.com/thumbnail?id=ABC123xyz&sz=w160"
    )


def test_authenticate_ignores_a_photo_route_that_is_not_http(monkeypatch):
    client = make_client()
    fake_jwt = make_cima_jwt(rutaFoto="javascript:alert(1)")
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": f"Bearer {fake_jwt}"}}
    )

    teacher = client.authenticate("docente.demo", "clave-demo-no-real")

    assert teacher.photo_url == ""


def test_authenticate_accepts_any_non_student_grupo_personal(monkeypatch):
    # No filtramos por categoría de personal (docente, administrativo,
    # etc.): solo se bloquea al alumnado.
    client = make_client()
    fake_jwt = make_cima_jwt(grupoPersonal="ADMINISTRATIVO")
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": fake_jwt}}
    )

    teacher = client.authenticate("docente.demo", "clave-demo-no-real")

    assert teacher.role == "DOCENTE"


def test_authenticate_rejects_student_accounts(monkeypatch):
    client = make_client()
    fake_jwt = make_cima_jwt(grupoPersonal="ALUMNO COLEGIO")
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": fake_jwt}}
    )

    with pytest.raises(InstitutionalAuthenticationError):
        client.authenticate("alumno1", "credencial")


def test_authenticate_treats_a_missing_token_as_invalid_credentials(monkeypatch):
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"content": {}})

    with pytest.raises(InstitutionalAuthenticationError):
        client.authenticate("docente.demo", "credencial-incorrecta")


def test_authenticate_rejects_an_already_expired_jwt(monkeypatch):
    client = make_client()
    now = int(time.time())
    fake_jwt = make_cima_jwt(iat=now - 7200, exp=now - 60)
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": fake_jwt}}
    )

    with pytest.raises(InstitutionalAuthenticationError):
        client.authenticate("docente.demo", "credencial")


def test_session_lifetime_never_exceeds_the_jwt_absolute_expiry(monkeypatch):
    client = make_client()
    now = int(time.time())
    # Token de 5 h emitido hace 4 h 59 m: solo le queda ~60 s.
    fake_jwt = make_cima_jwt(iat=now - 17_940, exp=now + 60)
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"content": {"token": fake_jwt}}
    )

    teacher = client.authenticate("docente.demo", "credencial")
    assert teacher.expires_in_seconds <= 60


def test_list_teacher_classrooms_uses_idlogueo_from_the_token_not_teacher_id(monkeypatch):
    client = make_client()
    fake_token = make_cima_jwt(idLogueo="500001")
    captured = {}

    def fake_request(method, path, *, token=None, payload=None):
        captured.update(method=method, path=path, token=token, payload=payload)
        return [
            {"id": 2331, "type": "N", "status": False, "description": "5TH - D PRIM. GRAU MAÑANA"},
            {"id": 2332, "type": "N", "status": False, "description": "5TH - E PRIM. GRAU MAÑANA"},
        ]

    monkeypatch.setattr(client, "_request", fake_request)
    # El teacher_id pasado aquí (idPersona, "1000001") es deliberadamente
    # distinto del idLogueo del token, para probar que se ignora.
    classrooms = client.list_teacher_classrooms(fake_token, "1000001")

    assert captured == {
        "method": "GET",
        "path": "/v1/teachers/500001/classrooms",
        "token": fake_token,
        "payload": None,
    }
    assert [c.institutional_id for c in classrooms] == ["2331", "2332"]
    assert [c.name for c in classrooms] == [
        "5TH - D PRIM. GRAU MAÑANA",
        "5TH - E PRIM. GRAU MAÑANA",
    ]
    assert all(c.grade is None and c.course is None and c.period is None for c in classrooms)
    assert [c.section_type for c in classrooms] == ["N", "N"]


def test_list_teacher_classrooms_rejects_a_non_list_payload(monkeypatch):
    client = make_client()
    fake_token = make_cima_jwt(idLogueo="500001")
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"classrooms": []})

    with pytest.raises(InstitutionalAPIError):
        client.list_teacher_classrooms(fake_token, "1000001")


def test_list_classroom_students_sends_the_cima_students_path(monkeypatch):
    client = make_client(
        students_path="/api/v2/studentschool/list/gradesectiongroup/{classroom_id}/type/{section_type}/order/{order}"
    )
    captured = {}
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: captured.update(
            method=a[0], path=a[1], token=k.get("token"), payload=k.get("payload")
        ) or []
    )

    client.list_classroom_students("teacher-token", "2331", "N")

    assert captured == {
        "method": "GET",
        "path": "/api/v2/studentschool/list/gradesectiongroup/2331/type/N/order/A",
        "token": "teacher-token",
        "payload": None,
    }


def test_list_classroom_students_defaults_section_type_when_missing(monkeypatch):
    client = make_client(
        students_path="/api/v2/studentschool/list/gradesectiongroup/{classroom_id}/type/{section_type}/order/{order}"
    )
    captured = {}
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: captured.update(path=a[1]) or []
    )

    # Sin section_type (ej. viene de un Classroom sin ese dato), se usa "N"
    # por ser el único valor observado hasta ahora, en vez de fallar.
    client.list_classroom_students("teacher-token", "2331")

    assert captured["path"] == "/api/v2/studentschool/list/gradesectiongroup/2331/type/N/order/A"


def test_list_classroom_students_maps_the_confirmed_cima_fields(monkeypatch):
    client = make_client()
    captured = {}

    def fake_request(method, path, *, token=None, payload=None):
        captured.update(method=method, path=path, token=token, payload=payload)
        # Forma real observada (ver probar_conexion_alumnos.py); sin sobre,
        # con campos extra (idPerson, photoRoute, institutionalEmail,
        # studentSchool) que MAXCIM ignora.
        return [
            {
                "idStudentSchool": 79398411,
                "firstName": "CIELITO ABIGAIL",
                "lastName": "CABRERA BURGA",
                "idPerson": 66696,
                "photoRoute": "https://drive.google.com/...",
                "institutionalEmail": "79398411@colegiocima.edu.pe",
                "studentSchool": "CABRERA BURGA CIELITO ABIGAIL",
            },
            {
                "idStudentSchool": 79333645,
                "firstName": "KIARA IVETT",
                "lastName": "CAIRAMPOMA CORDOVA",
                "idPerson": 66877,
                "photoRoute": "https://drive.google.com/...",
                "institutionalEmail": "79333645@colegiocima.edu.pe",
                "studentSchool": "CAIRAMPOMA CORDOVA KIARA IVETT",
            },
        ]

    monkeypatch.setattr(client, "_request", fake_request)

    students = client.list_classroom_students("teacher-token", "2331", "N")

    assert captured == {
        "method": "GET",
        "path": "/v1/classrooms/2331/students",
        "token": "teacher-token",
        "payload": None,
    }
    assert [student.institutional_id for student in students] == ["79398411", "79333645"]
    assert [student.full_name for student in students] == [
        "CABRERA BURGA, Cielito Abigail",
        "CAIRAMPOMA CORDOVA, Kiara Ivett",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"students": [{"idStudentSchool": 1, "lastName": "X", "firstName": "Y"}]},
        [{"idStudentSchool": "ALU-SIN-NOMBRE"}],
        ["no-es-un-registro"],
    ],
)
def test_list_classroom_students_rejects_malformed_payload(monkeypatch, payload):
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: payload)

    with pytest.raises(InstitutionalAPIError):
        client.list_classroom_students("teacher-token", "AULA-1A")


def test_students_path_is_loaded_from_config_and_exposes_readiness():
    client = InstitutionalClient.from_config(
        {
            "INSTITUTIONAL_API_BASE_URL": "https://api.cima.example",
            "INSTITUTIONAL_API_STUDENTS_PATH": "/v1/aulas/{classroom_id}/alumnos",
        }
    )

    assert client.students_path == "/v1/aulas/{classroom_id}/alumnos"
    assert client.students_ready is True
