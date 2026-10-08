# Protocolo serie MAXCIM_BASE v1

Arduino Nano clásico ATmega328P, USB 115200, ASCII. Cada línea es
`payload*CCCC\n`; CCCC es CRC16-CCITT-FALSE hexadecimal (polinomio 0x1021,
inicio 0xFFFF) calculado sobre los bytes ASCII del payload. Longitud máxima
de la línea: 180 bytes. Una trama inválida no renueva el watchdog.

Host → Nano:

- `H session`: identifica y establece sesión uint32 no cero; detiene motores,
  reinicia las secuencias de mando/telemetría, conserva contadores y fallos.
- `V session seq left_pps right_pps`: velocidad de cada rueda en pulsos FG/s,
  finita y no negativa. Secuencia uint32 estrictamente creciente desde 1.
- `D session seq left_permille right_permille`: prueba de banco de PWM,
  enteros 0..150, solo con cableado confirmado; no usa PI. No es odometría.
- `S session seq`: parada inmediata. También renueva watchdog.

Nano → host:

- `I session MAXCIM_BASE 1 flags`: identidad y versión, después de H.
- `T session seq uptime_ms left_ticks right_ticks left_pps right_pps left_permille right_permille flags ack_seq`:
  muestra a 20 Hz. Contadores FG uint32 absolutos y uptime uint32 con
  desbordamiento modular; secuencia de muestra uint32. PPS son mediciones
  no negativas. `ack_seq` es el último mando aceptado.

Flags: 1=cableado no confirmado (salidas bloqueadas), 2=watchdog vencido,
4=fallo FG/atasco enclavado, 8=calibración del control PI pendiente.
Flags 1/4 bloquean toda marcha; 8 bloquea V pero permite prueba D si el
cableado está confirmado. Un fallo 4 exige detener y revisar antes de
reiniciar físicamente el Nano. H no borra ese fallo. Una nueva H siempre
detiene antes de contestar, y solo acepta mandos de esa sesión.

Watchdog local 350 ms, parada al arranque, PWM alto=parado, BRAKE bajo=parado.
Ante consigna cero, ambas ruedas se controlan individualmente: una puede
quedar frenada y la otra avanzar. FG se cuenta por flanco FALLING, un flanco
por pulso; dos entradas independientes D2/D3. No invertir el signo de FG
según un cmd_vel: este protocolo solo controla avance físico confirmado.
Los contadores miden pulsos reales, pero un FG simple no detecta el sentido
de un empuje externo ni deslizamiento.

Pines propuestos para instalación nueva (no describen el cableado existente):
izquierda FG D2, PWM D9, CW/CCW D4, BRAKE D8;
derecha FG D3, PWM D10, CW/CCW D7, BRAKE D12.
Timer1 genera 25 kHz en D9/D10, sin alterar Timer0/millis().

El puente ROS requiere identidad correcta, telemetría reciente, calibración
confirmada y habilitación explícita para V. Al perder telemetría, reiniciarse
el Nano o caducar cmd_vel, detiene y deshabilita; no reanuda por sí solo.
No hay comandos de inversión: se rechaza cualquier combinación que requiera
una rueda hacia atrás. Reducir proporcionalmente ambas ruedas al saturar
conserva el radio de giro cuando es realizable.
