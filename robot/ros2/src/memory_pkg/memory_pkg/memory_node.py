"""Nodo de memoria personal por usuario.

Arquitectura
------------
Suscribe ``vision/faces`` para detectar cuándo un usuario conocido aparece en
escena. Al detectar un cambio estable (debounce), consulta Qdrant y publica las
memorias de alta importancia en ``memory/user_context`` como String JSON.
``gemini_live_node`` las inyecta en la sesión Gemini activa.

Expone también métodos async que ``gemini_live_node`` usa como proxies para la
búsqueda semántica (Qdrant) y el guardado asíncrono (n8n webhook).

Conectividad
------------
  Qdrant:  http://localhost:6333  (o $QDRANT_URL)
  n8n:     http://localhost:5678  (o $N8N_URL)
  Ambos corren en Docker con los puertos expuestos al host.

Parámetros ROS2
---------------
faces_topic                  (str,   'vision/faces')
context_topic                (str,   'memory/user_context')
qdrant_url                   (str,   '')     — vacío → usa $QDRANT_URL
qdrant_collection            (str,   'memorias_usuario')
n8n_url                      (str,   '')     — vacío → usa $N8N_URL
n8n_api_key                  (str,   '')     — vacío → usa $N8N_API_KEY
initial_load_types           (str,   'semantic,preference')
initial_load_min_importance  (float, 0.7)
initial_load_limit           (int,   20)
user_change_debounce         (float, 2.0)   — segundos de estabilidad
faces_max_age_s              (float, 5.0)   — datos más viejos = nodo caído
"""

import asyncio
import json
import os
import threading
import time

import httpx
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String


class MemoryNode(Node):

    def __init__(self):
        super().__init__('memory_node')

        self.declare_parameter('faces_topic', 'vision/faces')
        self.declare_parameter('context_topic', 'memory/user_context')
        self.declare_parameter('qdrant_url', '')
        self.declare_parameter('qdrant_collection', 'memorias_usuario')
        self.declare_parameter('n8n_url', '')
        self.declare_parameter('n8n_api_key', '')
        self.declare_parameter('initial_load_types', 'semantic,preference')
        self.declare_parameter('initial_load_min_importance', 0.7)
        self.declare_parameter('initial_load_limit', 20)
        self.declare_parameter('user_change_debounce', 2.0)
        self.declare_parameter('faces_max_age_s', 5.0)

        self._qdrant_url = (
            self.get_parameter('qdrant_url').value
            or os.environ.get('QDRANT_URL', 'http://localhost:6333')
        )
        self._collection = self.get_parameter('qdrant_collection').value
        self._n8n_url = (
            self.get_parameter('n8n_url').value
            or os.environ.get('N8N_URL', 'http://localhost:5678')
        )
        self._n8n_api_key = (
            self.get_parameter('n8n_api_key').value
            or os.environ.get('N8N_API_KEY', '')
        )
        self._initial_types = [
            t.strip()
            for t in self.get_parameter('initial_load_types').value.split(',')
        ]
        self._min_importance = float(
            self.get_parameter('initial_load_min_importance').value
        )
        self._initial_limit = int(self.get_parameter('initial_load_limit').value)
        self._debounce = float(self.get_parameter('user_change_debounce').value)

        # Estado de seguimiento del usuario activo
        self._current_user_id: int | None = None
        self._candidate_user_id: int | None = None
        self._candidate_since: float = 0.0
        self._lock = threading.Lock()

        # HTTP client asyncio (inicializado en el hilo async)
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            String,
            self.get_parameter('faces_topic').value,
            self._on_faces,
            sensor_qos,
        )
        self._ctx_pub = self.create_publisher(
            String, self.get_parameter('context_topic').value, 10
        )

        self._async_thread = threading.Thread(
            target=self._run_async_loop, daemon=True
        )
        self._async_thread.start()

        self.get_logger().info(
            f'MemoryNode listo | Qdrant={self._qdrant_url} | n8n={self._n8n_url}'
        )

    # ------------------------------------------------------------------
    # Loop asyncio dedicado
    # ------------------------------------------------------------------

    def _run_async_loop(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._async_main())

    async def _async_main(self):
        async with httpx.AsyncClient(timeout=10.0) as client:
            self._http = client
            # Mantiene el cliente abierto hasta que el loop se detenga
            while True:
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Callback ROS — hilo del executor
    # ------------------------------------------------------------------

    def _on_faces(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return

        now = time.monotonic()

        # Buscar el rostro reconocido con mayor similitud
        detected_id: int | None = None
        best_sim = 0.0
        for face in payload.get('rostros', []):
            if face.get('reconocido') and face.get('id') is not None:
                sim = float(face.get('similitud', 0.0))
                if sim > best_sim:
                    best_sim = sim
                    detected_id = int(face['id'])

        with self._lock:
            if detected_id != self._candidate_user_id:
                self._candidate_user_id = detected_id
                self._candidate_since = now

            stable = (now - self._candidate_since) >= self._debounce
            if stable and detected_id != self._current_user_id:
                self._current_user_id = detected_id
                if self._loop is not None:
                    if detected_id is not None:
                        asyncio.run_coroutine_threadsafe(
                            self._load_and_publish(detected_id), self._loop
                        )
                    else:
                        # Nadie reconocido en escena
                        self._publish_context(None, [])

    # ------------------------------------------------------------------
    # Carga inicial de memorias
    # ------------------------------------------------------------------

    async def _load_and_publish(self, user_id: int):
        try:
            memories = await self._scroll_memories(user_id)
        except Exception as exc:
            self.get_logger().error(
                f'Error cargando memorias para user_id={user_id}: {exc}'
            )
            memories = []
        self._publish_context(user_id, memories)

    async def _scroll_memories(self, user_id: int) -> list:
        """Consulta Qdrant con filtro de payload (sin embedding) para carga inicial."""
        if self._http is None:
            raise RuntimeError('HTTP client no inicializado aún')
        url = f'{self._qdrant_url}/collections/{self._collection}/points/scroll'
        body = {
            'filter': {
                'must': [
                    {'key': 'user_id', 'match': {'value': str(user_id)}},
                    {'key': 'importance', 'range': {'gte': self._min_importance}},
                    {'key': 'type', 'match': {'any': self._initial_types}},
                ]
            },
            'limit': self._initial_limit,
            'with_payload': True,
            'with_vector': False,
        }
        resp = await self._http.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return [pt['payload'] for pt in data.get('result', {}).get('points', [])]

    # ------------------------------------------------------------------
    # Métodos públicos — usados como proxies por gemini_live_node
    # ------------------------------------------------------------------

    async def search_memories(self, user_id: int, vector: list, limit: int = 5) -> list:
        """Búsqueda semántica en Qdrant. Llamado desde el loop de gemini_live_node."""
        if self._http is None:
            return []
        url = f'{self._qdrant_url}/collections/{self._collection}/points/search'
        body = {
            'vector': vector,
            'filter': {
                'must': [{'key': 'user_id', 'match': {'value': str(user_id)}}]
            },
            'limit': min(limit, 10),
            'with_payload': True,
            'with_vector': False,
            'score_threshold': 0.5,
        }
        resp = await self._http.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return [
            {'score': pt['score'], **pt['payload']}
            for pt in data.get('result', [])
        ]

    async def suggest_memory(self, user_id: int, content: str, type_hint: str) -> dict:
        """Envía sugerencia de memoria a n8n (fire-and-forget)."""
        import datetime as _dt
        url = f'{self._n8n_url}/webhook/memory-suggest'
        payload = {
            'user_id': str(user_id),
            'content': content,
            'type_hint': type_hint,
            'timestamp': _dt.datetime.utcnow().isoformat() + 'Z',
            'auth_token': self._n8n_api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            return {'ok': resp.status_code < 400}
        except Exception as exc:
            self.get_logger().warn(f'n8n no disponible: {exc}')
            return {'ok': False, 'error': str(exc)}

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------

    def _publish_context(self, user_id: int | None, memories: list):
        msg = String()
        msg.data = json.dumps(
            {'user_id': user_id, 'memorias': memories}, ensure_ascii=False
        )
        self._ctx_pub.publish(msg)
        self.get_logger().info(
            f'Contexto publicado: user_id={user_id}, '
            f'memorias={len(memories)}'
        )

    # ------------------------------------------------------------------

    def destroy_node(self):
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._async_thread.join(timeout=3.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MemoryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
