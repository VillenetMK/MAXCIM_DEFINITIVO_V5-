import subprocess
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import UInt8MultiArray


class MicrophoneNode(Node):
    def __init__(self):
        super().__init__('microphone_node')

        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('chunk_size', 1024)
        self.declare_parameter('channels', 1)
        # Nombre de dispositivo ALSA; "pipewire" usa el micrófono predeterminado del sistema
        self.declare_parameter('device', 'pipewire')

        self.sample_rate = self.get_parameter('sample_rate').value
        self.chunk_size = self.get_parameter('chunk_size').value
        self.channels = self.get_parameter('channels').value
        self.device = self.get_parameter('device').value

        self.pub = self.create_publisher(UInt8MultiArray, 'audio/raw', qos_profile_sensor_data)

        self._stop_event = threading.Event()
        self._proc = None
        self._worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker.start()

        self.get_logger().info(
            f'Microphone node listo | '
            f'{self.sample_rate} Hz, chunk={self.chunk_size}, '
            f'channels={self.channels}, device={self.device}'
        )

    def _capture_loop(self):
        cmd = [
            'arecord',
            '-D', self.device,
            '-f', 'S16_LE',
            '-r', str(self.sample_rate),
            '-c', str(self.channels),
            '-t', 'raw',
            '-q',
        ]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        bytes_per_chunk = self.chunk_size * 2 * self.channels  # 2 bytes por muestra int16
        try:
            while not self._stop_event.is_set():
                data = self._proc.stdout.read(bytes_per_chunk)
                if not data:
                    break
                msg = UInt8MultiArray()
                msg.data = list(data)
                self.pub.publish(msg)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait()

    def destroy_node(self):
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait()
        self._worker.join(timeout=5.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MicrophoneNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
