"""Registro de tools para el agente Gemini Live.

Para agregar una tool nueva basta con definir una función con type hints y
docstring, y decorarla con ``@tool``. La declaración (schema JSON) se genera
automáticamente desde la firma; el docstring es la descripción que ve el
modelo. Las funciones pueden ser síncronas (se ejecutan en un thread para no
bloquear el loop) o ``async``.

Ejemplo::

    @tool
    def encender_led(color: str, brillo: int = 100) -> str:
        \"\"\"Enciende el LED del robot con el color y brillo indicados.\"\"\"
        ...
        return 'ok'
"""

import datetime
import threading
import time

from google.genai import types

# name -> callable. El nodo lo consulta para despachar los tool calls.
TOOLS: dict = {}

# ---------------------------------------------------------------------------
# Estado compartido con los nodos ROS
#
# Las tools son funciones planas sin acceso a ROS; el nodo les inyecta los
# datos que necesitan a través de estos setters (patrón "último valor").
# ---------------------------------------------------------------------------

_faces_lock = threading.Lock()
_latest_faces = None  # (time.monotonic() de recepción, payload dict)

# Si el último dato de rostros es más viejo que esto, se considera que el
# nodo de visión no está publicando (publica a ~1 Hz incluso sin rostros).
FACES_MAX_AGE_S = 5.0


def update_faces(payload: dict):
    """Guarda el último JSON de ``vision/faces``; lo llama gemini_live_node."""
    global _latest_faces
    with _faces_lock:
        _latest_faces = (time.monotonic(), payload)


_register_fn = None  # callable(nombre) -> dict; lo inyecta gemini_live_node


def set_register_fn(fn):
    """Registra la función que llama al servicio ROS de registro de rostros."""
    global _register_fn
    _register_fn = fn


# ---------------------------------------------------------------------------
# Estado de memoria personal
# ---------------------------------------------------------------------------

_current_user_id: int | None = None
_user_id_lock = threading.Lock()

# Callables async inyectados por gemini_live_node desde sus métodos proxy
_search_memories_fn = None   # async (user_id, vector, limit) -> list
_suggest_memory_fn = None    # async (user_id, content, type_hint) -> dict
_genai_client = None         # genai.Client para generar embeddings


def set_memory_fns(search_fn, suggest_fn, genai_client):
    """Inyecta los callables de memoria. Lo llama gemini_live_node en __init__."""
    global _search_memories_fn, _suggest_memory_fn, _genai_client
    _search_memories_fn = search_fn
    _suggest_memory_fn = suggest_fn
    _genai_client = genai_client


def set_current_user(user_id: int | None):
    """Actualiza el usuario activo. Lo llama gemini_live_node al recibir memory/user_context."""
    global _current_user_id
    with _user_id_lock:
        _current_user_id = user_id


def get_current_user() -> int | None:
    with _user_id_lock:
        return _current_user_id


_save_robot_memory_fn = None   # async (content, tipo, importancia, tags) -> dict
_search_robot_memory_fn = None  # async (vector, limit) -> list


def set_robot_memory_fn(fn):
    """Registra el callable de guardado de memoria del robot. Lo llama gemini_live_node."""
    global _save_robot_memory_fn
    _save_robot_memory_fn = fn


def set_search_robot_memory_fn(fn):
    """Registra el callable de búsqueda de memoria del robot. Lo llama gemini_live_node."""
    global _search_robot_memory_fn
    _search_robot_memory_fn = fn


def tool(fn):
    """Registra ``fn`` como tool disponible para el agente."""
    TOOLS[fn.__name__] = fn
    return fn


def get_declarations() -> list:
    """Genera las FunctionDeclaration de todas las tools registradas."""
    return [
        types.FunctionDeclaration.from_callable_with_api_option(
            callable=fn, api_option='GEMINI_API'
        )
        for fn in TOOLS.values()
    ]


# ---------------------------------------------------------------------------
# Tools de ejemplo — reemplazar/ampliar según se necesite
# ---------------------------------------------------------------------------

@tool
def get_current_time() -> str:
    """Devuelve la fecha y hora actual del sistema."""
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@tool
def identificar_personas() -> dict:
    """Identifica por reconocimiento facial a las personas frente a la cámara.

    Úsala cada vez que veas a una persona o alguien te hable, para saber su
    nombre. Devuelve por cada rostro: nombre ('desconocido' si no está
    registrado), si te está mirando (look_at_me) y su distancia aproximada en
    metros. Quien te habla suele ser la persona más cercana que te mira.
    """
    with _faces_lock:
        snapshot = _latest_faces
    if snapshot is None:
        return {'error': 'el reconocimiento facial no está disponible '
                         '(face_recognition_node no ha publicado nada)'}
    received_at, payload = snapshot
    age = time.monotonic() - received_at
    if age > FACES_MAX_AGE_S:
        return {'error': f'datos de visión desactualizados ({age:.0f} s); '
                         'el nodo de reconocimiento parece detenido'}
    return {
        'hace_segundos': round(age, 1),
        'num_rostros': payload.get('num_rostros', 0),
        'rostros': [
            {
                'nombre': r.get('nombre'),
                'reconocido': r.get('reconocido'),
                'similitud': r.get('similitud'),
                'look_at_me': r.get('look_at_me'),
                'distancia_m': r.get('distance'),
            }
            for r in payload.get('rostros', [])
        ],
    }


@tool
async def buscar_memorias(query: str, limit: int = 5) -> dict:
    """Busca en la memoria personal del usuario actual por similitud semántica.

    Úsala cuando el usuario mencione algo que podría estar relacionado con
    conversaciones pasadas, preferencias o información personal ya conocida.
    Devuelve las memorias más relevantes con su tipo, contenido e importancia.
    No la uses si el usuario no está identificado.

    Args:
        query: descripción de lo que buscar en la memoria del usuario.
        limit: número máximo de memorias a devolver (por defecto 5, máximo 10).
    """
    user_id = get_current_user()
    if user_id is None:
        return {'error': 'No hay un usuario identificado actualmente'}
    if _search_memories_fn is None or _genai_client is None:
        return {'error': 'El sistema de memoria no está disponible'}

    try:
        resp = await _genai_client.aio.models.embed_content(
            model='gemini-embedding-001',
            contents=query,
        )
        vector = list(resp.embeddings[0].values)
    except Exception as exc:
        return {'error': f'No se pudo generar embedding de búsqueda: {exc}'}

    try:
        memories = await _search_memories_fn(user_id, vector, min(limit, 10))
    except Exception as exc:
        return {'error': f'Error buscando memorias: {exc}'}

    if not memories:
        return {'resultado': 'No encontré memorias relevantes para esa búsqueda'}

    return {
        'usuario_id': user_id,
        'memorias_encontradas': len(memories),
        'memorias': [
            {
                'contenido': m.get('content'),
                'tipo': m.get('type'),
                'importancia': m.get('importance'),
                'tags': m.get('tags', []),
                'similitud': round(float(m.get('score', 0)), 3),
            }
            for m in memories
        ],
    }


@tool
async def sugerir_memoria(contenido: str, type_hint: str = 'semantic') -> dict:
    """Sugiere guardar un dato importante sobre el usuario en su memoria personal.

    Llama esta herramienta cuando el usuario comparta información personal
    significativa: preferencias claras, experiencias importantes, metas o
    estados emocionales relevantes. No la uses para datos triviales o efímeros.
    La operación es asíncrona y no bloquea la conversación.

    Args:
        contenido: descripción clara y concisa del dato a recordar.
        type_hint: categoría de la memoria:
            'semantic'   — hechos y conocimiento general sobre el usuario,
            'episodic'   — experiencias o eventos concretos,
            'preference' — gustos, preferencias y aversiones,
            'emotional'  — estados emocionales o relaciones afectivas.
    """
    user_id = get_current_user()
    if user_id is None:
        return {'ok': False, 'error': 'No hay un usuario identificado actualmente'}
    if _suggest_memory_fn is None:
        return {'ok': False, 'error': 'El sistema de memoria no está disponible'}

    valid_types = {'semantic', 'episodic', 'preference', 'emotional'}
    if type_hint not in valid_types:
        type_hint = 'semantic'

    result = await _suggest_memory_fn(user_id, contenido, type_hint)
    if result.get('ok'):
        return {'ok': True, 'mensaje': 'Lo recordaré para próximas conversaciones.'}
    return {'ok': False, 'error': result.get('error', 'Error desconocido al guardar')}


@tool
async def buscar_memoria_robot(query: str, limit: int = 5) -> dict:
    """Busca en la memoria propia del robot por similitud semántica.

    Úsala cuando necesites recordar algo sobre ti mismo, tus misiones pasadas,
    aprendizajes previos o experiencias anteriores que no están en el contexto actual.

    Args:
        query: descripción de lo que quieres recordar.
        limit: número máximo de memorias a devolver (por defecto 5).
    """
    if _search_robot_memory_fn is None or _genai_client is None:
        return {'error': 'El sistema de memoria del robot no está disponible'}

    try:
        resp = await _genai_client.aio.models.embed_content(
            model='gemini-embedding-001',
            contents=query,
        )
        vector = list(resp.embeddings[0].values)
    except Exception as exc:
        return {'error': f'No se pudo generar embedding de búsqueda: {exc}'}

    try:
        memories = await _search_robot_memory_fn(vector, min(limit, 10))
    except Exception as exc:
        return {'error': f'Error buscando memorias del robot: {exc}'}

    if not memories:
        return {'resultado': 'No encontré memorias relevantes para esa búsqueda'}

    return {
        'memorias_encontradas': len(memories),
        'memorias': [
            {
                'contenido': m.get('content'),
                'tipo': m.get('type'),
                'importancia': m.get('importance'),
                'tags': m.get('tags', []),
                'similitud': round(float(m.get('score', 0)), 3),
            }
            for m in memories
        ],
    }


@tool
async def guardar_memoria_robot(
    contenido: str,
    tipo: str = 'learning',
    importancia: float = 0.75,
    tags: list[str] | None = None,
) -> dict:
    """Guarda una observación o aprendizaje en la memoria propia del robot.

    Úsala cuando aprendas algo relevante sobre tu entorno, el colegio, tus usuarios
    en general (no datos personales de uno específico), o cuando recibas una misión.
    No la uses para datos de usuarios individuales (usa sugerir_memoria para eso).

    Args:
        contenido: descripción clara y concisa del aprendizaje, misión o experiencia.
        tipo: categoría de la memoria:
            'learning'    — patrones observados o conocimiento adquirido,
            'mission'     — objetivos o tareas asignadas,
            'experience'  — eventos significativos vividos,
            'preference'  — preferencias operativas propias.
        importancia: relevancia de 0.0 a 1.0 (defecto 0.75).
        tags: lista de etiquetas opcionales para categorizar la memoria.
    """
    if _save_robot_memory_fn is None:
        return {'ok': False, 'error': 'Sistema de memoria del robot no disponible'}
    valid_types = {'learning', 'mission', 'experience', 'preference'}
    if tipo not in valid_types:
        tipo = 'learning'
    result = await _save_robot_memory_fn(contenido, tipo, importancia, tags or [])
    if result.get('ok'):
        return {'ok': True, 'mensaje': 'Lo recordaré entre sesiones.'}
    return {'ok': False, 'error': result.get('error', 'Error desconocido al guardar')}


@tool
def obtener_posicion_espacial() -> dict:
    """Devuelve la posición 3D de cada persona en el campo de visión del robot.

    Úsala cuando necesites saber dónde están físicamente las personas:
    a qué distancia están, si están a tu izquierda o derecha, y si te miran.
    Usa el sensor de profundidad real de la cámara, no una estimación.
    Ideal para orientar la conversación cuando hay varias personas, saber
    a quién dirigirte, o describir la escena espacialmente.
    No identifica por nombre; combínala con identificar_personas si necesitas
    nombre + posición a la vez.
    """
    with _faces_lock:
        snapshot = _latest_faces
    if snapshot is None:
        return {'error': 'reconocimiento facial no disponible '
                         '(face_recognition_node no ha publicado nada)'}
    received_at, payload = snapshot
    age = time.monotonic() - received_at
    if age > FACES_MAX_AGE_S:
        return {'error': f'datos de visión desactualizados ({age:.0f} s); '
                         'el nodo de reconocimiento parece detenido'}

    rostros = payload.get('rostros', [])
    if not rostros:
        return {'personas': 0,
                'descripcion': 'No hay ninguna persona visible en este momento.'}

    personas = []
    for r in rostros:
        z = r.get('z_m') or r.get('distance') or 0.0
        x = r.get('x_m', 0.0)
        nombre = r.get('nombre', 'desconocido')
        mirando = r.get('look_at_me', False)

        if x > 0.15:
            lateral = 'a tu derecha'
        elif x < -0.15:
            lateral = 'a tu izquierda'
        else:
            lateral = 'al frente'

        if z < 0.8:
            zona = 'muy cerca'
        elif z < 1.5:
            zona = 'cerca'
        elif z < 3.0:
            zona = 'a distancia media'
        else:
            zona = 'lejos'

        frase = f'{nombre} está {zona} ({z:.2f} m), {lateral}'
        if mirando:
            frase += ' y te mira directamente'

        personas.append({
            'nombre': nombre,
            'reconocido': r.get('reconocido', False),
            'z_m': round(z, 2),
            'x_m': round(x, 3),
            'lateral': lateral,
            'mirando_camara': mirando,
            'descripcion': frase,
        })

    # Ordenar por proximidad — la persona más cercana primero
    personas.sort(key=lambda p: p['z_m'])

    lineas = [p['descripcion'] for p in personas]
    descripcion_general = '. '.join(lineas) + '.'

    return {
        'hace_segundos': round(age, 1),
        'personas': len(personas),
        'descripcion': descripcion_general,
        'detalle': personas,
    }


@tool
def registrar_persona(nombre: str) -> dict:
    """Registra el rostro de la persona desconocida que tienes delante.

    Úsala solo cuando una persona que identificar_personas reporta como
    'desconocido' te diga su nombre y acepte registrarse. Antes de llamarla
    pide que mire a la cámara. Tarda unos segundos. Si falla, el campo
    'error' explica qué pedir a la persona (acercarse, mirarte, etc.) para
    reintentar; si devuelve 'ya_registrado_como', dile que ya la conoces por
    ese nombre. Si hay varias personas desconocidas a la vez, pide que solo
    quien se registra se acerque y te mire.

    Args:
        nombre: nombre con el que se registrará a la persona.
    """
    if _register_fn is None:
        return {'ok': False, 'error': 'el registro de rostros no está disponible'}
    nombre = (nombre or '').strip()
    if not nombre:
        return {'ok': False, 'error': 'falta el nombre de la persona'}
    return _register_fn(nombre)
