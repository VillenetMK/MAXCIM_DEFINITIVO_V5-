#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

/*
  ============================================================
  CONTROL DE 2 BRAZOS ROBÓTICOS CON ESP32 + PCA9685
  ============================================================

  Este código controla 6 servos usando un módulo PCA9685.

  Comunicación:
  - El ESP32 recibe comandos por Serial desde Python/Flask.
  - Python manda comandos como:
      V 0 5
      0 135
      0 135 1 110 2 75

  Hardware:
  - ESP32
  - Módulo PCA9685
  - Servos conectados en canales:
      Brazo izquierdo: canales 0, 1, 2
      Brazo derecho:   canales 4, 5, 6
*/


// ============================================================
// PINES I2C DEL ESP32
// ============================================================

// Pin SDA del bus I2C
#define I2C_SDA 21

// Pin SCL del bus I2C
#define I2C_SCL 22


// ============================================================
// CONFIGURACIÓN DEL PCA9685
// ============================================================

// Se crea el objeto del controlador PCA9685.
// Dirección I2C normal del PCA9685: 0x40
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40, Wire);


// ============================================================
// CONFIGURACIÓN GENERAL DE LOS SERVOS
// ============================================================

// Pulso mínimo aproximado para el servo.
// Este valor representa 0 grados.
#define SERVOMIN 100

// Pulso máximo aproximado para el servo.
// Este valor representa 270 grados.
#define SERVOMAX 520

// Intervalo por defecto entre cada paso del servo.
// Mientras mayor sea el número, más lento se mueve.
// Mientras menor sea, más rápido se mueve.
#define SERVO_INTERVAL 5


// ============================================================
// ESTRUCTURA PARA GUARDAR EL ESTADO DE CADA SERVO
// ============================================================

/*
  Esta estructura guarda toda la información necesaria
  para controlar cada servo de forma suave.

  channel:
    Canal del PCA9685 donde está conectado el servo.

  currentPulse:
    Pulso actual del servo.

  targetPulse:
    Pulso objetivo al que debe llegar.

  lastUpdate:
    Último tiempo en milisegundos en que se actualizó el servo.

  interval:
    Tiempo entre pasos.
    0 significa movimiento instantáneo.
    5, 10, 15, etc. significa movimiento suave/lento.

  name:
    Nombre descriptivo del servo.

  initialAngle:
    Ángulo inicial al que se moverá el servo cuando el sistema arranca.
*/
struct ServoState {
  int channel;
  int currentPulse;
  int targetPulse;
  unsigned long lastUpdate;
  unsigned long interval;
  const char* name;
  int initialAngle;
};


// ============================================================
// DISTRIBUCIÓN DE CANALES
// ============================================================

/*
  Brazo 1 izquierdo:
    CH0 = Hombro
    CH1 = Codo rotación
    CH2 = Codo vertical

  Brazo 2 derecho:
    CH4 = Hombro
    CH5 = Codo rotación
    CH6 = Codo vertical

  Límites aproximados usados en tu calibración:

  Brazo 1 izquierdo:
    Eje 1 / CH0 = 0° a 145°, centrado en 135°
    Eje 2 / CH1 = 0° a 110°, centrado en 110°
    Eje 3 / CH2 = 0° a 75°, centrado en 75°

  Brazo 2 derecho:
    Eje 1 / CH4 = 135° a 275°, centrado en 135°
    Eje 2 / CH5 = 60° a 150°, centrado en 50°
    Eje 3 / CH6 = 0° a 80°, centrado en 0°
*/


// ============================================================
// LISTA DE SERVOS CONTROLADOS
// ============================================================

/*
  Cada servo empieza con currentPulse = 310.

  310 es un pulso intermedio que se usa como posición inicial asumida.
  Luego, durante el homing suave, cada servo se mueve a su initialAngle.
*/
ServoState servos[6] = {
  // {channel, currentPulse, targetPulse, lastUpdate, interval, name, initialAngle}

  {0, 310, 310, 0, SERVO_INTERVAL, "Brazo 1 Hombro (Ch0)", 135},
  {1, 310, 310, 0, SERVO_INTERVAL, "Brazo 1 Codo Rot (Ch1)", 110},
  {2, 310, 310, 0, SERVO_INTERVAL, "Brazo 1 Codo Vert (Ch2)", 75},

  {4, 310, 310, 0, SERVO_INTERVAL, "Brazo 2 Hombro (Ch4)", 135},
  {5, 310, 310, 0, SERVO_INTERVAL, "Brazo 2 Codo Rot (Ch5)", 50},
  {6, 310, 310, 0, SERVO_INTERVAL, "Brazo 2 Codo Vert (Ch6)", 0}
};


// ============================================================
// CONVERTIR ÁNGULO A PULSO PWM
// ============================================================

/*
  Convierte un ángulo de 0° a 270° en un pulso válido para PCA9685.

  Ejemplo:
    angleToPulse(0)   -> SERVOMIN
    angleToPulse(270) -> SERVOMAX

  También limita el ángulo para evitar valores fuera de rango.
*/
int angleToPulse(int angle) {
  // Si el ángulo es menor a 0, se limita a 0
  if (angle < 0) angle = 0;

  // Si el ángulo es mayor a 270, se limita a 270
  if (angle > 270) angle = 270;

  // Convierte el ángulo al rango de pulsos del servo
  return map(angle, 0, 270, SERVOMIN, SERVOMAX);
}


// ============================================================
// SETUP PRINCIPAL
// ============================================================

void setup() {
  // Inicia comunicación serial con la PC/Raspberry/Jetson
  Serial.begin(115200);

  // Espera para que el monitor serial o Python tenga tiempo de conectarse
  delay(2000);

  /*
    Mensajes de ayuda comentados.
    Se pueden activar si quieres ver instrucciones por Serial.

    Comandos disponibles:
      Mover servo:
        <canal> <angulo>
        Ejemplo:
          0 135

      Mover varios servos:
        0 135 1 110 2 75

      Cambiar velocidad:
        V <canal> <intervalo>
        Ejemplo:
          V 0 10
  */

  /*
  Serial.println("--- GUIA Control de Angulo de Brazos 270° ---");
  Serial.println("Comandos disponibles:");
  Serial.println(" - Mover:   <canal> <angulo>            (ej. '0 135' o '0 90 1 180')");
  Serial.println(" - Velocid: V <canal> <intervalo>       (ej. 'V 0 10' o 'V 1 0')");
  Serial.println("   (Intervalo en ms por paso. 0=muy rápido, 10=lento. Defecto=3)");
  */

  /*
    Inicialización del bus I2C con los pines definidos.
    Si falla, el programa se queda detenido.
  */
  if (Wire.begin(I2C_SDA, I2C_SCL)) {
    Serial.println("I2C inicializado correctamente.");
  } else {
    Serial.println("Fallo en la inicialización de I2C!");

    delay(250);

    // Detiene el programa si el I2C falla
    while (true);
  }

  // Inicializa el PCA9685
  pwm.begin();

  // Configura frecuencia típica para servos: 50 Hz
  pwm.setPWMFreq(50);


  // ==========================================================
  // HOMING SUAVE INICIAL
  // ==========================================================

  /*
    Paso 1:
    Se coloca cada servo en una posición asumida inicial.
    Luego se calcula su destino real usando initialAngle.
  */
  for (int i = 0; i < 6; i++) {
    // Engancha el servo en el pulso actual asumido
    pwm.setPWM(servos[i].channel, 0, servos[i].currentPulse);

    // Calcula el pulso objetivo según el ángulo inicial configurado
    servos[i].targetPulse = angleToPulse(servos[i].initialAngle);

    // Guarda el tiempo actual para controlar movimiento suave
    servos[i].lastUpdate = millis();
  }

  /*
    Paso 2:
    Se mueve cada servo poco a poco hasta llegar a su posición inicial.

    Esto evita que el robot haga movimientos bruscos al encender.
  */
  Serial.println("Iniciando Homing suave a posiciones de inicio...");

  bool allHome = false;

  while (!allHome) {
    allHome = true;

    for (int i = 0; i < 6; i++) {
      // Actualiza el servo un paso hacia su objetivo
      updateServo(servos[i]);

      // Si algún servo aún no llegó, el homing continúa
      if (servos[i].currentPulse != servos[i].targetPulse) {
        allHome = false;
      }
    }

    // Pequeña pausa para no saturar el ESP32
    delay(1);
  }

  Serial.println("PCA9685 listo. Ambos brazos en sus posiciones iniciales configuradas.");
}


// ============================================================
// ACTUALIZAR SERVO CON MOVIMIENTO SUAVE
// ============================================================

/*
  Esta función mueve un servo hacia su targetPulse.

  Si interval = 0:
    Se mueve instantáneamente.

  Si interval > 0:
    Se mueve de 1 pulso en 1 pulso cada X milisegundos.
*/
void updateServo(ServoState &s) {
  // Si el servo ya llegó al objetivo, no hace nada
  if (s.currentPulse == s.targetPulse) return;

  // Movimiento instantáneo si interval es 0
  if (s.interval == 0) {
    s.currentPulse = s.targetPulse;
    pwm.setPWM(s.channel, 0, s.currentPulse);
    return;
  }

  // Tiempo actual del ESP32
  unsigned long currentMillis = millis();

  // Verifica si ya pasó el tiempo suficiente para mover otro paso
  if (currentMillis - s.lastUpdate >= s.interval) {
    s.lastUpdate = currentMillis;

    // Si el pulso actual es menor al objetivo, aumenta
    if (s.currentPulse < s.targetPulse) {
      s.currentPulse++;
    }

    // Si el pulso actual es mayor al objetivo, disminuye
    else {
      s.currentPulse--;
    }

    // Envía el nuevo pulso al PCA9685
    pwm.setPWM(s.channel, 0, s.currentPulse);
  }
}


// ============================================================
// BUFFER SERIAL
// ============================================================

/*
  serialBuffer:
    Guarda temporalmente el comando que llega por Serial.

  bufferIndex:
    Indica cuántos caracteres se han guardado.
*/
char serialBuffer[64];
int bufferIndex = 0;


// ============================================================
// PROCESAR COMANDOS SERIAL
// ============================================================

/*
  Esta función lee comandos enviados desde Python/Flask.

  Tipos de comandos aceptados:

  1) Cambiar velocidad:
      V canal intervalo

     Ejemplo:
      V 0 5

     Significa:
      Canal 0 se moverá con 5 ms por paso.

  2) Mover un servo:
      canal angulo

     Ejemplo:
      0 135

  3) Mover varios servos al mismo tiempo:
      canal angulo canal angulo canal angulo

     Ejemplo:
      0 135 1 110 2 75 4 135 5 50 6 0
*/
void handleSerial() {
  // Mientras haya datos disponibles en el puerto serial
  while (Serial.available()) {
    char c = Serial.read();

    /*
      Si llegó fin de línea, retorno de carro,
      o el buffer está lleno, se procesa el comando completo.
    */
    if (c == '\n' || c == '\r' || bufferIndex >= 63) {

      // Solo procesa si el buffer tiene contenido
      if (bufferIndex > 0) {
        // Cierra la cadena con carácter nulo
        serialBuffer[bufferIndex] = '\0';


        // ====================================================
        // COMANDO DE VELOCIDAD
        // ====================================================

        /*
          Si el comando empieza con V o v,
          se interpreta como cambio de velocidad.

          Formato:
            V canal intervalo

          Ejemplo:
            V 0 10
        */
        if (serialBuffer[0] == 'V' || serialBuffer[0] == 'v') {
          int channel, interval;

          /*
            serialBuffer + 1 ignora la letra V
            y lee los dos números siguientes.
          */
          if (sscanf(serialBuffer + 1, "%d %d", &channel, &interval) == 2) {

            // Busca el servo que tenga ese canal
            for (int i = 0; i < 6; i++) {
              if (servos[i].channel == channel) {
                // Cambia el intervalo de movimiento del servo
                servos[i].interval = interval;

                // Respuesta de confirmación
                Serial.print("ACK: Vel ");
                Serial.print(servos[i].name);
                Serial.print(" ajustada a ");
                Serial.print(interval);
                Serial.println(" ms/paso");

                break;
              }
            }
          }

          // Si el comando V está mal escrito
          else {
            Serial.println("ERR: Comando V mal formado. Ej: V 0 10");
          }
        }


        // ====================================================
        // COMANDO DE MOVIMIENTO
        // ====================================================

        else {
          /*
            Si no empieza con V, se interpreta como movimiento.

            Puede recibir:
              0 135

            O varios pares:
              0 135 1 110 2 75 4 135
          */

          char* ptr = serialBuffer;
          int channel, angle, bytesRead;

          /*
            sscanf lee pares de números:
              canal angulo

            %n guarda cuántos caracteres se leyeron,
            para avanzar el puntero y leer el siguiente par.
          */
          while (sscanf(ptr, "%d %d%n", &channel, &angle, &bytesRead) == 2) {
            // Avanza al siguiente par de datos dentro del buffer
            ptr += bytesRead;

            /*
              Valida que el canal pertenezca a los brazos:
                Brazo izquierdo: 0, 1, 2
                Brazo derecho:   4, 5, 6

              El canal 3 no se usa.
            */
            if ((channel >= 0 && channel <= 2) || (channel >= 4 && channel <= 6)) {

              // Convierte el ángulo recibido a pulso PWM
              int pulse = angleToPulse(angle);

              // Busca el servo correspondiente a ese canal
              for (int i = 0; i < 6; i++) {
                if (servos[i].channel == channel) {

                  /*
                    No mueve instantáneamente aquí.
                    Solo actualiza el objetivo.

                    El movimiento real lo hace updateServo()
                    dentro del loop principal.
                  */
                  servos[i].targetPulse = pulse;

                  // Respuesta de confirmación
                  Serial.print("ACK: Movido ");
                  Serial.print(servos[i].name);
                  Serial.print(" a ");
                  Serial.println(angle);

                  break;
                }
              }
            }
          }
        }

        // Limpia el buffer después de procesar el comando
        bufferIndex = 0;
      }
    }

    // Si todavía no llegó fin de línea, sigue guardando caracteres
    else {
      serialBuffer[bufferIndex++] = c;
    }
  }
}


// ============================================================
// LOOP PRINCIPAL
// ============================================================

void loop() {
  /*
    Primero revisa si llegó algún comando por Serial.
    Esto permite recibir órdenes desde Python/Flask.
  */
  handleSerial();

  /*
    Luego actualiza todos los servos.
    Cada servo avanza poco a poco hacia su targetPulse.
  */
  for (int i = 0; i < 6; i++) {
    updateServo(servos[i]);
  }
}
