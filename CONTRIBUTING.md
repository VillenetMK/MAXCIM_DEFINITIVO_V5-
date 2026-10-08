# Trabajar en equipo

Cada integrante trabaja en una rama y propone un pull request con problema, cambio y comprobación. Una tarea tiene un responsable y estado visible en la consola. No subir claves, dumps de estudiantes, pesos, vídeos ni archivos `.local.*`. No probar motores por cambios de software sin medir la configuración física que se está usando.

Antes de proponer cambios ejecutar `bash scripts/check.sh`. Si se cambia un contrato ROS, mantener juntos el mensaje/servicio, gateway, API y prueba de rechazo correspondiente. Si se modifica firmware, actualizar la versión/protocolo y probar pérdida de comunicación, arranque deshabilitado y parada.

La simulación permite desarrollar sin robot. Un resultado simulado se registra como simulado. Las validaciones físicas deben incluir revisión git, configuración medida, operador, fecha y resultado observado. No copiar una consigna al campo de posición medida.

No iniciar componentes de `robot/legacy`; migrar la capacidad necesaria a la ruta activa. Mantener efectos de hardware dentro de constructores/funciones explícitas, nunca al importar módulos. Los proveedores de IA solicitan acciones al gateway y no reciben puertos ni autoridad para rearmar.
