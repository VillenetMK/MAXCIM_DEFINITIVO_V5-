import serial
import serial.tools.list_ports
import time

def es_rplidar(puerto_device, baudrate=115200):
    """
    Envía el comando GET_HEALTH (0xA5 0x52) al puerto y verifica si responde
    con la cabecera estándar del RPLIDAR (0xA5 0x5A).
    """
    try:
        # Abrir el puerto serie de forma temporal
        with serial.Serial(puerto_device, baudrate=baudrate, timeout=1) as ser:
            time.sleep(0.1)
            ser.reset_input_buffer()

            # Comando RPLIDAR GET_HEALTH: 0xA5 0x52
            comando_get_health = bytearray([0xA5, 0x52])
            ser.write(comando_get_health)

            time.sleep(0.15)
            respuesta = ser.read(7) # Lee los primeros 7 bytes del descriptor de respuesta

            # Verificar si la respuesta empieza con los bytes del protocolo Slamtec (0xA5, 0x5A)
            if len(respuesta) >= 2 and respuesta[0] == 0xA5 and respuesta[1] == 0x5A:
                return True
    except Exception:
        pass

    return False

def clasificar_puertos():
    puertos_disponibles = [p.device for p in serial.tools.list_ports.comports() if "ttyUSB" in p.device]

    puerto_rplidar = None
    puerto_esp32 = None

    print(f"Puertos ttyUSB detectados: {puertos_disponibles}")

    for puerto in puertos_disponibles:
        print(f"Probando {puerto}...")
        if es_rplidar(puerto):
            puerto_rplidar = puerto
            print(f"  -> ¡RPLIDAR detectado en {puerto}!")
        else:
            # Por descarte (o handshake con tu propio firmware de ESP32)
            puerto_esp32 = puerto
            print(f"  -> Asignado como ESP32 a {puerto}")

    return puerto_rplidar, puerto_esp32

if __name__ == "__main__":
    rplidar_port, esp32_port = clasificar_puertos()

    print("\n--- RESULTADO FINAL ---")
    print(f"RPLIDAR PORT: {rplidar_port}")
    #print(f"ESP32 PORT  : {esp32_port}")
