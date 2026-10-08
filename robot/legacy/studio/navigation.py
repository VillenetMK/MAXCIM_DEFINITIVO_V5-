"""Supervised ROS navigation; velocities are returned to the existing base gateway.

Never publishes /cmd_vel. A lost browser, Jetson link, scan or pose cancels motion.
"""
import math
from pathlib import Path
import json
import threading
import time
import unicodedata


def normalized(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError('Nombre de destino inválido')
    return ''.join(c for c in unicodedata.normalize('NFD', name.strip().casefold())
                   if unicodedata.category(c) != 'Mn')


def bounded(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'El valor debe estar entre {low} y {high}')
    return float(value)


def angle_delta(a, b):
    return math.atan2(math.sin(a-b), math.cos(a-b))


class Navigation:
    def __init__(self, node):
        from nav_msgs.msg import Odometry, OccupancyGrid
        from sensor_msgs.msg import LaserScan
        from geometry_msgs.msg import Twist
        from nav2_msgs.action import NavigateToPose
        from rclpy.action import ActionClient
        from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy
        from tf2_ros import Buffer, TransformListener
        self.node, self.Goal = node, NavigateToPose.Goal
        self.client = ActionClient(node, NavigateToPose, '/navigate_to_pose')
        self.tf = Buffer()
        self.listener = TransformListener(self.tf, node)
        self.lock = threading.RLock()
        self.pose = self.scan = self.grid = None
        self.pose_at = self.scan_at = self.map_at = 0
        self.velocity = (0., 0.)
        self.velocity_at = 0
        self.task = None
        self.generation = 0
        self.handle = None
        self.last = 'En reposo'
        self.points = {}
        self.map_publisher = None
        self.profile = {}
        path = Path.home()/'.config/max-studio/navigation.json'
        if path.exists():
            self.profile = json.loads(path.read_text())
        node.create_subscription(Odometry, '/odom', self.on_odom, 1)
        node.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)
        node.create_subscription(OccupancyGrid, '/map', self.on_map,
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        node.create_subscription(Twist, '/max/nav_cmd_vel', self.on_velocity, 1)

    def fresh(self, stamp, limit):
        age = (self.node.get_clock().now().nanoseconds - stamp.sec*10**9-stamp.nanosec)/1e9
        return -.1 <= age <= limit

    def on_odom(self, msg):
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
        if self.fresh(msg.header.stamp, .4) and all(math.isfinite(x) for x in (p.x,p.y,yaw)):
            with self.lock:
                self.pose, self.pose_at = (p.x,p.y,yaw), time.monotonic()

    def on_scan(self, msg):
        if self.fresh(msg.header.stamp, .5):
            with self.lock:
                self.scan, self.scan_at = msg, time.monotonic()

    def on_map(self, msg, info):
        if msg.header.frame_id == 'map' and 0 < msg.info.width*msg.info.height <= 16000000:
            with self.lock:
                publisher=bytes(info.publisher_gid)
                if self.grid is not None and (time.monotonic()-self.map_at>15 or publisher!=self.map_publisher):
                    self.cancel('El mapa cambió; vuelve a nombrar los puntos')
                    self.points.clear()
                self.map_publisher=publisher
                self.grid, self.map_at = msg, time.monotonic()

    def on_velocity(self, msg):
        if all(math.isfinite(v) for v in (msg.linear.x,msg.angular.z)):
            with self.lock:
                self.velocity = (max(-.10,min(.10,msg.linear.x)),max(-.30,min(.30,msg.angular.z)))
                self.velocity_at = time.monotonic()

    def reason(self):
        if self.profile.get('verified') is not True:
            return 'Falta verificar dimensiones y recorrido de prueba de la base'
        try:
            radius = bounded(self.profile.get('robot_radius'), .3, 1.)
        except ValueError:
            return 'Dimensiones de la base inválidas'
        now = time.monotonic()
        if not self.pose or now-self.pose_at > .4:
            return 'Odometría ausente o atrasada'
        if not self.scan or now-self.scan_at > .5:
            return 'LiDAR ausente o atrasado'
        scan = self.scan
        if len(scan.ranges) < 100 or abs(scan.angle_increment)*(len(scan.ranges)-1) < 5.8:
            return 'Se requiere cobertura LiDAR alrededor de la base'
        valid = [v for v in scan.ranges if math.isfinite(v) and scan.range_min <= v <= scan.range_max]
        if len(valid) < len(scan.ranges)*.35:
            return 'Cobertura LiDAR insuficiente'
        if min(valid) < radius + .20:
            return 'Obstáculo cercano: movimiento cancelado'
        return ''

    def map_pose(self):
        from rclpy.time import Time
        transform = self.tf.lookup_transform('map','base_link',Time())
        if not self.fresh(transform.header.stamp, .5):
            raise ValueError('Localización del mapa atrasada')
        p, q = transform.transform.translation, transform.transform.rotation
        return p.x, p.y, math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))

    def save_point(self, name):
        key = normalized(name)
        if self.task:
            raise ValueError('Detén el movimiento antes de nombrar la posición')
        pose = self.map_pose()
        if not self.grid or time.monotonic()-self.map_at > 15:
            raise ValueError('Necesitas un mapa activo')
        if len(self.points) >= 32 and key not in self.points:
            raise ValueError('Máximo 32 puntos por sesión')
        self.points[key] = pose
        return 'Posición nombrada: '+key

    def goal_clear(self, x, y):
        grid = self.grid
        if not grid or time.monotonic()-self.map_at > 15:
            raise ValueError('Necesitas SLAM activo y un mapa reciente')
        info = grid.info
        q = info.origin.orientation
        yaw = math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
        dx, dy = x-info.origin.position.x, y-info.origin.position.y
        cx,cy = (math.cos(yaw)*dx+math.sin(yaw)*dy)/info.resolution,(-math.sin(yaw)*dx+math.cos(yaw)*dy)/info.resolution
        cells = math.ceil((self.profile['robot_radius']+.15)/info.resolution)
        for row in range(int(cy)-cells,int(cy)+cells+1):
            for col in range(int(cx)-cells,int(cx)+cells+1):
                if math.hypot(col-cx,row-cy) > cells:
                    continue
                if not 0 <= col < info.width or not 0 <= row < info.height:
                    raise ValueError('Destino fuera del mapa')
                value = grid.data[row*info.width+col]
                if value < 0 or value >= 50:
                    raise ValueError('El destino no tiene espacio libre suficiente')

    def cancel(self, message='Detenido'):
        with self.lock:
            self.generation += 1
            self.task = None
            self.velocity = (0.,0.)
            self.last = message
            handle, self.handle = self.handle, None
            if handle:
                handle.cancel_goal_async()

    def start(self, intent):
        with self.lock:
            reason = self.reason()
            if reason:
                raise ValueError(reason)
            if self.task:
                raise ValueError('Hay un movimiento en curso; di Maxim detente primero')
            kind = intent['kind']
            if kind in ('move','turn'):
                limits = (.05,1.) if kind == 'move' else (5,90)
                amount = bounded(intent.get('amount'), *limits)
                directions = ('adelante','atras') if kind == 'move' else ('izquierda','derecha')
                if intent.get('direction') not in directions:
                    raise ValueError('Dirección inválida')
                self.task = dict(kind=kind, start=self.pose, amount=amount,
                                 sign=1 if intent['direction']==directions[0] else -1,
                                 until=time.monotonic()+max(8,amount/.04 if kind=='move' else amount/8))
                self.last = 'Avance corto' if kind == 'move' else 'Girando'
                return self.last
            pose = self.map_pose()
            if kind == 'goto':
                target = self.points.get(normalized(intent.get('target')))
                if target is None:
                    raise ValueError('No conozco ese punto; nómbralo en el panel')
            elif kind in ('person','object'):
                point = intent.get('target_base')
                if not isinstance(point,list) or len(point)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point):
                    raise ValueError('No hay una posición medida del destino')
                x,y,_ = point
                distance = math.hypot(x,y)
                if distance <= 1.1:
                    raise ValueError('Ya estoy cerca; mantengo la separación')
                if distance > 5:
                    raise ValueError('Destino demasiado lejano para una observación visual')
                x,y = x*(distance-1.)/distance,y*(distance-1.)/distance
                target = (pose[0]+math.cos(pose[2])*x-math.sin(pose[2])*y,
                          pose[1]+math.sin(pose[2])*x+math.cos(pose[2])*y,
                          pose[2]+math.atan2(y,x))
            else:
                raise ValueError('Destino no permitido')
            self.goal_clear(target[0],target[1])
            if not self.client.server_is_ready():
                raise ValueError('Nav2 no está iniciado')
            self.generation += 1
            generation = self.generation
            self.task = {'kind':'nav','until':time.monotonic()+90,'generation':generation}
            self.velocity_at = 0
            self.last = 'Solicitando ruta'
            goal = self.Goal()
            goal.pose.header.frame_id = 'map'
            goal.pose.header.stamp = self.node.get_clock().now().to_msg()
            goal.pose.pose.position.x, goal.pose.pose.position.y = float(target[0]),float(target[1])
            goal.pose.pose.orientation.z,goal.pose.pose.orientation.w = math.sin(target[2]/2),math.cos(target[2]/2)
            future = self.client.send_goal_async(goal)
            def accepted(f):
                with self.lock:
                    try:
                        handle = f.result()
                        if not handle.accepted:
                            if self.generation==generation:self.cancel('Nav2 rechazó el destino')
                            return
                        if self.generation != generation:
                            handle.cancel_goal_async()
                            return
                        self.handle = handle
                        self.last = 'Navegando'
                        def completed(f):
                            with self.lock:
                                if self.generation==generation:
                                    try:self.last = 'Destino alcanzado' if f.result().status==4 else 'Navegación cancelada o fallida'
                                    except Exception:self.last='Navegación sin resultado'
                                    self.task,self.handle = None,None
                        handle.get_result_async().add_done_callback(completed)
                    except Exception:
                        if self.generation==generation:self.cancel('Fallo al solicitar ruta')
            future.add_done_callback(accepted)
            return self.last

    def output(self):
        with self.lock:
            if not self.task:
                return None
            reason = self.reason()
            if reason or time.monotonic()>self.task['until']:
                self.cancel(reason or 'Tiempo de movimiento agotado')
                return (0.,0.)
            task, pose = self.task,self.pose
            if task['kind']=='nav':
                try:self.map_pose()
                except Exception:
                    self.cancel('Se perdió la localización del mapa')
                    return (0.,0.)
                return self.velocity if time.monotonic()-self.velocity_at<.25 else (0.,0.)
            x,y,yaw = task['start']
            if task['kind']=='move':
                remaining = task['amount']-math.hypot(pose[0]-x,pose[1]-y)
                if remaining<=.015:
                    self.cancel('Desplazamiento terminado')
                    return (0.,0.)
                if abs(angle_delta(pose[2],yaw))>.35:
                    self.cancel('Desviación de orientación; detenido')
                    return (0.,0.)
                return (task['sign']*min(.08,max(.025,remaining*.5)),0.)
            remaining = math.radians(task['amount'])-abs(angle_delta(pose[2],yaw))
            if remaining<.025:
                self.cancel('Giro terminado')
                return (0.,0.)
            return (0.,task['sign']*min(.25,max(.07,remaining*.8)))

    def status(self):
        with self.lock:
            return {'motion':self.last,'busy':bool(self.task),'navigation_ready':self.client.server_is_ready(),
                    'base_blocked':self.reason(),'points':list(self.points)}
