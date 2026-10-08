from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import jwt
import requests

# CIMA no espera una IP real en "identifier": otros sistemas que ya consumen
# esta misma API envían este valor literal (confirmado inspeccionando su
# tráfico real). Se deja como constante para no reintroducir por error una
# IP real u otro valor.
CIMA_IDENTIFIER_PLACEHOLDER = "Sin IP"
# El endpoint de Google de CIMA no recibe contraseña: espera este literal en
# el campo "password" (según el ejemplo de petición entregado por CIMA).
CIMA_GOOGLE_PASSWORD_MARKER = "email"


class InstitutionalAPIError(RuntimeError):
    """Base error for institutional identity and enrollment lookups."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class InstitutionalConfigurationError(InstitutionalAPIError):
    def __init__(self, message: str = "La API institucional no está configurada."):
        super().__init__(message, 503)


class InstitutionalAuthenticationError(InstitutionalAPIError):
    def __init__(self, message: str = "El ID o la credencial institucional no son válidos."):
        super().__init__(message, 401)


@dataclass(frozen=True)
class AuthenticatedTeacher:
    institutional_id: str
    display_name: str
    role: str
    access_token: str
    expires_in_seconds: int
    # URL ya normalizada para usarse directamente en un <img>. Cadena vacía
    # cuando CIMA no envía `rutaFoto` o el enlace no es interpretable; en ese
    # caso la UI muestra las iniciales.
    photo_url: str = ""
    # Nombre exactamente como lo envía la API institucional (sin el
    # `.capitalize()` de `_format_display_name`), para guardarlo tal cual en
    # `material.fk_user_name`. Vacío cuando el cliente no distingue una forma
    # cruda (demo, tests); en ese caso se usa `display_name` como respaldo.
    raw_name: str = ""

    @property
    def initials(self) -> str:
        parts = [part for part in self.display_name.split() if part]
        return "".join(part[0].upper() for part in parts[:2]) or "DC"


@dataclass(frozen=True)
class Classroom:
    institutional_id: str
    name: str
    grade: str | None
    course: str | None
    period: str | None
    # Valor crudo del campo `type` de CIMA (ej. "N"). No se muestra en
    # ninguna pantalla; se conserva porque el endpoint de alumnos por aula
    # lo vuelve a pedir como parte de la URL (ver list_classroom_students).
    section_type: str | None = None


@dataclass(frozen=True)
class ClassroomStudent:
    institutional_id: str
    apellidos: str
    nombres: str

    @property
    def full_name(self) -> str:
        return f"{self.apellidos.upper()}, {self.nombres.title()}"


@dataclass(frozen=True)
class RecognizedStudent:
    institutional_id: str
    display_name: str
    role: str
    active: bool
    classroom_ids: frozenset[str]


class InstitutionalClient:
    """Strict HTTP adapter for CIMA's source-of-truth API.

    The adapter intentionally fails when a required field is absent. It never
    fabricates teachers, classrooms, enrollments, or student identities.
    """

    def __init__(
        self,
        *,
        base_url: str,
        login_path: str,
        google_login_path: str,
        classrooms_path: str,
        student_path: str,
        service_token: str,
        students_path: str = "",
        id_system: int = 0,
        timeout_seconds: float = 8.0,
        verify_tls: bool = True,
    ):
        self.base_url = base_url.rstrip("/") + "/" if base_url else ""
        self.login_path = login_path
        self.google_login_path = google_login_path
        self.classrooms_path = classrooms_path
        self.students_path = students_path
        self.student_path = student_path
        self.service_token = service_token
        # ID del sistema consumidor registrado ante CIMA (idSystem en el
        # body de login). No confundir con el ID de la docente: identifica
        # a MAXCIM frente a otras aplicaciones que comparten la misma API.
        self.id_system = id_system
        self.timeout_seconds = timeout_seconds
        self.verify_tls = verify_tls
        self.http = requests.Session()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "InstitutionalClient":
        return cls(
            base_url=str(config.get("INSTITUTIONAL_API_BASE_URL") or "").strip(),
            login_path=str(config.get("INSTITUTIONAL_API_LOGIN_PATH") or "").strip(),
            google_login_path=str(
                config.get("INSTITUTIONAL_API_GOOGLE_LOGIN_PATH") or ""
            ).strip(),
            classrooms_path=str(config.get("INSTITUTIONAL_API_CLASSROOMS_PATH") or "").strip(),
            students_path=str(config.get("INSTITUTIONAL_API_STUDENTS_PATH") or "").strip(),
            student_path=str(config.get("INSTITUTIONAL_API_STUDENT_PATH") or "").strip(),
            service_token=str(config.get("INSTITUTIONAL_API_SERVICE_TOKEN") or "").strip(),
            id_system=int(config.get("INSTITUTIONAL_API_ID_SYSTEM") or 0),
            timeout_seconds=float(config.get("INSTITUTIONAL_API_TIMEOUT_SECONDS") or 8),
            verify_tls=bool(config.get("INSTITUTIONAL_API_VERIFY_TLS", True)),
        )

    @property
    def login_ready(self) -> bool:
        return bool(self.base_url and self.login_path and self.classrooms_path and self.id_system)

    @property
    def recognition_ready(self) -> bool:
        return bool(self.base_url and self.student_path and self.service_token)

    @property
    def students_ready(self) -> bool:
        return bool(self.base_url and self.students_path)

    @property
    def google_login_ready(self) -> bool:
        return bool(self.base_url and self.google_login_path and self.id_system)

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise InstitutionalConfigurationError()
        return urljoin(self.base_url, path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.http.request(
                method,
                self._url(path),
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
                verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise InstitutionalAPIError("No se pudo contactar la API institucional.", 503) from exc

        if response.status_code in {401, 403}:
            raise InstitutionalAuthenticationError()
        if response.status_code == 404:
            raise InstitutionalAPIError("El registro institucional solicitado no existe.", 404)
        if response.status_code >= 400:
            raise InstitutionalAPIError(
                f"La API institucional respondió con estado {response.status_code}.",
                502,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise InstitutionalAPIError("La API institucional devolvió una respuesta no válida.") from exc

    @staticmethod
    def _required_text(record: dict[str, Any], field: str) -> str:
        value = str(record.get(field) or "").strip()
        if not value:
            raise InstitutionalAPIError(
                f"La respuesta institucional no contiene el campo obligatorio '{field}'."
            )
        return value

    def _parse_jwt_teacher(self, payload: Any) -> AuthenticatedTeacher:
        """Parses CIMA's `POST .../authentication/with/user` response.

        CIMA replies with `{"content": {"token": "Bearer <jwt>"}}`. There is
        no separate `teacher` envelope: the identity travels as unsigned
        claims inside the JWT itself (`idPersona`, `nombres`,
        `grupoPersonal`, `iat`/`exp`, …). We decode without verifying the
        signature because the token was obtained directly from CIMA over
        HTTPS within this same request — never supplied by the browser —
        so forging it would require compromising that channel, not MAXCIM.
        """
        content = payload.get("content") if isinstance(payload, dict) else None
        raw_token = str(content.get("token") or "").strip() if isinstance(content, dict) else ""
        if not raw_token:
            # CIMA doesn't appear to use a dedicated error envelope for bad
            # credentials on this endpoint: an otherwise-200 response with no
            # token is the observed signal for a rejected login.
            raise InstitutionalAuthenticationError()

        token = raw_token.replace("Bearer", "").strip()
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise InstitutionalAPIError("El token institucional no se pudo decodificar.") from exc

        # No verificamos la firma (ver docstring), pero sí rechazamos un token
        # ya vencido: sin esto, `exp - iat` daría una duración positiva y se
        # abriría una sesión nueva a partir de un JWT caducado.
        try:
            exp = int(claims["exp"])
        except (KeyError, TypeError, ValueError):
            exp = None
        if exp is not None and exp <= int(time.time()):
            raise InstitutionalAuthenticationError(
                "El token institucional ya expiró; vuelve a iniciar sesión."
            )

        teacher_id = self._required_text(claims, "idPersona")
        raw_name = self._required_text(claims, "nombres")
        # No filtramos por categorías de personal específicas (docente,
        # administrativo, etc.): hay distintas categorías/firmas
        # institucionales con acceso legítimo a MAXCIM. Lo único que se
        # bloquea es el alumnado, que comparte el mismo login institucional
        # pero no debe entrar a la consola.
        grupo = self._required_text(claims, "grupoPersonal")
        if "alumno" in grupo.lower():
            raise InstitutionalAuthenticationError(
                "Las cuentas de alumnos no pueden acceder a esta consola."
            )

        return AuthenticatedTeacher(
            institutional_id=teacher_id,
            display_name=self._format_display_name(raw_name),
            role="DOCENTE",
            access_token=token,
            expires_in_seconds=self._expires_in_from_claims(claims),
            photo_url=self._normalize_drive_photo_url(claims.get("rutaFoto")),
            raw_name=raw_name,
        )

    @staticmethod
    def _normalize_drive_photo_url(raw: Any) -> str:
        """Turns CIMA's `rutaFoto` into something a browser `<img>` can load.

        CIMA sends a Google Drive *share* link (p.ej.
        `https://drive.google.com/file/d/<id>/view?usp=drivesdk`), que no se
        puede incrustar directamente. Si logramos extraer el id del archivo lo
        reescribimos al endpoint de miniatura, que sí sirve un binario de
        imagen cuando el archivo está compartido con enlace. Cualquier otra
        URL http(s) se deja pasar tal cual; lo demás se descarta.
        """
        value = str(raw or "").strip()
        if not value:
            return ""

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            return ""

        host = parsed.netloc.lower()
        if host.endswith("drive.google.com"):
            match = re.search(r"/file/d/([^/]+)", parsed.path)
            file_id = match.group(1) if match else (
                parse_qs(parsed.query).get("id", [""])[0]
            )
            if file_id:
                return f"https://drive.google.com/thumbnail?id={file_id}&sz=w160"
        return value

    @staticmethod
    def _format_display_name(raw_name: str) -> str:
        # CIMA envía apellidos y nombres juntos y en mayúsculas, p.ej.
        # "DOCENTE DEMO UNO", sin separar unos de otros. Solo
        # normalizamos la capitalización para mostrarlo.
        return " ".join(part.capitalize() for part in raw_name.split())

    @staticmethod
    def _expires_in_from_claims(claims: dict[str, Any]) -> int:
        try:
            exp = int(claims["exp"])
            issued_lifetime = exp - int(claims["iat"])
        except (KeyError, TypeError, ValueError):
            return 3600
        # La sesión del navegador nunca debe durar más allá del `exp` absoluto
        # del JWT: si el token ya está cerca de expirar, acota a lo que le
        # queda en vez de a su duración original (`exp - iat`).
        remaining = exp - int(time.time())
        return max(1, min(issued_lifetime, remaining))

    def authenticate(self, institutional_id: str, credential: str) -> AuthenticatedTeacher:
        if not self.login_ready:
            raise InstitutionalConfigurationError()
        payload = self._request(
            "POST",
            self.login_path,
            payload={
                "username": institutional_id,
                "password": credential,
                "idSystem": self.id_system,
                "identifier": CIMA_IDENTIFIER_PLACEHOLDER,
            },
        )
        return self._parse_jwt_teacher(payload)

    def authenticate_google(self, verified_email: str) -> AuthenticatedTeacher:
        """Exchange a Google-verified institutional email for the CIMA session.

        MAXCIM ya validó el ID token de Google (firma, nonce, dominio y correo
        verificado); a CIMA solo se le envía el correo. Su endpoint de Google
        recibe el mismo cuerpo que el login normal, con `email` en lugar de
        `username` y el literal "email" como `password`. CIMA asocia el correo
        con una docente activa; MAXCIM nunca crea una docente solo a partir del
        perfil de Google.
        """

        if not self.google_login_ready:
            raise InstitutionalConfigurationError(
                "La validación institucional del acceso con Google no está configurada."
            )
        payload = self._request(
            "POST",
            self.google_login_path,
            payload={
                "email": verified_email,
                "password": CIMA_GOOGLE_PASSWORD_MARKER,
                "idSystem": self.id_system,
                "identifier": CIMA_IDENTIFIER_PLACEHOLDER,
            },
        )
        # Se asume el mismo sobre {"content": {"token": "<jwt>"}} que el login
        # con usuario y contraseña (ver docs/integration-contract.md, 3.2).
        return self._parse_jwt_teacher(payload)

    def list_teacher_classrooms(self, access_token: str, teacher_id: str) -> list[Classroom]:
        """Lists the sections assigned to a teacher.

        Confirmed against the real API (see probar_conexion_aulas.py): the
        path parameter CIMA expects here is `idLogueo`, a per-login id — NOT
        `idPersona` (the stable person id used elsewhere as
        `institutional_id`/`fk_user`). `idLogueo` lives inside the access
        token itself, so it's decoded from `access_token` rather than taken
        from `teacher_id`. `teacher_id` is kept in the signature only for
        interface compatibility with `DemoInstitutionalClient`, which also
        ignores it.
        """
        if not self.login_ready:
            raise InstitutionalConfigurationError()
        try:
            claims = jwt.decode(access_token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise InstitutionalAPIError("El token institucional no se pudo decodificar.") from exc
        login_id = self._required_text(claims, "idLogueo")

        path = self.classrooms_path.format(login_id=login_id)
        payload = self._request("GET", path, token=access_token)
        if not isinstance(payload, list):
            raise InstitutionalAPIError("La API institucional no devolvió la lista de aulas esperada.")

        classrooms: list[Classroom] = []
        for record in payload:
            if not isinstance(record, dict):
                raise InstitutionalAPIError("La API institucional devolvió un aula no válida.")
            # CIMA no separa grado/sección/sede/turno: todo viene junto en
            # `description` (ej. "5TH - D PRIM. GRAU MAÑANA"). Se usa tal
            # cual como nombre en vez de intentar partirlo con heurísticas.
            # `type` y `status` también vienen en la respuesta pero su
            # significado no está confirmado (status es `false` en todas las
            # aulas observadas hasta ahora), así que no se usan todavía.
            classrooms.append(Classroom(
                institutional_id=self._required_text(record, "id"),
                name=self._required_text(record, "description"),
                grade=None,
                course=None,
                period=None,
                section_type=str(record.get("type") or "").strip() or None,
            ))
        return classrooms

    @classmethod
    def _map_classroom_student(cls, record: dict[str, Any]) -> ClassroomStudent:
        """Mapea la respuesta confirmada de CIMA para alumnos por aula.

        Verificado contra datos reales (ver probar_conexion_alumnos.py):
        `idStudentSchool` coincide con el prefijo de `institutionalEmail`
        (ej. "79398411" en "79398411@colegiocima.edu.pe"), así que es el ID
        institucional estable del alumno — no `idPerson`, que es un ID
        interno distinto de CIMA (paralelo a `idPersona` para docentes).
        """
        return ClassroomStudent(
            institutional_id=cls._required_text(record, "idStudentSchool"),
            apellidos=cls._required_text(record, "lastName"),
            nombres=cls._required_text(record, "firstName"),
        )

    def list_classroom_students(
        self, access_token: str, classroom_id: str, section_type: str | None = None
    ) -> list[ClassroomStudent]:
        if not self.students_ready:
            raise InstitutionalConfigurationError(
                "La consulta institucional de alumnos por aula no está configurada."
            )
        # Confirmado contra la API real (ver probar_conexion_alumnos.py):
        # además del ID del aula, esta ruta vuelve a pedir el `type` que ya
        # vino en el listado de aulas ("N" es el único valor observado hasta
        # ahora) y un orden ("A" ascendente / "N" descendente, MAXCIM siempre
        # manda "A"). `section_type` es opcional porque `DemoInstitutionalClient`
        # y los tests con rutas genéricas no lo necesitan; con la ruta real
        # de CIMA sí hace falta pasar `classroom.section_type`.
        path = self.students_path.format(
            classroom_id=classroom_id,
            section_type=section_type or "N",
            order="A",
        )
        payload = self._request("GET", path, token=access_token)
        # Confirmado: una lista JSON desnuda, sin sobre.
        if not isinstance(payload, list):
            raise InstitutionalAPIError(
                "La API institucional no devolvió la lista de alumnos esperada."
            )

        students: list[ClassroomStudent] = []
        for record in payload:
            if not isinstance(record, dict):
                raise InstitutionalAPIError(
                    "La API institucional devolvió un alumno no válido."
                )
            students.append(self._map_classroom_student(record))
        return students

    def get_recognized_student(self, person_id: str) -> RecognizedStudent:
        if not self.recognition_ready:
            raise InstitutionalConfigurationError(
                "La validación institucional del reconocimiento facial no está configurada."
            )
        path = self.student_path.format(person_id=person_id)
        payload = self._request("GET", path, token=self.service_token)
        student = payload.get("student") if isinstance(payload, dict) else None
        if not isinstance(student, dict):
            raise InstitutionalAPIError("La API institucional no devolvió el perfil del alumno esperado.")

        classroom_ids = student.get("classroom_ids")
        if not isinstance(classroom_ids, list):
            raise InstitutionalAPIError("La API institucional no confirmó las matrículas activas del alumno.")

        role = self._required_text(student, "role").upper()
        status = self._required_text(student, "status").upper()
        return RecognizedStudent(
            institutional_id=self._required_text(student, "id"),
            display_name=self._required_text(student, "display_name"),
            role=role,
            active=status in {"ACTIVO", "ACTIVE"},
            classroom_ids=frozenset(str(item).strip() for item in classroom_ids if str(item).strip()),
        )
