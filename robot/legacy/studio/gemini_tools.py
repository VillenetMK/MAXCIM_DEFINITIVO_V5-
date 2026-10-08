"""Optional tool catalog for separate clients; never install in reasoning_pkg."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path.home()/'.local/share/max-studio-jetson'))
from wire import rpc_local

STUDIO_PHYSICAL_TOOLS = {'desplazarse', 'girar_base', 'detener_maxim', 'ir_a_punto',
                         'acercarse_a_persona', 'acercarse_a_objeto'}


def send_intent(intent):
    try:
        key = (Path.home()/'.config/max-studio/jetson-key').read_text().strip()
        answer = rpc_local(key, '/v2/intent', intent)
        if not answer.get('id'):
            return answer
        until = time.monotonic()+4
        while time.monotonic() < until:
            time.sleep(.15)
            result = rpc_local(key, '/v2/result', {'id':answer['id']})
            if result.get('state') != 'pending':
                return result
        return {'accepted':False, 'error':'No se confirmó la ejecución. No repitas automáticamente la orden.'}
    except Exception:
        return {'accepted':False, 'error':'MAX Studio no está disponible; no se confirmó ningún movimiento.'}


def install_tools(tool):
    @tool
    def estado_movimiento() -> dict:
        """Consulta el modo de control, el movimiento y los destinos conocidos antes de navegar.
        Los puntos nombrados son de la sesión actual. Nunca inventes coordenadas ni destinos.
        """
        return send_intent({'kind':'status'})

    @tool
    def desplazarse(direccion: str, metros: float = 0.25) -> dict:
        """Mueve la base de Maxim una distancia corta, de 0.05 a 1 metro.
        direccion solo puede ser adelante o atras. Úsala ante una orden explícita.
        La base no puede deslizarse lateralmente: necesita girar primero.
        No repitas automáticamente una orden bloqueada ni encadenes desplazamientos
        para superar el límite. La respuesta 'started' no significa que haya terminado.
        """
        return send_intent({'kind':'move','direction':direccion,'amount':metros})

    @tool
    def girar_base(direccion: str, grados: float = 30.0) -> dict:
        """Gira la base de Maxim izquierda o derecha, entre 5 y 90 grados.
        Requiere una petición explícita; no sirve para buscar a ciegas al hablante.
        """
        return send_intent({'kind':'turn','direction':direccion,'amount':grados})

    @tool
    def detener_maxim() -> dict:
        """Detén la base, cancela navegación y pasos pendientes de brazos inmediatamente
        al escuchar para, alto, quieto, detente o cancela. No pide confirmación.
        Un paso de brazos ya transmitido puede terminar.
        """
        return send_intent({'kind':'stop'})

    @tool
    def ir_a_punto(nombre: str) -> dict:
        """Navega hasta un punto nombrado en MAX Studio, por ejemplo puerta o mesa.
        Consulta estado_movimiento para conocer los nombres; no inventes ubicaciones.
        Se necesita SLAM, Nav2 y un camino libre. No afirmes que llegó si solo fue aceptado.
        """
        return send_intent({'kind':'goto','target':nombre})

    @tool
    def acercarse_a_persona(nombre: str) -> dict:
        """Acerca Maxim a una persona identificada y visible, manteniendo un metro de separación.
        Ante 'ven acá', necesitas identificar inequívocamente al hablante. Si no sabes
        quién llamó, pide su nombre; nunca supongas que es la persona más cercana.
        Requiere profundidad, cámara calibrada, localización y navegación disponibles.
        """
        return send_intent({'kind':'person','target':nombre})

    @tool
    def acercarse_a_objeto(nombre: str, centro_x: float, centro_y: float) -> dict:
        """Acerca Maxim al objeto indicado que está claramente visible en la imagen actual.
        centro_x y centro_y son coordenadas normalizadas 0..1 del centro del objeto,
        no coordenadas del mapa. Si hay varios objetos del mismo tipo, pide cuál.
        No inventes centros si el objeto no es visible. El robot calcula distancia
        con profundidad y mantiene un metro de separación. Sin calibración se rechaza.
        """
        return send_intent({'kind':'object','target':nombre,'u':centro_x,'v':centro_y})
