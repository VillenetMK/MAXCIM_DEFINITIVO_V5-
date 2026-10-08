# Procedencia y atribuciones

Fuentes autorizadas por Gabriel para consolidar MAXCIM:

| Repositorio | Revisión recuperada | Destino |
|---|---|---|
| VillenetMK/MAX | c27dbb01687b51966e2faf64a04522072011848b | robot/legacy y paquetes seleccionados en robot/ros2/src |
| arodasr-cima/maxcim_app | 540030df8632afeab60840a83ad411bd0812a5db | apps/education |
| VillenetMK/maxcim-base-linux | árbol 1a1fac837ad96a2829aee73a43c4d0e2154c10c9 | base, odometría, firmware Nano y pruebas |
| Archivo de Gabriel | MAXCIM CONEXIONES.drawio | docs/hardware/MAXCIM-CONEXIONES.drawio |

`source-manifest.json` enumera ruta original y SHA de blob de cada archivo importado. El SHA identifica la fuente; se normalizaron finales de línea a LF y algunos destinos recibieron cambios V5, por lo que no se afirma que su contenido siga idéntico. Las copias activas de audio/visión/memoria/conversación y RPLIDAR provienen de los archivos equivalentes en legacy.

Se excluyeron historiales duplicados, copias de respaldo, entornos, archivos de secretos, modelos/pesos, bases personales y datos de grabación. No se recuperó contenido de las máquinas físicas ni se usaron credenciales antiguas. Los iconos PNG educativos no estaban disponibles mediante la importación de texto.

Los archivos de terceros retienen sus avisos, entre ellos RPLIDAR/SLAMTEC. Algunos paquetes originales declaran MIT, otros tienen `TODO` o Proprietary; no se asigna una licencia global que conceda derechos sobre todos los componentes. Verificar titularidad/permisos antes de redistribuir fuera del alcance del proyecto. La carpeta legacy preserva atribuciones e implementación original; no se arranca automáticamente.

Cambios activos: API y panel V5, autoridad de control, contratos ROS, resolución explícita de home, rechazo de referencias/órdenes inválidas, puente al Nano CRC, secuenciador y firmware ESP32 sin homing automático, lanzadores por dispositivo, audio con QoS de sensor y gestos por voz autorizados. La documentación lista qué partes se han probado.
