from __future__ import annotations

import hashlib
import io
import math
import os
import re
import struct
import wave
import zlib

from services.institutional import (
    AuthenticatedTeacher,
    Classroom,
    ClassroomStudent,
    RecognizedStudent,
)


DEMO_TEACHER_ID = "DOC-DEMO-01"
DEMO_TEACHER_NAME = "Marín Reyes, Camila"
DEMO_ACCESS_TOKEN = "maxcim-demo-only-token"

DEMO_CLASSROOMS = (
    Classroom("AULA-DEMO-3A", "3RO A — Tutoría", "Tercero de primaria", "Tutoría", "2026"),
    Classroom("AULA-DEMO-2B", "2DO B — Comunicación", "Segundo de primaria", "Comunicación", "2026"),
    Classroom("AULA-DEMO-4C", "4TO C — Tutoría", "Cuarto de primaria", "Tutoría", "2026"),
    Classroom("AULA-DEMO-1A", "1RO A — Comunicación", "Primero de primaria", "Comunicación", "2026"),
    Classroom("AULA-DEMO-5B", "5TO B — Tutoría", "Quinto de primaria", "Tutoría", "2026"),
)

# Datos totalmente ficticios y aislados para recorrer el listado sin API.
DEMO_CLASSROOM_STUDENTS = {
    classroom.institutional_id: (
        ClassroomStudent(
            f"ALU-DEMO-{classroom.institutional_id[-2:]}-01",
            "Campos Nube",
            "Valentina",
        ),
        ClassroomStudent(
            f"ALU-DEMO-{classroom.institutional_id[-2:]}-02",
            "Ríos Estrella",
            "Mateo",
        ),
        ClassroomStudent(
            f"ALU-DEMO-{classroom.institutional_id[-2:]}-03",
            "Flores Luna",
            "Emilia",
        ),
    )
    for classroom in DEMO_CLASSROOMS
}


class DemoInstitutionalClient:
    """Isolated institutional adapter used only by the test repository.

    It implements the same interface as the real adapter so the screens and
    business flow stay identical without contacting CIMA's source database.
    """

    login_ready = True
    google_login_ready = True
    recognition_ready = True
    students_ready = True

    def authenticate(self, institutional_id: str, credential: str) -> AuthenticatedTeacher:
        teacher_id = str(institutional_id or "").strip()
        if not teacher_id or not str(credential or "").strip():
            from services.institutional import InstitutionalAuthenticationError

            raise InstitutionalAuthenticationError()
        return self._teacher(teacher_id)

    def authenticate_google(self, verified_email: str) -> AuthenticatedTeacher:
        return self._teacher(DEMO_TEACHER_ID)

    @staticmethod
    def _teacher(teacher_id: str = DEMO_TEACHER_ID) -> AuthenticatedTeacher:
        return AuthenticatedTeacher(
            institutional_id=teacher_id,
            display_name=DEMO_TEACHER_NAME,
            role="DOCENTE",
            access_token=DEMO_ACCESS_TOKEN,
            expires_in_seconds=8 * 60 * 60,
        )

    def list_teacher_classrooms(self, access_token: str, teacher_id: str) -> list[Classroom]:
        return list(DEMO_CLASSROOMS)

    def list_classroom_students(
        self, access_token: str, classroom_id: str, section_type: str | None = None
    ) -> list[ClassroomStudent]:
        return list(DEMO_CLASSROOM_STUDENTS.get(classroom_id, ()))

    def get_recognized_student(self, person_id: str) -> RecognizedStudent:
        normalized_id = str(person_id or "ALU-DEMO-1042").strip() or "ALU-DEMO-1042"
        names = {
            "ALU-DEMO-1042": "Valeria Mendoza",
            "ALU-DEMO-2048": "Mateo Salazar",
            "ALU-DEMO-4096": "Luciana Torres",
        }
        return RecognizedStudent(
            institutional_id=normalized_id,
            display_name=names.get(normalized_id, "Alumno de pruebas"),
            role="ALUMNO",
            active=True,
            classroom_ids=frozenset(item.institutional_id for item in DEMO_CLASSROOMS),
        )


def create_demo_story(
    *,
    character: str,
    setting: str,
    grade_level: str,
    objective: str,
    extra_details: str,
    duration_minutes: int,
    words_per_minute: int = 125,
) -> dict[str, object]:
    """Build a deterministic story-shaped fixture with the selected duration."""

    details = extra_details or "una sorpresa que el grupo descubre en conjunto"
    target_words = max(80, duration_minutes * words_per_minute)
    passages = [
        f"Había una vez {character}, que vivía una mañana especial en {setting}.",
        f"Todo comenzó cuando apareció un reto relacionado con {objective}.",
        f"Para resolverlo, recordó que también debía incluir {details}.",
        "Primero observó con calma, escuchó a quienes estaban cerca y preguntó qué necesitaban.",
        "Cada respuesta aportó una pista distinta y convirtió el problema en una oportunidad para colaborar.",
        "Aunque el camino parecía difícil, nadie se burló de las ideas de los demás.",
        "El personaje principal explicó su propuesta con palabras claras y esperó su turno para continuar.",
        "Después, el grupo probó una solución, reconoció lo que podía mejorar y volvió a intentarlo.",
        "Con paciencia descubrieron que aprender juntos era más valioso que terminar primero.",
        "Al caer la tarde, el reto estaba resuelto y todos podían contar qué habían aprendido.",
        f"La aventura mostró a estudiantes de {grade_level} que {objective} puede practicarse cada día.",
        "Desde entonces, cuando surgía una dificultad, respiraban, escuchaban y buscaban una respuesta entre todos.",
    ]
    words: list[str] = []
    passage_index = 0
    while len(words) < target_words - 1:
        words.extend(passages[passage_index % len(passages)].split())
        passage_index += 1
    words = words[: target_words - 1]
    words.append("Fin.")
    story = " ".join(words)
    title_character = re.sub(r"\s+", " ", character).strip().capitalize()
    return {
        "title": f"La aventura de {title_character}",
        "story": story,
        "summary": (
            f"{title_character} vive una aventura en {setting} y aprende, junto a sus amigos, "
            f"la importancia de {objective}."
        ),
        "target_duration_minutes": duration_minutes,
        "word_count": len(words),
    }


def create_demo_questions(text: str, counts: dict[str, int]) -> dict[str, list[dict[str, str]]]:
    first_sentence = next(
        (item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()),
        "El personaje comenzó una aventura.",
    )
    templates = {
        "literales": (
            "¿Qué ocurrió al inicio de la historia?",
            f"Se menciona explícitamente que: {first_sentence}",
        ),
        "inferenciales": (
            "¿Por qué fue importante escuchar las ideas de los demás?",
            "Porque permitió comprender el problema y encontrar una solución en equipo.",
        ),
        "criticas": (
            "¿Qué habrías hecho tú ante el mismo reto y por qué?",
            "Respuesta personal argumentada y relacionada con lo ocurrido en el cuento.",
        ),
    }
    result: dict[str, list[dict[str, str]]] = {}
    for question_type, count in counts.items():
        question, expected = templates[question_type]
        result[question_type] = [
            {
                "pregunta": question if index == 0 else f"{question[:-1]} ({index + 1})?",
                "respuesta_esperada": expected,
            }
            for index in range(count)
        ]
    return result


def create_demo_sentences(topic: str, grade_level: str, count: int) -> list[str]:
    """Deterministic sentence-shaped fixture for DEMO_MODE."""
    topic = re.sub(r"\s+", " ", topic).strip() or "el tema de clase"
    grade_level = re.sub(r"\s+", " ", grade_level).strip() or "el aula"
    templates = [
        f"Hoy aprendemos sobre {topic} con mucha atención.",
        f"Los estudiantes de {grade_level} conversan sobre {topic}.",
        f"Escribo una idea clara acerca de {topic} en mi cuaderno.",
        f"Comparto con mi compañero lo que sé de {topic}.",
        f"Leo en voz alta esta oración sobre {topic}.",
        f"Pienso una pregunta interesante relacionada con {topic}.",
    ]
    return [templates[index % len(templates)] for index in range(max(1, count))]


def create_demo_image_sentences(topic: str, grade_level: str, count: int) -> list[dict]:
    """Deterministic {texto, sustantivos:[a, b]} fixture for DEMO_MODE."""
    topic = re.sub(r"\s+", " ", topic).strip() or "la clase"
    templates = [
        (f"El niño observa el gato mientras habla de {topic}.", ["niño", "gato"]),
        (f"La maestra guarda el libro en la mesa al terminar {topic}.", ["libro", "mesa"]),
        ("Un perro corre detrás de la pelota en el patio.", ["perro", "pelota"]),
        ("La niña dibuja una casa junto a un árbol.", ["casa", "árbol"]),
        ("El agricultor lleva la fruta en una canasta.", ["fruta", "canasta"]),
        ("El pez nada cerca de la roca del río.", ["pez", "roca"]),
    ]
    return [
        {"texto": templates[index % len(templates)][0],
         "sustantivos": list(templates[index % len(templates)][1])}
        for index in range(max(1, count))
    ]


def extract_demo_sentences(file_storage) -> list[str]:
    """Deterministic sentence segmentation for DEMO_MODE: reuses the demo
    document text and splits it by line breaks and sentence-final punctuation."""
    text, _summary = process_demo_document(file_storage)
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sentences.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?…])\s+", line)
            if part.strip()
        )
    # De-dupe keeping order, so a short demo file still yields a tidy list.
    seen: set[str] = set()
    unique = []
    for sentence in sentences:
        key = sentence.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(sentence)
    return unique


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Minimal solid-color PNG encoder (no Pillow dependency)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def create_demo_noun_image(palabra: str, oracion: str = "") -> bytes:
    """Deterministic placeholder PNG for DEMO_MODE: a solid tile whose color is
    derived from the noun, so each one looks distinct without calling Gemini."""
    digest = hashlib.sha256((palabra or "x").strip().lower().encode("utf-8")).digest()
    # Pastel-ish: keep every channel in the upper half so it reads as a soft tile.
    rgb = tuple(128 + (digest[i] % 128) for i in range(3))
    return _solid_png(256, 256, rgb)


DEMO_BITS_WORD_POOL = (
    "barco", "bola", "casa", "cama", "dedo", "dado", "foca", "flor",
    "gato", "globo", "hoja", "huevo", "jirafa", "jabón", "koala", "kiwi",
    "luna", "lápiz", "mano", "mesa", "nube", "naranja", "ñandú", "ñoquis",
    "pelota", "pato", "queso", "quena", "rana", "ratón", "sol", "sapo",
    "taza", "tomate", "vaca", "vela", "waffle", "wifi", "xilófono", "xenón",
    "yate", "yoyo", "zapato", "zorro",
)


def create_demo_bits_words(silabas: list[str], count: int) -> list[dict]:
    """Deterministic {palabra} fixture for DEMO_MODE: cycles through the
    given syllables, picking a demo word that starts with each one (falls
    back to the full pool when none matches, ignoring the syllable-count mix,
    mirrors create_demo_sentences ignoring the real topic)."""
    syllables = list(silabas) or [""]
    pools = {
        syllable: [w for w in DEMO_BITS_WORD_POOL if w.startswith(syllable)]
        or list(DEMO_BITS_WORD_POOL)
        for syllable in syllables
    }
    words = []
    for index in range(max(1, count)):
        pool = pools[syllables[index % len(syllables)]]
        words.append(pool[index % len(pool)])
    return [{"palabra": word} for word in words]


def create_demo_bit_question(palabra: str) -> str:
    """Deterministic question stem for DEMO_MODE: never contains the word (it
    is the answer), so it mirrors what the real model must respect."""
    return "Esto es…"


def create_demo_scene_plan(story_text: str) -> dict:
    """DEMO_MODE: parte el cuento en hasta 4 escenas de oraciones consecutivas
    que lo cubren completo (cada escena se narra con su propio audio); la
    imagen de cada una es un mosaico de color (ver create_demo_noun_image).
    Espera un cuento de al menos 2 oraciones, como el resto del flujo."""
    normalized = " ".join(story_text.split())
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", normalized) if s]
    scene_count = min(4, len(sentences))
    size = math.ceil(len(sentences) / scene_count)
    chunks = [" ".join(sentences[i:i + size]) for i in range(0, len(sentences), size)]
    return {
        "personaje": "demo character",
        "escenas": [
            {"texto": chunk, "descripcion": f"Escena {index}: {chunk[:60]}"}
            for index, chunk in enumerate(chunks, start=1)
        ],
    }


def extract_demo_bits_words(file_storage) -> list[dict]:
    """Deterministic {palabra} fixture for DEMO_MODE: reuses the demo document
    text to decide how many words to return (mirrors extract_demo_image_sentences)."""
    text, _summary = process_demo_document(file_storage)
    words = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", text)
    count = max(3, min(8, len(words) or 3))
    return create_demo_bits_words([""], count)


def extract_demo_image_sentences(file_storage) -> list[dict]:
    """Deterministic {texto, sustantivos:[a, b]} fixture for DEMO_MODE: reuses
    the demo document text to decide how many sentences to return, then pairs
    each with a fixed set of concrete, drawable nouns (mirrors how
    extract_demo_sentences reuses the demo text for the plain `oracion` flow)."""
    text, _summary = process_demo_document(file_storage)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sentences: list[str] = []
    for line in lines:
        sentences.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?…])\s+", line)
            if part.strip()
        )
    count = max(3, min(6, len(sentences) or 3))
    return create_demo_image_sentences("la lectura", "el aula", count)


def process_demo_document(file_storage) -> tuple[str, str]:
    filename = os.path.basename(file_storage.filename or "cuento")
    extension = os.path.splitext(filename)[1].lower()
    raw = file_storage.read()
    decoded = raw.decode("utf-8", errors="ignore").strip() if extension == ".txt" else ""
    text = decoded if len(decoded) >= 20 else (
        f"Este es el contenido de prueba extraído de {filename}. "
        "Una niña encontró una caja de historias en la biblioteca de su colegio. "
        "Invitó a sus compañeros a escuchar, imaginar un final y compartir sus ideas con respeto. "
        "Entre todos descubrieron que cada respuesta podía enriquecer la aventura."
    )
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    summary = " ".join(sentences[:2])[:600]
    return text, summary


def create_demo_wav(text: str) -> tuple[bytes, float]:
    """Return a lightweight audible placeholder WAV whose length follows the text."""

    word_count = max(1, len(text.split()))
    duration_seconds = float(max(4, min(30, round(word_count / 2.2))))

    sample_rate = 8_000
    pattern_seconds = 2
    pattern = bytearray()
    tones = (523.25, 659.25, 783.99, 659.25)
    for sample in range(sample_rate * pattern_seconds):
        quarter = (sample // (sample_rate // 2)) % len(tones)
        frequency = tones[quarter]
        value = 128 + int(12 * math.sin(2 * math.pi * frequency * sample / sample_rate))
        pattern.append(max(0, min(255, value)))

    frame_count = int(duration_seconds * sample_rate)
    repeats, remainder = divmod(frame_count, len(pattern))
    frames = bytes(pattern) * repeats + bytes(pattern[:remainder])
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return output.getvalue(), duration_seconds


def create_demo_assessment(base_metrics: dict) -> dict[str, object]:
    participation = float(base_metrics.get("porcentaje_participacion") or 0)
    comprehension = float(base_metrics.get("porcentaje_comprension") or 0)
    score = round((participation + comprehension) / 2, 2)
    criteria = {
        "comunicacion_oral": {
            "nombre": "Comunicación oral",
            "puntuacion": score,
            "evidencia": "El alumno respondió con frases relacionadas con las preguntas de MAXCIM.",
        },
        "escucha_activa": {
            "nombre": "Escucha activa",
            "puntuacion": participation,
            "evidencia": "Respondió después de los turnos de pregunta registrados.",
        },
        "respeto_turnos": {
            "nombre": "Respeto de turnos",
            "puntuacion": participation,
            "evidencia": "La secuencia de conversación alternó entre MAXCIM y el alumno.",
        },
        "coherencia": {
            "nombre": "Coherencia de las respuestas",
            "puntuacion": comprehension,
            "evidencia": "Las respuestas marcadas como correctas guardan relación con la actividad.",
        },
    }
    return {
        "criterios": criteria,
        "porcentaje_interaccion_oral": score,
        "resumen": "Interacción de prueba completada y lista para la revisión de la docente.",
        "recomendacion_docente": "Revisa los porcentajes y ajusta la retroalimentación antes de aprobar.",
    }
