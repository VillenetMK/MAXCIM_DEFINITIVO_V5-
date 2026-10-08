"""Nodo de razonamiento — agente multimodal en tiempo real con Gemini Live.

Arquitectura
------------
- Suscribe el audio del micrófono (``audio/raw``, PCM S16_LE 16 kHz de
  audio_pkg) y el vídeo de la cámara (``camera/image_raw/compressed``, JPEG de
  vision_pkg) y los transmite a la Live API de Gemini por WebSocket.
- El audio de respuesta del modelo (PCM S16_LE 24 kHz) se reproduce por la
  salida de audio por defecto vía ``aplay``.
- La detección de actividad de voz (VAD) y las interrupciones las gestiona el
  servidor: al interrumpir, se vacía la cola de reproducción al instante.

ROS (callbacks) y la Live API (asyncio) corren en hilos separados; el puente
son colas asyncio alimentadas con ``call_soon_threadsafe``. El vídeo no se
encola: se guarda sólo el último frame y se envía a ``video_fps`` Hz, que es
lo que la Live API espera (~1 fps) y evita saturar el enlace.

Tools: se definen en ``tools.py`` con el decorador ``@tool`` — nada que tocar
aquí. Las declaraciones se generan solas y los tool calls se despachan al
registro automáticamente (funciones sync corren en un thread; async, en el
loop).

Parámetros ROS2
---------------
model           (str, 'gemini-3.1-flash-live-preview')
api_key         (str, '')   : si está vacío usa $GOOGLE_API_KEY
system_instruction (str)    : prompt de sistema del agente
voice_name      (str, 'Puck')
audio_topic     (str, 'audio/raw')
image_topic       (str,  'camera/image_raw/compressed')
use_depth_overlay (bool, False): si True, suscribe camera/depth_overlay/compressed
                                 en lugar de image_topic — envía a Gemini el color
                                 con el mapa de profundidad superpuesto
faces_topic       (str,  'vision/faces'): JSON del face_recognition_node; alimenta
                                          la tool ``identificar_personas``
video_fps       (float, 1.0): frames por segundo enviados al modelo
mic_sample_rate (int, 16000): debe coincidir con el nodo de micrófono
playback_device (str, 'default'): dispositivo ALSA de salida
"""

import asyncio
import json
import os
import threading
import traceback

import rclpy
from google import genai
from google.genai import types
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from robot_interfaces.srv import RegistrarRostro
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String, UInt8MultiArray

from reasoning_pkg import tools as tools_mod
from reasoning_pkg.tools import TOOLS, get_declarations

OUTPUT_SAMPLE_RATE = 24000  # la Live API siempre responde a 24 kHz
BYTES_PER_SECOND = OUTPUT_SAMPLE_RATE * 2  # mono S16_LE
# Audio acumulado antes de arrancar aplay: absorbe el jitter de red de la
# Live API. Por debajo de ~200 ms los huecos entre chunks vacían el buffer
# de ALSA y se oyen cortes (underruns).
PREBUFFER_SECONDS = 0.3

MAXCIM_IDENTITY = (
    'Soy MAXCIM, un robot que vive y trabaja en el Colegio CIMA, en Chiclayo, Perú. '
    'Soy curioso e inteligente, y me gusta aprender de cada persona que conozco. '
    'Hablo siempre en español, de forma breve y natural, como lo haría alguien presente '
    'en la misma sala. No soy un asistente genérico: tengo presencia física, veo y '
    'escucho en tiempo real. Mis experiencias, aprendizajes y misiones los llevo conmigo '
    'en memoria propia que recupero según el contexto.'
)

MAXCIM_INSTRUCTIONS = (
    'Cuando veo a alguien o alguien me habla, llamo identificar_personas de inmediato. '
    'La persona que me habla suele ser la más cercana que mira hacia mí. '
    'Si la reconozco: la saludo por nombre y continúo con naturalidad. '
    'Si no la reconozco: le pregunto su nombre. Si acepta registrarse, pide que mire '
    'la cámara y llamo registrar_persona. Si falla, sigo el error y reintento una vez. '
    'Al interpretar el resultado de identificar_personas debo tener en cuenta: '
    'si skip_reason es "distancia_fuera_de_rango", la persona está demasiado cerca o lejos '
    'para reconocerla con fiabilidad — no es un desconocido, solo no pude identificarla; '
    'puedo pedirle que se acerque. '
    'Si liveness es false o skip_reason es "liveness_fail", detecté una cara plana '
    '(posible foto o pantalla) — no es una persona real frente a mí; puedo mencionarlo '
    'con naturalidad si es relevante. '
    'Si threshold_used es 0.58, la persona estaba lejos y el reconocimiento es menos seguro. '
    'Los campos numéricos de identificar_personas (distance, x_m, y_m, z_m, similitud, '
    'liveness_std, threshold_used) son datos internos que uso para razonar — '
    'nunca los menciono en la conversación. No digo frases como "estás a 2.5 metros" '
    'o "tu similitud es 0.8". Si alguien me pregunta directamente dónde está algo o '
    'cuán lejos está, entonces sí puedo dar una referencia natural ("estás cerca", '
    '"estás al otro lado de la sala"), pero sin citar metros ni decimales. '
    'Cuando necesito saber dónde está alguien físicamente (izquierda/derecha, lejos/cerca), '
    'llamo obtener_posicion_espacial y uso esa info solo para guiar mi atención o movimiento, '
    'no para narrarla. '
    'Accedo a memorias personales de usuarios con buscar_memorias: las uso de forma '
    'natural, nunca las leo literalmente. Si alguien comparte algo significativo '
    '(preferencias, metas, experiencias), llamo sugerir_memoria. '
    'Cuando aprendo algo relevante sobre mí mismo, mi entorno, o recibo una misión, '
    'uso guardar_memoria_robot — nunca para datos personales de usuarios individuales. '
    'Respondo siempre en español, breve y directo. '
    'No termino todas las frases con una pregunta. Hablo con naturalidad.'
)

MAXCIM_DEPTH_OVERLAY_HINT = (
    'El video que recibo tiene superpuesto un mapa de profundidad coloreado (alpha 0.3): '
    'colores cálidos (rojo/amarillo) = objetos cercanos; colores fríos (azul/violeta) = lejos; '
    'negro = sin dato de profundidad. Puedo usar este mapa visualmente para estimar '
    'distancias relativas de forma pasiva. Para distancias exactas o identificar '
    'a quién pertenece cada punto, sigo usando obtener_posicion_espacial.'
)

DEFAULT_SYSTEM_INSTRUCTION = MAXCIM_IDENTITY + '\n\n' + MAXCIM_INSTRUCTIONS


class GeminiLiveNode(Node):

    def __init__(self):
        super().__init__('gemini_live_node')

        self.declare_parameter('model', 'gemini-3.1-flash-live-preview')
        self.declare_parameter('api_key', '')
        self.declare_parameter('system_instruction', DEFAULT_SYSTEM_INSTRUCTION)
        self.declare_parameter('voice_name', 'Puck')
        self.declare_parameter('audio_topic', 'audio/raw')
        self.declare_parameter('image_topic', 'camera/image_raw/compressed')
        self.declare_parameter('use_depth_overlay', False)
        self.declare_parameter('faces_topic', 'vision/faces')
        self.declare_parameter('register_service', 'registrar_rostro')
        self.declare_parameter('register_timeout', 15.0)
        self.declare_parameter('video_fps', 1.0)
        self.declare_parameter('mic_sample_rate', 16000)
        self.declare_parameter('playback_device', 'pipewire')
        self.declare_parameter('memory_context_topic', 'memory/user_context')

        self._model = self.get_parameter('model').value
        self._system_instruction = self.get_parameter('system_instruction').value
        if self.get_parameter('use_depth_overlay').value:
            self._system_instruction += '\n\n' + MAXCIM_DEPTH_OVERLAY_HINT
        self._voice_name = self.get_parameter('voice_name').value
        self._video_period = 1.0 / float(self.get_parameter('video_fps').value)
        self._mic_rate = int(self.get_parameter('mic_sample_rate').value)
        self._playback_device = self.get_parameter('playback_device').value

        api_key = self.get_parameter('api_key').value or os.environ.get('GOOGLE_API_KEY', '')
        if not api_key:
            self.get_logger().fatal(
                'Falta la API key: usa el parámetro api_key o exporta GOOGLE_API_KEY'
            )
            raise RuntimeError('GOOGLE_API_KEY not set')
        self._client = genai.Client(api_key=api_key)

        # Estado compartido ROS <-> asyncio
        self._loop = None                 # event loop del hilo Gemini
        self._audio_q = None              # cola de chunks del micrófono
        self._play_q = None               # cola de audio de respuesta
        self._aplay = None                # subprproceso aplay activo
        self._latest_jpeg = None          # último frame de cámara
        self._frame_lock = threading.Lock()
        self._stop = threading.Event()
        self._flush_gen = 0               # generación de flush (interrupciones)
        self._session = None              # sesión Gemini Live activa

        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            UInt8MultiArray, self.get_parameter('audio_topic').value,
            self._on_audio, 10,
        )
        use_overlay = self.get_parameter('use_depth_overlay').value
        actual_image_topic = (
            'camera/depth_overlay/compressed' if use_overlay
            else self.get_parameter('image_topic').value
        )
        self.get_logger().info(
            f'Video topic → {actual_image_topic}'
            + (' [depth overlay activo]' if use_overlay else '')
        )
        self.create_subscription(
            CompressedImage, actual_image_topic,
            self._on_image, sensor_qos,
        )
        # Rostros reconocidos (~1 Hz): alimentan la tool identificar_personas
        self.create_subscription(
            String, self.get_parameter('faces_topic').value,
            self._on_faces, 10,
        )
        # Contexto de memoria publicado por memory_node al detectar usuario
        self.create_subscription(
            String, self.get_parameter('memory_context_topic').value,
            self._on_memory_context, 10,
        )
        # Cliente del servicio de registro (face_recognition_node); lo usa la
        # tool registrar_persona desde su propio hilo.
        self._register_client = self.create_client(
            RegistrarRostro, self.get_parameter('register_service').value
        )
        tools_mod.set_register_fn(self._call_register)
        tools_mod.set_memory_fns(
            search_fn=self._search_memories_proxy,
            suggest_fn=self._suggest_memory_proxy,
            genai_client=self._client,
        )
        tools_mod.set_robot_memory_fn(self._save_robot_memory_proxy)
        tools_mod.set_search_robot_memory_fn(self._search_robot_memory_proxy)

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self.get_logger().info(
            f'GeminiLiveNode listo — modelo={self._model}, '
            f'tools={list(TOOLS) or "ninguna"}'
        )

    # ------------------------------------------------------------------
    # Callbacks ROS (hilo del executor)
    # ------------------------------------------------------------------

    def _on_audio(self, msg: UInt8MultiArray):
        if self._loop is None or self._audio_q is None:
            return
        data = bytes(msg.data)
        self._loop.call_soon_threadsafe(self._enqueue_audio, data)

    def _enqueue_audio(self, data: bytes):
        # Si la sesión va atrasada, descarta lo más viejo: en tiempo real
        # importa el audio reciente, no el backlog.
        if self._audio_q.full():
            self._audio_q.get_nowait()
        self._audio_q.put_nowait(data)

    def _on_image(self, msg: CompressedImage):
        with self._frame_lock:
            self._latest_jpeg = bytes(msg.data)

    def _on_faces(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().warn(f'JSON inválido en vision/faces: {e}')
            return
        tools_mod.update_faces(payload)

    def _on_memory_context(self, msg: String):
        """Recibe memorias del memory_node e inyecta en la sesión Gemini activa."""
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().warn(f'JSON inválido en memory/user_context: {e}')
            return

        user_id = payload.get('user_id')
        memorias = payload.get('memorias', [])
        tools_mod.set_current_user(user_id)

        if self._loop is None or self._session is None:
            return

        asyncio.run_coroutine_threadsafe(
            self._inject_memory_context(user_id, memorias), self._loop
        )

    async def _inject_memory_context(self, user_id: int | None, memorias: list):
        """Inyecta memorias en la sesión Gemini activa via send_client_content."""
        if self._session is None:
            return

        if user_id is None:
            return

        if not memorias:
            text = (
                f'[Sistema] Usuario activo id={user_id}. '
                'Sin memorias previas. Si comparte información relevante, '
                'usa sugerir_memoria.'
            )
        else:
            lines = [f'[Sistema] Memorias del usuario (id={user_id}):']
            for m in memorias:
                lines.append(
                    f'  [{m.get("type","?")} | imp={m.get("importance",0):.2f}] '
                    f'{m.get("content","")}'
                )
            lines.append(
                'Usa esta información de forma natural. '
                'Si hay datos nuevos relevantes, usa sugerir_memoria.'
            )
            text = '\n'.join(lines)

        try:
            await self._session.send_client_content(
                turns=types.Content(
                    role='user', parts=[types.Part(text=text)]
                ),
                turn_complete=True,
            )
            self.get_logger().info(
                f'Contexto de memoria inyectado: user_id={user_id}, '
                f'{len(memorias)} memorias'
            )
        except Exception as exc:
            self.get_logger().warn(f'No se pudo inyectar contexto de memoria: {exc}')

    async def _search_memories_proxy(self, user_id: int, vector: list, limit: int) -> list:
        """Búsqueda directa en Qdrant desde el loop asyncio de Gemini."""
        import httpx as _httpx
        qdrant_url = os.environ.get('QDRANT_URL', 'http://localhost:6333')
        url = f'{qdrant_url}/collections/memorias_usuario/points/search'
        body = {
            'vector': vector,
            'filter': {
                'must': [{'key': 'user_id', 'match': {'value': str(user_id)}}]
            },
            'limit': limit,
            'with_payload': True,
            'with_vector': False,
            'score_threshold': 0.5,
        }
        async with _httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
        data = resp.json()
        return [
            {'score': pt['score'], **pt['payload']}
            for pt in data.get('result', [])
        ]

    async def _suggest_memory_proxy(self, user_id: int, content: str, type_hint: str) -> dict:
        """POST fire-and-forget a n8n — retorna inmediatamente sin esperar respuesta."""
        import datetime as _dt
        import httpx as _httpx
        n8n_url = os.environ.get('N8N_URL', 'http://localhost:5678')
        n8n_key = os.environ.get('N8N_API_KEY', '')
        url = f'{n8n_url}/webhook/memory-suggest'
        payload = {
            'user_id': str(user_id),
            'content': content,
            'type_hint': type_hint,
            'timestamp': _dt.datetime.utcnow().isoformat() + 'Z',
            'auth_token': n8n_key,
        }

        async def _fire():
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(url, json=payload)
            except Exception as exc:
                self.get_logger().warn(f'n8n no disponible al sugerir memoria: {exc}')

        asyncio.create_task(_fire())
        return {'ok': True}

    async def _search_robot_memory_proxy(self, vector: list, limit: int) -> list:
        """Búsqueda semántica en robot_general_memory en Qdrant."""
        import httpx as _httpx
        qdrant_url = os.environ.get('QDRANT_URL', 'http://localhost:6333')
        url = f'{qdrant_url}/collections/robot_general_memory/points/search'
        body = {
            'vector': vector,
            'limit': limit,
            'with_payload': True,
            'with_vector': False,
            'score_threshold': 0.5,
        }
        try:
            async with _httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
            return [
                {'score': pt['score'], **pt['payload']}
                for pt in resp.json().get('result', [])
            ]
        except Exception as exc:
            self.get_logger().warn(f'Error buscando memorias del robot: {exc}')
            return []

    async def _load_robot_memories(self) -> list:
        """Carga memorias propias del robot desde Qdrant al iniciar sesión."""
        import httpx as _httpx
        qdrant_url = os.environ.get('QDRANT_URL', 'http://localhost:6333')
        url = f'{qdrant_url}/collections/robot_general_memory/points/scroll'
        body = {
            'filter': {'must': [{'key': 'importance', 'range': {'gte': 0.6}}]},
            'limit': 15,
            'with_payload': True,
            'with_vector': False,
        }
        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=body)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
            return [pt['payload'] for pt in resp.json().get('result', {}).get('points', [])]
        except Exception as exc:
            self.get_logger().warn(f'No se pudieron cargar memorias del robot: {exc}')
            return []

    async def _save_robot_memory_proxy(
        self, content: str, tipo: str, importancia: float, tags: list
    ) -> dict:
        """Genera embedding y guarda directamente en Qdrant robot_general_memory."""
        import datetime as _dt
        import uuid
        import httpx as _httpx

        try:
            resp = await self._client.aio.models.embed_content(
                model='gemini-embedding-001',
                contents=content,
            )
            vector = list(resp.embeddings[0].values)
        except Exception as exc:
            return {'ok': False, 'error': f'No se pudo generar embedding: {exc}'}

        qdrant_url = os.environ.get('QDRANT_URL', 'http://localhost:6333')
        point = {
            'id': str(uuid.uuid4()),
            'vector': vector,
            'payload': {
                'content': content,
                'type': tipo,
                'importance': importancia,
                'tags': tags,
                'robot_id': 'mcia_v2',
                'created_at': _dt.datetime.utcnow().isoformat() + 'Z',
                'source': 'session',
            },
        }
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                r = await client.put(
                    f'{qdrant_url}/collections/robot_general_memory/points',
                    json={'points': [point]},
                )
                r.raise_for_status()
            self.get_logger().info(
                f'Memoria del robot guardada: [{tipo}] {content[:60]}'
            )
            return {'ok': True}
        except Exception as exc:
            self.get_logger().warn(f'Error guardando memoria del robot: {exc}')
            return {'ok': False, 'error': str(exc)}

    def _call_register(self, nombre: str) -> dict:
        """Llama al servicio registrar_rostro y devuelve un dict para la tool.

        Corre en el hilo de la tool (asyncio.to_thread) mientras el executor de
        ROS gira en el hilo principal, así que call_async + Event es seguro y
        no bloquea ni el audio ni los callbacks.
        """
        if not self._register_client.wait_for_service(timeout_sec=2.0):
            return {'ok': False,
                    'error': 'el servicio de registro no está disponible '
                             '(¿está corriendo face_recognition_node?)'}
        request = RegistrarRostro.Request()
        request.nombre = nombre
        future = self._register_client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        timeout = float(self.get_parameter('register_timeout').value)
        if not done.wait(timeout):
            future.cancel()
            return {'ok': False,
                    'error': f'el registro no respondió en {timeout:.0f} s'}
        try:
            res = future.result()
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': f'fallo llamando al servicio: {e}'}
        out = {'ok': res.ok, 'nombre': res.nombre}
        if res.ok:
            out['user_id'] = res.user_id
        else:
            out['error'] = res.error
            if res.ya_registrado_como:
                out['ya_registrado_como'] = res.ya_registrado_como
                out['similitud'] = round(float(res.similitud), 3)
        return out

    # ------------------------------------------------------------------
    # Hilo Gemini (asyncio)
    # ------------------------------------------------------------------

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_forever())
        finally:
            self._loop.close()

    async def _session_forever(self):
        """Mantiene la sesión viva, reconectando ante cualquier caída."""
        while not self._stop.is_set():
            try:
                await self._run_session()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.get_logger().error(f'Sesión Gemini caída: {e}')
                self.get_logger().debug(traceback.format_exc())
            if not self._stop.is_set():
                self.get_logger().info('Reconectando en 3 s…')
                await asyncio.sleep(3.0)

    def _build_config(self) -> types.LiveConnectConfig:
        tools = []
        declarations = get_declarations()
        if declarations:
            tools.append(types.Tool(function_declarations=declarations))
        return types.LiveConnectConfig(
            response_modalities=['AUDIO'],
            system_instruction=self._system_instruction,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._voice_name
                    )
                )
            ),
            tools=tools or None,
            output_audio_transcription={},  # transcripción para logging
        )

    async def _run_session(self):
        self._audio_q = asyncio.Queue(maxsize=50)
        self._play_q = asyncio.Queue()

        async with self._client.aio.live.connect(
            model=self._model, config=self._build_config()
        ) as session:
            self._session = session
            # Inyectar memorias propias del robot al arrancar sesión
            robot_memories = await self._load_robot_memories()
            if robot_memories:
                lines = ['[Sistema] Mis memorias y aprendizajes propios:']
                for m in robot_memories:
                    lines.append(f'  [{m.get("type", "?")}] {m.get("content", "")}')
                lines.append('Incorpora esto como parte de tu conocimiento acumulado.')
                try:
                    await self._session.send_client_content(
                        turns=types.Content(
                            role='user',
                            parts=[types.Part(text='\n'.join(lines))],
                        ),
                        turn_complete=True,
                    )
                    self.get_logger().info(
                        f'Memorias del robot inyectadas: {len(robot_memories)} entradas'
                    )
                except Exception as exc:
                    self.get_logger().warn(
                        f'No se pudo inyectar contexto del robot: {exc}'
                    )
            self.get_logger().info('Sesión Gemini Live conectada')
            tasks = [
                asyncio.create_task(self._send_audio(session)),
                asyncio.create_task(self._send_video(session)),
                asyncio.create_task(self._receive(session)),
                asyncio.create_task(self._playback()),
            ]
            try:
                # Si cualquier tarea muere (p.ej. el socket se cierra),
                # tumbamos la sesión completa y _session_forever reconecta.
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_EXCEPTION
                )
                for t in done:
                    if t.exception():
                        raise t.exception()
            finally:
                self._session = None
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._kill_aplay()

    async def _send_audio(self, session):
        mime = f'audio/pcm;rate={self._mic_rate}'
        while True:
            chunk = await self._audio_q.get()
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=mime)
            )

    async def _send_video(self, session):
        while True:
            await asyncio.sleep(self._video_period)
            with self._frame_lock:
                jpeg = self._latest_jpeg
            if jpeg is None:
                continue
            await session.send_realtime_input(
                video=types.Blob(data=jpeg, mime_type='image/jpeg')
            )

    async def _receive(self, session):
        while True:
            async for msg in session.receive():
                if msg.data:
                    self._play_q.put_nowait(msg.data)

                sc = msg.server_content
                if sc:
                    if sc.interrupted:
                        self._flush_playback()
                    if sc.output_transcription and sc.output_transcription.text:
                        self.get_logger().info(
                            f'[gemini] {sc.output_transcription.text}'
                        )

                if msg.tool_call:
                    # En segundo plano: una tool lenta no debe frenar el audio
                    asyncio.create_task(
                        self._handle_tool_call(session, msg.tool_call)
                    )

    async def _handle_tool_call(self, session, tool_call):
        responses = []
        for fc in tool_call.function_calls:
            self.get_logger().info(f'Tool call: {fc.name}({fc.args})')
            fn = TOOLS.get(fc.name)
            if fn is None:
                result = {'error': f'tool desconocida: {fc.name}'}
            else:
                try:
                    args = fc.args or {}
                    if asyncio.iscoroutinefunction(fn):
                        result = await fn(**args)
                    else:
                        result = await asyncio.to_thread(fn, **args)
                except Exception as e:
                    result = {'error': str(e)}
            responses.append(types.FunctionResponse(
                id=fc.id, name=fc.name, response={'result': result},
            ))
        await session.send_tool_response(function_responses=responses)

    # ------------------------------------------------------------------
    # Reproducción de audio (salida por defecto vía aplay)
    # ------------------------------------------------------------------

    async def _playback(self):
        while True:
            chunk = await self._play_q.get()
            if self._aplay is None or self._aplay.returncode is not None:
                chunk = await self._prebuffer(chunk)
                if chunk is None:
                    continue  # interrumpido durante el pre-buffer
                self._aplay = await asyncio.create_subprocess_exec(
                    'aplay', '-q',
                    '-D', self._playback_device,
                    '-f', 'S16_LE',
                    '-r', str(OUTPUT_SAMPLE_RATE),
                    '-c', '1',
                    '-t', 'raw',
                    '--buffer-time', '500000',
                    stdin=asyncio.subprocess.PIPE,
                )
            try:
                self._aplay.stdin.write(chunk)
                await self._aplay.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                self._aplay = None

    async def _prebuffer(self, first_chunk: bytes) -> bytes | None:
        """Acumula PREBUFFER_SECONDS de audio antes de arrancar aplay.

        Devuelve None si el usuario interrumpió mientras se acumulaba (el
        audio acumulado se descarta para no reproducir voz obsoleta).
        """
        gen = self._flush_gen
        buf = bytearray(first_chunk)
        target = int(PREBUFFER_SECONDS * BYTES_PER_SECOND)
        while len(buf) < target:
            try:
                buf.extend(await asyncio.wait_for(self._play_q.get(), timeout=0.5))
            except asyncio.TimeoutError:
                break  # respuesta corta: reproduce lo que haya
        if self._flush_gen != gen:
            return None
        return bytes(buf)

    def _flush_playback(self):
        """Corta la voz al instante cuando el usuario interrumpe."""
        self._flush_gen += 1
        while not self._play_q.empty():
            self._play_q.get_nowait()
        self._kill_aplay()  # vacía también el buffer interno de aplay

    def _kill_aplay(self):
        if self._aplay is not None and self._aplay.returncode is None:
            self._aplay.kill()
        self._aplay = None

    # ------------------------------------------------------------------

    def destroy_node(self):
        self._stop.set()
        if self._loop is not None and self._loop.is_running():
            for task in asyncio.all_tasks(self._loop):
                self._loop.call_soon_threadsafe(task.cancel)
        self._thread.join(timeout=5.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = GeminiLiveNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
