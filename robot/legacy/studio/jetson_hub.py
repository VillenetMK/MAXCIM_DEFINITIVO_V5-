"""Volatile ROS media and intent bridge. Never opens USB or writes media to disk."""
import base64
from collections import deque
import json
import math
from pathlib import Path
import statistics
import struct
import threading
import time
import uuid

class Hub:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = deque(maxlen=32)
        self.results = {}
        self.seq = self.audio_seq = 0
        self.audio = deque(maxlen=8)
        self.image = None
        self.depth = self.info = self.faces = None
        self.enabled_until = 0
        self.consumer = None
        self.consumer_until = 0
        self.runtime = {}
        self.boot = uuid.uuid4().hex
        self.ros_error = ''
        self.geometry = None
        path = Path.home()/'.config/max-studio/camera_geometry.json'
        if path.exists():
            self.geometry = json.loads(path.read_text())

    def submit(self, intent):
        if not isinstance(intent, dict) or len(json.dumps(intent)) > 4096:
            raise ValueError('Orden inválida')
        kind = intent.get('kind')
        if kind not in ('arm', 'move', 'turn', 'stop', 'goto', 'person', 'object', 'status'):
            raise ValueError('Orden no permitida')
        with self.lock:
            if kind == 'status':
                return {'ok': True, **self.runtime, 'voice':time.monotonic()<self.enabled_until,
                        'camera_calibrated': self.calibrated()}
            if kind != 'stop' and time.monotonic() > self.enabled_until:
                return {'ok': False, 'accepted': False, 'error': 'Activa Control por voz en MAX Studio y mantén la sesión abierta.'}
            if kind in ('person', 'object'):
                intent = dict(intent, target_base=self.resolve(intent))
            self.seq += 1
            event = dict(intent, id=uuid.uuid4().hex, seq=self.seq, at=time.monotonic())
            self.events.append(event)
            return {'ok': True, 'id': event['id'], 'accepted': False, 'state': 'pending'}

    def calibrated(self):
        g = self.geometry
        if not isinstance(g, dict) or g.get('verified') is not True or g.get('intrinsics_verified') is not True:
            return False
        matrix = g.get('optical_to_base')
        if not isinstance(matrix, list) or len(matrix) != 16:
            return False
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in matrix):
            return False
        if matrix[12:] != [0, 0, 0, 1]:
            return False
        for i in range(3):
            for j in range(3):
                if abs(sum(matrix[4*i+k]*matrix[4*j+k] for k in range(3))-(i == j)) > .01:
                    return False
        determinant=(matrix[0]*(matrix[5]*matrix[10]-matrix[6]*matrix[9])
                     -matrix[1]*(matrix[4]*matrix[10]-matrix[6]*matrix[8])
                     +matrix[2]*(matrix[4]*matrix[9]-matrix[5]*matrix[8]))
        if abs(determinant-1)>.01:
            return False
        return True

    def resolve(self, intent):
        if not self.calibrated():
            raise ValueError('Falta medir y calibrar la posición de la cámara respecto a la base.')
        now = time.monotonic()
        if not self.depth or not self.info or now-self.depth[0] > .5:
            raise ValueError('No hay profundidad reciente y válida')
        _, depth = self.depth
        info = self.info
        if (depth.encoding != '16UC1' or depth.width != info.width or depth.height != info.height
                or depth.header.frame_id != info.header.frame_id):
            raise ValueError('Profundidad e intrínsecos no están alineados')
        if intent['kind'] == 'person':
            name = str(intent.get('target', '')).strip().casefold()
            if not name:
                raise ValueError('No puedo identificar al hablante solo por su voz. Indica el nombre de la persona visible.')
            if not self.faces or now-self.faces[0] > 1.5:
                raise ValueError('No hay detección reciente de personas')
            faces = [f for f in self.faces[1].get('rostros', [])
                     if f.get('reconocido') and not f.get('from_tracker') and f.get('bbox')
                     and f.get('skip_reason') is None and f.get('liveness') is True
                     and name in {str(f.get('nombre','')).casefold(), str(f.get('nombre_pila','')).casefold()}]
            if len(faces) != 1:
                raise ValueError('La persona no está visible o el nombre es ambiguo')
            x1, y1, x2, y2 = faces[0]['bbox']
            u, v = (x1+x2)/2, (y1+y2)/2
        else:
            # The model supplies only an image location, never a fabricated world pose.
            u, v = intent.get('u'), intent.get('v')
            if any(type(n) not in (float, int) or not math.isfinite(n) or not 0.05 <= n <= .95 for n in (u, v)):
                raise ValueError('Se necesita un objeto visible y un centro de imagen válido')
            u, v = u * info.width, v * info.height
        x, y = int(u), int(v)
        if not 4 <= x < depth.width-4 or not 4 <= y < depth.height-4:
            raise ValueError('Destino fuera de la imagen')
        values = []
        for row in range(y-3, y+4):
            for col in range(x-3, x+4):
                z = struct.unpack_from('>H' if depth.is_bigendian else '<H', depth.data, row*depth.step+2*col)[0]/1000
                if .4 <= z <= 5:
                    values.append(z)
        if len(values) < 30 or max(values)-min(values) > .5:
            raise ValueError('Profundidad insuficiente o ambigua en el destino')
        z = statistics.median(values)
        fx, fy, cx, cy = info.k[0], info.k[4], info.k[2], info.k[5]
        if fx <= 0 or fy <= 0:
            raise ValueError('Intrínsecos inválidos')
        point = [(u-cx)*z/fx, (v-cy)*z/fy, z, 1]
        matrix = self.geometry['optical_to_base']
        return [sum(matrix[4*i+j]*point[j] for j in range(4)) for i in range(3)]

    def poll(self, request):
        now = time.monotonic()
        owner = request.get('owner')
        if not isinstance(owner, str) or len(owner) != 32:
            raise ValueError('Sesión inválida')
        with self.lock:
            if self.consumer != owner and now < self.consumer_until:
                raise ValueError('Ya existe una sesión de Studio')
            self.consumer, self.consumer_until = owner, now+1.5
            self.enabled_until = now+1 if request.get('voice') is True else 0
            runtime = request.get('runtime', {})
            self.runtime = runtime if isinstance(runtime,dict) else {}
            for item in request.get('results', [])[:32]:
                if isinstance(item, dict) and isinstance(item.get('id'), str):
                    self.results[item['id']] = (now, item)
            self.results = {k:v for k,v in self.results.items() if now-v[0] < 10}
            since = request.get('after', self.seq)
            if type(since) is not int:
                raise ValueError('Cursor inválido')
            same_boot = request.get('boot') == self.boot
            events = [dict(e, age=now-e['at']) for e in self.events
                      if same_boot and e['seq'] > since and now-e['at'] < 2]
            result = {'boot': self.boot, 'seq': self.seq, 'events': events,
                      'camera_calibrated': self.calibrated(), 'ros_error': self.ros_error,
                      'audio_seq': self.audio_seq}
            if request.get('video') is True and self.image and now-self.image[0] < .6:
                result['jpeg'] = base64.b64encode(self.image[1]).decode()
            if request.get('audio') is True:
                after = request.get('audio_after', self.audio_seq)
                if type(after) is not int:raise ValueError('Cursor de audio inválido')
                chunks = [b for seq, t, b in self.audio if seq > after and now-t < .3]
                if chunks:
                    result['pcm'] = base64.b64encode(b''.join(chunks[-5:])).decode()
            return result

    def rpc(self, path, data):
        if path == '/v2/exchange':
            return self.poll(data)
        if path == '/v2/intent':
            return self.submit(data)
        if path == '/v2/result':
            with self.lock:
                return self.results.get(data.get('id'), (0, {'state':'pending'}))[1]
        raise ValueError('Ruta desconocida')

    def start_ros(self):
        try:
            import rclpy
            from sensor_msgs.msg import CompressedImage, Image, CameraInfo
            from std_msgs.msg import UInt8MultiArray, String
            from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy
            rclpy.init()
            node = rclpy.create_node('max_studio_jetson_bridge')
            def fresh(msg, limit=.6):
                stamp=msg.header.stamp
                age=(node.get_clock().now().nanoseconds-stamp.sec*10**9-stamp.nanosec)/1e9
                return -.1<=age<=limit
            def image(msg):
                if len(msg.data) <= 350000 and fresh(msg):
                    with self.lock:
                        self.image = (time.monotonic(), bytes(msg.data))
            def audio(msg):
                if len(msg.data) <= 8192 and len(msg.data)%2 == 0:
                    with self.lock:
                        self.audio_seq += 1
                        self.audio.append((self.audio_seq, time.monotonic(), bytes(msg.data)))
            def faces(msg):
                try:
                    data = json.loads(msg.data)
                    with self.lock:
                        self.faces = (time.monotonic(), data)
                except ValueError:
                    pass
            def depth(msg):
                if len(msg.data) <= 4000000 and fresh(msg):
                    with self.lock:
                        self.depth = (time.monotonic(), msg)
            def info(msg):
                with self.lock:
                    self.info = msg
            node.create_subscription(CompressedImage, '/camera/image_raw/compressed', image, qos_profile_sensor_data)
            node.create_subscription(UInt8MultiArray, '/audio/raw', audio, qos_profile_sensor_data)
            node.create_subscription(Image, '/camera/depth/image_raw', depth, qos_profile_sensor_data)
            node.create_subscription(CameraInfo, '/camera/image_raw/camera_info', info,
                QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
            node.create_subscription(String, '/vision/faces', faces, 1)
            # The original AI's action/motion topics belong to its own controller.
            # Studio never captures or duplicates those commands.
            threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
            self.node = node
        except Exception as error:
            self.ros_error = type(error).__name__ + ': ' + str(error)[:120]
