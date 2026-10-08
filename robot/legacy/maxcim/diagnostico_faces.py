"""Diagnóstico de reconocimiento facial.

Captura el topic vision/faces y reporta en detalle las similitudes,
skip_reason, liveness, track_id y track_age para cada rostro detectado.
Ejecutar con el stack completo corriendo:
    python3 diagnostico_faces.py
"""
import json
import os
import sys
import time

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
except ImportError:
    sys.exit("Ejecuta: source /opt/ros/jazzy/setup.bash && source install/setup.bash")

try:
    import psycopg2
    import numpy as np
except ImportError as e:
    sys.exit(f"Falta dependencia: {e}")


def load_db_embeddings():
    conn = psycopg2.connect(
        host=os.environ.get('FACE_DB_HOST', 'localhost'),
        port=int(os.environ.get('FACE_DB_PORT', '5432')),
        dbname=os.environ.get('FACE_DB_NAME', ''),
        user=os.environ.get('FACE_DB_USER', ''),
        password=os.environ.get('FACE_DB_PASSWORD', ''),
        connect_timeout=5,
    )
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.nombre, e.embedding
            FROM user_embedding e JOIN registered_user u ON e.user_id = u.id
        """)
        rows = cur.fetchall()
    conn.close()
    result = []
    for uid, nombre, raw in rows:
        if isinstance(raw, list):
            vec = np.array(raw, dtype=np.float32)
        else:
            vec = np.frombuffer(bytes(raw), dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            result.append((uid, nombre, vec / norm))
    return result


class FacesDiag(Node):
    def __init__(self, db_entries):
        super().__init__('faces_diag')
        self._db = db_entries
        self._empty_count = 0
        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(String, '/vision/faces', self._cb, sensor_qos)
        self.get_logger().info("Escuchando /vision/faces — colócate frente a la cámara...")

    def _cb(self, msg):
        try:
            d = json.loads(msg.data)
        except Exception:
            return

        num = d.get('num_rostros', 0)
        if num == 0:
            if self._empty_count % 5 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] sin caras en frame")
            self._empty_count += 1
            return

        self._empty_count = 0
        print(f"\n{'='*60}")
        print(f"[{time.strftime('%H:%M:%S')}] num_rostros={num}")
        for i, r in enumerate(d.get('rostros', [])):
            nombre    = r.get('nombre', '?')
            sim       = r.get('similitud', 0.0)
            dist      = r.get('distance', '?')
            liveness  = r.get('liveness')
            live_std  = r.get('liveness_std')
            skip      = r.get('skip_reason')
            thr       = r.get('threshold_used')
            from_trk  = r.get('from_tracker', False)
            reconocido = r.get('reconocido', False)
            track_id  = r.get('track_id')
            track_age = r.get('track_age')
            bbox      = r.get('bbox')

            src = 'TRACKER' if from_trk else ('DETECT' if bbox else 'EXTEND')
            candidato = r.get('mejor_candidato')
            sim_raw   = r.get('mejor_sim_raw')
            cand_str  = f" → casi {candidato!r}({sim_raw:.4f})" if candidato else ""
            print(f"  [{src}] Rostro {i+1}: {nombre!r} | sim={sim:.4f} | thr={thr} | look={r.get('look_at_me')}{cand_str}")
            print(f"          track_id={track_id} age={track_age}s | dist={dist}m | liveness={liveness}(std={live_std}mm) | skip={skip}")
        print(f"{'='*60}")


def main():
    rclpy.init()
    db = load_db_embeddings()
    print(f"\nBD: {len(db)} embeddings")
    # Agrupar por nombre para resumen
    from collections import defaultdict
    counts = defaultdict(int)
    for uid, nombre, _ in db:
        counts[nombre] += 1
    for nombre, n in sorted(counts.items()):
        print(f"  {nombre}: {n} embedding(s)")
    print()

    node = FacesDiag(db)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
