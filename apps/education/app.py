import io
import hmac
import json
import mimetypes
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import tempfile
import time
import unicodedata
import uuid
import wave
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from itsdangerous import BadData, URLSafeTimedSerializer
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session as browser_session,
    url_for,
)
from google import genai
from google.genai import types
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager

from extensions import db
from models import (
    Interaccion,
    Material,
    Periodo,
    Tema,
    TIPO_CUENTO,
    TIPO_ORACION,
    TIPO_ORACION_IMAGEN,
    TIPO_BITS,
    TIPOS_MATERIAL,
)
from services.demo import (
    DemoInstitutionalClient,
    create_demo_bit_question,
    create_demo_bits_words,
    create_demo_image_sentences,
    create_demo_noun_image,
    create_demo_questions,
    create_demo_scene_plan,
    create_demo_sentences,
    create_demo_story,
    create_demo_wav,
    extract_demo_bits_words,
    extract_demo_image_sentences,
    extract_demo_sentences,
    process_demo_document,
)
from services.fish_audio import FishAudioClient
from services.google_oauth import GoogleOIDCClient, GoogleOIDCError
from services.institutional import (
    InstitutionalAPIError,
    InstitutionalAuthenticationError,
    InstitutionalClient,
    InstitutionalConfigurationError,
)
from services.login_throttle import LoginThrottle
from services.periodos import current_periodo, periodo_for_date

load_dotenv()

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
# Modelo de generación de imágenes para "oraciones con imágenes": cada
# sustantivo concreto de la oración se dibuja por separado (NO la oración
# entera) y esa imagen ocupa su hueco; el texto va aparte, como HTML.
# Requiere una versión de `google-genai` que acepte response_modalities=["IMAGE"].
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.7-flash")

gemini_client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else None

# Fish Audio narra los cuentos (texto a voz). La voz sale de
# FISH_AUDIO_REFERENCE_ID (un ID de la biblioteca de voces de Fish); sin él,
# Fish usa su voz por defecto. Sin FISH_API_KEY no hay narración real (en
# DEMO_MODE se genera un audio de relleno).
FISH_API_KEY = os.environ.get("FISH_API_KEY", "").strip()
try:
    FISH_AUDIO_SPEED = float(os.environ.get("FISH_AUDIO_SPEED", "").strip() or 1.0)
except ValueError as exc:
    raise RuntimeError("FISH_AUDIO_SPEED debe ser un número entre 0.5 y 2.0.") from exc
fish_client = (
    FishAudioClient(
        api_key=FISH_API_KEY,
        model=os.environ.get("FISH_AUDIO_MODEL", "").strip(),
        reference_id=os.environ.get("FISH_AUDIO_REFERENCE_ID", "").strip(),
        speed=FISH_AUDIO_SPEED,
    )
    if FISH_API_KEY
    else None
)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Valores de ejemplo que `.env.example` publica. Si el `.env` efectivo los copia
# tal cual, el secreto es de conocimiento público y no protege nada (hallazgo
# C-02 de la auditoría). La comparación se hace en minúsculas.
PUBLISHED_PLACEHOLDER_SECRETS = frozenset({
    "cambia-este-secreto-compartido",
    "maxcim-demo-isolated-webhook",
    "changeme",
})


def _require_strong_secret(config: dict, key: str, *, min_len: int = 32) -> None:
    """Falla el arranque si un secreto de producción está vacío, es un marcador
    de ejemplo publicado o es demasiado corto. Se aplica solo con
    DEMO_MODE=false y fuera de las pruebas."""
    value = str(config.get(key) or "").strip()
    if not value:
        raise RuntimeError(
            f"{key} es obligatorio con DEMO_MODE=false. Genera uno con: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if value.lower() in PUBLISHED_PLACEHOLDER_SECRETS:
        raise RuntimeError(
            f"{key} tiene un valor de ejemplo público; es de conocimiento "
            "público y no protege nada. Genera uno nuevo aleatorio."
        )
    if len(value) < min_len:
        raise RuntimeError(
            f"{key} es demasiado corto ({len(value)} caracteres); usa al menos "
            f"{min_len} caracteres aleatorios (256 bits)."
        )


# This repository is intentionally the isolated test environment. The real
# repository keeps DEMO_MODE disabled and never imports this adapter.
# Fail-closed: si la variable falta, se asume producción. Demo es un bypass de
# autenticación (acepta cualquier credencial, omite CSRF, autoriza toda llamada
# robot); olvidar la variable no puede dejarlo encendido.
DEFAULT_DEMO_MODE = env_bool("DEMO_MODE", False)


def utc_now() -> datetime:
    """UTC stored without tzinfo for compatibility with MySQL DATETIME."""
    return datetime.now(UTC).replace(tzinfo=None)


# `fecha_hora` se guarda en UTC (ver utc_now / models._utc_now) para que la
# base no dependa de en qué huso corre el proceso. Perú no tiene horario de
# verano, así que la conversión es un offset fijo (UTC-5) sin ambigüedad.
LOCAL_TZ = ZoneInfo("America/Lima")


def to_local_time(value: datetime | None) -> datetime | None:
    """Convierte un `datetime` naive guardado en UTC a la hora de Perú, solo
    para mostrarlo en la consola de la docente. Los datos siguen viajando en
    UTC hacia el robot y hacia la base; esto es puramente de presentación."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(LOCAL_TZ)


MYSQL_HOST = os.environ.get("MYSQL_HOST") or os.environ.get("MYSQLHOST", "localhost")
MYSQL_PORT = os.environ.get("MYSQL_PORT") or os.environ.get("MYSQLPORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER") or os.environ.get("MYSQLUSER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD") or os.environ.get("MYSQLPASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE") or os.environ.get("MYSQLDATABASE", "")
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("MYSQL_URL", "")
if DATABASE_URL.startswith("mysql://"):
    DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://", 1)
if DATABASE_URL:
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
elif DEFAULT_DEMO_MODE:
    SQLALCHEMY_DATABASE_URI = os.environ.get("DEMO_DATABASE_URL", "sqlite:///maxcim_demo.db")
else:
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )

EXTRACT_PROMPT = (
    "Extrae todo el texto de este documento, tal como aparece, sin resumir, "
    "interpretar ni agregar comentarios. Responde solo con el texto extraído."
)
SUMMARY_PROMPT_TEMPLATE = (
    "Genera un resumen del siguiente texto, reduce el contenido de tal manera que no se pierda"
    "el sentido literal, en español, de forma clara "
    "y concisa:\n\n{text}"
)
SENTENCES_PROMPT = (
    "Este documento contiene una lista de oraciones que una docente quiere usar "
    "como material de práctica de lectura oral. Identifica cada oración individual "
    "tal como la docente la escribió: no resumas, no reformules, no inventes "
    "oraciones nuevas y no unas ni dividas oraciones. Corrige únicamente errores "
    "evidentes de espaciado o de salto de línea. Ignora títulos, numeración, "
    "viñetas y encabezados que no sean parte de una oración. Responde únicamente "
    'con un JSON de la forma {"oraciones": ["primera oración", "segunda oración"]}, '
    "en el mismo orden del documento y sin texto fuera del JSON."
)
SENTENCES_GENERATE_PROMPT = (
    "Genera oraciones originales en español para que estudiantes de {nivel} las "
    "practiquen en lectura oral. Tema: {tema}. Objetivo o detalles: {detalles}. "
    "Escribe exactamente {cantidad} oraciones, cada una completa, clara, "
    "apropiada para la edad y con una sola idea. No las numeres ni añadas "
    'títulos. Responde únicamente con un JSON de la forma {{"oraciones": '
    '["primera oración", "segunda oración"]}} y sin texto fuera del JSON.'
)
MAX_SENTENCES_PER_REQUEST = 40

# "Oraciones con imágenes": cada oración lleva exactamente dos sustantivos
# concretos que más adelante se reemplazan por imágenes. Por ahora solo se
# generan y se muestran para que la docente las revise (sin generar imágenes).
IMAGE_SENTENCES_GENERATE_PROMPT = (
    "Genera oraciones originales en español para que estudiantes de {nivel} las "
    "practiquen en lectura. Tema: {tema}. Objetivo o detalles: {detalles}. "
    "Escribe exactamente {cantidad} oraciones. Cada oración DEBE contener "
    "exactamente dos sustantivos comunes, concretos y fáciles de dibujar "
    "(objetos, animales, personas, alimentos o lugares); esos dos sustantivos se "
    "reemplazarán después por imágenes, así que evita sustantivos abstractos y "
    "nombres propios. Cada oración: completa, clara, con una sola idea, apropiada "
    "para la edad, sin numeración ni títulos. Responde únicamente con un JSON de "
    'la forma {{"oraciones": [{{"texto": "La niña dibuja una casa.", '
    '"sustantivos": ["niña", "casa"]}}]}} y sin texto fuera del JSON.'
)

# Extracción de "oraciones con imágenes" desde un documento que subió la
# docente: el modelo NO inventa oraciones, solo elige de entre las que ya
# están escritas aquellas que sirven para el ejercicio (exactamente dos
# sustantivos concretos y dibujables) y devuelve esos dos sustantivos por
# oración. Este material lo leen niños de corta edad, así que se descartan
# los nombres propios y los sustantivos que producirían una imagen ambigua.
IMAGE_SENTENCES_EXTRACT_PROMPT = (
    "Este documento contiene una lista de oraciones que una docente quiere usar "
    "como material de lectura con imágenes para niños de corta edad. Cada "
    "oración apta lleva EXACTAMENTE dos sustantivos comunes, concretos y "
    "fáciles de dibujar (objetos, animales, personas, alimentos o lugares), que "
    "después se reemplazarán por una imagen. "
    "Identifica cada oración tal como la docente la escribió: no la resumas, no "
    "la reformules, no inventes oraciones nuevas y no unas ni dividas oraciones. "
    "Corrige solo errores evidentes de espaciado o de salto de línea. "
    "DESCARTA por completo (no las incluyas en la respuesta) las oraciones que: "
    "no tengan exactamente dos de esos sustantivos; contengan nombres propios "
    "(personas, mascotas, marcas, lugares con nombre); o contengan un sustantivo "
    "que produciría una imagen ambigua o confusa para un niño pequeño "
    "(palabras abstractas, con varios significados, o difíciles de representar "
    "con un dibujo claro). Ignora títulos, numeración, viñetas y encabezados. "
    'Responde únicamente con un JSON de la forma {"oraciones": [{"texto": '
    '"Ese oso ama la miel.", "sustantivos": ["oso", "miel"]}]}, en el mismo '
    "orden del documento y sin texto fuera del JSON. Cada elemento debe traer "
    "los dos sustantivos tal como aparecen en su oración."
)
MAX_IMAGE_SENTENCES_PER_REQUEST = 20
# Tope por material al guardar (una docente puede subir un documento con
# muchas más oraciones que las que se generan de una tacada). Igual criterio
# que MAX_SENTENCES_PER_MATERIAL para las oraciones sin imagen.
MAX_IMAGE_SENTENCES_PER_MATERIAL = 120

# --- "Oraciones con imágenes": diseño (generación de las imágenes) -----------
# En el paso de diseño se genera una imagen por cada sustantivo, así que el
# tope es más bajo que en la extracción: 20 oraciones = 40 imágenes.
MAX_IMAGE_DESIGN_SENTENCES = 20
# Prompt corto a propósito: una caricatura educativa (estilo libro infantil),
# UN solo objeto por sustantivo (nunca la oración entera), grande y centrado,
# quieto y sin hacer la acción de la oración, sobre fondo blanco y sin texto.
# La oración (cuando hay una, ver `oracion` en generate_noun_image) solo sirve
# para desambiguar qué dibujar. Para "bits" no hay oración -son palabras
# sueltas- así que la fidelidad al significado más común de la palabra
# depende enteramente de esta instrucción explícita: sin ella, un modelo de
# imágenes puede confundir la palabra pedida con otra parecida o con un
# sentido raro/técnico (p.ej. "mesa" -> dibuja una "meseta").
NOUN_IMAGE_STYLE_PROMPT = (
    "Educational cartoon of a single «{palabra}», friendly children's book "
    "illustration style: clean lines, soft flat color, polished — not a crude "
    "emoji or doodle. Just the one object, calm and still (not doing any "
    "action), large and centered, filling most of the frame on a plain white "
    "background. No text, no other objects, no scenery, no ground shadow. "
    "«{palabra}» is an exact Spanish word: draw ONLY that word's single most "
    "common, everyday, concrete meaning (a specific object, animal, person, "
    "or food) that a young child would recognize — never a rare, technical, "
    "or geographic sense, and never a different word that merely looks or "
    "sounds similar.{contexto_clause}"
)
# Ajuste de una imagen de sustantivo/bit ya generada, a partir de lo que pide
# la docente en el paso de diseño (mismo estilo que NOUN_IMAGE_STYLE_PROMPT).
NOUN_IMAGE_EDIT_PROMPT = (
    "Edit the attached educational illustration of a single object for a "
    "children's book. Apply ONLY the change requested below and keep "
    "everything else exactly as it is: the same subject, the clean lines, the "
    "soft flat colors, the centered composition and the plain white "
    "background. The requested change was written by a teacher and may be in "
    "Spanish: {cambio} "
    "No text, no letters, no captions, no watermark."
)
NOUN_IMAGE_EDIT_MAX_CHARS = 500
# Imágenes en revisión (aún sin material) mientras la docente aprueba el
# diseño. Viven bajo UPLOADS_ROOT/_previews/<token>/ y se limpian al guardar
# o cuando superan esta antigüedad.
PREVIEW_STAGING_DIRNAME = "_previews"
PREVIEW_STAGING_MAX_AGE_SECONDS = 6 * 3600

# Ilustraciones por escena de un cuento. La IA decide cuántas escenas hay
# (dentro de este rango, para acotar el costo) y cada imagen se genera en una
# llamada aparte para que la consola pueda mostrar el avance.
STORY_SCENES_MIN = 2
STORY_SCENES_MAX = 10
# Estilo fijo de todas las escenas (no lo decide la IA del plan): dibujo animado
# 2D, nunca realista, para que el cuento se vea como una serie infantil.
STORY_SCENE_STYLE = (
    "2D cartoon animation style, like a modern children's animated TV series: "
    "bold clean outlines, flat vibrant colors with simple cel shading, "
    "simplified shapes, expressive friendly characters with big eyes, bright "
    "cheerful palette. Hand-drawn 2D look, not realistic, not photographic, "
    "no 3D render, no painterly or watercolor texture"
)
# Tono visual de las escenas: la docente lo elige (o deja "auto" y lo decide la
# IA). Se suma al estilo cartoon fijo: cambia el ambiente, la paleta y la
# composición, no la técnica de dibujo. `label` e `hint` se muestran en la
# consola; `prompt` (en inglés) es lo que recibe el modelo de imágenes.
STORY_SCENE_TONE_AUTO = "auto"
STORY_SCENE_TONE_FALLBACK = "fantasioso"
STORY_SCENE_TONES = {
    "realista": {
        "label": "Realista",
        "hint": "Lugares, colores y proporciones de la vida real.",
        "prompt": (
            "realistic everyday setting: real-world places, natural colors, "
            "believable proportions and natural daylight, grounded and true to "
            "life (the characters are still drawn in the 2D cartoon style)"
        ),
    },
    "fantasioso": {
        "label": "Fantasioso",
        "hint": "Magia, criaturas y paisajes de ensueño.",
        "prompt": (
            "magical dreamlike fantasy world: glowing lights and sparkles, "
            "whimsical creatures, imaginative landscapes and rich enchanted "
            "colors, a sense of wonder"
        ),
    },
    "aventurero": {
        "label": "Aventurero",
        "hint": "Movimiento, descubrimiento y paisajes amplios.",
        "prompt": (
            "adventurous and dynamic: bold energetic compositions, a strong "
            "sense of movement and discovery, wide open landscapes, vivid "
            "saturated colors and dramatic but friendly lighting"
        ),
    },
    "divertido": {
        "label": "Divertido",
        "hint": "Expresiones exageradas y situaciones graciosas.",
        "prompt": (
            "funny and playful: exaggerated expressions and poses, silly "
            "situations, bright bold colors and a lively, energetic mood"
        ),
    },
    "tierno": {
        "label": "Tierno",
        "hint": "Colores suaves, luz cálida y ambiente acogedor.",
        "prompt": (
            "warm, tender and cozy: soft pastel palette, gentle golden light, "
            "rounded shapes and a calm, comforting, affectionate atmosphere"
        ),
    },
    "misterioso": {
        "label": "Misterioso",
        "hint": "Atardecer o luna, sombras suaves y curiosidad, sin miedo.",
        "prompt": (
            "mysterious but child-friendly: dusk or moonlit ambience, soft "
            "shadows and light fog, deep blue and purple palette, curious and "
            "intriguing, never scary, dark or violent"
        ),
    },
}
# Cada escena se narra con su propio audio (el robot lo reproduce mientras
# muestra su imagen), así que el texto de las escenas no puede ser un resumen:
# las escenas deben ser tramos consecutivos del cuento que, juntos, lo cubran
# entero. Por eso el servidor divide el cuento en oraciones numeradas y la IA
# solo decide en qué oración empieza cada escena.
STORY_SCENES_PLAN_PROMPT = """
Vas a preparar las ilustraciones de un cuento infantil. El cuento ya está
dividido en oraciones numeradas. Agrupa oraciones consecutivas en escenas
visuales clave, en orden narrativo, de modo que las escenas cubran todo el
cuento (inicio, desarrollo y final) sin dejar oraciones fuera. Tú decides
cuántas escenas hacen falta, entre {min_escenas} y {max_escenas}, según la
longitud y los momentos importantes del cuento; no dupliques momentos
parecidos.

{tono_instruccion}

Devuelve solamente JSON con esta forma:
{{"personaje": "...", "escenas": [{{"desde": 1, "descripcion": "..."}}]}}

Todas las imágenes serán dibujos animados 2D estilo caricatura infantil; no
describas nada fotográfico ni realista.

- "personaje": descripción visual detallada, EN INGLÉS, del personaje principal
  y de cualquier personaje recurrente como personajes de dibujos animados
  (especie o edad, colores, pelaje o ropa, rasgos distintivos). Se repetirá en
  cada imagen para que se vea igual siempre.
- "desde": número de la primera oración de la escena. La primera escena empieza
  en la oración 1 y cada escena llega hasta la oración anterior a donde empieza
  la siguiente (la última llega hasta el final del cuento).
- "descripcion": qué se ve en la ilustración, EN INGLÉS: lugar, acción, emoción
  y personajes presentes. Sin texto escrito, letreros ni globos de diálogo.

Cuento (oraciones numeradas):
{cuento}
""".strip()
STORY_SCENE_IMAGE_PROMPT = (
    "Cartoon illustration for a children's story, {estilo}. Mood and tone: "
    "{tono}. Landscape composition, a single complete scene, suitable for "
    "young children. "
    "Scene: {descripcion} "
    "Main character(s), drawn as cartoon characters that must look exactly the "
    "same in every scene of the story: {personaje}. "
    "No text, no letters, no captions, no speech bubbles, no watermark."
)
# Edición de una escena ya dibujada: la docente escribe qué cambiar y el modelo
# retoca la imagen manteniendo todo lo demás.
STORY_SCENE_EDIT_MAX_CHARS = 500
STORY_SCENE_EDIT_PROMPT = (
    "Edit the attached illustration from a children's story. Apply ONLY the "
    "change requested below and keep everything else exactly as it is: the "
    "character design, the composition, the colors and the same 2D cartoon art "
    "style. The requested change was written by a teacher and may be in "
    "Spanish: {cambio} "
    "No text, no letters, no captions, no speech bubbles, no watermark."
)
STORY_SCENE_REFERENCE_CLAUSE = (
    "The attached image is an earlier illustration from this same story. Keep "
    "the character design, proportions, colors and the same 2D cartoon art "
    "style identical to it, but draw the new scene described above."
)

# Alternativa a la generación con IA: la docente puede subir su propia
# imagen para un sustantivo (ver /api/material/image-sentences/upload-image).
# El nombre y formato originales del archivo son indiferentes -nunca se
# confía en ellos-: se decodifica con Pillow y se vuelve a codificar como
# PNG, lo que de paso descarta cualquier archivo que no sea realmente una
# imagen y limita su tamaño en memoria.
MAX_NOUN_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
NOUN_IMAGE_MAX_DIMENSION = 1024
MAX_NOUN_IMAGE_PIXELS = 25_000_000

# "Bits" (bits de inteligencia): tarjetas de una sola palabra + una imagen,
# para practicar fonética con las sílabas que indique la docente. Más simple
# que "oraciones con imágenes": no hay oración ni plantilla que intercalar,
# cada bit es {palabra, imagen}. Las sílabas a trabajar son solo criterio de
# generación con IA, no se guardan por palabra.
MAX_BITS_PER_REQUEST = 20
# Tope por material al guardar (un documento puede traer más palabras que las
# que se generan/diseñan de una tacada). Igual criterio que
# MAX_IMAGE_SENTENCES_PER_MATERIAL.
MAX_BITS_PER_MATERIAL = 120
# Paso de diseño: una imagen por palabra, mismo tope que MAX_IMAGE_DESIGN_SENTENCES.
MAX_BITS_DESIGN_ITEMS = 20
MAX_BIT_WORD_CHARS = 60
MAX_BITS_CHARS = 8_000
# La docente escribe una lista de sílabas (separadas por coma o espacio);
# se acepta hasta este número de sílabas por lote y este largo por sílaba.
MAX_BITS_SYLLABLES = 6
MAX_BIT_SYLLABLE_CHARS = 10
# En la práctica, todas las sílabas que se trabajan en un mismo lote de bits
# suelen compartir la consonante inicial (p. ej. "ma, me, mi, mo, mu"), así
# que todas las palabras terminan empezando con esa misma consonante. Se
# detecta al vuelo (ver bits_shared_consonant) y se expone al robot -no se
# guarda en bits.json, se recalcula cada vez a partir de las palabras.
BITS_VOWELS = frozenset("AEIOUÁÉÍÓÚÜ")

# Los bits generados con IA son siempre para el mismo público: la docente
# elige las sílabas objetivo, cuántas palabras quiere y, con sus propias
# palabras, cuántas sílabas debe tener cada palabra (texto libre que va tal
# cual al prompt; si lo deja vacío se usa el valor por defecto).
BITS_TARGET_LEVEL = "inicial de 5 años"
BITS_DEFAULT_WORDS = 10
BITS_DEFAULT_SYLLABLE_COUNT = "2 sílabas"
MAX_BITS_SYLLABLE_COUNT_CHARS = 120

BITS_GENERATE_PROMPT = (
    "Genera palabras en español, reales y de uso común, para que niños de "
    "{nivel} practiquen fonética; usa vocabulario muy sencillo y cotidiano "
    "que conozcan a esa edad. Cada palabra DEBE EMPEZAR con alguna de estas "
    "sílabas objetivo -tiene que ser la primera sílaba de la palabra, no "
    "basta con que la sílaba aparezca en cualquier otra posición-: "
    "{silabas}. Reparte las {cantidad} palabras entre esas sílabas lo más "
    "parejo posible. Cantidad de sílabas que debe tener cada palabra, según "
    "indicó la docente: {cantidad_silabas}. Cumple esa indicación en todas "
    "las palabras; ninguna puede tener otra cantidad de sílabas. "
    "Objetivo o detalles: {detalles}. Elige palabras concretas y "
    "fáciles de representar con un dibujo inequívoco (evita palabras "
    "abstractas, difíciles, nombres propios, o palabras con varios "
    "significados donde el más común no sea justamente ese objeto/animal/"
    "persona/alimento). No repitas palabras. Escribe exactamente {cantidad} "
    'palabras. Responde únicamente con un JSON de la forma {{"palabras": '
    '["mano", "mapa"]}} y sin texto fuera del JSON.'
)

# Extracción de palabras para "bits" desde un documento que subió la docente:
# el modelo NO inventa palabras, solo identifica las que ya están escritas ahí
# (mismo criterio que IMAGE_SENTENCES_EXTRACT_PROMPT para oraciones, pero sin
# filtrar por sustantivos concretos: la docente ya curó su propia lista).
BITS_EXTRACT_PROMPT = (
    "Este documento contiene una lista de palabras que una docente quiere "
    "usar como material de fonética con imágenes para niños de corta edad "
    "(una palabra por línea, o separadas por comas o viñetas). Identifica "
    "cada palabra tal como está escrita: no inventes palabras nuevas ni la "
    "reformules, solo corrige errores evidentes de espaciado. Ignora "
    "títulos, numeración y encabezados que no sean palabras del ejercicio. "
    'Responde únicamente con un JSON de la forma {"palabras": ["mano", '
    '"lupa"]}, en el mismo orden del documento y sin texto fuera del JSON.'
)

# Pregunta que el robot dice y muestra por cada bit. La palabra del bit es la
# RESPUESTA que dará el niño, así que la pregunta es el comienzo de una frase
# que esa palabra completa ("El sol está…" -> "feliz"): no vale un "Esto es…"
# genérico, porque "Esto es feliz" no tiene sentido. La IA la sugiere mirando
# la imagen y la docente la revisa en el paso de diseño.
MAX_BIT_QUESTION_CHARS = 200
BITS_QUESTION_PROMPT = (
    "Eres parte de una actividad en la que un robot conversa con niños de "
    "{nivel}. El robot muestra una tarjeta con una imagen y el niño debe "
    "responder con UNA sola palabra: la palabra de la tarjeta. De cada tarjeta "
    "recibes esa palabra (la respuesta esperada) y su imagen. Escribe lo que el "
    "robot preguntará en voz alta y en pantalla: una frase incompleta o una "
    "pregunta corta en español que el niño completa diciendo exactamente esa "
    "palabra, de modo que la frase completa tenga sentido y concuerde en "
    "género, número y artículo. Observa la imagen para decidir por qué "
    "preguntar: si muestra un sol con cara feliz y la palabra es «feliz», la "
    "pregunta es «El sol está…»; si muestra una familia y la palabra es "
    "«familia», es «Esto es una…»; si muestra un perro y la palabra es "
    "«perro», es «Esto es un…». No uses «Esto es…» por costumbre: úsalo solo "
    "cuando la palabra nombra lo que se ve; si la palabra describe un estado, "
    "una cualidad o una acción, pregunta por eso (p. ej. «El sol está…», «La "
    "niña se siente…»). Reglas: la pregunta NUNCA contiene la palabra de la "
    "tarjeta ni pistas que la regalen; no incluyas saludos, felicitaciones ni "
    "transiciones («muy bien», «vamos con la siguiente»): el robot las agrega; "
    "máximo 12 palabras; si es una frase incompleta termina con puntos "
    "suspensivos (…). Responde únicamente con un JSON de la forma "
    '{{"preguntas": [{{"tarjeta": 1, "pregunta": "El sol está…"}}]}}, una '
    "entrada por tarjeta y sin texto fuera del JSON."
)

QUESTION_TYPES = ["literales", "inferenciales", "criticas"]
QUESTION_TYPE_DESCRIPTIONS = {
    "literales": "preguntas literales, que se responden con información explícita presente directamente en el texto",
    "inferenciales": "preguntas inferenciales, que requieren deducir información que el texto no dice explícitamente, a partir de sus pistas",
    "criticas": "preguntas críticas, que invitan a opinar, valorar o reflexionar críticamente sobre el texto",
}
QUESTIONS_PROMPT_TEMPLATE = (
    "A partir del siguiente texto, genera preguntas de comprensión lectora en "
    "español para estudiantes de nivel inicial y primaria. Genera exactamente esta cantidad "
    "de preguntas para cada tipo:\n{requirements}\n\nTexto:\n{text}\n\n"
    "Responde únicamente con un JSON de la forma "
    '{{"literales": [{{"pregunta": "...", "respuesta_esperada": "..."}}], '
    '"inferenciales": [...], "criticas": [...]}}, '
    "donde cada elemento contiene la pregunta y una respuesta esperada o criterio de evaluación. "
    "No agregues numeración, comentarios ni texto fuera del JSON."
)
MAX_QUESTIONS_PER_TYPE = 15

STORY_PROMPT_TEMPLATE = """
Crea un cuento educativo original en español para {nivel}. El personaje principal
es {personaje} y la historia ocurre en {escenario}. El objetivo pedagógico es
{objetivo}. Incorpora de forma natural estas respuestas adicionales del alumno:
{detalles}

El cuento debe ser apropiado para la edad, claro, positivo y útil para una
interacción oral posterior. La narración completa debe durar aproximadamente
{duracion_minutos} minuto(s), considerando un ritmo infantil claro de
{palabras_por_minuto} palabras por minuto. El campo "cuento" debe contener entre
{palabras_minimas} y {palabras_maximas} palabras, idealmente {palabras_objetivo};
respeta este rango. No incluyas preguntas todavía. Devuelve solamente JSON:
{{"titulo": "...", "cuento": "...", "resumen": "..."}}
""".strip()

STORY_LENGTH_CORRECTION_TEMPLATE = """
Reescribe el siguiente cuento educativo sin cambiar su personaje, escenario,
objetivo pedagógico ni hechos principales. El campo "cuento" debe quedar entre
{palabras_minimas} y {palabras_maximas} palabras para aproximarse a una narración
de {duracion_minutos} minuto(s). Conserva un resumen breve y devuelve solamente JSON:
{{"titulo": "...", "cuento": "...", "resumen": "..."}}

Cuento actual:
{cuento_actual}
""".strip()

STORY_DURATION_MINUTES_MIN = 1
STORY_DURATION_MINUTES_MAX = 15
STORY_NARRATION_WORDS_PER_MINUTE = 125
STORY_WORD_COUNT_TOLERANCE = 0.08

# Fish Audio divide el texto por su cuenta y mantiene la voz entre fragmentos
# (condition_on_previous_chunks), así que cada escena se envía de una vez. Solo
# se parte antes de enviar si un texto es larguísimo, porque no hay un máximo
# documentado por petición.
FISH_CHUNK_MAX_CHARS = 3000

# Etiquetas de emoción/tono que el modelo de voz de Fish Audio reconoce de
# forma fiable dentro del texto (p. ej. "...dijo [excited] ¡Vamos! [/excited]
# al bosque."). Es un ajuste puramente interno para narrar mejor: Gemini las
# inserta en el texto justo antes de narrar la escena (ver
# annotate_narration_for_tts), la docente nunca las ve ni las edita. Solo se
# aceptan estas -cualquier otra que Gemini invente se descarta antes de
# mandarla a Fish, que no la reconocería.
FISH_NARRATION_TAGS = (
    "happy", "sad", "angry", "excited", "calm", "nervous", "confident",
    "surprised", "satisfied", "delighted",
    "whispering", "shouting", "screaming", "soft tone", "in a hurry tone",
    "laughing", "sighing",
)
FISH_NARRATION_TAGS_ENABLED = env_bool("FISH_NARRATION_TAGS_ENABLED", True)

STORY_NARRATION_TAG_PROMPT = """
Vas a preparar un fragmento de un cuento infantil para narrarlo con una voz de
texto a voz que reconoce, dentro del texto, etiquetas de emoción y tono entre
corchetes. Inserta esas etiquetas donde ayuden a que la narración suene
expresiva y acorde a lo que sienten o cómo hablan los personajes (o el
narrador) en ese momento; unas pocas bien puestas bastan, no hace falta una
por oración ni una en cada fragmento.

Usa ÚNICAMENTE estas etiquetas, tal cual y entre corchetes: {etiquetas}. No
inventes otras ni las combines. No cambies, agregues ni quites ninguna
palabra del texto original -la narración debe decir exactamente lo mismo-,
solo inserta las etiquetas entre palabras u oraciones.

Personaje principal del cuento: {personaje}.

Fragmento a narrar:
{texto}

Responde únicamente con un JSON de la forma {{"texto_narrado": "..."}} y sin
texto fuera del JSON.
""".strip()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}
MAX_SOURCE_TEXT_CHARS = 120_000
MAX_SUMMARY_CHARS = 20_000
# Las oraciones se guardan como una lista JSON en `uploads/<id>/oraciones.json`,
# igual que las preguntas de un cuento; `path_preguntas` guarda esa ruta (ver
# models.py). Los registros antiguos con texto plano en `path_preguntas` se
# siguen leyendo dividiéndolos en oraciones al vuelo.
MAX_SENTENCES_CHARS = 20_000
MAX_SENTENCES_PER_MATERIAL = 120
MAX_SENTENCE_CHARS = 600
MAX_TTS_TEXT_CHARS = 30_000
MAX_TRANSCRIPT_CHARS = 20_000
MAX_LOGIN_ID_CHARS = 64
MAX_LOGIN_CREDENTIAL_CHARS = 256
MAX_OBJECTIVE_CHARS = 2_000

DIAS_ES = [
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
]
MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

MATERIAL_SKILLS = [
    "Todas las habilidades",
    "Comunicación oral",
    "Escucha activa",
    "Empatía",
    "Trabajo en equipo",
    "Resolución de conflictos",
]

QUESTION_CONFIGURATION = [
    {"key": "literales", "label": "Literales", "default": 3},
    {"key": "inferenciales", "label": "Inferenciales", "default": 2},
    {"key": "criticas", "label": "Críticas", "default": 1},
]

# Recursos descargables de un `cuento` vía la API del robot. Cada entrada mapea
# el segmento de la URL (/api/materials/<id>/<recurso>) a la columna de
# `Material` con la ruta del archivo (relativa a UPLOADS_ROOT, con el prefijo
# histórico `uploads/`), el Content-Type de la descarga y el nombre de archivo
# sugerido. Una `oracion` solo expone `oraciones` (ver download_material_resource).
# El framework añade `; charset=utf-8` a los tipos text/* y application/json.
MATERIAL_DOWNLOADS = {
    "texto": ("path_texto", "text/plain", "texto.txt"),
    "resumen": ("path_texto_resumen", "text/plain", "resumen.txt"),
    "audio": ("path_audio", "audio/wav", "audio.wav"),
    "preguntas": ("path_preguntas", "application/json", "preguntas.json"),
}


def extract_and_summarize(file_storage) -> tuple[str, str]:
    """Uploads the file to Gemini, extracts its text, then summarizes it."""
    filename = file_storage.filename or "documento"
    mime_type = file_storage.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    suffix = os.path.splitext(filename)[1]

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_storage.save(tmp)
            tmp_path = tmp.name

        uploaded_file = gemini_client.files.upload(
            file=tmp_path,
            config={"mime_type": mime_type, "display_name": filename},
        )

        extract_response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded_file, EXTRACT_PROMPT],
        )
        transcribed_text = extract_response.text.strip()

        summary_response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=SUMMARY_PROMPT_TEMPLATE.format(text=transcribed_text),
        )
        summary_text = summary_response.text.strip()

        return transcribed_text, summary_text
    finally:
        if uploaded_file is not None:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def normalize_sentences(raw_sentences) -> list[str]:
    """Cleans a raw list of sentence strings: trims, drops empties and
    duplicates, and caps both the per-sentence length and the total count."""
    seen: set[str] = set()
    sentences: list[str] = []
    for raw in raw_sentences or []:
        sentence = " ".join(str(raw or "").split()).strip()
        if not sentence:
            continue
        sentence = sentence[:MAX_SENTENCE_CHARS].strip()
        key = sentence.casefold()
        if key in seen:
            continue
        seen.add(key)
        sentences.append(sentence)
        if len(sentences) >= MAX_SENTENCES_PER_MATERIAL:
            break
    return sentences


def split_text_into_sentences(text: str) -> list[str]:
    """Best-effort sentence segmentation for plain text: splits on line breaks
    first (the docente usually writes one sentence per line) and then on
    sentence-final punctuation."""
    pieces: list[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pieces.extend(part for part in re.split(r"(?<=[.!?…])\s+", line) if part.strip())
    return normalize_sentences(pieces)


def extract_sentences(file_storage) -> list[str]:
    """Uploads the file to Gemini and asks it to identify each individual
    sentence the teacher listed, mirroring extract_and_summarize."""
    filename = file_storage.filename or "documento"
    mime_type = file_storage.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    suffix = os.path.splitext(filename)[1]

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_storage.save(tmp)
            tmp_path = tmp.name

        uploaded_file = gemini_client.files.upload(
            file=tmp_path,
            config={"mime_type": mime_type, "display_name": filename},
        )

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded_file, SENTENCES_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        raw_sentences = data.get("oraciones") if isinstance(data, dict) else data
        return normalize_sentences(raw_sentences if isinstance(raw_sentences, list) else [])
    finally:
        if uploaded_file is not None:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def image_sentence_template(texto: str, palabras: list[str]) -> str:
    """Devuelve la oración con la primera aparición de cada sustantivo
    reemplazada por su marcador posicional `{{0}}`, `{{1}}`, … El robot y la
    consola sustituyen esos marcadores por la imagen. Lanza ValueError si un
    sustantivo no aparece tal cual (como palabra completa) en la oración."""
    plantilla = " ".join(str(texto or "").split())
    for index, palabra in enumerate(palabras):
        needle = " ".join(str(palabra or "").split())
        if not needle:
            raise ValueError("Cada oración necesita sus dos sustantivos.")
        pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)", re.IGNORECASE)
        plantilla, replaced = pattern.subn(f"{{{{{index}}}}}", plantilla, count=1)
        if not replaced:
            raise ValueError(
                f"El sustantivo «{needle}» no aparece tal cual en la oración."
            )
    return plantilla


_IMAGE_SENTENCE_PLACEHOLDER = re.compile(r"\{\{([01])\}\}")


def split_image_sentence_template(plantilla: str, sustantivos: list[dict]) -> list[dict]:
    """Parte una plantilla ({{0}}/{{1}} como marcador, ver image_sentence_template)
    en tramos de texto e imagen, en orden, listos para intercalar en una
    vista previa -mismo criterio que ya usa renderDesignMixedLine() en
    material.js para el paso de diseño, pero aquí server-side para un
    material ya guardado (ver material_image_preview)."""
    parts: list[dict] = []
    cursor = 0
    for match in _IMAGE_SENTENCE_PLACEHOLDER.finditer(plantilla):
        if match.start() > cursor:
            parts.append({"type": "text", "value": plantilla[cursor:match.start()]})
        noun_index = int(match.group(1))
        if noun_index < len(sustantivos):
            parts.append({"type": "image", **sustantivos[noun_index]})
        cursor = match.end()
    if cursor < len(plantilla):
        parts.append({"type": "text", "value": plantilla[cursor:]})
    return parts


def _slugify_noun(word: str) -> str:
    """Nombre de archivo seguro a partir de un sustantivo, p.ej. "camión" ->
    "camion". Cadena vacía si no queda ningún carácter usable (el llamador
    cae entonces al identificador posicional de siempre)."""
    ascii_only = unicodedata.normalize("NFKD", word or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")[:40]


def _unique_noun_filename(word: str, position: int, noun_index: int, used: set[str]) -> str:
    """Nombre final (dentro de `img/`) para la imagen de un sustantivo: el
    propio sustantivo si produce un nombre de archivo válido y no choca con
    otro ya usado en este material («pollo.png»), o si no, el identificador
    posicional de siempre («2-0.png», igual que en el directorio de
    previsualización). Muta `used` para que la próxima llamada no repita
    nombre -dos oraciones pueden compartir un sustantivo pero llevar
    imágenes distintas."""
    base = _slugify_noun(word) or f"{position}-{noun_index}"
    name = f"{base}.png"
    suffix = 2
    while name in used:
        name = f"{base}-{suffix}.png"
        suffix += 1
    used.add(name)
    return name


def normalize_uploaded_noun_image(file_storage) -> bytes:
    """Valida que el archivo que subió la docente para un sustantivo sea
    realmente una imagen -nunca se confía en su extensión ni en el
    Content-Type que declaró el navegador- y la re-codifica como PNG, igual
    formato que las que genera Gemini. Lanza ValueError con un mensaje apto
    para mostrar si el archivo está vacío, es demasiado grande o no es una
    imagen decodificable."""
    raw = file_storage.read()
    if not raw:
        raise ValueError("El archivo está vacío.")
    if len(raw) > MAX_NOUN_IMAGE_UPLOAD_BYTES:
        raise ValueError(
            f"La imagen supera el tamaño máximo permitido "
            f"({MAX_NOUN_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )
    try:
        # verify() detecta corrupción sin decodificar los píxeles, pero deja
        # el objeto inutilizable para lo que sigue -por eso se reabre.
        Image.open(io.BytesIO(raw)).verify()
        img = Image.open(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("El archivo no es una imagen válida.") from exc
    # Un PNG pequeño puede declarar dimensiones enormes ("bomba de
    # descompresión"): se rechaza antes de decodificar los píxeles.
    if img.width * img.height > MAX_NOUN_IMAGE_PIXELS:
        raise ValueError("La imagen es demasiado grande.")
    try:
        img.load()
    except Exception as exc:
        raise ValueError("El archivo no es una imagen válida.") from exc

    if img.width <= 0 or img.height <= 0:
        raise ValueError("El archivo no es una imagen válida.")
    if img.width > NOUN_IMAGE_MAX_DIMENSION or img.height > NOUN_IMAGE_MAX_DIMENSION:
        img.thumbnail((NOUN_IMAGE_MAX_DIMENSION, NOUN_IMAGE_MAX_DIMENSION), Image.LANCZOS)
    img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def generate_noun_image(palabra: str, *, oracion: str = "") -> bytes:
    """Genera con Gemini un PNG del sustantivo `palabra` para que ocupe su
    hueco en una "oración con imágenes", o la imagen de una palabra suelta de
    "bits" (ahí `oracion` llega vacío: no hay oración que dé contexto).
    Devuelve los bytes de la imagen."""
    contexto = " ".join(str(oracion or "").split())
    contexto_clause = (
        f' Use the sentence «{contexto}» only as a further hint for what '
        f'«{palabra}» means.' if contexto else ""
    )
    prompt = NOUN_IMAGE_STYLE_PROMPT.format(
        palabra=" ".join(str(palabra or "").split()),
        contexto_clause=contexto_clause,
    )
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
    except TypeError as exc:  # SDK viejo: no conoce response_modalities
        raise RuntimeError(
            "La versión instalada de google-genai no soporta generación de "
            "imágenes. Actualiza con: pip install -U google-genai"
        ) from exc
    for candidate in response.candidates or []:
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return bytes(inline.data)
    raise ValueError("El modelo no devolvió ninguna imagen para este sustantivo.")


def _image_mime_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def story_scene_tone_options() -> list[dict[str, str]]:
    """Tonos elegibles para la consola (clave, etiqueta y ayuda)."""
    return [
        {"key": key, "label": tone["label"], "hint": tone["hint"]}
        for key, tone in STORY_SCENE_TONES.items()
    ]


def resolve_story_scene_tone(requested: str, suggested=None) -> str:
    """Tono final: el que eligió la docente; si dejó "auto", el que propuso la
    IA en el plan; y si tampoco es válido, el tono por defecto."""
    if requested in STORY_SCENE_TONES:
        return requested
    suggested = str(suggested or "").strip().lower()
    return suggested if suggested in STORY_SCENE_TONES else STORY_SCENE_TONE_FALLBACK


def _scene_tone_instruction(tone: str) -> str:
    if tone in STORY_SCENE_TONES:
        chosen = STORY_SCENE_TONES[tone]
        return (
            f"La docente eligió el tono visual «{chosen['label']}» "
            f"({chosen['hint']}). Las descripciones de las escenas deben ser "
            "coherentes con ese tono."
        )
    return (
        "Elige el tono visual que mejor encaje con el cuento y agrégalo al JSON "
        'como campo "tono", con solo la clave, una de: '
        f"{', '.join(STORY_SCENE_TONES)}. Las descripciones de las escenas deben "
        "ser coherentes con ese tono."
    )


def split_story_sentences(story_text: str) -> list[str]:
    """Oraciones del cuento con los espacios normalizados. Unidas con un espacio
    reproducen el cuento normalizado, que es lo que se compara al guardar."""
    normalized = " ".join(str(story_text or "").split())
    return [part for part in re.split(r"(?<=[.!?…])\s+", normalized) if part]


def normalize_story_text(text: str) -> str:
    return " ".join(str(text or "").split())


def plan_story_scenes(
    story_text: str, tone: str = STORY_SCENE_TONE_AUTO
) -> dict[str, object]:
    """Pide a Gemini que decida cuántas escenas ilustrar y en qué oración
    empieza cada una. Devuelve {"personaje", "tono", "escenas": [{"texto",
    "descripcion"}]}; los `texto` son tramos literales y consecutivos que
    cubren el cuento completo."""
    sentences = split_story_sentences(story_text)
    numbered = "\n".join(f"{number}. {sentence}" for number, sentence in enumerate(sentences, start=1))
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=STORY_SCENES_PLAN_PROMPT.format(
            min_escenas=STORY_SCENES_MIN,
            max_escenas=STORY_SCENES_MAX,
            tono_instruccion=_scene_tone_instruction(tone),
            cuento=numbered,
        ),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return _normalize_scene_plan(json.loads(response.text), sentences, tone)


def _normalize_scene_plan(
    data, sentences: list[str], tone: str = STORY_SCENE_TONE_AUTO
) -> dict[str, object]:
    """Arma las escenas a partir de la oración donde empieza cada una. Cada
    escena llega hasta donde empieza la siguiente, así que no puede haber
    huecos, solapes ni texto inventado aunque la IA se equivoque en los
    números: se ordenan, se acotan y la primera siempre empieza en la 1."""
    if not isinstance(data, dict):
        raise ValueError("Gemini no devolvió un plan de escenas válido.")
    personaje = " ".join(str(data.get("personaje") or "").split())
    total = len(sentences)
    described: dict[int, str] = {}
    for raw in data.get("escenas") or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("desde")
        if isinstance(start, str) and start.strip().isdigit():
            start = int(start.strip())
        descripcion = " ".join(str(raw.get("descripcion") or "").split())
        if isinstance(start, bool) or not isinstance(start, int) or not descripcion:
            continue
        described.setdefault(min(max(start, 1), total), descripcion)
    starts = sorted(described)[:STORY_SCENES_MAX]
    if len(starts) < STORY_SCENES_MIN or not personaje:
        raise ValueError("Gemini no devolvió suficientes escenas para el cuento.")
    first = starts[0]
    if first != 1:
        # La IA no ilustró el inicio: esa parte se suma a la primera escena.
        described[1] = described.pop(first)
        starts[0] = 1
    scenes = []
    for position, start in enumerate(starts):
        end = starts[position + 1] - 1 if position + 1 < len(starts) else total
        scenes.append({
            "texto": " ".join(sentences[start - 1:end]),
            "descripcion": described[start],
        })
    return {
        "personaje": personaje,
        "tono": resolve_story_scene_tone(tone, data.get("tono")),
        "escenas": scenes,
    }


def generate_story_scene_image(
    descripcion: str,
    personaje: str,
    tono: str,
    reference_image: bytes | None = None,
) -> bytes:
    """Genera la ilustración de una escena en estilo cartoon 2D
    (STORY_SCENE_STYLE) con el tono elegido. `reference_image` (la primera
    escena ya generada) se manda como referencia para que el personaje y el
    estilo se mantengan iguales en todo el cuento."""
    prompt = STORY_SCENE_IMAGE_PROMPT.format(
        estilo=STORY_SCENE_STYLE,
        tono=STORY_SCENE_TONES[resolve_story_scene_tone(tono)]["prompt"],
        personaje=personaje,
        descripcion=descripcion,
    )
    contents: list = [prompt]
    if reference_image:
        contents.append(STORY_SCENE_REFERENCE_CLAUSE)
        contents.append(types.Part.from_bytes(
            data=reference_image, mime_type=_image_mime_type(reference_image),
        ))
    response = gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    return _first_inline_image(response, "El modelo no devolvió ninguna imagen para esta escena.")


def edit_noun_image(image: bytes, instruction: str) -> bytes:
    """Modifica la imagen de un sustantivo o bit ya generada según lo que pidió
    la docente, conservando estilo, sujeto y fondo salvo lo indicado."""
    response = gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[
            NOUN_IMAGE_EDIT_PROMPT.format(cambio=instruction),
            types.Part.from_bytes(data=image, mime_type=_image_mime_type(image)),
        ],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    return _first_inline_image(response, "El modelo no devolvió la imagen editada.")


def edit_story_scene_image(image: bytes, instruction: str) -> bytes:
    """Modifica una ilustración ya generada según lo que pidió la docente,
    conservando personaje, composición y estilo salvo lo indicado."""
    response = gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[
            STORY_SCENE_EDIT_PROMPT.format(cambio=instruction),
            types.Part.from_bytes(data=image, mime_type=_image_mime_type(image)),
        ],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    return _first_inline_image(response, "El modelo no devolvió la imagen editada.")


def _first_inline_image(response, missing_message: str) -> bytes:
    for candidate in response.candidates or []:
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return bytes(inline.data)
    raise ValueError(missing_message)


def generate_questions(text: str, counts: dict[str, int]) -> dict[str, list[dict[str, str]]]:
    """Asks Gemini for reading-comprehension questions, grouped by type, in the
    quantities requested."""
    requirements = "\n".join(
        f"- {count} {QUESTION_TYPE_DESCRIPTIONS[qtype]}"
        for qtype, count in counts.items()
        if count > 0
    )

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=QUESTIONS_PROMPT_TEMPLATE.format(requirements=requirements, text=text),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)

    normalized = {}
    for qtype, count in counts.items():
        questions = []
        for raw_question in data.get(qtype, [])[:count]:
            if isinstance(raw_question, dict):
                statement = str(raw_question.get("pregunta") or "").strip()
                expected = str(raw_question.get("respuesta_esperada") or "").strip()
            else:
                # Backwards compatibility with older Gemini responses.
                statement = str(raw_question).strip()
                expected = ""
            if statement:
                questions.append({
                    "pregunta": statement,
                    "respuesta_esperada": expected,
                })
        normalized[qtype] = questions

    return normalized


def generate_sentences(
    topic: str, grade_level: str, count: int, extra_details: str,
    existing: list[str] | None = None,
) -> list[str]:
    """Asks Gemini for a draft list of reading-practice sentences on a topic.
    When `existing` is given, the topic may be blank (inferred from them) and
    Gemini is told not to repeat any of them."""
    existing = existing or []
    prompt = SENTENCES_GENERATE_PROMPT.format(
        nivel=grade_level or "primaria",
        tema=topic or "el mismo tema de las oraciones de referencia",
        detalles=extra_details or "Sin detalles adicionales.",
        cantidad=count,
    )
    if existing:
        listado = "\n".join(f"- {sentence}" for sentence in existing[:MAX_SENTENCES_PER_REQUEST])
        prompt += (
            "\n\nNo repitas ni parafrasees estas oraciones que ya existen; genera "
            f"oraciones distintas y complementarias:\n{listado}"
        )
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    raw_sentences = data.get("oraciones") if isinstance(data, dict) else data
    return normalize_sentences(raw_sentences if isinstance(raw_sentences, list) else [])


def normalize_image_sentences(
    raw_items, *, limit: int = MAX_IMAGE_SENTENCES_PER_REQUEST
) -> list[dict[str, object]]:
    """Keeps only well-formed {texto, sustantivos:[a, b]} entries: text non-empty
    and exactly two non-empty nouns. De-dupes by text, caps the count at
    `limit` (the per-request cap by default; save_material passes the larger
    per-material cap)."""
    seen: set[str] = set()
    items: list[dict[str, object]] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        texto = " ".join(str(raw.get("texto") or "").split()).strip()[:MAX_SENTENCE_CHARS]
        nouns = [
            " ".join(str(n or "").split()).strip()[:MAX_SENTENCE_CHARS]
            for n in (raw.get("sustantivos") or [])
        ]
        nouns = [n for n in nouns if n]
        if not texto or len(nouns) != 2:
            continue
        key = texto.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({"texto": texto, "sustantivos": nouns})
        if len(items) >= limit:
            break
    return items


def extract_image_sentences(file_storage) -> list[dict[str, object]]:
    """Uploads the teacher's document to Gemini and asks it to pick out the
    sentences already written there that work as "oraciones con imágenes"
    (exactly two concrete, drawable nouns, no proper nouns, no ambiguous
    nouns). Mirrors extract_sentences; returns [{texto, sustantivos:[a, b]}]."""
    filename = file_storage.filename or "documento"
    mime_type = file_storage.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    suffix = os.path.splitext(filename)[1]

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_storage.save(tmp)
            tmp_path = tmp.name

        uploaded_file = gemini_client.files.upload(
            file=tmp_path,
            config={"mime_type": mime_type, "display_name": filename},
        )

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded_file, IMAGE_SENTENCES_EXTRACT_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        raw_items = data.get("oraciones") if isinstance(data, dict) else data
        return normalize_image_sentences(raw_items if isinstance(raw_items, list) else [])
    finally:
        if uploaded_file is not None:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def generate_image_sentences(
    topic: str, grade_level: str, count: int, extra_details: str,
) -> list[dict[str, object]]:
    """Asks Gemini for reading sentences that each carry exactly two concrete
    nouns (later swapped for images). Returns [{texto, sustantivos:[a, b]}]."""
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=IMAGE_SENTENCES_GENERATE_PROMPT.format(
            nivel=grade_level or "primaria",
            tema=topic,
            detalles=extra_details or "Sin detalles adicionales.",
            cantidad=count,
        ),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    raw_items = data.get("oraciones") if isinstance(data, dict) else data
    return normalize_image_sentences(raw_items if isinstance(raw_items, list) else [])


def normalize_bits_words(
    raw_items, *, limit: int = MAX_BITS_PER_REQUEST
) -> list[dict[str, object]]:
    """Keeps only well-formed {palabra} entries: accepts either bare strings
    (Gemini's {"palabras": [...]} shape) or {palabra, ...} dicts (frontend
    payloads, which also carry staging_index). Trims, length-caps, de-dupes
    by casefold, caps the count at `limit`. Mirrors normalize_image_sentences,
    simplified to a single word instead of {texto, sustantivos}."""
    seen: set[str] = set()
    items: list[dict[str, object]] = []
    for raw in raw_items or []:
        palabra = raw.get("palabra") if isinstance(raw, dict) else raw
        palabra = " ".join(str(palabra or "").split()).strip()[:MAX_BIT_WORD_CHARS]
        if not palabra:
            continue
        key = palabra.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({"palabra": palabra})
        if len(items) >= limit:
            break
    return items


def extract_bits_words(file_storage) -> list[dict[str, object]]:
    """Uploads the teacher's document to Gemini and asks it to pick out the
    words already written there. Mirrors extract_image_sentences; returns
    [{palabra}]."""
    filename = file_storage.filename or "documento"
    mime_type = file_storage.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    suffix = os.path.splitext(filename)[1]

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_storage.save(tmp)
            tmp_path = tmp.name

        uploaded_file = gemini_client.files.upload(
            file=tmp_path,
            config={"mime_type": mime_type, "display_name": filename},
        )

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded_file, BITS_EXTRACT_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        raw_items = data.get("palabras") if isinstance(data, dict) else data
        return normalize_bits_words(raw_items if isinstance(raw_items, list) else [])
    finally:
        if uploaded_file is not None:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def normalize_bits_syllables(
    raw, *, limit: int = MAX_BITS_SYLLABLES
) -> list[str]:
    """Parses the syllables the teacher typed (a free-text list separated by
    commas, spaces or line breaks, or already a list from the frontend) into
    a deduped, lowercased list, capped at `limit` items of
    `MAX_BIT_SYLLABLE_CHARS` characters each."""
    pieces = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw or ""))
    seen: set[str] = set()
    syllables: list[str] = []
    for piece in pieces:
        # NFC: una "ñ" descompuesta (n + tilde) no debe contar distinta de "ñ".
        syllable = unicodedata.normalize("NFC", str(piece or "")).strip().lower()
        syllable = syllable[:MAX_BIT_SYLLABLE_CHARS]
        if not syllable or syllable in seen:
            continue
        seen.add(syllable)
        syllables.append(syllable)
        if len(syllables) >= limit:
            break
    return syllables


def filter_bits_by_syllables(
    items: list[dict[str, object]], silabas: list[str]
) -> list[dict[str, object]]:
    """Descarta las palabras que no empiezan con ninguna de las sílabas
    elegidas: el modelo a veces se equivoca y aquí la regla es exacta."""
    return [
        item for item in items
        if unicodedata.normalize("NFC", str(item["palabra"])).casefold().startswith(
            tuple(silabas)
        )
    ]


def bits_shared_consonant(items: list[dict[str, object]]) -> str | None:
    """La consonante inicial común a todas las palabras de un material
    `bits` guardado, o None si no la comparten todas (palabras armadas a
    mano o con sílabas de distinta consonante) o si esa letra común no es
    una consonante. Se recalcula a partir de las palabras cada vez que se
    pide -no se persiste- así que también funciona con bits guardados antes
    de esta función."""
    initials = {
        unicodedata.normalize("NFC", str(item.get("palabra") or ""))[:1].upper()
        for item in items
    }
    initials.discard("")
    if len(initials) != 1:
        return None
    (initial,) = initials
    return initial if initial.isalpha() and initial not in BITS_VOWELS else None


def generate_bits_words(
    silabas: list[str], count: int, extra_details: str,
    syllable_count: str = BITS_DEFAULT_SYLLABLE_COUNT,
) -> list[dict[str, object]]:
    """Asks Gemini for `count` words for children of BITS_TARGET_LEVEL that
    start with one of the target syllables and have the number of syllables
    the teacher described in free text (`syllable_count`, passed verbatim),
    later paired with an image each. Returns [{palabra}], keeping only the
    words that really start with one of the syllables."""
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=BITS_GENERATE_PROMPT.format(
            nivel=BITS_TARGET_LEVEL,
            silabas=", ".join(silabas),
            cantidad_silabas=syllable_count,
            detalles=extra_details or "Sin detalles adicionales.",
            cantidad=count,
        ),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    raw_items = data.get("palabras") if isinstance(data, dict) else data
    words = normalize_bits_words(raw_items if isinstance(raw_items, list) else [])
    return filter_bits_by_syllables(words, silabas)


def clean_bit_question(raw) -> str:
    """Pregunta de un bit tal como la escribió o editó la docente: espacios
    normalizados y con tope de largo. Vacía si no hay nada."""
    return " ".join(str(raw or "").split()).strip()[:MAX_BIT_QUESTION_CHARS]


def normalize_bit_question(raw, palabra: str) -> str:
    """Como clean_bit_question, pero para lo que devuelve la IA: descarta (deja
    vacía) la pregunta que contenga la propia palabra, porque esa palabra es la
    respuesta del niño y la pregunta no puede regalarla."""
    question = clean_bit_question(raw)
    word = " ".join(str(palabra or "").split()).casefold()
    if question and word and re.search(
        rf"(?<!\w){re.escape(word)}(?!\w)", question.casefold()
    ):
        return ""
    return question


def suggest_bits_questions(cards: list[tuple[str, bytes]]) -> list[str]:
    """Pide a Gemini, mirando cada imagen, la pregunta que el robot hará por
    tarjeta. `cards` es [(palabra, imagen_png)]; devuelve una pregunta por
    tarjeta, en el mismo orden ("" si la IA no dio una válida)."""
    contents: list = [BITS_QUESTION_PROMPT.format(nivel=BITS_TARGET_LEVEL)]
    for number, (palabra, image) in enumerate(cards, start=1):
        contents.append(f"Tarjeta {number} · palabra: «{palabra}»")
        contents.append(types.Part.from_bytes(data=image, mime_type=_image_mime_type(image)))
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    rows = data.get("preguntas") if isinstance(data, dict) else data
    by_card: dict[int, object] = {}
    for position, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("tarjeta", position))
        except (TypeError, ValueError):
            number = position
        by_card.setdefault(number, row.get("pregunta"))
    return [
        normalize_bit_question(by_card.get(number), palabra)
        for number, (palabra, _image) in enumerate(cards, start=1)
    ]


def generate_story(
    character: str,
    setting: str,
    grade_level: str,
    objective: str,
    extra_details: str,
    duration_minutes: int,
) -> dict[str, object]:
    target_words, min_words, max_words = _story_word_limits(duration_minutes)
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=STORY_PROMPT_TEMPLATE.format(
            nivel=grade_level,
            personaje=character,
            escenario=setting,
            objetivo=objective,
            detalles=extra_details or "Sin detalles adicionales.",
            duracion_minutos=duration_minutes,
            palabras_por_minuto=STORY_NARRATION_WORDS_PER_MINUTE,
            palabras_minimas=min_words,
            palabras_maximas=max_words,
            palabras_objetivo=target_words,
        ),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    data = _parse_story_response(response.text)
    word_count = _count_words(data["story"])

    # Gemini follows a target range better than an exact token count. If the
    # first draft falls outside the range, one focused rewrite keeps the user
    # flow reliable without looping indefinitely or multiplying API costs.
    if not min_words <= word_count <= max_words:
        correction = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=STORY_LENGTH_CORRECTION_TEMPLATE.format(
                palabras_minimas=min_words,
                palabras_maximas=max_words,
                duracion_minutos=duration_minutes,
                cuento_actual=json.dumps({
                    "titulo": data["title"],
                    "cuento": data["story"],
                    "resumen": data["summary"],
                }, ensure_ascii=False),
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        corrected = _parse_story_response(correction.text)
        corrected_word_count = _count_words(corrected["story"])
        if abs(corrected_word_count - target_words) < abs(word_count - target_words):
            data = corrected
            word_count = corrected_word_count

    data.update({
        "target_duration_minutes": duration_minutes,
        "word_count": word_count,
        "estimated_duration_seconds": round(
            word_count / STORY_NARRATION_WORDS_PER_MINUTE * 60
        ),
    })
    return data


def _parse_story_response(response_text: str) -> dict[str, str]:
    data = json.loads(response_text)
    title = str(data.get("titulo") or "").strip()
    story = str(data.get("cuento") or "").strip()
    summary = str(data.get("resumen") or "").strip()
    if not title or not story or not summary:
        raise ValueError("Gemini no devolvió un cuento completo.")
    return {"title": title, "story": story, "summary": summary}


def _parse_duration_minutes(value) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(
            f"La duración debe ser un número entero entre {STORY_DURATION_MINUTES_MIN} "
            f"y {STORY_DURATION_MINUTES_MAX} minutos."
        )
    try:
        duration = int(value)
    except (TypeError, ValueError):
        duration = 0
    if str(value).strip() not in {str(duration), f"{duration}.0"}:
        duration = 0
    if not STORY_DURATION_MINUTES_MIN <= duration <= STORY_DURATION_MINUTES_MAX:
        raise ValueError(
            f"La duración debe estar entre {STORY_DURATION_MINUTES_MIN} "
            f"y {STORY_DURATION_MINUTES_MAX} minutos."
        )
    return duration


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+(?:[’'-]\w+)*\b", text, flags=re.UNICODE))


def _story_word_limits(duration_minutes: int) -> tuple[int, int, int]:
    target = duration_minutes * STORY_NARRATION_WORDS_PER_MINUTE
    tolerance = max(10, round(target * STORY_WORD_COUNT_TOLERANCE))
    return target, target - tolerance, target + tolerance


def _split_text_into_chunks(text: str, max_chars: int = FISH_CHUNK_MAX_CHARS) -> list[str]:
    """Splits text into sentence-aligned chunks no longer than max_chars."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks or [text.strip()]


def generate_speech(text: str) -> tuple[bytes, float]:
    """Narra `text` con Fish Audio. Devuelve el WAV y su duración medida.
    Lanza ValueError si el texto no tiene palabras que narrar y FishAudioError
    si el servicio falla."""
    if not fish_client:
        raise RuntimeError("FISH_API_KEY no está configurada.")
    if not _count_words(text):
        raise ValueError("El texto no contiene palabras que se puedan narrar.")

    pcm_data = bytearray()
    for chunk in _split_text_into_chunks(text, FISH_CHUNK_MAX_CHARS):
        pcm_data.extend(fish_client.synthesize_pcm(chunk))

    sample_rate = fish_client.sample_rate
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(pcm_data))

    duration_seconds = len(pcm_data) / (sample_rate * 2)
    return buffer.getvalue(), round(duration_seconds, 2)


_NARRATION_TAG_RE = re.compile(r"\[([^\[\]]*)\]")


def _sanitize_narration_tags(annotated: str, original: str) -> str:
    """Se queda solo con las etiquetas de FISH_NARRATION_TAGS (sin distinguir
    mayúsculas; cualquier otra corchete que Gemini haya inventado se borra) y
    comprueba que, quitándolas todas, el texto sea -palabra por palabra- el
    original: si Gemini alteró algo más que insertar etiquetas, se descarta y
    se devuelve `original` sin etiquetas, para no arriesgar la fidelidad de la
    narración por una mejora cosmética."""
    allowed = {tag.casefold() for tag in FISH_NARRATION_TAGS}
    cleaned = _NARRATION_TAG_RE.sub(
        lambda m: f"[{m.group(1)}]" if m.group(1).strip().casefold() in allowed else "",
        annotated,
    )
    without_tags = _NARRATION_TAG_RE.sub("", cleaned)
    if normalize_story_text(without_tags) != normalize_story_text(original):
        return original
    return re.sub(r" {2,}", " ", cleaned).strip()


def annotate_narration_for_tts(texto: str, personaje: str = "") -> str:
    """Le pide a Gemini que inserte etiquetas de emoción/tono de Fish Audio
    (FISH_NARRATION_TAGS) dentro de `texto`, para narrar la escena de forma
    más expresiva. Es un ajuste interno: nunca cambia lo que se lee -si
    Gemini reescribe alguna palabra, si la respuesta no es válida o si la
    llamada falla, se devuelve `texto` tal cual, sin etiquetas-."""
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=STORY_NARRATION_TAG_PROMPT.format(
                etiquetas=", ".join(f"[{tag}]" for tag in FISH_NARRATION_TAGS),
                personaje=personaje or "sin datos",
                texto=texto,
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
        annotated = str(data.get("texto_narrado") or "") if isinstance(data, dict) else ""
    except Exception:
        return texto
    if not annotated:
        return texto
    return _sanitize_narration_tags(annotated, texto)


def _wav_duration_seconds(path: str) -> float:
    with wave.open(path, "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        if frame_rate <= 0:
            raise wave.Error("Frecuencia de audio inválida.")
        return wav_file.getnframes() / frame_rate


def _concat_wav_files(sources: list[str], destination: str) -> None:
    """Une varios WAV, en orden, en uno solo. Lanza wave.Error si no todos
    tienen los mismos canales, ancho de muestra y frecuencia (p. ej. audios
    reales mezclados con los del modo demo)."""
    format_params = None
    with wave.open(destination, "wb") as output:
        for source in sources:
            with wave.open(source, "rb") as part:
                current = (part.getnchannels(), part.getsampwidth(), part.getframerate())
                if format_params is None:
                    format_params = current
                    output.setnchannels(current[0])
                    output.setsampwidth(current[1])
                    output.setframerate(current[2])
                elif current != format_params:
                    raise wave.Error("Los audios tienen formatos distintos.")
                output.writeframes(part.readframes(part.getnframes()))


def format_period_label(today: date) -> str:
    return f"{DIAS_ES[today.weekday()]} {today.day}, {MESES_ES[today.month - 1]}"


# Umbrales de la barra "Resultados por material" del avance de aula: el color
# es la severidad del acierto (bueno/atención/bajo), nunca una identidad de
# serie, así que son fijos y no se mezclan con ninguna otra paleta de la app.
MATERIAL_PROGRESS_GOOD_THRESHOLD = 56
MATERIAL_PROGRESS_WARNING_THRESHOLD = 31


def material_progress_rows(student_interactions: list) -> list[dict]:
    """Agrupa las interacciones YA CARGADAS de un alumno por material (o
    "Conversación" para las que no tienen material) y calcula, para cada
    grupo, su relación aciertos/total.

    No dispara ninguna consulta nueva: recibe la misma lista de objetos
    `Interaccion` (con `.material` precargado por `contains_eager`) que ya se
    usaba para el tally general de la fila, y solo la reagrupa en memoria.
    El orden de salida es el de la primera interacción con cada material -la
    lista de entrada ya viene ordenada por fecha ascendente-, así que las
    barras aparecen en el orden en que el alumno fue trabajando cada una."""
    buckets: dict[int | None, dict] = {}
    for item in student_interactions:
        key = item.id_material
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "name": item.material.nombre_material if item.material else "Conversación",
                "is_conversation": item.material is None,
                "correct": 0,
                "total": 0,
            }
            buckets[key] = bucket
        bucket["total"] += 1
        if item.rpta_correcta:
            bucket["correct"] += 1

    rows = []
    for bucket in buckets.values():
        percent = round(bucket["correct"] / bucket["total"] * 100)
        if percent >= MATERIAL_PROGRESS_GOOD_THRESHOLD:
            severity = "good"
        elif percent >= MATERIAL_PROGRESS_WARNING_THRESHOLD:
            severity = "warning"
        else:
            severity = "danger"
        rows.append({**bucket, "percent": percent, "severity": severity})
    return rows


def create_app(test_config: dict | None = None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Los archivos de los materiales (texto, audio, preguntas, oraciones) y
        # los audios de respuesta de las interacciones viven FUERA de static/
        # para que no se sirvan sin autenticación. La consola los entrega por
        # /media/<token> (URL firmada y con caducidad) y el robot por
        # /api/materials/<id>/<recurso> (secreto compartido).
        UPLOADS_ROOT=os.environ.get("MAXCIM_UPLOADS_DIR")
        or os.path.join(app.instance_path, "uploads"),
        MAXCIM_WEBHOOK_SECRET=os.environ.get("MAXCIM_WEBHOOK_SECRET", ""),
        SESSION_TOKEN_ENCRYPTION_KEY=os.environ.get("SESSION_TOKEN_ENCRYPTION_KEY", ""),
        INSTITUTIONAL_API_BASE_URL=os.environ.get("INSTITUTIONAL_API_BASE_URL", ""),
        INSTITUTIONAL_API_LOGIN_PATH=os.environ.get("INSTITUTIONAL_API_LOGIN_PATH", ""),
        INSTITUTIONAL_API_GOOGLE_LOGIN_PATH=os.environ.get(
            "INSTITUTIONAL_API_GOOGLE_LOGIN_PATH", ""
        ),
        INSTITUTIONAL_API_CLASSROOMS_PATH=os.environ.get("INSTITUTIONAL_API_CLASSROOMS_PATH", ""),
        INSTITUTIONAL_API_STUDENTS_PATH=os.environ.get("INSTITUTIONAL_API_STUDENTS_PATH", ""),
        INSTITUTIONAL_API_STUDENT_PATH=os.environ.get("INSTITUTIONAL_API_STUDENT_PATH", ""),
        INSTITUTIONAL_API_SERVICE_TOKEN=os.environ.get("INSTITUTIONAL_API_SERVICE_TOKEN", ""),
        INSTITUTIONAL_API_ID_SYSTEM=int(os.environ.get("INSTITUTIONAL_API_ID_SYSTEM", "0") or 0),
        INSTITUTIONAL_API_TIMEOUT_SECONDS=float(os.environ.get("INSTITUTIONAL_API_TIMEOUT_SECONDS", "8")),
        INSTITUTIONAL_API_VERIFY_TLS=env_bool("INSTITUTIONAL_API_VERIFY_TLS", True),
        GOOGLE_OAUTH_CLIENT_ID=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        GOOGLE_OAUTH_CLIENT_SECRET=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        GOOGLE_OAUTH_ALLOWED_DOMAINS=os.environ.get("GOOGLE_OAUTH_ALLOWED_DOMAINS", ""),
        GOOGLE_OAUTH_REDIRECT_URI=os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", ""),
        GOOGLE_OAUTH_TIMEOUT_SECONDS=float(os.environ.get("GOOGLE_OAUTH_TIMEOUT_SECONDS", "8")),
        LOGIN_MAX_ATTEMPTS=int(os.environ.get("LOGIN_MAX_ATTEMPTS", "3")),
        LOGIN_LOCKOUT_SECONDS=int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900")),
        DEMO_MODE=DEFAULT_DEMO_MODE,
        SESSION_COOKIE_NAME="maxcim_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", not DEFAULT_DEMO_MODE),
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.config["UPLOADS_ROOT"], exist_ok=True)

    if app.config.get("DEMO_MODE"):
        # Demo solo puede correr sobre su SQLite aislada. Demo + base remota =
        # el bypass de autenticación encendido sobre datos reales (hallazgo
        # A-02); mejor no arrancar.
        if not app.config.get("TESTING") and not str(
            app.config.get("SQLALCHEMY_DATABASE_URI", "")
        ).startswith("sqlite:"):
            raise RuntimeError(
                "DEMO_MODE=true solo puede usar SQLite aislada, pero hay una "
                "base remota configurada. Demo acepta cualquier credencial: no "
                "puede tocar datos reales. Pon DEMO_MODE=false o quita DATABASE_URL."
            )
        os.makedirs(app.instance_path, exist_ok=True)
        if not app.config.get("SECRET_KEY"):
            app.config["SECRET_KEY"] = secrets.token_hex(32)
        if not app.config.get("SESSION_TOKEN_ENCRYPTION_KEY"):
            app.config["SESSION_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
        if not app.config.get("MAXCIM_WEBHOOK_SECRET"):
            app.config["MAXCIM_WEBHOOK_SECRET"] = "maxcim-demo-isolated-webhook"
    elif not app.config.get("TESTING"):
        # Producción: los secretos compartidos no pueden quedar vacíos, ser un
        # marcador de `.env.example` ni ser triviales. El helper se reutiliza
        # para otros secretos en hallazgos posteriores.
        _require_strong_secret(app.config, "MAXCIM_WEBHOOK_SECRET")
        # Con SECRET_KEY débil o ausente las cookies de sesión y los tokens
        # firmados (media, aulas) se pueden falsificar.
        _require_strong_secret(app.config, "SECRET_KEY")
        # Las credenciales de la docente viajan hacia CIMA: solo por HTTPS y
        # con el certificado verificado (salvo desarrollo local explícito).
        if not env_bool("ALLOW_INSECURE_SESSION_COOKIE", False):
            institutional_url = str(app.config.get("INSTITUTIONAL_API_BASE_URL") or "").strip()
            if institutional_url and not institutional_url.lower().startswith("https://"):
                raise RuntimeError(
                    "INSTITUTIONAL_API_BASE_URL debe usar https:// con DEMO_MODE=false."
                )
            if not app.config.get("INSTITUTIONAL_API_VERIFY_TLS", True):
                raise RuntimeError(
                    "INSTITUTIONAL_API_VERIFY_TLS=false no está permitido con DEMO_MODE=false."
                )
        # La sesión de la docente vive entera en la cookie firmada (incluye el
        # JWT de CIMA cifrado). Fuera de demo se exige HTTPS: el valor del
        # `.env` se ignora para que no pueda quedar mal configurado.
        #
        # Escape hatch SOLO para desarrollo local sobre http://: con
        # ALLOW_INSECURE_SESSION_COOKIE=true se respeta SESSION_COOKIE_SECURE=false
        # y no se fuerza el esquema https (si no, la cookie no viaja por HTTP y
        # las URLs `_external` saldrían https://). Nunca lo pongas en un
        # despliegue real.
        allow_insecure_cookie = env_bool("ALLOW_INSECURE_SESSION_COOKIE", False)
        if allow_insecure_cookie and not app.config.get("SESSION_COOKIE_SECURE"):
            app.logger.warning(
                "ALLOW_INSECURE_SESSION_COOKIE=true: la cookie de sesión NO es "
                "Secure. Solo debe usarse en desarrollo local sobre http://."
            )
        else:
            if not app.config.get("SESSION_COOKIE_SECURE"):
                app.logger.warning(
                    "SESSION_COOKIE_SECURE llegó en false; se fuerza a true (DEMO_MODE=false)."
                )
            app.config["SESSION_COOKIE_SECURE"] = True
            app.config["PREFERRED_URL_SCHEME"] = "https"
        # Detrás de un terminador TLS, Gunicorn recibe HTTP en el salto interno.
        # ProxyFix hace que Flask lea el esquema/host reales de X-Forwarded-*,
        # necesario para emitir la cookie Secure y armar URLs https://.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)

    institutional_client = app.config.get("INSTITUTIONAL_CLIENT")
    if institutional_client is None:
        institutional_client = (
            DemoInstitutionalClient()
            if app.config.get("DEMO_MODE")
            else InstitutionalClient.from_config(app.config)
        )
    app.extensions["institutional_client"] = institutional_client
    google_oidc_client = app.config.get("GOOGLE_OIDC_CLIENT") or GoogleOIDCClient.from_config(
        app.config
    )
    app.extensions["google_oidc_client"] = google_oidc_client
    login_throttle = LoginThrottle(
        max_attempts=app.config.get("LOGIN_MAX_ATTEMPTS", 3),
        lockout_seconds=app.config.get("LOGIN_LOCKOUT_SECONDS", 900),
    )
    app.extensions["login_throttle"] = login_throttle
    # Tope por IP (cualquier usuario): el bloqueo por (IP, usuario) no frena un
    # "password spraying" que prueba una clave sobre muchas cuentas. Es holgado
    # para no castigar a un colegio entero detrás de una misma IP pública, y un
    # login exitoso no lo reinicia (si no, una cuenta propia lo resetearía).
    login_ip_throttle = LoginThrottle(
        max_attempts=app.config.get("LOGIN_IP_MAX_ATTEMPTS", 100),
        lockout_seconds=app.config.get("LOGIN_LOCKOUT_SECONDS", 900),
    )
    app.extensions["login_ip_throttle"] = login_ip_throttle

    def token_cipher() -> Fernet:
        key = str(app.config.get("SESSION_TOKEN_ENCRYPTION_KEY") or "").strip()
        if not key:
            raise InstitutionalConfigurationError(
                "Falta configurar SESSION_TOKEN_ENCRYPTION_KEY en el servidor."
            )
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise InstitutionalConfigurationError(
                "SESSION_TOKEN_ENCRYPTION_KEY no tiene un formato Fernet válido."
            ) from exc

    def safe_next_path(value: str | None) -> str:
        # Los navegadores tratan "\" como "/", así que "/\evil.com" equivale a
        # "//evil.com" (redirección abierta); tampoco se admiten caracteres de
        # control (tabulaciones/saltos de línea que el navegador descarta).
        candidate = str(value or "")
        if (
            candidate.startswith("/")
            and not candidate.startswith("//")
            and "\\" not in candidate
            and not any(ord(char) < 32 or ord(char) == 127 for char in candidate)
        ):
            return candidate
        return url_for("dashboard")

    def complete_teacher_login(authenticated, next_path: str | None = None):
        # No hay tabla `sesion_web_docente` en este esquema: la sesión del
        # docente vive solo en la cookie firmada de Flask. El access token
        # institucional va cifrado dentro de la cookie (no en claro) porque
        # es un bearer token hacia la API institucional.
        cipher = token_cipher()
        destination = safe_next_path(next_path)
        browser_session.clear()
        browser_session["teacher_id"] = authenticated.institutional_id
        browser_session["teacher_name"] = authenticated.display_name
        # Nombre sin formatear, tal como lo envía la API institucional: es el
        # que se guarda en `material.fk_user_name` (ver save_material). Si el
        # cliente no distingue una forma cruda (demo), cae en display_name.
        browser_session["teacher_raw_name"] = authenticated.raw_name or authenticated.display_name
        browser_session["teacher_role"] = authenticated.role
        browser_session["teacher_photo"] = authenticated.photo_url
        browser_session["teacher_token"] = cipher.encrypt(
            authenticated.access_token.encode("utf-8")
        ).decode("ascii")
        browser_session["teacher_expires_at"] = (
            utc_now() + timedelta(seconds=authenticated.expires_in_seconds)
        ).isoformat()
        browser_session.permanent = False
        return redirect(destination)

    def login_readiness() -> dict[str, bool]:
        if app.config.get("DEMO_MODE"):
            return {"password_ready": True, "google_ready": True, "any_ready": True}
        secure_session_ready = bool(
            app.config.get("SECRET_KEY")
            and app.config.get("SESSION_TOKEN_ENCRYPTION_KEY")
        )
        password_ready = bool(
            secure_session_ready and getattr(institutional_client, "login_ready", False)
        )
        google_ready = bool(
            secure_session_ready
            and getattr(institutional_client, "google_login_ready", False)
            and getattr(google_oidc_client, "ready", False)
        )
        return {
            "password_ready": password_ready,
            "google_ready": google_ready,
            "any_ready": password_ready or google_ready,
        }

    def render_login_page(error: str | None = None, status_code: int = 200):
        if not error and app.config.get("SECRET_KEY"):
            error = browser_session.pop("google_login_error", None)
        readiness = login_readiness()
        return render_template(
            "login.html",
            error=error,
            api_ready=readiness["any_ready"],
            password_ready=readiness["password_ready"],
            google_ready=readiness["google_ready"],
            demo_mode=bool(app.config.get("DEMO_MODE")),
        ), status_code

    def google_redirect_uri() -> str:
        configured = str(app.config.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
        if configured:
            return configured
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return url_for("google_callback", _external=True, _scheme=scheme)

    def google_error_redirect(message: str):
        browser_session["google_login_error"] = message
        return redirect(url_for("login"))

    def current_teacher() -> dict | None:
        cached = getattr(g, "maxcim_teacher", None)
        if cached is not None:
            return cached

        if app.config.get("TESTING") and app.config.get("TEST_TEACHER"):
            teacher = dict(app.config["TEST_TEACHER"])
            g.maxcim_teacher = teacher
            return teacher

        token_blob = browser_session.get("teacher_token")
        expires_at_raw = browser_session.get("teacher_expires_at")
        if not token_blob or not expires_at_raw:
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            browser_session.clear()
            return None
        if expires_at <= utc_now():
            browser_session.clear()
            return None
        try:
            access_token = token_cipher().decrypt(token_blob.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            browser_session.clear()
            return None

        name_parts = [
            part for part in str(browser_session.get("teacher_name") or "").split() if part
        ]
        teacher = {
            "id": browser_session.get("teacher_id"),
            "name": browser_session.get("teacher_name"),
            "raw_name": browser_session.get("teacher_raw_name") or browser_session.get("teacher_name"),
            "initials": "".join(part[0].upper() for part in name_parts[:2]) or "DC",
            "role": browser_session.get("teacher_role"),
            "photo_url": browser_session.get("teacher_photo") or "",
            "access_token": access_token,
        }
        g.maxcim_teacher = teacher
        return teacher

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_teacher() is None:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "La sesión institucional no es válida o expiró."}), 401
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    def csrf_token() -> str:
        if not app.config.get("SECRET_KEY"):
            return ""
        token = browser_session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            browser_session["csrf_token"] = token
        return token

    app.jinja_env.globals["csrf_token"] = csrf_token

    # --- Rutas de los archivos de materiales (fuera de static/) ---------------

    def uploads_relpath(stored_path: str) -> str:
        """Ruta de un archivo relativa a UPLOADS_ROOT.

        En la BD las rutas se guardan como `uploads/<id>/texto.txt` (prefijo
        histórico de cuando vivían bajo static/); aquí se le quita ese prefijo
        y se normaliza para impedir escapar del directorio (`..`, rutas
        absolutas, letra de unidad de Windows, UNC `\\\\host\\share`).
        """
        rel = str(stored_path or "").replace("\\", "/").strip().lstrip("/")
        if rel.startswith("uploads/"):
            rel = rel[len("uploads/"):]
        # Normalización POSIX (barras `/`): send_from_directory usa rutas
        # estilo POSIX incluso en Windows, así que no se debe introducir `\`.
        rel = posixpath.normpath(rel)
        if (
            not rel
            or rel in (".", "..")
            or rel.startswith(("../", "/"))
            or ":" in rel  # letra de unidad Windows (C:/...) o esquema
            or ntpath.splitdrive(rel)[0]  # unidad o UNC
        ):
            raise ValueError("Ruta de material fuera del directorio permitido.")
        return rel

    def uploads_abspath(stored_path: str) -> str:
        """Ruta absoluta del archivo, garantizada dentro de UPLOADS_ROOT.

        Además de la validación léxica de `uploads_relpath`, resuelve enlaces
        simbólicos y confirma con `commonpath` que el resultado real no se
        salió de la raíz (defensa contra symlinks apuntando afuera)."""
        root = os.path.realpath(app.config["UPLOADS_ROOT"])
        candidate = os.path.realpath(
            os.path.join(root, uploads_relpath(stored_path))
        )
        try:
            if os.path.commonpath([root, candidate]) != root:
                raise ValueError
        except ValueError:
            raise ValueError("Ruta de material fuera del directorio permitido.")
        return candidate

    # --- URLs firmadas para servir esos archivos a la consola ----------------

    MEDIA_URL_MAX_AGE_SECONDS = 3600

    def _media_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="maxcim-media")

    def media_url(stored_path: str | None) -> str:
        """URL firmada hacia un archivo de material, válida 1 h y **atada a la
        docente autenticada**: el token lleva su `id`, así que una URL copiada
        (historial, captura, log) no sirve en la sesión de otra docente.
        Reemplaza a `url_for('static', ...)` para que la ruta interna
        (`uploads/<uuid>/...`) no quede en texto plano en el HTML ni en los
        logs de acceso."""
        if not stored_path:
            return ""
        teacher = current_teacher() or {}
        token = _media_serializer().dumps(
            {"p": uploads_relpath(stored_path), "t": str(teacher.get("id"))}
        )
        return url_for("teacher_media", token=token)

    app.jinja_env.globals["media_url"] = media_url
    app.jinja_env.filters["local_dt"] = to_local_time

    # --- Identificadores de aula/alumno en las URLs de la consola -----------
    # Token firmado (no cifrado): el ID institucional viaja dentro pero no en
    # texto plano en la ruta, no se puede falsificar ni reutilizar en la
    # sesión de otra docente, caduca, y rotar SECRET_KEY invalida todos.

    REF_URL_MAX_AGE_SECONDS = 86_400  # 24 h; se regeneran en cada carga de página

    def _ref_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="maxcim-teacher-ref")

    def classroom_ref(classroom_id) -> str:
        teacher = current_teacher() or {}
        return _ref_serializer().dumps(
            {"k": "c", "t": str(teacher.get("id")), "c": str(classroom_id)}
        )

    def student_ref(classroom_id, student_id) -> str:
        teacher = current_teacher() or {}
        return _ref_serializer().dumps(
            {
                "k": "s",
                "t": str(teacher.get("id")),
                "c": str(classroom_id),
                "s": str(student_id),
            }
        )

    app.jinja_env.globals["classroom_ref"] = classroom_ref
    app.jinja_env.globals["student_ref"] = student_ref

    class _RefError(Exception):
        """Un ref de aula/alumno inválido: firma mala, caducado, de otra
        docente, o del tipo equivocado (aula vs alumno)."""

    def _load_ref(token: str, expected_kind: str) -> dict:
        teacher = current_teacher() or {}
        try:
            data = _ref_serializer().loads(
                str(token), max_age=REF_URL_MAX_AGE_SECONDS
            )
        except BadData:
            raise _RefError()
        if (
            not isinstance(data, dict)
            or data.get("k") != expected_kind
            or str(data.get("t")) != str(teacher.get("id"))
        ):
            raise _RefError()
        return data

    def resolve_classroom_ref(token: str) -> str:
        return _load_ref(token, "c")["c"]

    def resolve_student_ref(token: str) -> tuple[str, str]:
        data = _load_ref(token, "s")
        if "s" not in data:
            raise _RefError()
        return data["c"], data["s"]

    @app.context_processor
    def inject_environment():
        return {"demo_mode": bool(app.config.get("DEMO_MODE"))}

    def webhook_authorized() -> bool:
        if app.config.get("DEMO_MODE"):
            return True
        if app.config.get("TESTING") and app.config.get("TEST_WEBHOOK_AUTHORIZED", True):
            return True
        expected = app.config.get("MAXCIM_WEBHOOK_SECRET", "")
        received = request.headers.get("X-MAXCIM-Webhook-Secret", "")
        return bool(expected and received and hmac.compare_digest(expected, received))

    @app.before_request
    def enforce_csrf():
        if request.method in {"GET", "HEAD", "OPTIONS"} or app.config.get("TESTING"):
            return None
        if request.path == "/api/interacciones":
            # Endpoint robot-side: no es un formulario de navegador, así que
            # no lleva token CSRF. Su propia autorización (el secreto
            # compartido, vía webhook_authorized()) decide con un 401 claro
            # en vez de un 403 genérico de CSRF.
            return None
        if webhook_authorized():
            return None
        expected = str(browser_session.get("csrf_token") or "")
        received = str(
            request.headers.get("X-CSRF-Token")
            or request.form.get("csrf_token")
            or ""
        )
        if not expected or not received or not hmac.compare_digest(expected, received):
            if request.path.startswith("/api/"):
                return jsonify({"error": "La solicitud no superó la validación de seguridad."}), 403
            return render_login_page("La sesión del formulario expiró.", 403)
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # Solo se restringen scripts y objetos (la consola no usa scripts ni
        # manejadores en línea): un XSS futuro no podría ejecutar código. Los
        # estilos, imágenes y medios quedan sin restringir a propósito. NO
        # añadir `form-action`: el botón de Google es un <form> que redirige a
        # accounts.google.com, y los navegadores aplican esa directiva también
        # a la redirección (bloquearía el login con Google).
        response.headers.setdefault(
            "Content-Security-Policy",
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'self'",
        )
        # HSTS solo si de verdad se sirve por HTTPS (Secure cookie activa); en
        # desarrollo local sobre http:// forzaría al navegador a exigir TLS.
        if not app.config.get("DEMO_MODE") and app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        if (
            request.path.startswith("/api/")
            or request.path.startswith("/auth/")
            or request.path.startswith("/login")
            or request.path.startswith("/aulas")
            or request.path.startswith("/media/")
            or request.path in {
            "/dashboard", "/material",
            }
        ):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.route("/service-worker.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "service-worker.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if current_teacher() else "login"))

    @app.route("/health")
    def health():
        payload = {"status": "ok"}
        if app.config.get("DEMO_MODE"):
            payload["environment"] = "test"
        return jsonify(payload)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_teacher():
            return redirect(url_for("dashboard"))

        error = None
        status_code = 200
        if request.method == "POST":
            institutional_id = str(request.form.get("institutional_id") or "").strip()
            credential = str(request.form.get("credential") or "")
            if not institutional_id or not credential:
                error = "Ingresa tu ID y credencial institucional."
                status_code = 400
            elif len(institutional_id) > MAX_LOGIN_ID_CHARS or len(credential) > MAX_LOGIN_CREDENTIAL_CHARS:
                error = "El ID o la credencial exceden el largo permitido."
                status_code = 400
            else:
                client_ip = request.remote_addr or ""
                wait_seconds = max(
                    login_throttle.retry_after(client_ip, institutional_id),
                    login_ip_throttle.retry_after(client_ip, "*"),
                )
                if wait_seconds:
                    # Bloqueado: no se consulta a CIMA, así que un ataque de
                    # fuerza bruta tampoco la satura ni consume sus intentos.
                    minutes = (wait_seconds + 59) // 60
                    response, status = render_login_page(
                        "Demasiados intentos fallidos. Vuelve a intentarlo en "
                        f"{minutes} minuto{'s' if minutes != 1 else ''}.",
                        429,
                    )
                    response = app.make_response((response, status))
                    response.headers["Retry-After"] = str(wait_seconds)
                    return response
                try:
                    authenticated = institutional_client.authenticate(institutional_id, credential)
                    login_throttle.record_success(client_ip, institutional_id)
                    return complete_teacher_login(
                        authenticated,
                        request.args.get("next"),
                    )
                except InstitutionalAuthenticationError as exc:
                    # Solo cuentan las credenciales rechazadas; una caída de
                    # CIMA (503/502) no debe bloquear a nadie.
                    login_ip_throttle.record_failure(client_ip, "*")
                    remaining = login_throttle.record_failure(client_ip, institutional_id)
                    error = str(exc)
                    status_code = exc.status_code
                    if remaining == 0:
                        error = (
                            "Demasiados intentos fallidos. Tu acceso queda bloqueado "
                            f"por {max(1, login_throttle.lockout_seconds // 60)} minutos."
                        )
                        status_code = 429
                    else:
                        error += (
                            f" Te quedan {remaining} "
                            f"intento{'s' if remaining != 1 else ''}."
                        )
                except InstitutionalAPIError as exc:
                    error = str(exc)
                    status_code = exc.status_code

        return render_login_page(error, status_code)

    @app.route("/login/google")
    def google_login():
        if current_teacher():
            return redirect(url_for("dashboard"))
        if app.config.get("DEMO_MODE"):
            authenticated = institutional_client.authenticate_google("demo-google-login")
            return complete_teacher_login(authenticated, request.args.get("next"))
        if not login_readiness()["google_ready"]:
            return render_login_page(
                "El acceso institucional con Google todavía no está configurado.",
                503,
            )

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier, code_challenge = google_oidc_client.create_pkce_pair()
        browser_session["google_oauth_state"] = state
        browser_session["google_oauth_nonce"] = nonce
        browser_session["google_oauth_code_verifier"] = code_verifier
        browser_session["google_oauth_next"] = safe_next_path(request.args.get("next"))
        authorization_url = google_oidc_client.authorization_url(
            redirect_uri=google_redirect_uri(),
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
        )
        return redirect(authorization_url)

    @app.route("/auth/google/callback")
    def google_callback():
        expected_state = str(browser_session.pop("google_oauth_state", ""))
        nonce = str(browser_session.pop("google_oauth_nonce", ""))
        code_verifier = str(browser_session.pop("google_oauth_code_verifier", ""))
        next_path = str(browser_session.pop("google_oauth_next", ""))

        if request.args.get("error"):
            return google_error_redirect("El acceso con Google fue cancelado.")
        received_state = str(request.args.get("state") or "")
        code = str(request.args.get("code") or "")
        if (
            not expected_state
            or not received_state
            or not hmac.compare_digest(expected_state, received_state)
            or not nonce
            or not code_verifier
            or not code
        ):
            return google_error_redirect(
                "La respuesta de Google no corresponde a esta sesión de acceso."
            )

        try:
            identity = google_oidc_client.exchange_and_verify(
                code=code,
                redirect_uri=google_redirect_uri(),
                nonce=nonce,
                code_verifier=code_verifier,
            )
            authenticated = institutional_client.authenticate_google(identity.email)
            return complete_teacher_login(authenticated, next_path)
        except (GoogleOIDCError, InstitutionalAPIError) as exc:
            return google_error_redirect(str(exc))

    @app.route("/logout", methods=["POST"])
    def logout():
        # Sin sesion_web_docente no hay nada que revocar del lado del
        # servidor: cerrar sesión es simplemente borrar la cookie.
        browser_session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        teacher = current_teacher()
        try:
            institutional_classrooms = institutional_client.list_teacher_classrooms(
                teacher["access_token"], teacher["id"]
            )
        except InstitutionalAPIError as exc:
            return render_template(
                "integration_error.html",
                user=teacher,
                active_nav="tablon",
                message=str(exc),
            ), exc.status_code

        teacher_materials = Material.query.filter_by(fk_user=str(teacher["id"])).all()
        material_ids = [material.id for material in teacher_materials]
        # Estos agregados del tablón ("Interacciones registradas", "Promedio de
        # aciertos", "Alumnos participantes") se limitan a las interacciones
        # sobre materiales de la docente. Las conversaciones libres
        # (id_material NULL) NO se incluyen aquí: no tienen material dueño, así
        # que para atribuirlas a la docente habría que cruzar cada una con la
        # matrícula del aula (una llamada por aula a la API institucional en
        # cada carga del tablón, justo lo que este bloque evita). El desglose
        # que sí las cuenta —rotuladas "Conversación"— vive en
        # /aulas/<id>/avance y en el detalle del alumno.
        interactions = (
            Interaccion.query.filter(Interaccion.id_material.in_(material_ids)).all()
            if material_ids
            else []
        )
        correct_count = sum(1 for item in interactions if item.rpta_correcta)
        average_score = (
            round(correct_count / len(interactions) * 100) if interactions else None
        )
        participating_students = {item.fk_alumno for item in interactions}

        stat_cards = [
            {"value": len(institutional_classrooms), "label": "Aulas a cargo", "color": "#2f5bcf"},
            {"value": len(interactions), "label": "Interacciones registradas", "color": "#d64545"},
            {"value": f"{average_score}%" if average_score is not None else "—", "label": "Promedio de aciertos", "color": "#1f9d55"},
            {"value": len(participating_students), "label": "Alumnos participantes", "color": "#132a5e"},
        ]

        # El desglose de interacciones por aula se ve en /aulas/<id>/avance
        # (cruza cada interacción con la matrícula vigente de la API). Aquí no
        # se calcula para no encadenar una llamada por aula en cada carga del
        # tablón.
        aulas = []
        for classroom in institutional_classrooms:
            words = [word for word in classroom.name.split() if word]
            aulas.append({
                "id": classroom.institutional_id,
                "name": classroom.name,
                "grade": classroom.grade,
                "course": classroom.course,
                "period": classroom.period,
                "initials": "".join(word[0].upper() for word in words[:2]) or "AU",
                "score": None,
                "pending": 0,
            })

        periods = sorted({classroom.period for classroom in institutional_classrooms if classroom.period})
        return render_template(
            "dashboard.html",
            active_nav="tablon",
            user=teacher,
            stat_cards=stat_cards,
            aulas=aulas,
            today_label=format_period_label(date.today()),
            periodo_label=" · ".join(periods) if periods else "Periodo institucional activo",
        )

    def teacher_classroom_or_error(teacher, classroom_id):
        """Obtiene un aula solo si la API confirma que pertenece a la docente."""
        classrooms = institutional_client.list_teacher_classrooms(
            teacher["access_token"], teacher["id"]
        )
        classroom = next(
            (
                item
                for item in classrooms
                if str(item.institutional_id) == str(classroom_id)
            ),
            None,
        )
        if classroom is None:
            raise InstitutionalAPIError(
                "El aula solicitada no pertenece a la docente autenticada.", 404
            )
        return classroom

    def render_classroom_error(teacher, exc):
        return render_template(
            "integration_error.html",
            user=teacher,
            active_nav="tablon",
            message=str(exc),
        ), exc.status_code

    def classroom_context(teacher, classroom_id):
        classroom = teacher_classroom_or_error(teacher, classroom_id)
        students = institutional_client.list_classroom_students(
            teacher["access_token"], classroom.institutional_id, classroom.section_type
        )
        return classroom, students

    def _bad_ref_error(teacher):
        return render_classroom_error(
            teacher,
            InstitutionalAPIError(
                "El enlace no es válido o no corresponde a tu sesión.", 404
            ),
        )

    @app.route("/aulas/<ref>")
    @login_required
    def classroom_detail(ref):
        teacher = current_teacher()
        try:
            classroom_id = resolve_classroom_ref(ref)
        except _RefError:
            return _bad_ref_error(teacher)
        try:
            classroom, students = classroom_context(teacher, classroom_id)
        except InstitutionalAPIError as exc:
            return render_classroom_error(teacher, exc)
        return render_template(
            "classroom_detail.html",
            active_nav="tablon",
            user=teacher,
            classroom=classroom,
            students=students,
        )

    def resolve_interaction_filters(teacher):
        """Resuelve los filtros de Periodo y Tema para las vistas de
        interacciones (avance de aula e historial de un alumno).

        Mismo criterio que ya usa /temas: `?periodo=` ausente arranca acotado
        al periodo académico vigente hoy -para no listar de entrada todo el
        historial de golpe-; presente (aunque venga vacío o inválido) respeta
        la elección explícita de la docente, incluyendo "Todos los
        periodos". `?tema=` funciona igual, pero sin el parámetro el valor
        por defecto es el primer tema de la docente dentro de ese periodo (no
        "todos los temas")."""
        available_periodos = Periodo.query.order_by(Periodo.fecha_inicio).all()
        raw_periodo_id = request.args.get("periodo")
        if raw_periodo_id is None:
            active_periodo = current_periodo()
            selected_periodo_id = active_periodo.id if active_periodo else None
        else:
            selected_periodo_id = None
            if raw_periodo_id.strip():
                try:
                    candidate_periodo_id = int(raw_periodo_id)
                except (TypeError, ValueError):
                    pass
                else:
                    if any(p.id == candidate_periodo_id for p in available_periodos):
                        selected_periodo_id = candidate_periodo_id

        # Temas de la propia docente (no se comparten entre docentes),
        # acotados por periodo en la plantilla (period_filter.js).
        available_temas = (
            Tema.query.filter_by(fk_user=str(teacher["id"]))
            .order_by(Tema.id_periodo, Tema.nombre)
            .all()
        )
        raw_tema_id = request.args.get("tema")
        if raw_tema_id is None:
            selected_tema_id = next(
                (tema.id for tema in available_temas if tema.id_periodo == selected_periodo_id),
                None,
            ) if selected_periodo_id is not None else None
        else:
            selected_tema_id = None
            if raw_tema_id.strip():
                try:
                    candidate_tema_id = int(raw_tema_id)
                except (TypeError, ValueError):
                    pass
                else:
                    if any(t.id == candidate_tema_id for t in available_temas):
                        selected_tema_id = candidate_tema_id

        if selected_tema_id is not None:
            # El periodo del tema manda si llegan desincronizados (p.ej. un
            # enlace viejo con `tema` pero sin `periodo`, o con un `periodo`
            # que ya no le corresponde a ese tema).
            selected_periodo_id = next(
                (tema.id_periodo for tema in available_temas if tema.id == selected_tema_id),
                selected_periodo_id,
            )

        return available_periodos, selected_periodo_id, available_temas, selected_tema_id

    def resolve_material_filter(teacher, selected_tema_id):
        """Resuelve el filtro de Material para el historial de un alumno -un
        nivel más abajo de tema, solo en esa vista (avance de aula se queda
        en periodo/tema). Sin tema elegido no hay de dónde listar materiales
        -el desplegable queda vacío-, así que a diferencia de tema (que sí
        tiene un valor por defecto) `?material=` ausente simplemente no
        filtra: se listan todas las interacciones del tema."""
        available_materials = []
        if selected_tema_id is not None:
            available_materials = (
                Material.query.filter_by(fk_user=str(teacher["id"]), id_tema=selected_tema_id)
                .order_by(Material.nombre_material)
                .all()
            )

        raw_material_id = request.args.get("material")
        selected_material_id = None
        if raw_material_id and raw_material_id.strip():
            try:
                candidate_material_id = int(raw_material_id)
            except (TypeError, ValueError):
                pass
            else:
                if any(m.id == candidate_material_id for m in available_materials):
                    selected_material_id = candidate_material_id

        return available_materials, selected_material_id

    @app.route("/aulas/<ref>/avance")
    @login_required
    def classroom_progress(ref):
        teacher = current_teacher()
        try:
            classroom_id = resolve_classroom_ref(ref)
        except _RefError:
            return _bad_ref_error(teacher)
        try:
            classroom, students = classroom_context(teacher, classroom_id)
        except InstitutionalAPIError as exc:
            return render_classroom_error(teacher, exc)

        available_periodos, selected_periodo_id, available_temas, selected_tema_id = (
            resolve_interaction_filters(teacher)
        )

        student_ids = [str(student.institutional_id) for student in students]
        interactions = []
        if student_ids:
            # outerjoin + el OR con id_material NULL: una conversación libre no
            # tiene material dueño, así que se atribuye a la docente por la
            # matrícula vigente del aula (igual criterio que docs/… sección 4).
            interactions_query = (
                Interaccion.query.outerjoin(Material)
                .options(contains_eager(Interaccion.material))
                .filter(
                    Interaccion.fk_alumno.in_(student_ids),
                    db.or_(
                        Material.fk_user == str(teacher["id"]),
                        Interaccion.id_material.is_(None),
                    ),
                )
            )
            if selected_periodo_id is not None:
                interactions_query = interactions_query.filter(
                    Interaccion.id_periodo == selected_periodo_id
                )
            if selected_tema_id is not None:
                # Vía Material, igual que en student_detail: la interacción no
                # guarda su propio id_tema, así que filtrar por tema exige un
                # material con ese tema (las conversaciones sueltas quedan fuera).
                interactions_query = interactions_query.filter(
                    Material.id_tema == selected_tema_id
                )
            interactions = interactions_query.order_by(
                Interaccion.fecha_hora.asc(), Interaccion.id.asc()
            ).all()

        interactions_by_student = {student_id: [] for student_id in student_ids}
        for interaction in interactions:
            interactions_by_student[interaction.fk_alumno].append(interaction)
        progress_rows = []
        for student in students:
            student_interactions = interactions_by_student[str(student.institutional_id)]
            correct = sum(1 for item in student_interactions if item.rpta_correcta)
            progress_rows.append({
                "student": student,
                "materials": material_progress_rows(student_interactions),
                "correct": correct,
                "total": len(student_interactions),
            })

        return render_template(
            "classroom_progress.html",
            active_nav="tablon",
            user=teacher,
            classroom=classroom,
            progress_rows=progress_rows,
            periodos=available_periodos,
            selected_periodo_id=selected_periodo_id,
            temas=available_temas,
            selected_tema_id=selected_tema_id,
        )

    @app.route("/aulas/alumno/<ref>")
    @login_required
    def student_detail(ref):
        teacher = current_teacher()
        try:
            classroom_id, student_id = resolve_student_ref(ref)
        except _RefError:
            return _bad_ref_error(teacher)
        try:
            classroom, students = classroom_context(teacher, classroom_id)
        except InstitutionalAPIError as exc:
            return render_classroom_error(teacher, exc)

        student = next(
            (
                item
                for item in students
                if str(item.institutional_id) == str(student_id)
            ),
            None,
        )
        if student is None:
            return render_classroom_error(
                teacher,
                InstitutionalAPIError(
                    "El alumno solicitado no pertenece al aula indicada.", 404
                ),
            )

        available_periodos, selected_periodo_id, available_temas, selected_tema_id = (
            resolve_interaction_filters(teacher)
        )
        available_materials, selected_material_id = resolve_material_filter(
            teacher, selected_tema_id
        )

        interactions_query = (
            Interaccion.query.outerjoin(Material)
            .options(contains_eager(Interaccion.material))
            .filter(
                Interaccion.fk_alumno == str(student.institutional_id),
                db.or_(
                    Material.fk_user == str(teacher["id"]),
                    Interaccion.id_material.is_(None),
                ),
            )
        )
        all_interactions = interactions_query.order_by(
            Interaccion.fecha_hora.desc(), Interaccion.id.desc()
        ).all()
        interactions = all_interactions
        if (
            selected_periodo_id is not None
            or selected_tema_id is not None
            or selected_material_id is not None
        ):
            filtered_query = interactions_query
            if selected_periodo_id is not None:
                filtered_query = filtered_query.filter(
                    Interaccion.id_periodo == selected_periodo_id
                )
            if selected_tema_id is not None:
                # Vía Material: la interacción no guarda su propio id_tema
                # (a diferencia de id_periodo), así que el filtro exige un
                # material con ese tema — las conversaciones sueltas
                # (id_material NULL) quedan fuera cuando se filtra por tema.
                filtered_query = filtered_query.filter(Material.id_tema == selected_tema_id)
            if selected_material_id is not None:
                filtered_query = filtered_query.filter(
                    Interaccion.id_material == selected_material_id
                )
            interactions = filtered_query.order_by(
                Interaccion.fecha_hora.desc(), Interaccion.id.desc()
            ).all()

        breakdown_counts = {}
        for interaction in all_interactions:
            counts = breakdown_counts.setdefault(
                interaction.id_periodo,
                {"correct": 0, "total": 0},
            )
            counts["total"] += 1
            if interaction.rpta_correcta:
                counts["correct"] += 1

        periodo_breakdown = []
        for periodo in available_periodos:
            counts = breakdown_counts.get(periodo.id)
            if counts:
                periodo_breakdown.append({
                    "periodo": periodo,
                    "correct": counts["correct"],
                    "total": counts["total"],
                })
        without_periodo_counts = breakdown_counts.get(None)
        if without_periodo_counts:
            periodo_breakdown.append({
                "periodo": None,
                "correct": without_periodo_counts["correct"],
                "total": without_periodo_counts["total"],
            })

        return render_template(
            "student_detail.html",
            active_nav="tablon",
            user=teacher,
            classroom=classroom,
            student=student,
            interactions=interactions,
            periodos=available_periodos,
            selected_periodo_id=selected_periodo_id,
            periodo_breakdown=periodo_breakdown,
            temas=available_temas,
            selected_tema_id=selected_tema_id,
            materials=available_materials,
            selected_material_id=selected_material_id,
        )

    @app.route("/media/<token>")
    @login_required
    def teacher_media(token):
        """Sirve un archivo de material a la consola a partir de una URL
        firmada por `media_url()`. El token lleva la ruta interna y el `id` de
        la docente y caduca a la hora; una URL válida en la sesión de otra
        docente se rechaza."""
        teacher = current_teacher() or {}
        try:
            data = _media_serializer().loads(token, max_age=MEDIA_URL_MAX_AGE_SECONDS)
        except BadData:
            return jsonify({"error": "El enlace del archivo no es válido o expiró."}), 404
        if not isinstance(data, dict) or str(data.get("t")) != str(teacher.get("id")):
            return jsonify({"error": "El enlace no corresponde a tu sesión."}), 403
        try:
            # Ruta absoluta ya resuelta y verificada dentro de UPLOADS_ROOT; se
            # sirve esa misma (no una relativa que se vuelva a unir), para no
            # validar un path y abrir otro.
            abs_path = uploads_abspath(str(data.get("p") or ""))
        except ValueError:
            return jsonify({"error": "Ruta de archivo no permitida."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": "Archivo no encontrado."}), 404
        return send_file(abs_path)

    @app.route("/material")
    @login_required
    def material():
        teacher = current_teacher()
        available_periodos = Periodo.query.order_by(Periodo.fecha_inicio).all()
        active_periodo = current_periodo()
        # Solo los temas de esta docente (no se comparten entre docentes) y
        # solo para elegirlos al subir material — el mismo criterio de
        # aislamiento por fk_user que ya usa el listado de materiales.
        available_temas = (
            Tema.query.filter_by(fk_user=str(teacher["id"]))
            .order_by(Tema.nombre)
            .all()
        )
        materials = (
            Material.query.filter_by(fk_user=str(teacher["id"]))
            .order_by(Material.fecha_subido.desc(), Material.id.desc())
            .all()
        )
        sentences_by_material = {
            m.id: material_sentences(m)
            for m in materials
            if m.es_oracion or m.es_oracion_imagen
        }
        # Vista previa compuesta (texto + imágenes) para las "oraciones con
        # imágenes"; las oraciones planas se quedan con solo el texto de
        # sentences_by_material (ver material.html).
        image_sentences_by_material = {
            m.id: material_image_preview(m)
            for m in materials
            if m.es_oracion_imagen
        }
        bits_by_material = {
            m.id: material_bits_preview(m)
            for m in materials
            if m.es_bits
        }
        return render_template(
            "material.html",
            active_nav="material",
            user=teacher,
            materials=materials,
            sentences_by_material=sentences_by_material,
            image_sentences_by_material=image_sentences_by_material,
            bits_by_material=bits_by_material,
            skills=MATERIAL_SKILLS,
            question_configuration=QUESTION_CONFIGURATION,
            bits_default_words=BITS_DEFAULT_WORDS,
            bits_default_syllable_count=BITS_DEFAULT_SYLLABLE_COUNT,
            max_bits_syllable_count_chars=MAX_BITS_SYLLABLE_COUNT_CHARS,
            max_bits_syllables=MAX_BITS_SYLLABLES,
            scene_tones=story_scene_tone_options(),
            periodos=available_periodos,
            temas=available_temas,
            current_periodo_id=active_periodo.id if active_periodo else None,
            # Si hoy no cae dentro de ningún periodo (entre años escolares, por
            # ejemplo), el desplegable de Año del modal de subida igual
            # necesita un valor por defecto razonable: el año más reciente
            # registrado, para no dejarlo en el primero (probablemente el más
            # antiguo) de la lista.
            current_periodo_anio=(
                active_periodo.anio if active_periodo
                else max((p.anio for p in available_periodos), default=None)
            ),
        )

    @app.route("/temas")
    @login_required
    def temas():
        teacher = current_teacher()
        available_periodos = Periodo.query.order_by(Periodo.fecha_inicio).all()
        active_periodo = current_periodo()

        # A diferencia del filtro de "Interacciones"/"Avance" (que por
        # defecto muestra todos los periodos), acá se parte del periodo
        # vigente hoy: es el caso de uso más común al entrar a esta vista.
        # "?periodo=" ausente => periodo de hoy; "?periodo=" presente (aunque
        # sea vacío o inválido) => respeta la elección explícita, incluyendo
        # "Todos los periodos".
        raw_periodo_id = request.args.get("periodo")
        if raw_periodo_id is None:
            selected_periodo_id = active_periodo.id if active_periodo else None
        else:
            selected_periodo_id = None
            if raw_periodo_id.strip():
                try:
                    candidate_periodo_id = int(raw_periodo_id)
                except (TypeError, ValueError):
                    pass
                else:
                    if any(p.id == candidate_periodo_id for p in available_periodos):
                        selected_periodo_id = candidate_periodo_id

        temas_query = Tema.query.filter_by(fk_user=str(teacher["id"]))
        if selected_periodo_id is not None:
            temas_query = temas_query.filter_by(id_periodo=selected_periodo_id)
        available_temas = temas_query.order_by(Tema.id_periodo, Tema.nombre).all()

        # Cuántos materiales usa cada tema, para poder explicar en la UI por
        # qué un borrado quedó bloqueado sin que la docente tenga que
        # adivinarlo (mismo criterio que delete_material con interacciones).
        material_counts: dict[int, int] = {}
        materiales_con_tema = (
            Material.query.filter_by(fk_user=str(teacher["id"]))
            .filter(Material.id_tema.isnot(None))
            .all()
        )
        for m in materiales_con_tema:
            material_counts[m.id_tema] = material_counts.get(m.id_tema, 0) + 1

        return render_template(
            "temas.html",
            active_nav="temas",
            user=teacher,
            periodos=available_periodos,
            temas=available_temas,
            selected_periodo_id=selected_periodo_id,
            material_counts=material_counts,
            current_periodo_id=active_periodo.id if active_periodo else None,
            current_periodo_anio=(
                active_periodo.anio if active_periodo
                else max((p.anio for p in available_periodos), default=None)
            ),
        )

    @app.route("/api/temas", methods=["POST"])
    @login_required
    def create_tema():
        teacher = current_teacher()
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "El nombre del tema es obligatorio."}), 400
        if len(nombre) > 120:
            return jsonify({"error": "El nombre del tema excede el límite permitido."}), 413

        raw_periodo_id = (request.form.get("id_periodo") or "").strip()
        if not raw_periodo_id:
            return jsonify({"error": "Elige el periodo del tema."}), 400
        try:
            periodo_id = int(raw_periodo_id)
        except (TypeError, ValueError):
            return jsonify({"error": "El periodo indicado no es válido."}), 400
        if not (1 <= periodo_id <= 2_147_483_647):
            return jsonify({"error": "El periodo indicado no es válido."}), 400
        if db.session.get(Periodo, periodo_id) is None:
            return jsonify({"error": "El periodo indicado no es válido."}), 400

        tema = Tema(nombre=nombre, fk_user=str(teacher["id"]), id_periodo=periodo_id)
        db.session.add(tema)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "Ya tienes un tema con ese nombre en ese periodo."}), 409

        return jsonify({"id": tema.id, "nombre": tema.nombre, "id_periodo": tema.id_periodo})

    @app.route("/api/temas/<int:tema_id>", methods=["PATCH"])
    @login_required
    def rename_tema(tema_id):
        teacher = current_teacher()
        tema = db.session.get(Tema, tema_id)
        # Aislamiento por docente: un tema de otra docente es "no encontrado"
        # para esta sesión, no un 403 que confirmaría que el id existe.
        if tema is None or tema.fk_user != str(teacher["id"]):
            return jsonify({"error": "Tema no encontrado."}), 404

        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "El nombre del tema es obligatorio."}), 400
        if len(nombre) > 120:
            return jsonify({"error": "El nombre del tema excede el límite permitido."}), 413

        tema.nombre = nombre
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "Ya tienes un tema con ese nombre en ese periodo."}), 409

        return jsonify({"id": tema.id, "nombre": tema.nombre, "id_periodo": tema.id_periodo})

    @app.route("/api/temas/<int:tema_id>", methods=["DELETE"])
    @login_required
    def delete_tema(tema_id):
        teacher = current_teacher()
        tema = db.session.get(Tema, tema_id)
        if tema is None or tema.fk_user != str(teacher["id"]):
            return jsonify({"error": "Tema no encontrado."}), 404

        material_count = Material.query.filter_by(id_tema=tema.id).count()
        if material_count:
            return jsonify({
                "error": (
                    f"Este tema tiene {material_count} material"
                    f"{'es' if material_count != 1 else ''} asignado"
                    f"{'s' if material_count != 1 else ''} y no se puede eliminar."
                )
            }), 409

        db.session.delete(tema)
        try:
            db.session.commit()
        except IntegrityError:
            # El `material_count` de arriba puede haber quedado desactualizado
            # (otra pestaña/petición asignó un material a este tema justo
            # después de esa lectura); la FK de material.id_tema lo detecta
            # aquí. Con FK enforcement activo en SQLite (ver extensions.py)
            # esto también se puede disparar en pruebas, no solo en MySQL.
            db.session.rollback()
            return jsonify({
                "error": "Este tema tiene material asignado y no se puede eliminar."
            }), 409
        return jsonify({"deleted": True})

    @app.route("/api/material/process", methods=["POST"])
    @login_required
    def process_material():
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "No se recibió ningún archivo."}), 400
        extension = os.path.splitext(uploaded.filename)[1].lower()
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({"error": "El archivo debe ser DOC, DOCX, PDF o TXT."}), 400

        material_type = (request.form.get("tipo_material") or TIPO_CUENTO).strip()
        if material_type not in TIPOS_MATERIAL:
            return jsonify({"error": "El tipo de material no es válido."}), 400

        if material_type == TIPO_ORACION:
            if not gemini_client and app.config.get("DEMO_MODE"):
                return jsonify({"sentences": extract_demo_sentences(uploaded)})
            if not gemini_client:
                return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
            try:
                sentences = extract_sentences(uploaded)
            except Exception:
                app.logger.exception("No se pudieron identificar las oraciones con Gemini")
                return jsonify({"error": "No se pudieron identificar las oraciones con Gemini."}), 502
            if not sentences:
                return jsonify({"error": "No se encontró ninguna oración en el documento."}), 422
            return jsonify({"sentences": sentences})

        if material_type == TIPO_ORACION_IMAGEN:
            if not gemini_client and app.config.get("DEMO_MODE"):
                return jsonify({"items": extract_demo_image_sentences(uploaded)})
            if not gemini_client:
                return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
            try:
                items = extract_image_sentences(uploaded)
            except Exception:
                app.logger.exception("No se pudieron identificar las oraciones con Gemini")
                return jsonify({"error": "No se pudieron identificar las oraciones con Gemini."}), 502
            if not items:
                return jsonify({
                    "error": "No se encontró ninguna oración con dos sustantivos aptos en el documento."
                }), 422
            return jsonify({"items": items})

        if material_type == TIPO_BITS:
            if not gemini_client and app.config.get("DEMO_MODE"):
                return jsonify({"items": extract_demo_bits_words(uploaded)})
            if not gemini_client:
                return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
            try:
                items = extract_bits_words(uploaded)
            except Exception:
                app.logger.exception("No se pudieron identificar las palabras con Gemini")
                return jsonify({"error": "No se pudieron identificar las palabras con Gemini."}), 502
            if not items:
                return jsonify({"error": "No se encontró ninguna palabra apta en el documento."}), 422
            return jsonify({"items": items})

        if not gemini_client and app.config.get("DEMO_MODE"):
            transcribed_text, summary_text = process_demo_document(uploaded)
            return jsonify({
                "transcribed_text": transcribed_text,
                "summary_text": summary_text,
            })
        if not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        try:
            transcribed_text, summary_text = extract_and_summarize(uploaded)
        except Exception:
            app.logger.exception("No se pudo procesar el documento con Gemini")
            return jsonify({"error": "No se pudo procesar el documento con Gemini."}), 502

        return jsonify({
            "transcribed_text": transcribed_text,
            "summary_text": summary_text,
        })

    @app.route("/api/material/tts", methods=["POST"])
    @login_required
    def material_tts():
        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return jsonify({"error": "No hay texto para convertir a audio."}), 400
        if len(text) > MAX_TTS_TEXT_CHARS:
            return jsonify({"error": "El texto es demasiado largo para generar audio."}), 413
        if not fish_client and app.config.get("DEMO_MODE"):
            audio_bytes, duration_seconds = create_demo_wav(text)
            response = Response(audio_bytes, mimetype="audio/wav")
            response.headers["X-MAXCIM-Audio-Duration-Seconds"] = f"{duration_seconds:.2f}"
            response.headers["X-MAXCIM-Demo-Audio"] = "true"
            return response
        if not fish_client:
            return jsonify({"error": "FISH_API_KEY no está configurada en el servidor."}), 503

        try:
            audio_bytes, duration_seconds = generate_speech(text)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("No se pudo generar el audio con Fish Audio")
            return jsonify({"error": "No se pudo generar el audio con Fish Audio."}), 502

        response = Response(audio_bytes, mimetype="audio/wav")
        response.headers["X-MAXCIM-Audio-Duration-Seconds"] = f"{duration_seconds:.2f}"
        return response

    @app.route("/api/material/questions", methods=["POST"])
    @login_required
    def material_questions():
        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        counts_payload = payload.get("counts") or {}

        if not text:
            return jsonify({"error": "No hay texto para generar preguntas."}), 400
        if len(text) > MAX_SOURCE_TEXT_CHARS:
            return jsonify({"error": "El texto es demasiado largo para generar preguntas."}), 413

        counts = {}
        for qtype in QUESTION_TYPES:
            try:
                count = int(counts_payload.get(qtype, 0))
            except (TypeError, ValueError):
                count = 0
            counts[qtype] = max(0, min(count, MAX_QUESTIONS_PER_TYPE))

        if not any(counts.values()):
            return jsonify({"error": "Indica al menos una pregunta para generar."}), 400
        if not gemini_client and app.config.get("DEMO_MODE"):
            return jsonify({"questions": create_demo_questions(text, counts)})
        if not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        try:
            questions = generate_questions(text, counts)
        except Exception:
            app.logger.exception("No se pudieron generar las preguntas con Gemini")
            return jsonify({"error": "No se pudieron generar las preguntas con Gemini."}), 502

        return jsonify({"questions": questions})

    @app.route("/api/story/generate", methods=["POST"])
    @login_required
    def story_generate():
        payload = request.get_json(silent=True) or {}
        character = str(payload.get("character") or "").strip()
        setting = str(payload.get("setting") or "").strip()
        grade_level = str(payload.get("grade_level") or "").strip()
        objective = str(payload.get("objective") or "").strip()
        extra_details = str(payload.get("extra_details") or "").strip()
        duration_value = payload.get("duration_minutes")

        missing = [
            label
            for label, value in (
                ("personaje principal", character),
                ("lugar de la historia", setting),
                ("nivel del aula", grade_level),
                ("objetivo pedagógico", objective),
            )
            if not value
        ]
        if duration_value in (None, ""):
            missing.append("duración del cuento")
        if missing:
            return jsonify({"error": f"Falta indicar: {', '.join(missing)}."}), 400
        try:
            duration_minutes = _parse_duration_minutes(duration_value)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        field_limits = {
            "personaje principal": (character, 120),
            "lugar de la historia": (setting, 160),
            "nivel del aula": (grade_level, 100),
            "objetivo pedagógico": (objective, 240),
            "detalles adicionales": (extra_details, 1_000),
        }
        too_long = [label for label, (value, limit) in field_limits.items() if len(value) > limit]
        if too_long:
            return jsonify({"error": f"Excede el límite permitido: {', '.join(too_long)}."}), 413
        if not gemini_client and app.config.get("DEMO_MODE"):
            return jsonify(create_demo_story(
                character=character,
                setting=setting,
                grade_level=grade_level,
                objective=objective,
                extra_details=extra_details,
                duration_minutes=duration_minutes,
                words_per_minute=STORY_NARRATION_WORDS_PER_MINUTE,
            ))
        if not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        try:
            story = generate_story(
                character=character,
                setting=setting,
                grade_level=grade_level,
                objective=objective,
                extra_details=extra_details,
                duration_minutes=duration_minutes,
            )
        except Exception:
            app.logger.exception("No se pudo crear el cuento con Gemini")
            return jsonify({"error": "No se pudo crear el cuento con Gemini."}), 502

        return jsonify(story)

    @app.route("/api/sentences/generate", methods=["POST"])
    @login_required
    def sentences_generate():
        payload = request.get_json(silent=True) or {}
        topic = str(payload.get("topic") or "").strip()
        grade_level = str(payload.get("grade_level") or "").strip()
        extra_details = str(payload.get("extra_details") or "").strip()
        raw_existing = payload.get("existing")
        existing = normalize_sentences(raw_existing if isinstance(raw_existing, list) else [])

        # El tema es obligatorio salvo que ya haya oraciones de referencia
        # (botón "generar 5 más" dentro de la revisión): en ese caso la IA
        # infiere el tema a partir de ellas.
        if not topic and not existing:
            return jsonify({"error": "Falta indicar: tema."}), 400
        try:
            count = int(payload.get("count"))
        except (TypeError, ValueError):
            count = 0
        if not 1 <= count <= MAX_SENTENCES_PER_REQUEST:
            return jsonify({
                "error": f"La cantidad debe estar entre 1 y {MAX_SENTENCES_PER_REQUEST} oraciones."
            }), 400
        field_limits = {"tema": (topic, 160), "nivel del aula": (grade_level, 100),
                        "detalles adicionales": (extra_details, 1_000)}
        too_long = [label for label, (value, limit) in field_limits.items() if len(value) > limit]
        if too_long:
            return jsonify({"error": f"Excede el límite permitido: {', '.join(too_long)}."}), 413

        title = f"Oraciones: {topic}"[:120] if topic else "Oraciones generadas con IA"
        if not gemini_client and app.config.get("DEMO_MODE"):
            sentences = create_demo_sentences(topic or "las oraciones de práctica", grade_level, count)
        elif not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
        else:
            try:
                sentences = generate_sentences(topic, grade_level, count, extra_details, existing)
            except Exception:
                app.logger.exception("No se pudieron generar las oraciones con Gemini")
                return jsonify({"error": "No se pudieron generar las oraciones con Gemini."}), 502

        seen = {sentence.casefold() for sentence in existing}
        sentences = [s for s in sentences if s.casefold() not in seen]
        if not sentences:
            return jsonify({"error": "La IA no devolvió oraciones nuevas."}), 502

        return jsonify({"title": title, "sentences": sentences})

    @app.route("/api/sentences/generate-images", methods=["POST"])
    @login_required
    def image_sentences_generate():
        """Borrador de oraciones para "oraciones con imágenes": cada oración con
        dos sustantivos concretos. Solo genera y devuelve las oraciones para que
        la docente las revise; la generación de imágenes es un paso posterior
        que todavía no existe."""
        payload = request.get_json(silent=True) or {}
        topic = str(payload.get("topic") or "").strip()
        grade_level = str(payload.get("grade_level") or "").strip()
        extra_details = str(payload.get("extra_details") or "").strip()

        if not topic:
            return jsonify({"error": "Falta indicar: tema."}), 400
        try:
            count = int(payload.get("count"))
        except (TypeError, ValueError):
            count = 0
        if not 1 <= count <= MAX_IMAGE_SENTENCES_PER_REQUEST:
            return jsonify({
                "error": f"La cantidad debe estar entre 1 y {MAX_IMAGE_SENTENCES_PER_REQUEST} oraciones."
            }), 400
        field_limits = {"tema": (topic, 160), "nivel del aula": (grade_level, 100),
                        "detalles adicionales": (extra_details, 1_000)}
        too_long = [label for label, (value, limit) in field_limits.items() if len(value) > limit]
        if too_long:
            return jsonify({"error": f"Excede el límite permitido: {', '.join(too_long)}."}), 413

        title = f"Oraciones con imágenes: {topic}"[:120]
        if not gemini_client and app.config.get("DEMO_MODE"):
            items = create_demo_image_sentences(topic, grade_level, count)
        elif not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
        else:
            try:
                items = generate_image_sentences(topic, grade_level, count, extra_details)
            except Exception:
                app.logger.exception("No se pudieron generar las oraciones con Gemini")
                return jsonify({"error": "No se pudieron generar las oraciones con Gemini."}), 502

        if not items:
            return jsonify({"error": "La IA no devolvió oraciones con dos sustantivos."}), 502

        return jsonify({"title": title, "items": items})

    @app.route("/api/bits/generate", methods=["POST"])
    @login_required
    def bits_generate():
        """Borrador de palabras para "bits": cada palabra empieza con alguna
        de las sílabas elegidas. Cuerpo JSON: {silabas: "ma, me, mi", count,
        syllable_count?, extra_details?}. El público (BITS_TARGET_LEVEL) es
        fijo; syllable_count es texto libre con la cantidad de sílabas de
        cada palabra (va directo al prompt; por defecto "2 sílabas"). Solo
        genera y devuelve
        las palabras para que la docente las revise; la generación de
        imágenes es el paso de diseño (/api/bits/prepare)."""
        payload = request.get_json(silent=True) or {}
        extra_details = str(payload.get("extra_details") or "").strip()

        raw_syllables = payload.get("silabas")
        silabas = normalize_bits_syllables(raw_syllables) if isinstance(
            raw_syllables, (str, list)
        ) else []
        if not silabas:
            return jsonify({"error": "Falta indicar: al menos una sílaba a trabajar."}), 400
        try:
            count = int(payload.get("count"))
        except (TypeError, ValueError):
            count = 0
        if not 1 <= count <= MAX_BITS_PER_REQUEST:
            return jsonify({
                "error": f"La cantidad debe estar entre 1 y {MAX_BITS_PER_REQUEST} palabras."
            }), 400
        syllable_count = " ".join(str(payload.get("syllable_count") or "").split())
        if len(syllable_count) > MAX_BITS_SYLLABLE_COUNT_CHARS:
            return jsonify({
                "error": "Excede el límite permitido: cantidad de sílabas por palabra."
            }), 413
        syllable_count = syllable_count or BITS_DEFAULT_SYLLABLE_COUNT
        if len(extra_details) > 1_000:
            return jsonify({"error": "Excede el límite permitido: detalles adicionales."}), 413

        title = f"Bits: sílabas {', '.join(silabas)}"
        if not gemini_client and app.config.get("DEMO_MODE"):
            items = create_demo_bits_words(silabas, count)
        elif not gemini_client:
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503
        else:
            try:
                items = generate_bits_words(
                    silabas, count, extra_details, syllable_count
                )
            except Exception:
                app.logger.exception("No se pudieron generar las palabras con Gemini")
                return jsonify({"error": "No se pudieron generar las palabras con Gemini."}), 502

        if not items:
            return jsonify({"error": "La IA no devolvió palabras."}), 502

        return jsonify({"title": title, "items": items})

    # --- "Oraciones con imágenes": diseño (generar y aprobar las imágenes) ----
    # Las imágenes se generan ANTES de guardar el material y viven en un
    # directorio temporal (`_previews/<token>/`). La docente las revisa una a
    # una, puede regenerar cualquiera, y al aprobar el diseño /api/material/save
    # las mueve al material definitivo. Un token abandonado se borra solo por
    # antigüedad (PREVIEW_STAGING_MAX_AGE_SECONDS).

    def _previews_root() -> str:
        return os.path.join(app.config["UPLOADS_ROOT"], PREVIEW_STAGING_DIRNAME)

    def _staging_dir(token: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
            raise ValueError("Token de previsualización inválido.")
        return os.path.join(_previews_root(), token)

    def _sweep_stale_previews() -> None:
        cutoff = time.time() - PREVIEW_STAGING_MAX_AGE_SECONDS
        try:
            names = os.listdir(_previews_root())
        except OSError:
            return
        for name in names:
            path = os.path.join(_previews_root(), name)
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass

    def _staging_image_name(sentence_index: int, noun_index: int) -> str:
        return f"{sentence_index}-{noun_index}.png"

    def _make_noun_image(palabra: str, oracion: str) -> bytes:
        if not gemini_client and app.config.get("DEMO_MODE"):
            return create_demo_noun_image(palabra, oracion)
        return generate_noun_image(palabra, oracion=oracion)

    def _image_sentences_ready() -> bool:
        return bool(gemini_client) or bool(app.config.get("DEMO_MODE"))

    def _edit_staged_image(staging: str, image_name: str, raw_instruction):
        """Aplica la indicación de la docente a una imagen en revisión y la
        sobrescribe. Compartido por /image-sentences/edit y /bits/edit.
        Devuelve None si todo salió bien, o la respuesta de error ya armada;
        ante cualquier fallo la imagen original queda intacta."""
        instruction = " ".join(str(raw_instruction or "").split())
        if not instruction:
            return jsonify({"error": "Escribe qué quieres cambiar de la imagen."}), 400
        if len(instruction) > NOUN_IMAGE_EDIT_MAX_CHARS:
            return jsonify({
                "error": f"El cambio no puede superar {NOUN_IMAGE_EDIT_MAX_CHARS} caracteres."
            }), 413
        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        image_path = os.path.join(staging, image_name)
        try:
            with open(image_path, "rb") as f:
                current = f.read()
        except OSError:
            return jsonify({"error": "Esta imagen todavía no existe para editar."}), 409

        try:
            if not gemini_client and app.config.get("DEMO_MODE"):
                edited = create_demo_noun_image(instruction)
            else:
                edited = edit_noun_image(current, instruction)
            with open(image_path, "wb") as f:
                f.write(edited)
        except Exception:
            app.logger.exception("No se pudo editar la imagen %s", image_name)
            return jsonify({"error": "No se pudo editar la imagen. Inténtalo de nuevo."}), 502
        return None

    def _noun_source(oracion: dict, noun_index: int) -> str:
        """"ia" o "manual", según si la imagen actual de ese sustantivo vino
        de Gemini o de un archivo que subió la docente. Solo es informativo
        para la consola (p.ej. una insignia en el diseño); el material
        guardado no distingue el origen de cada imagen."""
        fuentes = oracion.get("fuentes") or []
        return fuentes[noun_index] if noun_index < len(fuentes) else "ia"

    def _staged_preview_payload(token: str, meta: dict) -> list[dict]:
        payload = []
        for index, oracion in enumerate(meta.get("oraciones", [])):
            palabras = oracion.get("sustantivos", [])
            payload.append({
                "index": index,
                "texto": oracion.get("texto", ""),
                "plantilla": oracion.get("plantilla", ""),
                "sustantivos": [
                    {
                        "palabra": palabra,
                        "fuente": _noun_source(oracion, noun_index),
                        "imagen_url": url_for(
                            "image_sentence_preview_image",
                            token=token,
                            s=index,
                            n=noun_index,
                        ),
                    }
                    for noun_index, palabra in enumerate(palabras)
                ],
            })
        return payload

    def _apply_noun_word_change(
        meta: dict, staging: str, oracion: dict, noun_index: int, nueva_palabra: str
    ) -> None:
        """Si `nueva_palabra` viene y difiere de la actual, la aplica y
        recalcula la plantilla de la oración; persiste meta.json. Compartido
        por /regenerate y /upload-image: ambas pueden cambiar la palabra a
        la vez que reemplazan su imagen. Lanza ValueError (mensaje apto para
        mostrar) si la nueva palabra no aparece tal cual en la oración."""
        if not nueva_palabra or nueva_palabra == oracion["sustantivos"][noun_index]:
            return
        candidato = list(oracion["sustantivos"])
        candidato[noun_index] = nueva_palabra
        oracion["plantilla"] = image_sentence_template(oracion["texto"], candidato)
        oracion["sustantivos"] = candidato
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    @app.route("/api/material/image-sentences/prepare", methods=["POST"])
    @login_required
    def image_sentences_prepare():
        """Genera las imágenes de cada sustantivo y las deja en revisión.
        Cuerpo JSON: {title, items:[{texto, sustantivos:[a, b]}]}."""
        payload = request.get_json(silent=True) or {}
        raw_items = payload.get("items")
        items = normalize_image_sentences(
            raw_items if isinstance(raw_items, list) else [],
            limit=MAX_IMAGE_DESIGN_SENTENCES,
        )
        if not items:
            return jsonify({
                "error": "Cada oración debe tener su texto y exactamente dos sustantivos."
            }), 400

        oraciones = []
        for position, item in enumerate(items, start=1):
            try:
                plantilla = image_sentence_template(item["texto"], item["sustantivos"])
            except ValueError as exc:
                return jsonify({"error": f"Oración {position}: {exc}"}), 400
            oraciones.append({
                "texto": item["texto"],
                "plantilla": plantilla,
                "sustantivos": list(item["sustantivos"]),
                "fuentes": ["ia", "ia"],
            })

        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        _sweep_stale_previews()
        token = uuid.uuid4().hex
        staging = _staging_dir(token)
        os.makedirs(staging, exist_ok=True)
        try:
            for sentence_index, oracion in enumerate(oraciones):
                for noun_index, palabra in enumerate(oracion["sustantivos"]):
                    data = _make_noun_image(palabra, oracion["texto"])
                    with open(
                        os.path.join(staging, _staging_image_name(sentence_index, noun_index)),
                        "wb",
                    ) as f:
                        f.write(data)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            app.logger.exception("No se pudieron generar las imágenes de las oraciones")
            return jsonify({"error": "No se pudieron generar las imágenes con Gemini."}), 502

        meta = {
            "created_at": utc_now().isoformat(),
            "title": (payload.get("title") or "").strip(),
            "oraciones": oraciones,
        }
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({"token": token, "items": _staged_preview_payload(token, meta)})

    @app.route("/api/material/image-sentences/regenerate", methods=["POST"])
    @login_required
    def image_sentences_regenerate():
        """Regenera una sola imagen del diseño en revisión. Cuerpo JSON:
        {token, sentence_index, noun_index, palabra?}. Si `palabra` cambia, se
        recalcula la plantilla de esa oración."""
        payload = request.get_json(silent=True) or {}
        try:
            staging = _staging_dir(payload.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        oraciones = meta.get("oraciones", [])
        try:
            sentence_index = int(payload.get("sentence_index"))
            noun_index = int(payload.get("noun_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "Índice de oración o sustantivo inválido."}), 400
        if not (0 <= sentence_index < len(oraciones)) or noun_index not in (0, 1):
            return jsonify({"error": "Índice de oración o sustantivo inválido."}), 400

        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        oracion = oraciones[sentence_index]
        nueva_palabra = " ".join(str(payload.get("palabra") or "").split())
        try:
            _apply_noun_word_change(meta, staging, oracion, noun_index, nueva_palabra)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        palabra = oracion["sustantivos"][noun_index]
        try:
            data = _make_noun_image(palabra, oracion["texto"])
        except Exception:
            app.logger.exception("No se pudo regenerar la imagen del sustantivo")
            return jsonify({"error": "No se pudo regenerar la imagen con Gemini."}), 502
        with open(
            os.path.join(staging, _staging_image_name(sentence_index, noun_index)), "wb"
        ) as f:
            f.write(data)
        oracion.setdefault("fuentes", ["ia", "ia"])[noun_index] = "ia"
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({
            "palabra": palabra,
            "plantilla": oracion["plantilla"],
            "fuente": "ia",
            "imagen_url": url_for(
                "image_sentence_preview_image",
                token=payload.get("token"),
                s=sentence_index,
                n=noun_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route("/api/material/image-sentences/edit", methods=["POST"])
    @login_required
    def image_sentences_edit():
        """Ajusta la imagen ya generada de un sustantivo según la indicación de
        la docente, sin regenerarla desde cero. Cuerpo JSON:
        {token, sentence_index, noun_index, instruction}."""
        payload = request.get_json(silent=True) or {}
        try:
            staging = _staging_dir(payload.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        oraciones = meta.get("oraciones", [])
        sentence_index = payload.get("sentence_index")
        noun_index = payload.get("noun_index")
        if (
            isinstance(sentence_index, bool)
            or not isinstance(sentence_index, int)
            or not 0 <= sentence_index < len(oraciones)
            or isinstance(noun_index, bool)
            or noun_index not in (0, 1)
        ):
            return jsonify({"error": "Índice de oración o sustantivo inválido."}), 400

        error = _edit_staged_image(
            staging,
            _staging_image_name(sentence_index, noun_index),
            payload.get("instruction"),
        )
        if error:
            return error

        return jsonify({
            "imagen_url": url_for(
                "image_sentence_preview_image",
                token=payload.get("token"),
                s=sentence_index,
                n=noun_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route("/api/material/image-sentences/upload-image", methods=["POST"])
    @login_required
    def image_sentences_upload_image():
        """Reemplaza, con una imagen que sube la docente, la de un sustantivo
        del diseño en revisión -alternativa a "regenerar con IA"
        (/image-sentences/regenerate). Cuerpo multipart/form-data: token,
        sentence_index, noun_index, palabra? (igual que regenerate) y el
        archivo en el campo `imagen`. El nombre original del archivo no
        importa -se descarta-: la imagen final, al aprobar el diseño, se
        nombra según el sustantivo (ver _unique_noun_filename en /save)."""
        try:
            staging = _staging_dir(request.form.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        oraciones = meta.get("oraciones", [])
        try:
            sentence_index = int(request.form.get("sentence_index"))
            noun_index = int(request.form.get("noun_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "Índice de oración o sustantivo inválido."}), 400
        if not (0 <= sentence_index < len(oraciones)) or noun_index not in (0, 1):
            return jsonify({"error": "Índice de oración o sustantivo inválido."}), 400

        uploaded = request.files.get("imagen")
        if not uploaded or not (uploaded.filename or "").strip():
            return jsonify({"error": "Selecciona un archivo de imagen."}), 400
        try:
            data = normalize_uploaded_noun_image(uploaded)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        oracion = oraciones[sentence_index]
        nueva_palabra = " ".join(str(request.form.get("palabra") or "").split())
        try:
            _apply_noun_word_change(meta, staging, oracion, noun_index, nueva_palabra)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        with open(
            os.path.join(staging, _staging_image_name(sentence_index, noun_index)), "wb"
        ) as f:
            f.write(data)
        oracion.setdefault("fuentes", ["ia", "ia"])[noun_index] = "manual"
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({
            "palabra": oracion["sustantivos"][noun_index],
            "plantilla": oracion["plantilla"],
            "fuente": "manual",
            "imagen_url": url_for(
                "image_sentence_preview_image",
                token=request.form.get("token"),
                s=sentence_index,
                n=noun_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route(
        "/api/material/image-sentences/preview/<token>/<int:s>/<int:n>", methods=["GET"]
    )
    @login_required
    def image_sentence_preview_image(token, s, n):
        try:
            staging = _staging_dir(token)
        except ValueError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        path = os.path.join(staging, _staging_image_name(s, n))
        try:
            # Se lee a memoria (en vez de send_file) para no dejar el archivo
            # abierto: /api/material/save borra este directorio justo después
            # de aprobar el diseño y un handle vivo lo impediría en Windows.
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        response = Response(data, mimetype="image/png")
        response.headers["Cache-Control"] = "no-store"
        return response

    # --- "Bits": diseño (generar y aprobar las imágenes) -----------------------
    # Mismo esquema que "oraciones con imágenes" arriba (staging por token bajo
    # `_previews/<token>/`), pero una sola imagen por palabra (no hay sentido
    # ni sustantivos que enumerar): _staging_dir/_sweep_stale_previews/
    # _make_noun_image/_image_sentences_ready se reutilizan tal cual.

    def _staging_bit_image_name(item_index: int) -> str:
        return f"{item_index}.png"

    def _apply_bit_word_change(meta: dict, staging: str, item: dict, nueva_palabra: str) -> None:
        """Si `nueva_palabra` viene y difiere de la actual, la aplica y
        persiste meta.json. Compartido por /regenerate y /upload-image.
        Más simple que _apply_noun_word_change: no hay plantilla que
        recalcular ni "la palabra no aparece en la oración" que validar."""
        if not nueva_palabra or nueva_palabra == item["palabra"]:
            return
        item["palabra"] = nueva_palabra
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    def _suggest_bit_questions(cards: list[tuple[str, bytes]]) -> list[str]:
        if not gemini_client and app.config.get("DEMO_MODE"):
            return [create_demo_bit_question(palabra) for palabra, _image in cards]
        return suggest_bits_questions(cards)

    def _staged_bits_preview_payload(token: str, meta: dict) -> list[dict]:
        return [
            {
                "index": index,
                "palabra": item.get("palabra", ""),
                "pregunta": item.get("pregunta", ""),
                "fuente": item.get("fuente", "ia"),
                "imagen_url": url_for("bit_preview_image", token=token, i=index),
            }
            for index, item in enumerate(meta.get("items", []))
        ]

    @app.route("/api/bits/prepare", methods=["POST"])
    @login_required
    def bits_prepare():
        """Genera una imagen por palabra y las deja en revisión. Cuerpo JSON:
        {title, items:[{palabra}]}."""
        payload = request.get_json(silent=True) or {}
        raw_items = payload.get("items")
        items = normalize_bits_words(
            raw_items if isinstance(raw_items, list) else [],
            limit=MAX_BITS_DESIGN_ITEMS,
        )
        if not items:
            return jsonify({"error": "Cada bit debe tener su palabra."}), 400

        bits = [{"palabra": item["palabra"], "pregunta": "", "fuente": "ia"} for item in items]

        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        _sweep_stale_previews()
        token = uuid.uuid4().hex
        staging = _staging_dir(token)
        os.makedirs(staging, exist_ok=True)
        images: list[bytes] = []
        try:
            for item_index, item in enumerate(bits):
                data = _make_noun_image(item["palabra"], "")
                images.append(data)
                with open(
                    os.path.join(staging, _staging_bit_image_name(item_index)), "wb"
                ) as f:
                    f.write(data)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            app.logger.exception("No se pudieron generar las imágenes de los bits")
            return jsonify({"error": "No se pudieron generar las imágenes con Gemini."}), 502

        # Con las imágenes ya hechas, la IA propone la pregunta del robot para
        # cada una (una sola consulta para todas). Si falla no se pierde el
        # diseño: las preguntas quedan vacías y la docente las escribe o pide
        # una sugerencia por bit.
        try:
            questions = _suggest_bit_questions(
                [(item["palabra"], image) for item, image in zip(bits, images)]
            )
        except Exception:
            app.logger.exception("No se pudieron sugerir las preguntas de los bits")
            questions = []
        for item, question in zip(bits, questions):
            item["pregunta"] = question

        meta = {
            "created_at": utc_now().isoformat(),
            "title": (payload.get("title") or "").strip(),
            "items": bits,
        }
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({"token": token, "items": _staged_bits_preview_payload(token, meta)})

    @app.route("/api/bits/regenerate", methods=["POST"])
    @login_required
    def bits_regenerate():
        """Regenera una sola imagen del diseño en revisión. Cuerpo JSON:
        {token, item_index, palabra?}."""
        payload = request.get_json(silent=True) or {}
        try:
            staging = _staging_dir(payload.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        items = meta.get("items", [])
        try:
            item_index = int(payload.get("item_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "Índice de bit inválido."}), 400
        if not (0 <= item_index < len(items)):
            return jsonify({"error": "Índice de bit inválido."}), 400

        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        item = items[item_index]
        nueva_palabra = " ".join(str(payload.get("palabra") or "").split())
        _apply_bit_word_change(meta, staging, item, nueva_palabra)

        palabra = item["palabra"]
        try:
            data = _make_noun_image(palabra, "")
        except Exception:
            app.logger.exception("No se pudo regenerar la imagen del bit")
            return jsonify({"error": "No se pudo regenerar la imagen con Gemini."}), 502
        with open(
            os.path.join(staging, _staging_bit_image_name(item_index)), "wb"
        ) as f:
            f.write(data)
        item["fuente"] = "ia"
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({
            "palabra": palabra,
            "fuente": "ia",
            "imagen_url": url_for(
                "bit_preview_image", token=payload.get("token"), i=item_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route("/api/bits/suggest-question", methods=["POST"])
    @login_required
    def bits_suggest_question():
        """Sugiere de nuevo la pregunta del robot para un bit del diseño en
        revisión, mirando su imagen actual (por si se regeneró, se editó o se
        subió otra). Cuerpo JSON: {token, item_index, palabra?}; `palabra` es lo
        que la docente tiene escrito ahora, aunque aún no lo haya aplicado."""
        payload = request.get_json(silent=True) or {}
        try:
            staging = _staging_dir(payload.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        items = meta.get("items", [])
        item_index = payload.get("item_index")
        if (
            isinstance(item_index, bool)
            or not isinstance(item_index, int)
            or not 0 <= item_index < len(items)
        ):
            return jsonify({"error": "Índice de bit inválido."}), 400

        palabra = " ".join(str(payload.get("palabra") or "").split())[:MAX_BIT_WORD_CHARS]
        palabra = palabra or items[item_index].get("palabra", "")
        if not palabra:
            return jsonify({"error": "Escribe la palabra antes de sugerir la pregunta."}), 400
        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        try:
            with open(os.path.join(staging, _staging_bit_image_name(item_index)), "rb") as f:
                image = f.read()
        except OSError:
            return jsonify({"error": "Este bit todavía no tiene imagen."}), 409

        try:
            question = _suggest_bit_questions([(palabra, image)])[0]
        except Exception:
            app.logger.exception("No se pudo sugerir la pregunta del bit")
            return jsonify({"error": "No se pudo sugerir la pregunta. Inténtalo de nuevo."}), 502
        if not question:
            return jsonify({
                "error": "La IA no propuso una pregunta válida. Inténtalo de nuevo o escríbela tú."
            }), 502
        return jsonify({"pregunta": question})

    @app.route("/api/bits/edit", methods=["POST"])
    @login_required
    def bits_edit():
        """Ajusta la imagen ya generada de un bit según la indicación de la
        docente, sin regenerarla desde cero. Cuerpo JSON:
        {token, item_index, instruction}."""
        payload = request.get_json(silent=True) or {}
        try:
            staging = _staging_dir(payload.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        item_index = payload.get("item_index")
        if (
            isinstance(item_index, bool)
            or not isinstance(item_index, int)
            or not 0 <= item_index < len(meta.get("items", []))
        ):
            return jsonify({"error": "Índice de bit inválido."}), 400

        error = _edit_staged_image(
            staging, _staging_bit_image_name(item_index), payload.get("instruction")
        )
        if error:
            return error

        return jsonify({
            "imagen_url": url_for(
                "bit_preview_image", token=payload.get("token"), i=item_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route("/api/bits/upload-image", methods=["POST"])
    @login_required
    def bits_upload_image():
        """Reemplaza, con una imagen que sube la docente, la de un bit del
        diseño en revisión. Cuerpo multipart/form-data: token, item_index,
        palabra? (igual que regenerate) y el archivo en el campo `imagen`."""
        try:
            staging = _staging_dir(request.form.get("token"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return jsonify({
                "error": "La previsualización expiró. Vuelve a generar el diseño."
            }), 404

        items = meta.get("items", [])
        try:
            item_index = int(request.form.get("item_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "Índice de bit inválido."}), 400
        if not (0 <= item_index < len(items)):
            return jsonify({"error": "Índice de bit inválido."}), 400

        uploaded = request.files.get("imagen")
        if not uploaded or not (uploaded.filename or "").strip():
            return jsonify({"error": "Selecciona un archivo de imagen."}), 400
        try:
            data = normalize_uploaded_noun_image(uploaded)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        item = items[item_index]
        nueva_palabra = " ".join(str(request.form.get("palabra") or "").split())
        _apply_bit_word_change(meta, staging, item, nueva_palabra)

        with open(
            os.path.join(staging, _staging_bit_image_name(item_index)), "wb"
        ) as f:
            f.write(data)
        item["fuente"] = "manual"
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({
            "palabra": item["palabra"],
            "fuente": "manual",
            "imagen_url": url_for(
                "bit_preview_image", token=request.form.get("token"), i=item_index,
            ) + f"?v={secrets.token_hex(4)}",
        })

    @app.route("/api/bits/preview/<token>/<int:i>", methods=["GET"])
    @login_required
    def bit_preview_image(token, i):
        try:
            staging = _staging_dir(token)
        except ValueError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        path = os.path.join(staging, _staging_bit_image_name(i))
        try:
            # Se lee a memoria (en vez de send_file) por la misma razón que
            # image_sentence_preview_image: /api/material/save borra este
            # directorio justo después de aprobar el diseño.
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        response = Response(data, mimetype="image/png")
        response.headers["Cache-Control"] = "no-store"
        return response

    # --- Ilustraciones por escena de un cuento generado con IA ----------------
    # Mismo esquema que el diseño de imágenes: todo vive en `_previews/<token>/`
    # (meta.json + escena-<i>.png) hasta que se limpie por antigüedad. El plan
    # (cuántas escenas y qué se ve en cada una) se pide una vez; cada imagen se
    # genera con su propia petición para que la consola muestre el avance.

    def _scene_image_name(index: int) -> str:
        return f"escena-{index}.png"

    def _scenes_meta(token: str):
        """Devuelve (staging, meta) del plan de escenas, o lanza ValueError con
        un mensaje apto para mostrar."""
        try:
            staging = _staging_dir(token)
        except ValueError:
            raise ValueError("Las escenas expiraron. Vuelve a generarlas.")
        try:
            with open(os.path.join(staging, "meta.json"), "rb") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            raise ValueError("Las escenas expiraron. Vuelve a generarlas.")
        if not isinstance(meta.get("escenas"), list):
            raise ValueError("Las escenas expiraron. Vuelve a generarlas.")
        return staging, meta

    def _scene_audio_name(index: int) -> str:
        return f"escena-{index}.wav"

    def _scene_preview_url(token: str, index: int) -> str:
        return url_for("story_scene_preview_image", token=token, i=index) + (
            f"?v={secrets.token_hex(4)}"
        )

    def _scene_audio_preview_url(token: str, index: int) -> str:
        return url_for("story_scene_preview_audio", token=token, i=index) + (
            f"?v={secrets.token_hex(4)}"
        )

    def _staged_scenes_for_save(token: str, story_text: str) -> list[dict[str, object]]:
        """Escenas de la carpeta de previsualización listas para guardarse con
        el cuento: [{texto, imagen, audio, duracion_s}] con las rutas de origen.
        Lanza ValueError (mensaje apto para mostrar) si expiraron, si el texto
        del cuento cambió desde que se crearon o si a alguna le falta su imagen
        o su audio: el robot necesita las dos cosas de cada escena."""
        staging, meta = _scenes_meta(token)
        scenes = meta["escenas"]
        texts = [str(scene.get("texto") or "") for scene in scenes]
        if normalize_story_text(" ".join(texts)) != normalize_story_text(story_text):
            raise ValueError(
                "El texto del cuento cambió después de crear las escenas. Vuelve a generarlas."
            )
        staged = []
        for index, texto in enumerate(texts):
            image = os.path.join(staging, _scene_image_name(index))
            audio = os.path.join(staging, _scene_audio_name(index))
            if not os.path.isfile(image):
                raise ValueError(f"Falta la imagen de la escena {index + 1}.")
            if not os.path.isfile(audio):
                raise ValueError(f"Falta el audio de la escena {index + 1}.")
            try:
                duration = _wav_duration_seconds(audio)
            except (EOFError, OSError, wave.Error):
                raise ValueError(f"El audio de la escena {index + 1} no es válido.")
            staged.append({
                "texto": texto, "imagen": image, "audio": audio, "duracion_s": duration,
            })
        return staged

    def _store_scenes(material_dir: str, staged: list[dict[str, object]]) -> None:
        """Copia las escenas al material: escenas/escena_<i>.{png|jpg|webp} y
        escenas/escena_<i>.wav, más escenas.json con el texto y la duración de
        cada una y qué imagen va con qué audio (sin columnas nuevas: la carpeta del
        material ya lo contiene)."""
        os.makedirs(os.path.join(material_dir, "escenas"), exist_ok=True)
        rows = []
        for index, scene in enumerate(staged):
            with open(scene["imagen"], "rb") as f:
                head = f.read(16)
            extension = {
                "image/jpeg": "jpg", "image/webp": "webp",
            }.get(_image_mime_type(head), "png")
            image_rel = f"escenas/escena_{index}.{extension}"
            audio_rel = f"escenas/escena_{index}.wav"
            shutil.copyfile(scene["imagen"], os.path.join(material_dir, image_rel))
            shutil.copyfile(scene["audio"], os.path.join(material_dir, audio_rel))
            rows.append({
                "indice": index,
                "texto": scene["texto"],
                "imagen": image_rel,
                "audio": audio_rel,
                "duracion_s": round(float(scene["duracion_s"]), 2),
            })
        with open(os.path.join(material_dir, "escenas.json"), "wb") as f:
            f.write(json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"))

    @app.route("/api/story/scenes/plan", methods=["POST"])
    @login_required
    def story_scenes_plan():
        """Cuerpo JSON: {story, tone?}. La IA decide cuántas escenas ilustrar;
        `tone` es una clave de STORY_SCENE_TONES o "auto" (por defecto), en
        cuyo caso la IA elige el tono según el cuento."""
        payload = request.get_json(silent=True) or {}
        story_text = str(payload.get("story") or "").strip()
        tone = str(payload.get("tone") or STORY_SCENE_TONE_AUTO).strip()
        if not story_text:
            return jsonify({"error": "Escribe o genera el cuento antes de crear las escenas."}), 400
        if len(story_text) > MAX_SOURCE_TEXT_CHARS:
            return jsonify({"error": "El cuento excede el límite permitido."}), 413
        if tone != STORY_SCENE_TONE_AUTO and tone not in STORY_SCENE_TONES:
            return jsonify({"error": "El tono de las imágenes no es válido."}), 400
        if len(split_story_sentences(story_text)) < STORY_SCENES_MIN:
            return jsonify({
                "error": f"El cuento es muy corto para dividirlo en escenas: necesita al menos {STORY_SCENES_MIN} oraciones."
            }), 400
        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        try:
            if not gemini_client and app.config.get("DEMO_MODE"):
                plan = create_demo_scene_plan(story_text)
                plan["tono"] = resolve_story_scene_tone(tone)
            else:
                plan = plan_story_scenes(story_text, tone)
        except Exception:
            app.logger.exception("No se pudo planear las escenas del cuento")
            return jsonify({"error": "No se pudieron planear las escenas con Gemini."}), 502

        _sweep_stale_previews()
        token = uuid.uuid4().hex
        staging = _staging_dir(token)
        os.makedirs(staging, exist_ok=True)
        meta = {"created_at": utc_now().isoformat(), **plan}
        with open(os.path.join(staging, "meta.json"), "wb") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

        return jsonify({
            "token": token,
            "tone": plan["tono"],
            "tone_label": STORY_SCENE_TONES[plan["tono"]]["label"],
            "scenes": [
                {"index": index, "texto": scene["texto"]}
                for index, scene in enumerate(plan["escenas"])
            ],
        })

    @app.route("/api/story/scenes/image", methods=["POST"])
    @login_required
    def story_scene_image():
        """Genera (o regenera) la imagen de una escena. Cuerpo JSON:
        {token, index}. Las escenas posteriores a la primera usan la primera
        imagen como referencia para conservar al personaje."""
        payload = request.get_json(silent=True) or {}
        try:
            staging, meta = _scenes_meta(str(payload.get("token") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        scenes = meta["escenas"]
        index = payload.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(scenes):
            return jsonify({"error": "Escena inválida."}), 400

        reference = None
        if index != 0:
            try:
                with open(os.path.join(staging, _scene_image_name(0)), "rb") as f:
                    reference = f.read()
            except OSError:
                reference = None

        scene = scenes[index]
        try:
            if not gemini_client and app.config.get("DEMO_MODE"):
                data = create_demo_noun_image(scene["descripcion"])
            else:
                data = generate_story_scene_image(
                    scene["descripcion"],
                    str(meta.get("personaje") or ""),
                    str(meta.get("tono") or ""),
                    reference,
                )
            with open(os.path.join(staging, _scene_image_name(index)), "wb") as f:
                f.write(data)
        except Exception:
            app.logger.exception("No se pudo generar la imagen de la escena %s", index)
            return jsonify({"error": "No se pudo generar la imagen de esta escena."}), 502

        return jsonify({"index": index, "imagen_url": _scene_preview_url(payload["token"], index)})

    @app.route("/api/story/scenes/edit", methods=["POST"])
    @login_required
    def story_scene_edit():
        """Edita la imagen ya generada de una escena. Cuerpo JSON:
        {token, index, instruction}. Sobrescribe la imagen de esa escena."""
        payload = request.get_json(silent=True) or {}
        try:
            staging, meta = _scenes_meta(str(payload.get("token") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        index = payload.get("index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(meta["escenas"])
        ):
            return jsonify({"error": "Escena inválida."}), 400
        instruction = " ".join(str(payload.get("instruction") or "").split())
        if not instruction:
            return jsonify({"error": "Escribe qué quieres cambiar de la imagen."}), 400
        if len(instruction) > STORY_SCENE_EDIT_MAX_CHARS:
            return jsonify({
                "error": f"El cambio no puede superar {STORY_SCENE_EDIT_MAX_CHARS} caracteres."
            }), 413
        if not _image_sentences_ready():
            return jsonify({"error": "GOOGLE_API_KEY no está configurada en el servidor."}), 503

        image_path = os.path.join(staging, _scene_image_name(index))
        try:
            with open(image_path, "rb") as f:
                current = f.read()
        except OSError:
            return jsonify({"error": "Esta escena todavía no tiene imagen para editar."}), 409

        try:
            if not gemini_client and app.config.get("DEMO_MODE"):
                edited = create_demo_noun_image(instruction)
            else:
                edited = edit_story_scene_image(current, instruction)
            with open(image_path, "wb") as f:
                f.write(edited)
        except Exception:
            app.logger.exception("No se pudo editar la imagen de la escena %s", index)
            return jsonify({"error": "No se pudo editar la imagen. Inténtalo de nuevo."}), 502

        return jsonify({"index": index, "imagen_url": _scene_preview_url(payload["token"], index)})

    @app.route("/api/story/scenes/preview/<token>/<int:i>", methods=["GET"])
    @login_required
    def story_scene_preview_image(token, i):
        try:
            staging = _staging_dir(token)
        except ValueError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        try:
            with open(os.path.join(staging, _scene_image_name(i)), "rb") as f:
                data = f.read()
        except OSError:
            return jsonify({"error": "Imagen no encontrada."}), 404
        response = Response(data, mimetype=_image_mime_type(data))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/story/scenes/audio", methods=["POST"])
    @login_required
    def story_scene_audio():
        """Narra (o vuelve a narrar) el texto de una escena. Cuerpo JSON:
        {token, index}. Cada escena tiene su propio audio para que el robot
        cambie de imagen al terminar de leerla, sin tiempos que sincronizar."""
        payload = request.get_json(silent=True) or {}
        try:
            staging, meta = _scenes_meta(str(payload.get("token") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        scenes = meta["escenas"]
        index = payload.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(scenes):
            return jsonify({"error": "Escena inválida."}), 400
        if not fish_client and not app.config.get("DEMO_MODE"):
            return jsonify({"error": "FISH_API_KEY no está configurada en el servidor."}), 503

        text = str(scenes[index].get("texto") or "")
        try:
            if not fish_client:
                audio_bytes, duration_seconds = create_demo_wav(text)
            else:
                narracion = text
                if FISH_NARRATION_TAGS_ENABLED and gemini_client:
                    narracion = annotate_narration_for_tts(text, str(meta.get("personaje") or ""))
                audio_bytes, duration_seconds = generate_speech(narracion)
            with open(os.path.join(staging, _scene_audio_name(index)), "wb") as f:
                f.write(audio_bytes)
        except Exception:
            app.logger.exception("No se pudo narrar la escena %s", index)
            return jsonify({"error": "No se pudo narrar esta escena."}), 502

        return jsonify({
            "index": index,
            "audio_url": _scene_audio_preview_url(payload["token"], index),
            "duration_seconds": duration_seconds,
        })

    @app.route("/api/story/scenes/audio-preview/<token>/<int:i>", methods=["GET"])
    @login_required
    def story_scene_preview_audio(token, i):
        try:
            staging = _staging_dir(token)
        except ValueError:
            return jsonify({"error": "Audio no encontrado."}), 404
        try:
            with open(os.path.join(staging, _scene_audio_name(i)), "rb") as f:
                data = f.read()
        except OSError:
            return jsonify({"error": "Audio no encontrado."}), 404
        response = Response(data, mimetype="audio/wav")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/material/save", methods=["POST"])
    @login_required
    def save_material():
        teacher = current_teacher()
        title = (request.form.get("title") or "").strip() or "Material sin título"
        material_type = (request.form.get("tipo_material") or TIPO_CUENTO).strip()
        if material_type not in TIPOS_MATERIAL:
            return jsonify({"error": "El tipo de material no es válido."}), 400
        # Todo material se clasifica en un periodo. El campo puede llegar
        # ausente (cliente antiguo o llamada externa) y entonces se usa el
        # periodo vigente hoy; vacío o sin periodo vigente es un error, no se
        # permite guardar material sin periodo.
        raw_periodo_id = request.form.get("id_periodo")
        if raw_periodo_id is None:
            active_periodo = current_periodo()
            if active_periodo is None:
                return jsonify({
                    "error": "No hay un periodo vigente; indica el periodo del material."
                }), 400
            material_periodo_id = active_periodo.id
        elif not raw_periodo_id.strip():
            return jsonify({"error": "Debes indicar el periodo del material."}), 400
        else:
            try:
                material_periodo_id = int(raw_periodo_id)
            except (TypeError, ValueError):
                return jsonify({"error": "El periodo indicado no es válido."}), 400
            # Limite de un INT firmado: fuera de rango, `db.session.get` puede
            # lanzar un error de la base de datos en vez de simplemente no
            # encontrar la fila (p.ej. overflow al enlazar el parámetro).
            if not (1 <= material_periodo_id <= 2_147_483_647):
                return jsonify({"error": "El periodo indicado no es válido."}), 400
            if db.session.get(Periodo, material_periodo_id) is None:
                return jsonify({"error": "El periodo indicado no es válido."}), 400

        # Todo material debe pertenecer a un tema (de su mismo periodo). No se
        # permite guardar material sin tema.
        raw_tema_id = request.form.get("id_tema")
        if raw_tema_id is None or not raw_tema_id.strip():
            return jsonify({"error": "Debes asignar un tema al material."}), 400
        else:
            try:
                material_tema_id = int(raw_tema_id)
            except (TypeError, ValueError):
                return jsonify({"error": "El tema indicado no es válido."}), 400
            if not (1 <= material_tema_id <= 2_147_483_647):
                return jsonify({"error": "El tema indicado no es válido."}), 400
            tema = db.session.get(Tema, material_tema_id)
            # Aislamiento por docente: un tema de otra docente no es "no
            # encontrado" desde su punto de vista, es simplemente inválido
            # para ella — mismo mensaje genérico que un id inexistente, para
            # no filtrar si el id pertenece a alguien más.
            if tema is None or tema.fk_user != str(teacher["id"]):
                return jsonify({"error": "El tema indicado no es válido."}), 400
            # Un tema pertenece a un periodo concreto: si el material queda
            # en un periodo distinto (o sin periodo), asignarle ese tema no
            # tendría sentido y rompería el filtro año/periodo/tema del
            # modal, así que se rechaza explícitamente en vez de guardarlo
            # inconsistente.
            if tema.id_periodo != material_periodo_id:
                return jsonify({
                    "error": "El tema indicado no pertenece al periodo seleccionado."
                }), 400

        if len(title) > 255:
            return jsonify({"error": "El título excede el límite permitido."}), 413

        if material_type == TIPO_ORACION:
            sentences_json_raw = (request.form.get("sentences_json") or "").strip()
            if sentences_json_raw:
                try:
                    parsed = json.loads(sentences_json_raw)
                except json.JSONDecodeError:
                    return jsonify({"error": "Las oraciones no tienen un formato JSON válido."}), 400
                if not isinstance(parsed, list):
                    return jsonify({"error": "Las oraciones no tienen un formato JSON válido."}), 400
                sentences = normalize_sentences(parsed)
            else:
                # Compatibilidad: un cliente antiguo aún puede enviar el texto crudo.
                sentences = split_text_into_sentences(request.form.get("sentences_text") or "")

            if not sentences:
                return jsonify({"error": "Escribe al menos una oración."}), 400
            if sum(len(sentence) for sentence in sentences) > MAX_SENTENCES_CHARS:
                return jsonify({"error": "Las oraciones exceden el límite permitido."}), 413

            material_dir_name = uuid.uuid4().hex
            material_dir = os.path.join(app.config["UPLOADS_ROOT"], material_dir_name)
            os.makedirs(material_dir, exist_ok=True)
            with open(os.path.join(material_dir, "oraciones.json"), "wb") as f:
                f.write(json.dumps(sentences, ensure_ascii=False, indent=2).encode("utf-8"))

            material = Material(
                nombre_material=title,
                tipo_material=TIPO_ORACION,
                path_preguntas=f"uploads/{material_dir_name}/oraciones.json",
                path_texto=None,
                path_texto_resumen=None,
                path_audio=None,
                fk_user=str(teacher["id"]),
                id_periodo=material_periodo_id,
                id_tema=material_tema_id,
                # Tal como lo envía la API institucional, sin el formateo de
                # `display_name` (ver AuthenticatedTeacher.raw_name).
                fk_user_name=(str(teacher.get("raw_name") or teacher.get("name") or "").strip() or None),
            )
            db.session.add(material)
            try:
                db.session.commit()
            except IntegrityError:
                # El periodo o el tema validados arriba pueden haber sido
                # borrados por otra petición justo antes de este commit; la
                # FK lo detecta en vez de dejar una fila huérfana.
                db.session.rollback()
                return jsonify({
                    "error": "El periodo o el tema indicado ya no está disponible. Vuelve a intentarlo."
                }), 409
            return jsonify({"material_id": material.id})

        if material_type == TIPO_ORACION_IMAGEN:
            sentences_json_raw = (request.form.get("sentences_json") or "").strip()
            if not sentences_json_raw:
                return jsonify({"error": "Escribe al menos una oración con dos sustantivos."}), 400
            try:
                parsed = json.loads(sentences_json_raw)
            except json.JSONDecodeError:
                return jsonify({"error": "Las oraciones no tienen un formato JSON válido."}), 400
            if not isinstance(parsed, list):
                return jsonify({"error": "Las oraciones no tienen un formato JSON válido."}), 400

            staging_token = (request.form.get("staging_token") or "").strip()

            # Modo editor: la docente sube sus propias imágenes junto con el
            # resto del formulario, sin pasar por el staging de arriba (eso
            # es solo para el diseño con IA) - un archivo
            # "imagen_<staging_index>_<noun_index>" por cada sustantivo, con
            # el mismo staging_index que ya manda cada oración (ver
            # index_by_texto más abajo).
            manual_image_files = {
                key: value for key, value in request.files.items() if key.startswith("imagen_")
            }

            # `staging_index` (si viene) apunta al hueco del `_previews/<token>/`
            # cuyas imágenes usar; permite que la docente haya quitado filas en
            # el paso de diseño. Se conserva junto al item normalizado.
            index_by_texto = {}
            for raw in parsed if isinstance(parsed, list) else []:
                if isinstance(raw, dict) and raw.get("texto"):
                    key = " ".join(str(raw["texto"]).split()).casefold()
                    if "staging_index" in raw:
                        try:
                            index_by_texto[key] = int(raw["staging_index"])
                        except (TypeError, ValueError):
                            pass

            # Descarta las que la docente dejó sin exactamente dos sustantivos
            # (mismo criterio que la extracción y la generación con IA).
            items = normalize_image_sentences(
                parsed,
                limit=(
                    MAX_IMAGE_DESIGN_SENTENCES if staging_token
                    else MAX_IMAGE_SENTENCES_PER_MATERIAL
                ),
            )
            if not items:
                return jsonify({
                    "error": "Cada oración debe tener su texto y exactamente dos sustantivos."
                }), 400
            total_chars = sum(
                len(item["texto"]) + sum(len(n) for n in item["sustantivos"])
                for item in items
            )
            if total_chars > MAX_SENTENCES_CHARS:
                return jsonify({"error": "Las oraciones exceden el límite permitido."}), 413

            # Con `staging_token`: flujo completo con imágenes ya aprobadas. Sin
            # él: guardado simple, solo texto + sustantivos (cliente antiguo).
            staging = None
            if staging_token:
                try:
                    staging = _staging_dir(staging_token)
                    with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                        staging_meta = json.load(f)
                except (ValueError, OSError, json.JSONDecodeError):
                    return jsonify({
                        "error": "La previsualización expiró. Vuelve a generar el diseño."
                    }), 400
                staged_count = len(staging_meta.get("oraciones", []))

            material_dir_name = uuid.uuid4().hex
            material_dir = os.path.join(app.config["UPLOADS_ROOT"], material_dir_name)
            os.makedirs(material_dir, exist_ok=True)

            if staging:
                oraciones_payload = []
                img_dir = os.path.join(material_dir, "img")
                os.makedirs(img_dir, exist_ok=True)
                # Nombres de archivo finales: preferimos el propio sustantivo
                # ("pollo.png") sobre el identificador posicional de la
                # previsualización, tanto para las imágenes generadas por IA
                # como para las que subió la docente -da igual el nombre con
                # el que llegó el archivo. `used_names` evita que dos
                # sustantivos iguales en oraciones distintas (con imágenes
                # distintas) choquen en el mismo nombre.
                used_names: set[str] = set()
                for position, item in enumerate(items):
                    try:
                        plantilla = image_sentence_template(item["texto"], item["sustantivos"])
                    except ValueError as exc:
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({"error": f"Oración {position + 1}: {exc}"}), 400
                    key = " ".join(item["texto"].split()).casefold()
                    src_index = index_by_texto.get(key, position)
                    if not (0 <= src_index < staged_count):
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({
                            "error": f"Falta la imagen de la oración {position + 1}. "
                                     "Vuelve a generar el diseño."
                        }), 400
                    sustantivos = []
                    for noun_index, palabra in enumerate(item["sustantivos"]):
                        src = os.path.join(
                            staging, _staging_image_name(src_index, noun_index)
                        )
                        if not os.path.isfile(src):
                            shutil.rmtree(material_dir, ignore_errors=True)
                            return jsonify({
                                "error": f"Falta la imagen de la oración {position + 1}. "
                                         "Regénerala antes de aprobar."
                            }), 400
                        rel = f"img/{_unique_noun_filename(palabra, position, noun_index, used_names)}"
                        shutil.copyfile(src, os.path.join(material_dir, rel))
                        sustantivos.append({"palabra": palabra, "imagen": rel})
                    oraciones_payload.append({
                        "texto": item["texto"],
                        "plantilla": plantilla,
                        "sustantivos": sustantivos,
                    })
            elif manual_image_files:
                # Modo editor: sin IA de por medio, las 2 imágenes de cada
                # sustantivo llegan tal cual las subió la docente.
                oraciones_payload = []
                img_dir = os.path.join(material_dir, "img")
                os.makedirs(img_dir, exist_ok=True)
                used_names: set[str] = set()
                for position, item in enumerate(items):
                    try:
                        plantilla = image_sentence_template(item["texto"], item["sustantivos"])
                    except ValueError as exc:
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({"error": f"Oración {position + 1}: {exc}"}), 400
                    key = " ".join(item["texto"].split()).casefold()
                    src_index = index_by_texto.get(key, position)
                    sustantivos = []
                    for noun_index, palabra in enumerate(item["sustantivos"]):
                        file_storage = manual_image_files.get(f"imagen_{src_index}_{noun_index}")
                        if file_storage is None or not (file_storage.filename or "").strip():
                            shutil.rmtree(material_dir, ignore_errors=True)
                            return jsonify({
                                "error": f"Falta una imagen de la oración {position + 1}."
                            }), 400
                        try:
                            data = normalize_uploaded_noun_image(file_storage)
                        except ValueError as exc:
                            shutil.rmtree(material_dir, ignore_errors=True)
                            return jsonify({"error": f"Oración {position + 1}: {exc}"}), 400
                        rel = f"img/{_unique_noun_filename(palabra, position, noun_index, used_names)}"
                        with open(os.path.join(material_dir, rel), "wb") as f:
                            f.write(data)
                        sustantivos.append({"palabra": palabra, "imagen": rel})
                    oraciones_payload.append({
                        "texto": item["texto"],
                        "plantilla": plantilla,
                        "sustantivos": sustantivos,
                    })
            else:
                oraciones_payload = items

            with open(os.path.join(material_dir, "oraciones.json"), "wb") as f:
                f.write(json.dumps(oraciones_payload, ensure_ascii=False, indent=2).encode("utf-8"))

            material = Material(
                nombre_material=title,
                tipo_material=TIPO_ORACION_IMAGEN,
                path_preguntas=f"uploads/{material_dir_name}/oraciones.json",
                path_texto=None,
                path_texto_resumen=None,
                path_audio=None,
                fk_user=str(teacher["id"]),
                id_periodo=material_periodo_id,
                id_tema=material_tema_id,
                fk_user_name=(str(teacher.get("raw_name") or teacher.get("name") or "").strip() or None),
            )
            db.session.add(material)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                shutil.rmtree(material_dir, ignore_errors=True)
                return jsonify({
                    "error": "El periodo o el tema indicado ya no está disponible. Vuelve a intentarlo."
                }), 409
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
            return jsonify({"material_id": material.id})

        if material_type == TIPO_BITS:
            bits_json_raw = (request.form.get("bits_json") or "").strip()
            if not bits_json_raw:
                return jsonify({"error": "Escribe al menos una palabra."}), 400
            try:
                parsed = json.loads(bits_json_raw)
            except json.JSONDecodeError:
                return jsonify({"error": "Los bits no tienen un formato JSON válido."}), 400
            if not isinstance(parsed, list):
                return jsonify({"error": "Los bits no tienen un formato JSON válido."}), 400

            staging_token = (request.form.get("staging_token") or "").strip()

            # Modo editor: la docente sube su propia imagen junto con el resto
            # del formulario, sin pasar por el staging de arriba (eso es solo
            # para el diseño con IA) - un archivo "imagen_<staging_index>" por
            # bit, con el mismo staging_index que ya manda cada bit (ver
            # index_by_palabra más abajo). A diferencia de oracion_imagen, no
            # hay sufijo de sustantivo: cada bit lleva una sola imagen.
            manual_image_files = {
                key: value for key, value in request.files.items() if key.startswith("imagen_")
            }

            # `staging_index` (si viene) apunta al hueco del `_previews/<token>/`
            # cuyas imágenes usar; permite que la docente haya quitado filas en
            # el paso de diseño. Se conserva junto al item normalizado.
            index_by_palabra = {}
            # La pregunta que el robot hará por cada bit (opcional): viaja en el
            # mismo objeto que la palabra y se enlaza a ella igual que el índice.
            question_by_palabra = {}
            for raw in parsed if isinstance(parsed, list) else []:
                if isinstance(raw, dict) and raw.get("palabra"):
                    key = " ".join(str(raw["palabra"]).split()).casefold()
                    question_by_palabra.setdefault(key, clean_bit_question(raw.get("pregunta")))
                    if "staging_index" in raw:
                        try:
                            index_by_palabra[key] = int(raw["staging_index"])
                        except (TypeError, ValueError):
                            pass

            # Descarta los que la docente dejó sin palabra (mismo criterio que
            # la extracción y la generación con IA).
            items = normalize_bits_words(
                parsed,
                limit=(MAX_BITS_DESIGN_ITEMS if staging_token else MAX_BITS_PER_MATERIAL),
            )
            if not items:
                return jsonify({"error": "Cada bit debe tener su palabra."}), 400
            total_chars = sum(len(item["palabra"]) for item in items)
            if total_chars > MAX_BITS_CHARS:
                return jsonify({"error": "Los bits exceden el límite permitido."}), 413

            # A diferencia de oracion_imagen, bits no tiene un tercer camino
            # "solo texto": no hay datos previos a la función que soportar, así
            # que un bit sin imagen (ni staging_token ni imagen_* subida) es un
            # error, no un guardado válido.
            if not staging_token and not manual_image_files:
                return jsonify({
                    "error": "Cada bit necesita su imagen. Genera el diseño con IA o sube "
                             "las imágenes en el editor manual."
                }), 400

            staging = None
            if staging_token:
                try:
                    staging = _staging_dir(staging_token)
                    with open(os.path.join(staging, "meta.json"), "r", encoding="utf-8") as f:
                        staging_meta = json.load(f)
                except (ValueError, OSError, json.JSONDecodeError):
                    return jsonify({
                        "error": "La previsualización expiró. Vuelve a generar el diseño."
                    }), 400
                staged_count = len(staging_meta.get("items", []))

            material_dir_name = uuid.uuid4().hex
            material_dir = os.path.join(app.config["UPLOADS_ROOT"], material_dir_name)
            os.makedirs(material_dir, exist_ok=True)

            bits_payload = []
            img_dir = os.path.join(material_dir, "img")
            os.makedirs(img_dir, exist_ok=True)
            used_names: set[str] = set()

            if staging:
                for position, item in enumerate(items):
                    key = item["palabra"].casefold()
                    src_index = index_by_palabra.get(key, position)
                    if not (0 <= src_index < staged_count):
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({
                            "error": f"Falta la imagen del bit {position + 1}. "
                                     "Vuelve a generar el diseño."
                        }), 400
                    src = os.path.join(staging, _staging_bit_image_name(src_index))
                    if not os.path.isfile(src):
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({
                            "error": f"Falta la imagen del bit {position + 1}. "
                                     "Regénerala antes de aprobar."
                        }), 400
                    rel = f"img/{_unique_noun_filename(item['palabra'], position, 0, used_names)}"
                    shutil.copyfile(src, os.path.join(material_dir, rel))
                    bits_payload.append({
                        "palabra": item["palabra"],
                        "imagen": rel,
                        "pregunta": question_by_palabra.get(key, ""),
                    })
            else:
                # Modo editor: sin IA de por medio, la imagen de cada palabra
                # llega tal cual la subió la docente.
                for position, item in enumerate(items):
                    key = item["palabra"].casefold()
                    src_index = index_by_palabra.get(key, position)
                    file_storage = manual_image_files.get(f"imagen_{src_index}")
                    if file_storage is None or not (file_storage.filename or "").strip():
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({
                            "error": f"Falta la imagen del bit {position + 1}."
                        }), 400
                    try:
                        data = normalize_uploaded_noun_image(file_storage)
                    except ValueError as exc:
                        shutil.rmtree(material_dir, ignore_errors=True)
                        return jsonify({"error": f"Bit {position + 1}: {exc}"}), 400
                    rel = f"img/{_unique_noun_filename(item['palabra'], position, 0, used_names)}"
                    with open(os.path.join(material_dir, rel), "wb") as f:
                        f.write(data)
                    bits_payload.append({
                        "palabra": item["palabra"],
                        "imagen": rel,
                        "pregunta": question_by_palabra.get(key, ""),
                    })

            with open(os.path.join(material_dir, "bits.json"), "wb") as f:
                f.write(json.dumps(bits_payload, ensure_ascii=False, indent=2).encode("utf-8"))

            material = Material(
                nombre_material=title,
                tipo_material=TIPO_BITS,
                path_preguntas=f"uploads/{material_dir_name}/bits.json",
                path_texto=None,
                path_texto_resumen=None,
                path_audio=None,
                fk_user=str(teacher["id"]),
                id_periodo=material_periodo_id,
                id_tema=material_tema_id,
                fk_user_name=(str(teacher.get("raw_name") or teacher.get("name") or "").strip() or None),
            )
            db.session.add(material)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                shutil.rmtree(material_dir, ignore_errors=True)
                return jsonify({
                    "error": "El periodo o el tema indicado ya no está disponible. Vuelve a intentarlo."
                }), 409
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
            return jsonify({"material_id": material.id})

        transcribed_text = (request.form.get("transcribed_text") or "").strip()
        summary_text = (request.form.get("summary_text") or "").strip()
        questions_json_raw = (request.form.get("questions_json") or "").strip()
        audio_full = request.files.get("audio_full")

        if not transcribed_text or not summary_text:
            return jsonify({"error": "Falta el texto completo o el resumen."}), 400
        if len(transcribed_text) > MAX_SOURCE_TEXT_CHARS or len(summary_text) > MAX_SUMMARY_CHARS:
            return jsonify({"error": "El título, texto o resumen excede el límite permitido."}), 413
        if not questions_json_raw:
            return jsonify({"error": "Falta generar las preguntas."}), 400
        try:
            questions_data = json.loads(questions_json_raw)
        except json.JSONDecodeError:
            return jsonify({"error": "Las preguntas no tienen un formato JSON válido."}), 400
        if not isinstance(questions_data, list) or not questions_data:
            return jsonify({"error": "Falta generar las preguntas."}), 400
        if len(questions_data) > len(QUESTION_TYPES) * MAX_QUESTIONS_PER_TYPE:
            return jsonify({"error": "Hay demasiadas preguntas en el material."}), 413
        invalid_questions = [
            question
            for question in questions_data
            if not isinstance(question, dict)
            or not str(question.get("pregunta") or question.get("enunciado") or "").strip()
            or not str(question.get("respuesta_esperada") or "").strip()
        ]
        if invalid_questions:
            return jsonify({
                "error": "Cada pregunta debe incluir un enunciado y una respuesta esperada revisados por la docente."
            }), 400
        # Escenas ilustradas y narradas: el robot reproduce el cuento escena por
        # escena, así que son la fuente del audio completo (se arma uniendo sus
        # audios). Se validan antes de crear nada en disco para no dejar una
        # carpeta a medias si algo falta. Sin escenas solo se acepta el flujo
        # anterior, con un `audio_full` ya generado.
        scenes_token = (request.form.get("scenes_token") or "").strip()
        staged_scenes = []
        if scenes_token:
            try:
                staged_scenes = _staged_scenes_for_save(scenes_token, transcribed_text)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        if not staged_scenes and not audio_full:
            return jsonify({
                "error": "Falta crear las escenas del cuento (imagen y audio de cada una)."
            }), 400

        material_dir_name = uuid.uuid4().hex
        material_dir = os.path.join(app.config["UPLOADS_ROOT"], material_dir_name)
        os.makedirs(material_dir, exist_ok=True)

        text_files = {
            "texto.txt": transcribed_text.encode("utf-8"),
            "resumen.txt": summary_text.encode("utf-8"),
            "preguntas.json": json.dumps(questions_data, ensure_ascii=False, indent=2).encode("utf-8"),
        }
        for filename, content in text_files.items():
            with open(os.path.join(material_dir, filename), "wb") as f:
                f.write(content)

        full_audio_path = os.path.join(material_dir, "audio.wav")

        if staged_scenes:
            # Con escenas, el audio completo siempre sale de ellas (un
            # `audio_full` enviado además se ignora): así nunca puede narrar
            # algo distinto de lo que el robot reproduce por escena.
            try:
                _store_scenes(material_dir, staged_scenes)
                _concat_wav_files([scene["audio"] for scene in staged_scenes], full_audio_path)
            except wave.Error:
                shutil.rmtree(material_dir, ignore_errors=True)
                return jsonify({
                    "error": "Los audios de las escenas no son compatibles entre sí. Vuelve a generarlas."
                }), 400
            except OSError:
                app.logger.exception("No se pudieron guardar las escenas del cuento")
                shutil.rmtree(material_dir, ignore_errors=True)
                return jsonify({"error": "No se pudieron guardar las escenas del cuento."}), 500
        else:
            audio_full.save(full_audio_path)
            try:
                _wav_duration_seconds(full_audio_path)
            except (EOFError, OSError, wave.Error):
                shutil.rmtree(material_dir, ignore_errors=True)
                return jsonify({"error": "El audio completo no es un WAV válido."}), 400

        material = Material(
            nombre_material=title,
            tipo_material=TIPO_CUENTO,
            path_texto=f"uploads/{material_dir_name}/texto.txt",
            path_texto_resumen=f"uploads/{material_dir_name}/resumen.txt",
            path_preguntas=f"uploads/{material_dir_name}/preguntas.json",
            path_audio=f"uploads/{material_dir_name}/audio.wav",
            fk_user=str(teacher["id"]),
            fk_user_name=(str(teacher.get("name") or "").strip() or None),
            id_periodo=material_periodo_id,
            id_tema=material_tema_id,
        )
        db.session.add(material)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            shutil.rmtree(material_dir, ignore_errors=True)
            return jsonify({
                "error": "El periodo o el tema indicado ya no está disponible. Vuelve a intentarlo."
            }), 409

        if staged_scenes:
            shutil.rmtree(_staging_dir(scenes_token), ignore_errors=True)

        return jsonify({"material_id": material.id})

    @app.route("/api/material/<int:material_id>", methods=["DELETE"])
    @login_required
    def delete_material(material_id):
        teacher = current_teacher()
        material = db.session.get(Material, material_id)
        if not material or material.fk_user != str(teacher["id"]):
            return jsonify({"error": "Material no encontrado."}), 404

        interaction_count = Interaccion.query.filter_by(id_material=material.id).count()
        if interaction_count:
            return jsonify({
                "error": (
                    f"Este material tiene {interaction_count} interacción"
                    f"{'es' if interaction_count != 1 else ''} registrada"
                    f"{'s' if interaction_count != 1 else ''} y no se puede eliminar."
                )
            }), 409

        # Tanto un cuento como una oración guardan sus archivos en la misma
        # carpeta uploads/<id>/...; basta con tomar el directorio de una de sus
        # rutas para borrarlos todos junto con el registro. Las oraciones
        # antiguas (texto plano en path_preguntas) no tienen carpeta.
        path_anchor = material.path_texto or (
            material.path_preguntas
            if stored_as_material_path(material.path_preguntas or "")
            else None
        )
        material_dir = None
        if path_anchor:
            try:
                candidate = os.path.dirname(uploads_abspath(path_anchor))
            except ValueError:
                candidate = None
            # Solo se borra si es una subcarpeta directa de UPLOADS_ROOT con
            # nombre de UUID (hex de 32): nunca la raíz ni una ruta calculada
            # a partir de un `path_*` manipulado (p.ej. `uploads/texto.txt`
            # daría dirname == UPLOADS_ROOT).
            root = os.path.realpath(app.config["UPLOADS_ROOT"])
            if (
                candidate
                and os.path.dirname(candidate) == root
                and re.fullmatch(r"[0-9a-f]{32}", os.path.basename(candidate))
            ):
                material_dir = candidate

        db.session.delete(material)
        db.session.commit()

        if material_dir and os.path.isdir(material_dir):
            shutil.rmtree(material_dir, ignore_errors=True)

        return jsonify({"deleted": True})

    def stored_as_material_path(value: str) -> bool:
        """A path_preguntas that points at a file we wrote, vs. legacy inline
        text left over from before oraciones were stored as a JSON file."""
        return value.startswith("uploads/") and value.endswith(".json")

    def _material_sentences_file(material):
        """Raw JSON list stored at uploads/<id>/oraciones.json, or None for a
        legacy `oracion` that still keeps plain text inline in path_preguntas."""
        raw = material.path_preguntas or ""
        if not stored_as_material_path(raw):
            return None
        try:
            with open(uploads_abspath(raw), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def material_image_sentences(material) -> list[dict[str, object]]:
        """Canonical form for an `oracion_imagen` material, tolerant of both
        stored shapes:
          - texto-only  {texto, sustantivos:["a","b"]}          (sin imágenes)
          - con imágenes {texto, plantilla, sustantivos:[{palabra, imagen}]}
        Returns [{texto, plantilla|None, sustantivos:[{palabra, imagen|None}]}].
        `imagen` es la ruta relativa a la carpeta del material (p.ej.
        "img/0-1.png") o None."""
        rows = _material_sentences_file(material) or []
        seen: set[str] = set()
        out: list[dict[str, object]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            texto = " ".join(str(raw.get("texto") or "").split()).strip()
            if not texto:
                continue
            sustantivos = []
            for entry in raw.get("sustantivos") or []:
                if isinstance(entry, dict):
                    palabra = " ".join(str(entry.get("palabra") or "").split()).strip()
                    imagen = str(entry.get("imagen") or "").strip() or None
                else:
                    palabra = " ".join(str(entry or "").split()).strip()
                    imagen = None
                if palabra:
                    sustantivos.append({"palabra": palabra, "imagen": imagen})
            if len(sustantivos) != 2:
                continue
            key = texto.casefold()
            if key in seen:
                continue
            seen.add(key)
            plantilla = " ".join(str(raw.get("plantilla") or "").split()).strip() or None
            out.append({"texto": texto, "plantilla": plantilla, "sustantivos": sustantivos})
            if len(out) >= MAX_IMAGE_SENTENCES_PER_MATERIAL:
                break
        return out

    def _material_image_url(material, sentence_index, noun_index):
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return url_for(
            "download_material_image",
            material_id=material.id,
            s=sentence_index,
            n=noun_index,
            _external=True,
            _scheme=scheme,
        )

    def _material_image_stored_path(material, rel: str) -> str:
        """Ruta de la imagen de un sustantivo (p.ej. "img/pollo.png",
        relativa a la carpeta del material) resuelta a la ruta guardada
        completa (relativa a UPLOADS_ROOT), tal como la esperan
        `uploads_abspath` y `media_url`."""
        base_dir = posixpath.dirname(str(material.path_preguntas or ""))
        return posixpath.join(base_dir, rel)

    def material_image_preview(material) -> list[dict[str, object]]:
        """Vista previa de una "oración con imágenes" ya guardada, para
        desplegarla en la consola tal como se ve en el paso de diseño: el
        texto con cada sustantivo reemplazado por su imagen -o, si el
        material se guardó sin imágenes (cliente antiguo), el propio
        sustantivo resaltado en su lugar (ver split_image_sentence_template)."""
        preview = []
        for oracion in material_image_sentences(material):
            sustantivos = [
                {
                    "palabra": noun["palabra"],
                    "imagen_url": (
                        media_url(_material_image_stored_path(material, noun["imagen"]))
                        if noun["imagen"] else None
                    ),
                }
                for noun in oracion["sustantivos"]
            ]
            plantilla = oracion["plantilla"] or oracion["texto"]
            preview.append({
                "texto": oracion["texto"],
                "parts": split_image_sentence_template(plantilla, sustantivos),
            })
        return preview

    def serialize_image_sentences(material) -> list[dict[str, object]]:
        """Robot-facing view of every sentence: the full text, the template with
        {{0}}/{{1}} markers, and each noun with the authenticated URL of its
        image (or None when the material was saved without images)."""
        detalle = []
        for index, oracion in enumerate(material_image_sentences(material)):
            detalle.append({
                "oracion_completa": oracion["texto"],
                "plantilla": oracion["plantilla"] or oracion["texto"],
                "sustantivos": [
                    {
                        "palabra": noun["palabra"],
                        "imagen_url": (
                            _material_image_url(material, index, noun_index)
                            if noun["imagen"] else None
                        ),
                    }
                    for noun_index, noun in enumerate(oracion["sustantivos"])
                ],
            })
        return detalle

    def material_sentences(material) -> list[str]:
        # Una "oración con imágenes" guarda objetos {texto, sustantivos}; para
        # los consumidores que solo quieren el texto (la tarjeta del listado)
        # se devuelve la lista de textos.
        if material.es_oracion_imagen:
            return [item["texto"] for item in material_image_sentences(material)]
        data = _material_sentences_file(material)
        if data is not None:
            return normalize_sentences(data)
        return split_text_into_sentences(material.path_preguntas or "")

    def _material_bits_file(material):
        """Raw JSON list stored at uploads/<id>/bits.json, or None if
        path_preguntas doesn't point at a file we wrote."""
        raw = material.path_preguntas or ""
        if not stored_as_material_path(raw):
            return None
        try:
            with open(uploads_abspath(raw), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def material_bits_items(material) -> list[dict[str, object]]:
        """Canonical form for a `bits` material: [{palabra, imagen|None,
        pregunta}]. `imagen` es la ruta relativa a la carpeta del material
        (p.ej. "img/mano.png") o None; `pregunta` es lo que el robot dice y
        muestra por ese bit ("" en los bits guardados antes de existir).
        Mirrors material_image_sentences, simplified to a single word per item
        instead of {texto, sustantivos}."""
        rows = _material_bits_file(material) or []
        seen: set[str] = set()
        out: list[dict[str, object]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            palabra = " ".join(str(raw.get("palabra") or "").split()).strip()
            if not palabra:
                continue
            key = palabra.casefold()
            if key in seen:
                continue
            seen.add(key)
            imagen = str(raw.get("imagen") or "").strip() or None
            out.append({
                "palabra": palabra,
                "imagen": imagen,
                "pregunta": clean_bit_question(raw.get("pregunta")),
            })
            if len(out) >= MAX_BITS_PER_MATERIAL:
                break
        return out

    def _material_bits_image_url(material, item_index):
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return url_for(
            "download_material_bit_image",
            material_id=material.id,
            i=item_index,
            _external=True,
            _scheme=scheme,
        )

    def _material_bits_image_stored_path(material, rel: str) -> str:
        base_dir = posixpath.dirname(str(material.path_preguntas or ""))
        return posixpath.join(base_dir, rel)

    def material_bits_preview(material) -> list[dict[str, object]]:
        """Vista previa de un material "bits" ya guardado, para la tarjeta de
        la consola: palabra + imagen firmada (o None si por algún motivo no
        tiene imagen)."""
        return [
            {
                "palabra": item["palabra"],
                "imagen_url": (
                    media_url(_material_bits_image_stored_path(material, item["imagen"]))
                    if item["imagen"] else None
                ),
            }
            for item in material_bits_items(material)
        ]

    def serialize_bits(material) -> list[dict[str, object]]:
        """Robot-facing view: cada palabra con la pregunta que el robot debe
        hacer (None en los bits guardados sin ella: el robot usa entonces su
        frase por defecto) y la URL autenticada de su imagen."""
        return [
            {
                "palabra": item["palabra"],
                "pregunta": item["pregunta"] or None,
                "imagen_url": (
                    _material_bits_image_url(material, index) if item["imagen"] else None
                ),
            }
            for index, item in enumerate(material_bits_items(material))
        ]

    def _material_scenes_stored_path(material, rel: str) -> str:
        base_dir = posixpath.dirname(str(material.path_texto or ""))
        return posixpath.join(base_dir, rel)

    def material_scenes_items(material) -> list[dict[str, object]]:
        """Escenas guardadas de un cuento: [{texto, imagen, audio, duracion_s}],
        con `imagen` y `audio` relativos a la carpeta del material, leídas de
        escenas.json. Lista vacía si el cuento no tiene escenas (los cuentos
        anteriores a esta función tampoco)."""
        if not material.es_cuento or not material.path_texto:
            return []
        try:
            with open(
                uploads_abspath(_material_scenes_stored_path(material, "escenas.json")),
                "r", encoding="utf-8",
            ) as f:
                rows = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(rows, list):
            return []
        out: list[dict[str, object]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                return []
            texto = str(raw.get("texto") or "").strip()
            imagen = str(raw.get("imagen") or "").strip()
            audio = str(raw.get("audio") or "").strip()
            if not texto or not imagen or not audio:
                return []
            try:
                duracion = round(float(raw.get("duracion_s")), 2)
            except (TypeError, ValueError):
                duracion = None
            out.append({"texto": texto, "imagen": imagen, "audio": audio, "duracion_s": duracion})
        return out

    def serialize_scenes(material) -> list[dict[str, object]]:
        """Vista para el robot: por escena, en orden de lectura, su texto, la
        duración de su audio y las URL autenticadas de la imagen y del audio.
        El robot reproduce `audio_url` mostrando `imagen_url` y pasa a la
        siguiente escena al terminar."""
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return [
            {
                "indice": index,
                "texto": item["texto"],
                "duracion_s": item["duracion_s"],
                "imagen_url": url_for(
                    "download_material_scene_image",
                    material_id=material.id, i=index, _external=True, _scheme=scheme,
                ),
                "audio_url": url_for(
                    "download_material_scene_audio",
                    material_id=material.id, i=index, _external=True, _scheme=scheme,
                ),
            }
            for index, item in enumerate(material_scenes_items(material))
        ]

    def _material_resource_url(material, recurso):
        # Los `*_url` apuntan al endpoint autenticado del robot, no a
        # /static/: bajarlos exige el secreto compartido y el identificador de
        # la docente (ver contrato §2.5). Ya no exponen la ruta interna.
        #
        # `_external=True` arma la URL con el esquema que ve este proceso, no
        # el que ve el robot: detrás de un proxy/balanceador que termina TLS
        # (el caso normal en producción), Flask no sabe que el cliente llegó
        # por HTTPS y devuelve `http://`, con lo que el robot no puede
        # descargar (mismo problema que ya resolvía `google_redirect_uri`
        # para el callback de Google; aquí se aplica el mismo criterio).
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return url_for(
            "download_material_resource",
            material_id=material.id,
            recurso=recurso,
            _external=True,
            _scheme=scheme,
        )

    def _material_classification(material):
        return {
            "id_periodo": material.id_periodo,
            "id_tema": material.id_tema,
            "periodo": (
                {"id": material.periodo.id, "nombre": material.periodo.nombre}
                if material.periodo else None
            ),
            "tema": (
                {"id": material.tema.id, "nombre": material.tema.nombre}
                if material.tema else None
            ),
        }

    def serialize_material(material):
        if material.es_bits:
            # Puramente presentacional, igual que oracion_imagen: no hay
            # preguntas/respuestas guardadas para un bit, el robot decide qué
            # preguntar y evalúa la respuesta por su cuenta.
            return {
                "id": material.id,
                "titulo": material.nombre_material,
                "tipo_material": material.tipo_material,
                "fecha_subido": material.fecha_subido.isoformat() if material.fecha_subido else None,
                "fk_user": material.fk_user,
                "docente": material.fk_user_name,
                **_material_classification(material),
                "bits": serialize_bits(material),
                "consonante": bits_shared_consonant(material_bits_items(material)),
                "texto_completo_url": None,
                "texto_resumen_url": None,
                "audio_completo_url": None,
                "preguntas_url": None,
                "preguntas": [],
            }
        if material.es_oracion or material.es_oracion_imagen:
            is_path = stored_as_material_path(material.path_preguntas or "")
            payload = {
                "id": material.id,
                "titulo": material.nombre_material,
                "tipo_material": material.tipo_material,
                "fecha_subido": material.fecha_subido.isoformat() if material.fecha_subido else None,
                "fk_user": material.fk_user,
                "docente": material.fk_user_name,
                **_material_classification(material),
                "oraciones": material_sentences(material),
                "oraciones_url": (
                    _material_resource_url(material, "oraciones") if is_path else None
                ),
                "texto_completo_url": None,
                "texto_resumen_url": None,
                "audio_completo_url": None,
                "preguntas_url": None,
                "preguntas": [],
            }
            if material.es_oracion_imagen:
                # Además del texto plano (clave `oraciones`, común a los dos
                # tipos), el robot recibe por oración: el texto completo, la
                # plantilla con marcadores {{0}}/{{1}} y la URL de la imagen de
                # cada sustantivo, para armar la pantalla.
                payload["oraciones_detalle"] = serialize_image_sentences(material)
            return payload
        try:
            with open(uploads_abspath(material.path_preguntas), "r", encoding="utf-8") as f:
                preguntas = json.load(f)
        except (OSError, ValueError):
            preguntas = []
        return {
            "id": material.id,
            "titulo": material.nombre_material,
            "tipo_material": material.tipo_material,
            "fecha_subido": material.fecha_subido.isoformat() if material.fecha_subido else None,
            "fk_user": material.fk_user,
            "docente": material.fk_user_name,
            **_material_classification(material),
            "texto_completo_url": _material_resource_url(material, "texto") if material.path_texto else None,
            "texto_resumen_url": _material_resource_url(material, "resumen") if material.path_texto_resumen else None,
            "audio_completo_url": _material_resource_url(material, "audio") if material.path_audio else None,
            "preguntas_url": _material_resource_url(material, "preguntas") if material.path_preguntas else None,
            "preguntas": preguntas,
            "escenas": serialize_scenes(material),
        }

    def robot_teacher_query():
        """Identificadores de la docente que llegan en la query del robot.

        `docente` es el nombre tal como MAXCIM lo guarda en `fk_user_name`
        (comparación sin distinguir mayúsculas ni acentos). `teacher_id` (alias
        `dni`) es el `idPersona` de CIMA y se sigue aceptando como alternativa.
        El nombre **no es único**: si dos docentes se llaman igual, la consulta
        devuelve los materiales de ambas.
        """
        teacher_id = (request.args.get("teacher_id") or request.args.get("dni") or "").strip()
        docente = " ".join((request.args.get("docente") or "").split())
        return teacher_id, docente

    def robot_owner_filter(teacher_id: str, docente: str):
        if teacher_id:
            return Material.fk_user == teacher_id
        return db.func.lower(Material.fk_user_name) == docente.lower()

    def robot_material_belongs(material, teacher_id: str, docente: str) -> bool:
        if teacher_id:
            return material.fk_user == teacher_id
        return bool(material.fk_user_name) and (
            material.fk_user_name.lower() == docente.lower()
        )

    def robot_material_or_error(material_id, *, require_identifier: bool):
        """Carga un material para la API del robot y valida la propiedad por
        nombre de la docente (o `idPersona`). Devuelve (material, None) o
        (None, (resp, status))."""
        teacher_id, docente = robot_teacher_query()
        if require_identifier and not teacher_id and not docente:
            return None, (
                jsonify({"error": "Falta identificar a la docente (docente o teacher_id)."}),
                400,
            )
        material = db.session.get(Material, material_id)
        if not material:
            return None, (jsonify({"error": "Material no encontrado."}), 404)
        if (teacher_id or docente) and not robot_material_belongs(material, teacher_id, docente):
            return None, (
                jsonify({"error": "El material no pertenece a esa docente."}),
                403,
            )
        return material, None

    # Robot-side endpoint. Every request must use the shared MAXCIM secret.
    @app.route("/api/materials", methods=["GET"])
    def list_materials():
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        teacher_id, docente = robot_teacher_query()
        if not teacher_id and not docente:
            return jsonify({
                "error": "Falta identificar a la docente (docente o teacher_id)."
            }), 400

        tipo = (request.args.get("tipo") or "").strip().lower()
        if tipo and tipo not in TIPOS_MATERIAL:
            return jsonify({"error": "El tipo de material no es válido."}), 400

        query = Material.query.filter(robot_owner_filter(teacher_id, docente))
        if tipo:
            query = query.filter_by(tipo_material=tipo)
        materials = query.order_by(
            Material.fecha_subido.desc(), Material.id.desc()
        ).all()
        return jsonify([serialize_material(m) for m in materials])

    def _resolve_teacher_ids(teacher_id: str, docente: str) -> list[str] | None:
        """IDs institucionales (`fk_user`) a los que apunta la query del robot.
        Con `teacher_id` es directo; con `docente` (el nombre) se resuelve a
        partir de `material.fk_user_name`, así que una docente sin ningún
        material no se puede ubicar solo por nombre. Devuelve None si no hay
        forma de resolverla."""
        if teacher_id:
            return [teacher_id]
        rows = (
            db.session.query(Material.fk_user)
            .filter(db.func.lower(Material.fk_user_name) == docente.lower())
            .distinct()
            .all()
        )
        ids = [row[0] for row in rows if row[0]]
        return ids or None

    def serialize_tema(tema, materiales_count=None):
        return {
            "id": tema.id,
            "nombre": tema.nombre,
            "fk_user": tema.fk_user,
            "periodo": (
                {
                    "id": tema.periodo.id,
                    "nombre": tema.periodo.nombre,
                    "anio": tema.periodo.anio,
                }
                if tema.periodo
                else None
            ),
            "materiales_count": materiales_count,
        }

    # Robot-side endpoint: los temas de una docente (para agrupar sus
    # materiales en pantalla). Mismo control de acceso que /api/materials:
    # secreto compartido + identificación de la docente (`teacher_id`/`dni` o
    # `docente`). Filtro opcional `periodo={id}`.
    @app.route("/api/temas", methods=["GET"])
    def list_temas():
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        teacher_id, docente = robot_teacher_query()
        if not teacher_id and not docente:
            return jsonify({
                "error": "Falta identificar a la docente (docente o teacher_id)."
            }), 400

        teacher_ids = _resolve_teacher_ids(teacher_id, docente)
        if not teacher_ids:
            return jsonify([])

        query = Tema.query.filter(Tema.fk_user.in_(teacher_ids))
        raw_periodo = (request.args.get("periodo") or "").strip()
        if raw_periodo:
            try:
                periodo_id = int(raw_periodo)
            except (TypeError, ValueError):
                return jsonify({"error": "El periodo indicado no es válido."}), 400
            query = query.filter(Tema.id_periodo == periodo_id)

        temas = query.order_by(Tema.id_periodo, Tema.nombre).all()

        counts: dict[int, int] = {}
        if temas:
            rows = (
                db.session.query(Material.id_tema, db.func.count(Material.id))
                .filter(Material.id_tema.in_([t.id for t in temas]))
                .group_by(Material.id_tema)
                .all()
            )
            counts = {tema_id: total for tema_id, total in rows}

        return jsonify([serialize_tema(t, counts.get(t.id, 0)) for t in temas])

    @app.route("/api/materials/<int:material_id>", methods=["GET"])
    def get_material(material_id):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        # El identificador de la docente (`docente` o `teacher_id`) es
        # obligatorio y debe coincidir con el dueño del material (hallazgo
        # A-03): sin esto, con solo el secreto se recorren los IDs enteros y se
        # descubre a qué docente pertenece cada material.
        material, error = robot_material_or_error(material_id, require_identifier=True)
        if error:
            return error
        return jsonify(serialize_material(material))

    # Robot-side endpoint: descarga el archivo exacto de un material del docente
    # para guardarlo en local. Hace falta identificar a la docente (`docente`,
    # el nombre, o `teacher_id`) y debe coincidir con el dueño del material. Un
    # `cuento` expone texto/resumen/audio/preguntas; una `oracion`
    # solo expone `oraciones`.
    @app.route("/api/materials/<int:material_id>/<recurso>", methods=["GET"])
    def download_material_resource(material_id, recurso):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        material, error = robot_material_or_error(material_id, require_identifier=True)
        if error:
            return error

        if recurso == "oraciones":
            if not (material.es_oracion or material.es_oracion_imagen):
                return jsonify({
                    "error": "Este material es un cuento; usa texto, resumen, audio "
                             "o preguntas."
                }), 404
            payload = {"oraciones": material_sentences(material)}
            if material.es_oracion_imagen:
                payload["oraciones_detalle"] = serialize_image_sentences(material)
            return jsonify(payload)

        if recurso == "bits":
            if not material.es_bits:
                return jsonify({
                    "error": "Este material no es de tipo bits; usa 'bits' solo para ese tipo."
                }), 404
            return jsonify({
                "bits": serialize_bits(material),
                "consonante": bits_shared_consonant(material_bits_items(material)),
            })

        if recurso not in MATERIAL_DOWNLOADS:
            return jsonify({"error": "Recurso de material no reconocido."}), 404
        if material.es_oracion or material.es_oracion_imagen:
            return jsonify({
                "error": "Este material es una oración; solo expone 'oraciones'."
            }), 404
        if material.es_bits:
            return jsonify({
                "error": "Este material es de bits; solo expone 'bits'."
            }), 404

        attr, mimetype, download_name = MATERIAL_DOWNLOADS[recurso]
        stored_path = getattr(material, attr) or ""
        if not stored_path:
            return jsonify({"error": f"El material no tiene {recurso}."}), 404
        try:
            abs_path = uploads_abspath(stored_path)  # resuelto + contenido en UPLOADS_ROOT
        except ValueError:
            return jsonify({"error": f"No se encontró el archivo de {recurso}."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": f"No se encontró el archivo de {recurso}."}), 404
        return send_file(
            abs_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=download_name,
        )

    # Robot-side endpoint: descarga la imagen de un sustantivo de una "oración
    # con imágenes" (`<oración>` y `<sustantivo>` son índices 0-based, tal como
    # llegan en `oraciones_detalle`). Mismo control de acceso que el resto de
    # la API del robot: secreto compartido + identificación de la docente dueña.
    @app.route(
        "/api/materials/<int:material_id>/imagen/<int:s>/<int:n>", methods=["GET"]
    )
    def download_material_image(material_id, s, n):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        material, error = robot_material_or_error(material_id, require_identifier=True)
        if error:
            return error
        if not material.es_oracion_imagen:
            return jsonify({"error": "Este material no tiene imágenes de oraciones."}), 404

        oraciones = material_image_sentences(material)
        if not (0 <= s < len(oraciones)) or n not in (0, 1):
            return jsonify({"error": "Imagen de oración no encontrada."}), 404
        rel = oraciones[s]["sustantivos"][n]["imagen"]
        if not rel:
            return jsonify({"error": "Esta oración se guardó sin imágenes."}), 404

        try:
            abs_path = uploads_abspath(_material_image_stored_path(material, rel))
        except ValueError:
            return jsonify({"error": "Imagen de oración no encontrada."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": "Imagen de oración no encontrada."}), 404
        return send_file(
            abs_path,
            mimetype="image/png",
            as_attachment=True,
            download_name=f"oracion_{s}_sustantivo_{n}.png",
        )

    # Robot-side endpoint: descarga la imagen de un bit (`<i>` es el índice
    # 0-based tal como llega en `bits`). Mismo control de acceso que
    # download_material_image.
    @app.route("/api/materials/<int:material_id>/bit-imagen/<int:i>", methods=["GET"])
    def download_material_bit_image(material_id, i):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        material, error = robot_material_or_error(material_id, require_identifier=True)
        if error:
            return error
        if not material.es_bits:
            return jsonify({"error": "Este material no tiene imágenes de bits."}), 404

        items = material_bits_items(material)
        if not (0 <= i < len(items)):
            return jsonify({"error": "Imagen de bit no encontrada."}), 404
        rel = items[i]["imagen"]
        if not rel:
            return jsonify({"error": "Este bit se guardó sin imagen."}), 404

        try:
            abs_path = uploads_abspath(_material_bits_image_stored_path(material, rel))
        except ValueError:
            return jsonify({"error": "Imagen de bit no encontrada."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": "Imagen de bit no encontrada."}), 404
        return send_file(
            abs_path,
            mimetype="image/png",
            as_attachment=True,
            download_name=f"bit_{i}.png",
        )

    # Robot-side endpoints: imagen y audio de una escena de un cuento (`<i>` es
    # el índice 0-based tal como llega en `escenas`). Mismo control de acceso
    # que download_material_bit_image.
    def _send_material_scene_file(material_id, i, kind):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        material, error = robot_material_or_error(material_id, require_identifier=True)
        if error:
            return error
        items = material_scenes_items(material)
        if not (0 <= i < len(items)):
            return jsonify({"error": "Escena no encontrada."}), 404

        try:
            abs_path = uploads_abspath(_material_scenes_stored_path(material, items[i][kind]))
        except ValueError:
            return jsonify({"error": "Escena no encontrada."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": "Escena no encontrada."}), 404
        if kind == "audio":
            mimetype, extension = "audio/wav", "wav"
        else:
            with open(abs_path, "rb") as f:
                mimetype = _image_mime_type(f.read(16))
            extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mimetype, "png")
        return send_file(
            abs_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"escena_{i}.{extension}",
        )

    @app.route("/api/materials/<int:material_id>/escena-imagen/<int:i>", methods=["GET"])
    def download_material_scene_image(material_id, i):
        return _send_material_scene_file(material_id, i, "imagen")

    @app.route("/api/materials/<int:material_id>/escena-audio/<int:i>", methods=["GET"])
    def download_material_scene_audio(material_id, i):
        return _send_material_scene_file(material_id, i, "audio")

    def parse_optional_bool(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "si", "sí"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        raise ValueError("El valor booleano no es válido.")

    def _interaccion_audio_url(interaccion):
        # Mismo criterio que _material_resource_url: sin `_scheme` explícito,
        # detrás de un proxy que termina TLS, `_external=True` devolvería
        # `http://` (el proceso no ve el HTTPS del cliente).
        scheme = "https" if app.config.get("PREFERRED_URL_SCHEME") == "https" else request.scheme
        return url_for(
            "download_interaccion_audio",
            interaccion_id=interaccion.id,
            _external=True,
            _scheme=scheme,
        )

    def serialize_interaccion(interaccion):
        return {
            "id": interaccion.id,
            "id_material": interaccion.id_material,
            "fk_alumno": interaccion.fk_alumno,
            "fecha_hora": interaccion.fecha_hora.isoformat() if interaccion.fecha_hora else None,
            "pregunta": interaccion.pregunta,
            "respuesta": interaccion.respuesta,
            # URL autenticada al audio que MAXCIM ya almacenó (ver
            # registrar_interaccion); ya no expone la ruta interna.
            "audio_rpta_url": _interaccion_audio_url(interaccion),
            "apreciacion_robot": interaccion.apreciacion_robot,
            "rpta_correcta": interaccion.rpta_correcta,
            "periodo": (
                {"id": interaccion.periodo.id, "nombre": interaccion.periodo.nombre}
                if interaccion.periodo
                else None
            ),
        }

    # Robot-side endpoint. MAXCIM ya no gestiona sesiones ni reconocimiento
    # facial: el robot resuelve por su cuenta qué alumno tiene enfrente y qué
    # material está usando, y reporta cada turno de pregunta/respuesta con
    # una sola llamada. `multipart/form-data` (no JSON) porque el robot sube
    # aquí el archivo de audio de la respuesta; MAXCIM lo guarda en
    # UPLOADS_ROOT igual que hace con el audio de un material (antes solo se
    # guardaba la ruta de texto que reportaba el robot; ver contrato §2.3).
    @app.route("/api/interacciones", methods=["POST"])
    def registrar_interaccion():
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401

        fk_alumno = str(request.form.get("fk_alumno") or "").strip()
        pregunta = str(request.form.get("pregunta") or "").strip()
        respuesta = str(request.form.get("respuesta") or "").strip()
        apreciacion_robot = str(request.form.get("apreciacion_robot") or "").strip()
        audio_rpta = request.files.get("audio_rpta")

        # `id_material` es opcional: si falta o llega vacío/null, el turno es
        # una conversación libre del alumno con MAXCIM (sin material asociado).
        raw_material_id = request.form.get("id_material")
        material_id = None
        if raw_material_id not in (None, ""):
            try:
                material_id = int(raw_material_id)
            except (TypeError, ValueError):
                return jsonify({"error": "id_material no es válido."}), 400
        try:
            rpta_correcta = parse_optional_bool(request.form.get("rpta_correcta"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        missing = [
            label
            for label, value in (
                ("fk_alumno", fk_alumno),
                ("pregunta", pregunta),
                ("respuesta", respuesta),
                ("apreciacion_robot", apreciacion_robot),
            )
            if not value
        ]
        if not audio_rpta or not audio_rpta.filename:
            missing.append("audio_rpta")
        if rpta_correcta is None:
            missing.append("rpta_correcta")
        if missing:
            return jsonify({"error": f"Faltan campos obligatorios: {', '.join(missing)}."}), 400
        if len(fk_alumno) > 50:
            return jsonify({"error": "fk_alumno excede el límite permitido."}), 413
        if (
            len(pregunta) > MAX_TRANSCRIPT_CHARS
            or len(respuesta) > MAX_TRANSCRIPT_CHARS
            or len(apreciacion_robot) > MAX_TRANSCRIPT_CHARS
        ):
            return jsonify({
                "error": "La pregunta, la respuesta o la apreciación exceden el límite permitido."
            }), 413

        material = None
        if material_id is not None:
            material = db.session.get(Material, material_id)
            if not material:
                return jsonify({"error": "Material no encontrado."}), 404
        if material is not None:
            interaction_periodo_id = material.id_periodo
        else:
            interaction_periodo = periodo_for_date(utc_now().date())
            interaction_periodo_id = interaction_periodo.id if interaction_periodo else None

        interaccion_dir_name = uuid.uuid4().hex
        interaccion_dir = os.path.join(app.config["UPLOADS_ROOT"], interaccion_dir_name)
        os.makedirs(interaccion_dir, exist_ok=True)
        audio_path = os.path.join(interaccion_dir, "audio_rpta.wav")
        audio_rpta.save(audio_path)
        try:
            _wav_duration_seconds(audio_path)
        except (EOFError, OSError, wave.Error):
            shutil.rmtree(interaccion_dir, ignore_errors=True)
            return jsonify({"error": "El audio de la respuesta no es un WAV válido."}), 400

        interaccion = Interaccion(
            id_material=material.id if material else None,
            fk_alumno=fk_alumno,
            pregunta=pregunta,
            respuesta=respuesta,
            path_audio_rpta=f"uploads/{interaccion_dir_name}/audio_rpta.wav",
            apreciacion_robot=apreciacion_robot,
            rpta_correcta=rpta_correcta,
            id_periodo=interaction_periodo_id,
        )
        db.session.add(interaccion)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            shutil.rmtree(interaccion_dir, ignore_errors=True)
            app.logger.exception("No se pudo registrar la interacción")
            return jsonify({"error": "No se pudo registrar la interacción."}), 500
        return jsonify(serialize_interaccion(interaccion)), 201

    # Robot-side endpoint: descarga el audio de la respuesta que MAXCIM
    # almacenó al registrar la interacción (ver registrar_interaccion). Exige
    # identificar a la docente y que el material de la interacción le
    # pertenezca (hallazgo A-03).
    @app.route("/api/interacciones/<int:interaccion_id>/audio", methods=["GET"])
    def download_interaccion_audio(interaccion_id):
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        teacher_id, docente = robot_teacher_query()
        if not teacher_id and not docente:
            return jsonify({
                "error": "Falta identificar a la docente (docente o teacher_id)."
            }), 400
        interaccion = db.session.get(Interaccion, interaccion_id)
        if not interaccion:
            return jsonify({"error": "Interacción no encontrada."}), 404
        material = (
            db.session.get(Material, interaccion.id_material)
            if interaccion.id_material
            else None
        )
        if material is None or not robot_material_belongs(material, teacher_id, docente):
            # Conversación libre (sin material) o material de otra docente.
            return jsonify({"error": "La interacción no pertenece a esa docente."}), 403
        try:
            abs_path = uploads_abspath(interaccion.path_audio_rpta)
        except ValueError:
            return jsonify({"error": "No se encontró el audio de la respuesta."}), 404
        if not os.path.isfile(abs_path):
            return jsonify({"error": "No se encontró el audio de la respuesta."}), 404
        return send_file(
            abs_path,
            mimetype="audio/wav",
            as_attachment=True,
            download_name="respuesta.wav",
        )

    # Robot-side endpoint: consulta el historial (por material y/o alumno).
    @app.route("/api/interacciones", methods=["GET"])
    def list_interacciones():
        if not webhook_authorized():
            return jsonify({"error": "Integración no autorizada."}), 401
        teacher_id, docente = robot_teacher_query()
        if not teacher_id and not docente:
            return jsonify({
                "error": "Falta identificar a la docente (docente o teacher_id)."
            }), 400
        material_id = request.args.get("id_material")
        fk_alumno = (request.args.get("fk_alumno") or "").strip()
        # Exigir al menos un filtro además de la docente: el robot siempre
        # consulta por un material o por un alumno concreto.
        if not material_id and not fk_alumno:
            return jsonify({
                "error": "Indica al menos id_material o fk_alumno."
            }), 400
        # Solo interacciones de materiales de esta docente (hallazgo A-03). Las
        # conversaciones libres (id_material NULL) no tienen dueña y quedan
        # fuera de la API del robot hasta el hallazgo A-05.
        query = (
            Interaccion.query
            .join(Material, Interaccion.id_material == Material.id)
            .filter(robot_owner_filter(teacher_id, docente))
        )
        if material_id:
            try:
                query = query.filter(Interaccion.id_material == int(material_id))
            except ValueError:
                return jsonify({"error": "id_material no es válido."}), 400
        if fk_alumno:
            query = query.filter(Interaccion.fk_alumno == fk_alumno)
        interacciones = query.order_by(Interaccion.fecha_hora.desc()).limit(200).all()
        return jsonify([serialize_interaccion(item) for item in interacciones])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5000")),
        debug=env_bool("FLASK_DEBUG", False),
    )
