# Aplicación educativa

Se importó el código de `arodasr-cima/maxcim_app` en `apps/education`: plataforma docente Flask, temas/materiales/interacciones, bits, historias, imágenes, narración Fish Audio, Google e integración institucional. Se preservan sus pruebas y lógica de autenticación. No se ha rediseñado ni conectado una base institucional real durante esta entrega.

La consola V5 tiene su propia cuenta de equipo; la app educativa mantiene cuentas docentes institucionales. El apartado Aprendizaje indica esta separación y enlaza esta guía. No se está afirmando SSO ni una integración de contenidos al robot que aún no se ha comprobado.

## Ejecutar

Desde una venv separada:

```bash
cd apps/education
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app:create_app run --host 127.0.0.1 --port 8081
```

Antes de arrancar con datos reales configurar `DEMO_MODE=false`, `DATABASE_URL` (o MYSQL_HOST/PORT/USER/PASSWORD/DATABASE), `SECRET_KEY` y `SESSION_TOKEN_ENCRYPTION_KEY` de tipo Fernet. La API institucional requiere HTTPS y verificación TLS. Configurar las rutas `INSTITUTIONAL_API_*` según el contrato real; no se han inventado endpoints ni claves. `GOOGLE_API_KEY` activa generación y `FISH_API_KEY` narración; sin ellas no existe ese servicio real.

Para un ensayo de interfaz sin institución usar **explícitamente** `DEMO_MODE=true`; sus identidades, materiales y audio de relleno no son evidencia de resultados reales del proyecto. El código original deja demo desactivado por defecto. Para producción usar Gunicorn y proxy TLS, no el servidor de desarrollo Flask.

```bash
python -m pip install pytest
python -m pytest tests
```

Estas pruebas no se ejecutaron en el entorno actual porque no se pudieron instalar Flask/SQLAlchemy. Solo se comprobó la sintaxis Python. En la importación faltaron dos iconos PNG binarios; se utiliza el SVG original en manifest y caché para evitar recursos inexistentes. El icono Apple PNG queda pendiente.

La siguiente integración de contenido debe definir el contrato de material y los eventos de reproducción/interacción por ROS 2 antes de escribir en tablas institucionales. No existe ningún envío automático de resultados escolares desde la consola nueva.
