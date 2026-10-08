# Conexiones para una instalación nueva

Esta es una **propuesta de cableado** compatible con el firmware. Las fotos del
montaje existente no permiten asegurar a qué pines llega cada conductor.
Identificar por función y continuidad; no identificar solo por color.
Realizar el cableado sin alimentación de motores.

## Señales de los dos motores

| Función del cable | Motor izquierdo | Motor derecho | Observación |
|---|---|---|---|
| VCC | Positivo de fuente de motores | Positivo de fuente de motores | 12 V o 24 V solo según etiqueta de la variante instalada. |
| GND | Retorno a fuente, común con GND Nano | Retorno a fuente, común con GND Nano | La corriente del motor no debe circular a través de la placa Nano. |
| FG | D2 | D3 | Dos señales independientes, cada una con pull-up 4.7 kΩ a 5 V del Nano según ficha. |
| PWM | D9 | D10 | Dos salidas independientes, 25 kHz, nivel bajo activo. |
| CW/CCW | D4 | D7 | Nivel fijo configurado para el sentido permitido de cada unidad. |
| BRAKE | D8 | D12 | Bajo detiene; alto permite marcha. |

Conectar **USB del Nano a USB de la Raspberry**. Nano clásico y sus señales
usan 5 V; la Raspberry se comunica por USB. No conectar FG de 5 V a GPIO de
3.3 V de la Raspberry. No llevar VCC de motor al pin 5V del Nano ni alimentar
los motores desde USB. El motor de esta ficha ya lleva controlador integrado.

Los dos motores pueden compartir alimentación adecuada, pero necesitan PWM y
FG separados. Si esos cables están unidos en las borneras actuales, separarlos
para permitir velocidades y mediciones independientes.

## Estado al reiniciar

Durante el bootloader los pines pueden quedar como entradas. Para definir el
estado de parada incluso antes de que arranque el programa, prever en cada
entrada PWM una resistencia de pull-up a 5 V de la lógica y en cada BRAKE una
resistencia de pull-down a GND (10 kΩ como punto inicial a comprobar).
Verificar con el driver real: BRAKE <=0.6 V debe mantener la parada; PWM alto
debe ser >=2.5 V. Los valores deben ser compatibles con las resistencias
internas del controlador y el orden de alimentación; no dar esto por validado
solo porque el software compila. Con Nano apagado la entrada BRAKE debe seguir
en parada. Mantener disponible el corte físico de alimentación de motores.

## Sentido de avance

La ficha distingue CW/CCW eléctricos y advierte que algunas reducciones solo
admiten un sentido. No invertir alimentación ni probar arbitrariamente ambos
sentidos. El firmware **no cambia dirección mientras funciona**.

`LEFT_FORWARD_LEVEL_HIGH` y `RIGHT_FORWARD_LEVEL_HIGH` se fijan en `config.h`
tras comprobar el sentido permitido y el montaje. Dos motores enfrentados
pueden necesitar niveles distintos para impulsar el chasis hacia delante.
Si el sentido permitido y el montaje no coinciden, hay que resolver el montaje
mecánico o la variante del motor; variar PWM no invierte una rueda.

## Mediciones pendientes

- Tensión y variante de ambos motores, relación de reducción si está indicada.
- Diámetro efectivo de cada rueda y distancia entre los centros de contacto
  de las dos ruedas motrices, en metros.
- Pulsos FALLING FG por vuelta de rueda, después de la reductora.
- Nivel fijo CW/CCW que corresponde al avance permitido en cada lado.

La frase «6 pulsos/vuelta» de la ficha no basta para introducir 6 en ROS: hay
que determinar si corresponde al rotor o al eje de salida. No se presupone
que ambas ruedas tengan exactamente la misma calibración.
