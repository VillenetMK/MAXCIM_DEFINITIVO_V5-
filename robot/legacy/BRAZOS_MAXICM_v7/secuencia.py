import serial
import time
import sys

# Configura tu puerto serie aquí.
# En Linux suele ser /dev/ttyUSB0 o /dev/ttyACM0
# En Windows sería usualmente 'COM3' o 'COM4'
PORT = '/dev/ttyUSB0'
BAUD_RATE = 115200

def send_command(ser, cmd):
    print(f"Enviando: {cmd}")
    ser.write((cmd + '\n').encode('utf-8'))
    # Pequeña pausa para no saturar el buffer
    time.sleep(0.1)

    # Leer y mostrar lo que responde el ESP32
    while ser.in_waiting > 0:
        response = ser.readline().decode('utf-8', errors='ignore').strip()
        if response:
            print(f"ESP32 responde: {response}")

def main():
    try:
        print(f"Conectando a {PORT} a {BAUD_RATE} baudios...")
        # El timeout=1 hace que la lectura no se bloquee eternamente
        ser = serial.Serial(PORT, BAUD_RATE, timeout=1)

        # Al abrir el puerto serial, el ESP32 suele reiniciarse.
        # Le damos tiempo para que termine su Setup()
        print("Esperando a que el ESP32 inicie...")
        time.sleep(3)

        # Limpiamos cualquier mensaje de arranque que haya enviado el ESP32
        while ser.in_waiting > 0:
            ser.readline()

        print("\n¡Conexión establecida! Iniciando bucle de animación.\n(Presiona Ctrl+C para detener)")

        canal = 0

        while True:
            # --- FASE 1: Movimiento RAPIDO ---
            print("--- Fase 1: Movimiento Rápido ---")
            send_command(ser, f"V {canal} 0")   # Velocidad máxima (instantánea o 0 ms)
            send_command(ser, f"{canal} 270")   # Mover a 270 grados
            time.sleep(1.5)                     # Esperar 1.5 segundos
            print("")

            # --- FASE 2: Movimiento MUY LENTO ---
            print("--- Fase 2: Movimiento Muy Lento ---")
            send_command(ser, f"V {canal} 15")  # Velocidad muy lenta (15 ms por paso)
            send_command(ser, f"{canal} 0")     # Regresar a 0 grados
            time.sleep(6)                       # Esperamos más porque el movimiento tarda
            print("")

            # --- FASE 3: Movimiento NORMAL ---
            print("--- Fase 3: Movimiento Normal (Centro) ---")
            send_command(ser, f"V {canal} 4")   # Velocidad normal (4 ms por paso)
            send_command(ser, f"{canal} 135")   # Posición central
            time.sleep(2)                       # Esperar 2 segundos
            print("")

    except serial.SerialException as e:
        print(f"Error abriendo el puerto serial: {e}")
        print(f"Ayuda: Verifica que {PORT} sea el correcto y que no tengas abierto el Monitor Serie de Arduino.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nSecuencia detenida por el usuario (Ctrl+C).")
    finally:
        # Nos aseguramos de cerrar el puerto correctamente al salir
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Puerto serial cerrado.")

if __name__ == "__main__":
    main()
