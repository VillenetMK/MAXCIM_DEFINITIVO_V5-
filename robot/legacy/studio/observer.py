"""Read-only, bounded ROS visualization snapshots."""
import base64
import math
import threading
import time


class Observer:
    def __init__(self):
        import rclpy
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import LaserScan
        from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy, ReliabilityPolicy
        self.rclpy = rclpy
        rclpy.init()
        self.node = rclpy.create_node('max_studio_observer')
        self.lock = threading.Lock()
        self.data = {'scan': [], 'map': None, 'pose': None}
        self.map_at = self.scan_at = 0
        self.node.create_subscription(LaserScan, '/scan', self.scan, qos_profile_sensor_data)
        self.node.create_subscription(Odometry, '/odom', self.odom, 1)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
        self.node.create_subscription(OccupancyGrid, '/map', self.map, qos)
        self.thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True)
        self.thread.start()

    def scan(self, msg):
        if time.monotonic() - self.scan_at < .2:
            return
        self.scan_at = time.monotonic()
        stride = max(1, len(msg.ranges) // 180)
        points = []
        for i in range(0, len(msg.ranges), stride):
            distance = msg.ranges[i]
            if math.isfinite(distance) and msg.range_min <= distance <= msg.range_max:
                angle = msg.angle_min + i * msg.angle_increment
                points.append([round(distance * math.cos(angle), 3), round(distance * math.sin(angle), 3)])
        with self.lock:
            self.data['scan'] = points

    def odom(self, msg):
        p = msg.pose.pose.position
        with self.lock:
            self.data['pose'] = {'x': round(p.x, 3), 'y': round(p.y, 3)}

    def map(self, msg):
        if time.monotonic() - self.map_at < 1:
            return
        self.map_at = time.monotonic()
        w, h = msg.info.width, msg.info.height
        if not w or not h or w*h > 16000000:
            return
        step = max(1, math.ceil(max(w, h) / 180))
        cells = bytes(0 if msg.data[y*w+x] < 0 else 2 if msg.data[y*w+x] >= 50 else 1
                      for y in range(0, h, step) for x in range(0, w, step))
        with self.lock:
            self.data['map'] = {'width': math.ceil(w/step), 'height': math.ceil(h/step),
                                'resolution': msg.info.resolution*step,
                                'cells': base64.b64encode(cells).decode(), 'revision': self.map_at}

    def snapshot(self):
        with self.lock:
            return dict(self.data, scan_fresh=time.monotonic()-self.scan_at < 1,
                        map_age=round(time.monotonic()-self.map_at, 1) if self.map_at else None)

    def close(self):
        self.rclpy.try_shutdown()
        self.thread.join(timeout=2)
