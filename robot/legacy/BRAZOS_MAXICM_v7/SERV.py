import json
import os
import time
import threading
from pathlib import Path

import serial
import serial.tools.list_ports
from flask import Flask, jsonify, render_template, request


# ===============================
# BASE DEL PROYECTO
# ===============================

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

STEPS_FILE = DATA_DIR / "pasos_individuales.json"
MOVEMENTS_FILE = DATA_DIR / "movimientos_creados.json"
QUICK_ACTIONS_FILE = DATA_DIR / "acciones_rapidas.json"

STEPS_DIR = DATA_DIR / "pasos"
MOVEMENTS_DIR = DATA_DIR / "movimientos"
QUICK_ACTIONS_DIR = DATA_DIR / "acciones"

STEPS_DIR.mkdir(exist_ok=True)
MOVEMENTS_DIR.mkdir(exist_ok=True)
QUICK_ACTIONS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


# ===============================
# CONFIGURACIÓN SERIAL
# ===============================

BAUD_RATE = 115200
serial_lock = threading.Lock()


def detectar_puerto_esp32():
    """
    Detecta automáticamente el puerto serial del ESP32.

    Prioridad:
    1. Puerto definido manualmente por variable de entorno ESP32_PORT.
    2. /dev/ttyAMA10 si existe.
    3. Puertos USB típicos: ttyUSB, ttyACM.
    4. Puertos UART: ttyAMA, ttyS.
    5. COM en Windows.
    """

    # 1. Permite forzar el puerto manualmente sin tocar código:
    # Windows:
    #   set ESP32_PORT=COM3
    # Linux:
    #   export ESP32_PORT=/dev/ttyAMA10
    puerto_manual = os.environ.get("ESP32_PORT")

    if puerto_manual:
        # En Windows, los puertos COM (ej. COM3) no se pueden validar con os.path.exists.
        # Aceptamos el puerto directamente en Windows o si tiene formato COM, delegando el error al bloque try-except de conexión.
        es_com_port = puerto_manual.upper().startswith("COM") or "COM" in puerto_manual.upper()
        if os.name == "nt" or es_com_port or os.path.exists(puerto_manual):
            print(f"Puerto ESP32 forzado por variable ESP32_PORT: {puerto_manual}")
            return puerto_manual

    # 2. Prioridad especial para tu caso actual
    puertos_prioritarios = [
        "/dev/ttyUSB0",
        "/dev/ttyUSB1",
        "/dev/ttyACM0",
        "/dev/ttyACM1",
        "/dev/ttyAMA10",
    ]

    for puerto in puertos_prioritarios:
        if os.path.exists(puerto):
            print(f"Puerto ESP32 detectado por prioridad: {puerto}")
            return puerto

    # 3. Buscar usando pyserial
    ports = list(serial.tools.list_ports.comports())

    keywords = [
        "USB",
        "UART",
        "CP210",
        "CP210x",
        "CH340",
        "CH341",
        "Silicon Labs",
        "USB Serial",
        "USB2.0-Serial",
        "ACM",
        "Arduino",
        "Espressif",
        "ESP32",
    ]

    for port in ports:
        text = f"{port.device} {port.description} {port.manufacturer}"

        for key in keywords:
            if key.lower() in text.lower():
                print(f"Puerto ESP32 detectado por descripción: {port.device}")
                return port.device

    # 4. Buscar puertos Linux adicionales
    if os.name != "nt":
        patrones_linux = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/ttyAMA*",
            "/dev/ttyS*",
        ]

        for patron in patrones_linux:
            try:
                for puerto in sorted(Path("/dev").glob(Path(patron).name)):
                    puerto_str = str(puerto)

                    if os.path.exists(puerto_str):
                        print(f"Puerto serial detectado por patrón Linux: {puerto_str}")
                        return puerto_str
            except Exception:
                pass

    # 5. Fallback para Windows u otros sistemas
    if ports:
        print(f"Puerto serial detectado como fallback: {ports[0].device}")
        return ports[0].device

    return None


SERIAL_PORT = detectar_puerto_esp32()
esp32 = None

try:
    if SERIAL_PORT:
        esp32 = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        print(f"Conectado exitosamente al ESP32 en {SERIAL_PORT}")
    else:
        print("No se detectó puerto serial. Modo simulación activado.")
except Exception as e:
    print(f"Advertencia: No se pudo conectar al ESP32 ({e}). Modo simulación activado.")
    esp32 = None


# ===============================
# CONTROL DE SECUENCIAS EN SEGUNDO PLANO
# ===============================

sequence_running = False
sequence_lock = threading.Lock()


def ejecutar_lista_pasos_background(sequence_names):
    global sequence_running

    try:
        ejecutar_lista_pasos(sequence_names)
    except Exception as e:
        print(f"ERROR ejecutando secuencia en segundo plano: {e}")
    finally:
        with sequence_lock:
            sequence_running = False


def iniciar_secuencia_background(sequence_names):
    global sequence_running

    with sequence_lock:
        if sequence_running:
            return False

        sequence_running = True

    hilo = threading.Thread(
        target=ejecutar_lista_pasos_background,
        args=(sequence_names,),
        daemon=True,
    )

    hilo.start()
    return True


# ===============================
# CONFIGURACIÓN DE SERVOS
# ===============================

SERVOS_CONFIG = {
    0: {
        "name": "Brazo 1 IZQ Hombro",
        "min": 0,
        "max": 145,
        "current": 135,
        "home": 135,
        "interval": 5,
    },
    1: {
        "name": "Brazo 1 IZQ Codo Rot",
        "min": 0,
        "max": 200,
        "current": 110,
        "home": 110,
        "interval": 5,
    },
    2: {
        "name": "Brazo 1 IZQ Codo Vert",
        "min": 0,
        "max": 75,
        "current": 75,
        "home": 75,
        "interval": 5,
    },
    4: {
        "name": "Brazo 2 DER Hombro",
        "min": 125,
        "max": 270,
        "current": 135,
        "home": 135,
        "interval": 5,
    },
    5: {
        "name": "Brazo 2 DER Codo Rot",
        "min": 0,
        "max": 150,
        "current": 50,
        "home": 50,
        "interval": 5,
    },
    6: {
        "name": "Brazo 2 DER Codo Vert",
        "min": 0,
        "max": 270,
        "current": 0,
        "home": 0,
        "interval": 5,
    },
}


# ===============================
# JSON HELPERS
# ===============================

def load_json_file(path):
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read().strip()

            if not content:
                return {}

            return json.loads(content)

    except json.JSONDecodeError:
        print(f"ERROR: JSON inválido en {path}")
        return {}


def save_json_file(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def load_json_folder(folder_path):
    """
    Lee todos los archivos .json dentro de una carpeta y los combina en un solo diccionario.

    Ejemplo:
        data/pasos/brazo_izquierdo.json
        data/pasos/brazo_derecho.json

    Todos esos pasos se juntan como si estuvieran en un solo JSON.
    """
    merged_data = {}

    if not folder_path.exists():
        return merged_data

    for json_file in sorted(folder_path.glob("*.json")):
        file_data = load_json_file(json_file)

        for key, value in file_data.items():
            if key in merged_data:
                print(f"Advertencia: clave duplicada '{key}' en {json_file}")

            merged_data[key] = value

    return merged_data


def load_steps():
    steps = {}

    # Archivo principal usado por el dashboard al crear o editar pasos.
    steps.update(load_json_file(STEPS_FILE))

    # Archivos separados organizados manualmente dentro de data/pasos/.
    steps.update(load_json_folder(STEPS_DIR))

    return steps


def save_steps(steps):
    # Lo creado/editado desde el dashboard se guarda en el archivo principal.
    save_json_file(STEPS_FILE, steps)


def load_movements():
    movements = {}

    # Archivo principal usado por el dashboard.
    movements.update(load_json_file(MOVEMENTS_FILE))

    # Archivos separados dentro de data/movimientos/.
    movements.update(load_json_folder(MOVEMENTS_DIR))

    return movements


def save_movements(movements):
    save_json_file(MOVEMENTS_FILE, movements)


def load_quick_actions():
    actions = {}

    # Archivo principal usado por el dashboard.
    actions.update(load_json_file(QUICK_ACTIONS_FILE))

    # Archivos separados dentro de data/acciones/.
    actions.update(load_json_folder(QUICK_ACTIONS_DIR))

    return actions


def save_quick_actions(actions):
    save_json_file(QUICK_ACTIONS_FILE, actions)


# ===============================
# COMUNICACIÓN ESP32
# ===============================

def enviar_comando(comando):
    with serial_lock:
        if esp32 and esp32.is_open:
            esp32.write(f"{comando}\n".encode("utf-8"))
            esp32.flush()
            print(f"Enviado a ESP32: {comando}")
        else:
            print(f"[Simulación] Comando: {comando}")


def limitar_angulo(channel, angle):
    config = SERVOS_CONFIG[channel]
    return max(config["min"], min(config["max"], int(angle)))


def calcular_movimiento_suave(channel, nuevo_angulo, custom_interval=None):
    config = SERVOS_CONFIG[channel]
    angulo_actual = config["current"]
    intervalo = custom_interval if custom_interval is not None else config["interval"]

    pulsos_actual = 100 + int((angulo_actual * 420) / 270)
    pulsos_nuevo = 100 + int((nuevo_angulo * 420) / 270)
    diferencia_pulsos = abs(pulsos_nuevo - pulsos_actual)

    tiempo_estimado_ms = diferencia_pulsos * intervalo
    return tiempo_estimado_ms / 1000.0


def ejecutar_paso_individual(step_data):
    channel = int(step_data["channel"])
    target_angle = limitar_angulo(channel, int(step_data["angle"]))
    speed_interval = int(step_data.get("interval", 5))

    enviar_comando(f"V {channel} {speed_interval}")
    SERVOS_CONFIG[channel]["interval"] = speed_interval

    tiempo_viaje = calcular_movimiento_suave(channel, target_angle, speed_interval)

    SERVOS_CONFIG[channel]["current"] = target_angle
    enviar_comando(f"{channel} {target_angle}")

    return tiempo_viaje


def ejecutar_home_general():
    comando_bulk = ""
    tiempos_home = []

    for ch, config in SERVOS_CONFIG.items():
        enviar_comando(f"V {ch} 5")
        config["interval"] = 5

        tiempos_home.append(calcular_movimiento_suave(ch, config["home"], 5))

        config["current"] = config["home"]
        comando_bulk += f"{ch} {config['home']} "

    enviar_comando(comando_bulk.strip())

    return max(tiempos_home) if tiempos_home else 1


def resolver_a_bloques(name, visited=None):
    if visited is None:
        visited = set()

    if isinstance(name, list):
        return resolver_lista_a_bloques(name, "parallel", visited)

    steps = load_steps()
    movements = load_movements()
    quick_actions = load_quick_actions()

    if name == "[ IR A HOME COMIENZO ]":
        home_steps = []
        for ch, config in SERVOS_CONFIG.items():
            home_steps.append({
                "channel": ch,
                "angle": config["home"],
                "interval": 5,
                "delay": 0.0,
                "name": f"HOME-CH{ch}"
            })
        return [home_steps]

    if name in visited:
        print(f"Advertencia: referencia circular detectada en {name}. Se omitió.")
        return []

    if name in quick_actions:
        action = quick_actions[name]
        mode = action.get("mode", "sequence")
        sequence = action.get("sequence", [])

        visited.add(name)
        bloques = resolver_lista_a_bloques(sequence, mode, visited)
        visited.remove(name)
        return bloques

    if name in movements:
        movement = movements[name]
        mode = movement.get("mode", "sequence")
        sequence = movement.get("sequence", [])

        visited.add(name)
        bloques = resolver_lista_a_bloques(sequence, mode, visited)
        visited.remove(name)
        return bloques

    if name in steps:
        step_data = steps[name]
        return [[{
            "channel": int(step_data["channel"]),
            "angle": int(step_data["angle"]),
            "interval": int(step_data.get("interval", 5)),
            "delay": float(step_data.get("delay", 0)),
            "name": name
        }]]

    print(f"Advertencia: no encontrado en JSON: {name}")
    return []


def resolver_lista_a_bloques(sequence, mode, visited=None):
    if visited is None:
        visited = set()

    if not sequence:
        return []

    if mode == "parallel":
        bloques_elementos = [resolver_a_bloques(item, visited.copy()) for item in sequence]
        max_len = max(len(b) for b in bloques_elementos) if bloques_elementos else 0

        bloques_combinados = []
        for i in range(max_len):
            bloque_paralelo = []
            for b in bloques_elementos:
                if i < len(b):
                    bloque_paralelo.extend(b[i])
            bloques_combinados.append(bloque_paralelo)
        return bloques_combinados
    else:
        bloques_concatenados = []
        for item in sequence:
            bloques_concatenados.extend(resolver_a_bloques(item, visited))
        return bloques_concatenados


def ejecutar_bloque_pasos(bloque_pasos, wait_seconds=None):
    if not bloque_pasos:
        return

    # Evitamos colisiones filtrando canales repetidos en el mismo bloque paralelo (el último prevalece)
    canales_unicos = {}
    for step in bloque_pasos:
        ch = step["channel"]
        if ch in canales_unicos:
            print(f"Advertencia: Conflicto de canal detectado para el canal {ch}. Se sobrescribirá con el paso '{step.get('name')}'.")
        canales_unicos[ch] = step
    bloque_pasos_filtrado = list(canales_unicos.values())

    comando_bulk = []
    tiempos_estimados = []
    delays_individuales = []

    for step in bloque_pasos_filtrado:
        channel = step["channel"]
        angle = step["angle"]
        interval = step["interval"]
        delay = step["delay"]
        name = step.get("name", f"Ch {channel}")

        angle = limitar_angulo(channel, angle)

        tiempo_estimado = calcular_movimiento_suave(channel, angle, interval)
        tiempos_estimados.append(tiempo_estimado)
        delays_individuales.append(delay)

        print(
            f"Bloque paralelo -> {name}: "
            f"V {channel} {interval} | {channel} {angle} | delay={delay}"
        )

        enviar_comando(f"V {channel} {interval}")

        SERVOS_CONFIG[channel]["interval"] = interval
        SERVOS_CONFIG[channel]["current"] = angle

        comando_bulk.append(f"{channel} {angle}")

    if comando_bulk:
        enviar_comando(" ".join(comando_bulk))

    if wait_seconds is None:
        wait_seconds = 0

    wait_seconds = float(wait_seconds)

    if wait_seconds <= 0 and tiempos_estimados:
        wait_seconds = max(tiempos_estimados) + max(delays_individuales or [0])

    if wait_seconds > 0:
        print(f"Esperando bloque paralelo: {round(wait_seconds, 2)}s")
        time.sleep(wait_seconds)


def ejecutar_bloque_parallel(sequence_names, wait_seconds=None):
    """
    Ejecuta varios pasos o movimientos en paralelo.
    """
    bloques = resolver_lista_a_bloques(sequence_names, "parallel")
    if not bloques:
        return

    for idx, bloque in enumerate(bloques):
        is_last = (idx == len(bloques) - 1)
        block_wait = wait_seconds if is_last else None
        ejecutar_bloque_pasos(bloque, wait_seconds=block_wait)


def ejecutar_lista_pasos(sequence_names, visited=None):
    if visited is None:
        visited = set()

    steps = load_steps()
    movements = load_movements()
    quick_actions = load_quick_actions()

    for name in sequence_names:
        if isinstance(name, list):
            ejecutar_bloque_parallel(name)
            continue

        if name == "[ IR A HOME COMIENZO ]":
            tiempo_home = ejecutar_home_general()
            time.sleep(tiempo_home)
            continue

        if name in visited:
            print(f"Advertencia: referencia circular detectada en {name}. Se omitió.")
            continue

        if name in quick_actions:
            action = quick_actions[name]
            mode = action.get("mode", "sequence")
            sequence = action.get("sequence", [])
            wait_value = action.get("wait", None)

            print(f"Ejecutando acción rápida: {name} | mode={mode}")

            visited.add(name)

            if mode == "parallel":
                ejecutar_bloque_parallel(sequence, wait_value)
            else:
                ejecutar_lista_pasos(sequence, visited)

            visited.remove(name)
            continue

        if name in movements:
            movement = movements[name]
            mode = movement.get("mode", "sequence")
            sequence = movement.get("sequence", [])
            wait_value = movement.get("wait", None)

            print(f"Ejecutando movimiento creado: {name} | mode={mode}")

            visited.add(name)

            if mode == "parallel":
                ejecutar_bloque_parallel(sequence, wait_value)
            else:
                ejecutar_lista_pasos(sequence, visited)

            visited.remove(name)
            continue

        if name in steps:
            step_data = steps[name]

            channel = int(step_data["channel"])
            angle = int(step_data["angle"])
            interval = int(step_data.get("interval", 5))
            delay = float(step_data.get("delay", 0))

            angle = limitar_angulo(channel, angle)

            print(
                f"JSON directo -> {name}: "
                f"V {channel} {interval} | {channel} {angle} | delay={delay}"
            )

            enviar_comando(f"V {channel} {interval}")
            enviar_comando(f"{channel} {angle}")

            SERVOS_CONFIG[channel]["interval"] = interval
            SERVOS_CONFIG[channel]["current"] = angle

            if delay > 0:
                time.sleep(delay)

            continue

        print(f"Advertencia: no encontrado en JSON: {name}")


# ===============================
# RUTAS WEB
# ===============================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(
        {
            "serial_port": SERIAL_PORT,
            "serial_connected": bool(esp32 and esp32.is_open),
            "sequence_running": sequence_running,
            "servos": SERVOS_CONFIG,
            "steps": load_steps(),
            "movements": load_movements(),
            "quick_actions": load_quick_actions(),
        }
    )


@app.route("/api/move", methods=["POST"])
def move_servo():
    data = request.json

    channel = int(data["channel"])
    action = data["action"]

    config = SERVOS_CONFIG[channel]
    target_angle = config["current"]

    if action == "step_up":
        target_angle = min(config["max"], config["current"] + 1)
    elif action == "step_down":
        target_angle = max(config["min"], config["current"] - 1)
    elif action == "angle":
        target_angle = limitar_angulo(channel, int(data["value"]))

    tiempo_segundos = calcular_movimiento_suave(channel, target_angle)

    config["current"] = target_angle
    enviar_comando(f"{channel} {target_angle}")

    return jsonify(
        {
            "status": "success",
            "current_angle": target_angle,
            "estimated_time_seconds": round(tiempo_segundos, 3),
        }
    )


@app.route("/api/home", methods=["POST"])
def go_home():
    started = iniciar_secuencia_background(["[ IR A HOME COMIENZO ]"])

    if not started:
        return jsonify(
            {
                "status": "busy",
                "message": "Ya hay una secuencia ejecutándose",
            }
        ), 409

    return jsonify(
        {
            "status": "started",
            "message": "Home enviado al robot",
        }
    )


# ===============================
# PASOS INDIVIDUALES
# ===============================

@app.route("/api/steps", methods=["POST"])
def save_step():
    data = request.json

    step_name = data.get("name", "").strip()
    channel = int(data["channel"])
    angle = limitar_angulo(channel, int(data["angle"]))
    interval = int(data.get("interval", 5))
    delay = float(data.get("delay", 0.5))

    if not step_name:
        return jsonify({"status": "error", "message": "Nombre vacío"}), 400

    steps = load_steps()
    steps[step_name] = {
        "channel": channel,
        "angle": angle,
        "interval": interval,
        "delay": delay,
    }

    save_steps(steps)

    return jsonify({"status": "success", "steps": steps})


@app.route("/api/steps/delete", methods=["POST"])
def delete_step():
    data = request.json
    step_name = data.get("name", "").strip()

    steps = load_steps()

    if step_name in steps:
        del steps[step_name]
        save_steps(steps)
        return jsonify({"status": "success", "steps": steps})

    return jsonify({"status": "error", "message": "Paso no encontrado"}), 400


@app.route("/api/steps/run_single", methods=["POST"])
def run_single_step():
    data = request.json
    step_name = data.get("name", "").strip()

    steps = load_steps()

    if step_name in steps:
        started = iniciar_secuencia_background([step_name])

        if not started:
            return jsonify(
                {
                    "status": "busy",
                    "message": "Ya hay una secuencia ejecutándose",
                }
            ), 409

        return jsonify(
            {
                "status": "started",
                "message": "Paso enviado al robot",
            }
        )

    return jsonify({"status": "error", "message": "Paso no encontrado"}), 400


# ===============================
# SECUENCIA GENERAL
# ===============================

@app.route("/api/sequence/run", methods=["POST"])
def run_sequence():
    data = request.json
    sequence_names = data.get("sequence", [])

    if not sequence_names:
        return jsonify({"status": "error", "message": "Secuencia vacía"}), 400

    started = iniciar_secuencia_background(sequence_names)

    if not started:
        return jsonify(
            {
                "status": "busy",
                "message": "Ya hay una secuencia ejecutándose",
            }
        ), 409

    return jsonify(
        {
            "status": "started",
            "message": "Secuencia enviada al robot",
        }
    )


# ===============================
# MOVIMIENTOS CREADOS
# ===============================

@app.route("/api/movements/save", methods=["POST"])
def save_movement():
    data = request.json

    movement_name = data.get("name", "").strip()
    sequence = data.get("sequence", [])
    save_mode = data.get("save_mode", data.get("mode", "replace"))
    execution_mode = data.get("execution_mode", data.get("run_mode", None))
    wait = data.get("wait", None)

    if save_mode in ["parallel", "sequence"]:
        execution_mode = save_mode
        save_mode = "replace"

    if not movement_name:
        return jsonify({"status": "error", "message": "Nombre vacío"}), 400

    if not sequence:
        return jsonify({"status": "error", "message": "Secuencia vacía"}), 400

    movements = load_movements()
    existing = movements.get(movement_name, {})

    if save_mode == "keep" and movement_name in movements:
        return jsonify(
            {
                "status": "exists",
                "message": "El movimiento ya existe y se mantuvo sin cambios",
                "movements": movements,
            }
        )

    if save_mode == "append" and movement_name in movements:
        old_sequence = movements[movement_name].get("sequence", [])
        movements[movement_name]["sequence"] = old_sequence + sequence
        movements[movement_name]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        if execution_mode:
            movements[movement_name]["mode"] = execution_mode
        if wait is not None:
            movements[movement_name]["wait"] = float(wait)
    else:
        movement_data = {
            "name": movement_name,
            "sequence": sequence,
            "created_at": existing.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if execution_mode:
            movement_data["mode"] = execution_mode
        elif "mode" in existing:
            movement_data["mode"] = existing["mode"]

        if wait is not None:
            movement_data["wait"] = float(wait)
        elif "wait" in existing:
            movement_data["wait"] = existing["wait"]

        movements[movement_name] = movement_data

    save_movements(movements)

    return jsonify({"status": "success", "movements": movements})


@app.route("/api/movements/delete", methods=["POST"])
def delete_movement():
    data = request.json
    movement_name = data.get("name", "").strip()

    movements = load_movements()

    if movement_name in movements:
        del movements[movement_name]
        save_movements(movements)
        return jsonify({"status": "success", "movements": movements})

    return jsonify({"status": "error", "message": "Movimiento no encontrado"}), 400


@app.route("/api/movements/run", methods=["POST"])
def run_movement():
    data = request.json
    movement_name = data.get("name", "").strip()

    movements = load_movements()

    if movement_name not in movements:
        return jsonify({"status": "error", "message": "Movimiento no encontrado"}), 400

    started = iniciar_secuencia_background([movement_name])

    if not started:
        return jsonify(
            {
                "status": "busy",
                "message": "Ya hay una secuencia ejecutándose",
            }
        ), 409

    return jsonify(
        {
            "status": "started",
            "movement": movement_name,
            "message": "Movimiento enviado al robot",
        }
    )


# ===============================
# ACCIONES RÁPIDAS EDITABLES
# ===============================

@app.route("/api/quick_actions/save", methods=["POST"])
def save_quick_action():
    data = request.json

    action_name = data.get("name", "").strip()
    sequence = data.get("sequence", [])
    save_mode = data.get("save_mode", data.get("mode", "replace"))
    execution_mode = data.get("execution_mode", data.get("run_mode", None))
    wait = data.get("wait", None)

    if save_mode in ["parallel", "sequence"]:
        execution_mode = save_mode
        save_mode = "replace"

    if not action_name:
        return jsonify({"status": "error", "message": "Nombre vacío"}), 400

    if not sequence:
        return jsonify({"status": "error", "message": "Secuencia vacía"}), 400

    actions = load_quick_actions()
    existing = actions.get(action_name, {})

    if save_mode == "keep" and action_name in actions:
        return jsonify(
            {
                "status": "exists",
                "message": "La acción rápida ya existe y se mantuvo sin cambios",
                "quick_actions": actions,
            }
        )

    if save_mode == "append" and action_name in actions:
        old_sequence = actions[action_name].get("sequence", [])
        actions[action_name]["sequence"] = old_sequence + sequence
        actions[action_name]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        if execution_mode:
            actions[action_name]["mode"] = execution_mode
        if wait is not None:
            actions[action_name]["wait"] = float(wait)
    else:
        action_data = {
            "name": action_name,
            "sequence": sequence,
            "created_at": existing.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if execution_mode:
            action_data["mode"] = execution_mode
        elif "mode" in existing:
            action_data["mode"] = existing["mode"]

        if wait is not None:
            action_data["wait"] = float(wait)
        elif "wait" in existing:
            action_data["wait"] = existing["wait"]

        actions[action_name] = action_data

    save_quick_actions(actions)

    return jsonify({"status": "success", "quick_actions": actions})


@app.route("/api/quick_actions/delete", methods=["POST"])
def delete_quick_action():
    data = request.json
    action_name = data.get("name", "").strip()

    actions = load_quick_actions()

    if action_name in actions:
        del actions[action_name]
        save_quick_actions(actions)
        return jsonify({"status": "success", "quick_actions": actions})

    return jsonify({"status": "error", "message": "Acción rápida no encontrada"}), 400


@app.route("/api/quick_actions/run", methods=["POST"])
def run_quick_action():
    data = request.json
    action_name = data.get("name", "").strip()

    actions = load_quick_actions()

    if action_name not in actions:
        return jsonify({"status": "error", "message": "Acción rápida no encontrada"}), 400

    started = iniciar_secuencia_background([action_name])

    if not started:
        return jsonify(
            {
                "status": "busy",
                "message": "Ya hay una secuencia ejecutándose",
            }
        ), 409

    return jsonify(
        {
            "status": "started",
            "action": action_name,
            "message": "Acción rápida enviada al robot",
        }
    )


# ===============================
# MAIN
# ===============================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,
        threaded=True,
    )
