from __future__ import annotations

from datetime import UTC, date, datetime

from extensions import db


def _utc_now() -> datetime:
    """UTC stored without tzinfo for compatibility with MySQL DATETIME.

    Mirrors app.utc_now() — kept local to avoid a models -> app import.
    """
    return datetime.now(UTC).replace(tzinfo=None)


# Valores permitidos de `material.tipo_material` (ver bd_app.sql). Un cuento
# usa todas las rutas; una oración (con o sin imágenes) solo guarda su
# contenido en `path_preguntas`, apuntando a un JSON en uploads/<id>/.
TIPO_CUENTO = "cuento"
TIPO_ORACION = "oracion"
# "Oraciones con imágenes": cada oración lleva exactamente dos sustantivos
# concretos (dibujables) que más adelante se reemplazan por imágenes. Se
# guarda como una lista JSON de objetos {texto, sustantivos:[a, b]} en
# uploads/<id>/oraciones.json (una `oracion` guarda ahí una lista de strings).
TIPO_ORACION_IMAGEN = "oracion_imagen"
# "Bits" (bits de inteligencia): tarjetas palabra+imagen para practicar
# fonética con las sílabas que indique la docente (criterio solo de
# generación con IA, no se guarda por palabra). Se guarda como una lista JSON de objetos
# {palabra, imagen} en uploads/<id>/bits.json — sin plantilla ni texto: a
# diferencia de oracion_imagen no hay una oración en la que intercalar
# imágenes, cada bit es una sola palabra con una sola imagen.
TIPO_BITS = "bits"
TIPOS_MATERIAL = (TIPO_CUENTO, TIPO_ORACION, TIPO_ORACION_IMAGEN, TIPO_BITS)


class Periodo(db.Model):
    """Bimestre académico definido para un año escolar.

    Refleja exactamente `periodo` en bd_app.sql; ese archivo es la fuente de
    verdad para columnas, tipos e índices.
    """

    __tablename__ = "periodo"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date, nullable=False)

    materiales = db.relationship("Material", back_populates="periodo")
    interacciones = db.relationship("Interaccion", back_populates="periodo")
    temas = db.relationship("Tema", back_populates="periodo")


class Tema(db.Model):
    """Unidad temática que una docente usa para organizar su propio material
    dentro de un periodo (p.ej. "Animales", "La familia"). No se comparte
    entre docentes ni entre periodos: cada tema pertenece a una docente y a
    un bimestre concretos. Refleja exactamente `tema` en bd_app.sql; ese
    archivo es la fuente de verdad para columnas, tipos e índices.
    """

    __tablename__ = "tema"
    __table_args__ = (
        # Declarada también aquí (y no solo en la migración) para que SQLite
        # la aplique igual en las pruebas: sin esto, create_tema() nunca
        # vería el IntegrityError que espera para devolver 409.
        #
        # Colación: en MySQL (migrations/005_tema.sql) la tabla usa
        # utf8mb4_0900_ai_ci, insensible a mayúsculas/acentos, así que
        # "Animales" y "animales" chocan como duplicados. SQLite compara en
        # binario sin importar aquí una colación equivalente, así que en
        # pruebas y en DEMO_MODE (que también corre sobre SQLite) esas dos
        # variantes SÍ se guardan como temas distintos — divergencia conocida
        # y aceptada, no un intento fallido de replicar el comportamiento de
        # MySQL. Si esto importa alguna vez, hay que registrar una colación
        # SQLite equivalente vía sqlalchemy.event en vez de asumir que este
        # UniqueConstraint por sí solo cubre el caso acento/mayúscula.
        db.UniqueConstraint(
            "fk_user", "id_periodo", "nombre", name="uq_tema_docente_periodo_nombre"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    # ID institucional de la docente, igual que `material.fk_user`: no es una
    # FK real, la tabla `docente` vive en la API institucional.
    fk_user = db.Column(db.String(50), nullable=False, index=True)
    id_periodo = db.Column(
        db.Integer,
        db.ForeignKey("periodo.id"),
        nullable=False,
        index=True,
    )

    periodo = db.relationship("Periodo", back_populates="temas")
    # `passive_deletes=True` es obligatorio para que el borrado bloqueado de
    # DELETE /api/temas/<id> funcione de verdad: sin esto, SQLAlchemy pone en
    # NULL el `id_tema` de cada material referenciado ANTES del DELETE del
    # tema (comportamiento por defecto de la relación, no algo que la FK
    # pueda impedir), así que un borrado que se cuela pasado el chequeo de
    # `material_count` (p.ej. por una lectura obsoleta en una carrera)
    # desasignaría el tema en silencio en vez de fallar — justo lo que se
    # decidió NO hacer. Con esto, el DELETE se emite tal cual y es la FK de
    # material.id_tema (ON DELETE por defecto = RESTRICT, ver
    # migrations/005_tema.sql) la que lo rechaza con IntegrityError si algo
    # todavía apunta al tema.
    materiales = db.relationship(
        "Material", back_populates="tema", passive_deletes=True
    )


class Material(db.Model):
    """Material prepared by a teacher for an activity with MAXCIM.

    Mirrors `material` in bd_app.sql exactly — see that file for the source
    of truth on columns, types and indexes.
    """

    __tablename__ = "material"

    id = db.Column(db.Integer, primary_key=True)
    nombre_material = db.Column(db.String(255), nullable=False)
    tipo_material = db.Column(db.String(255), nullable=False)
    path_audio = db.Column(db.String(500), nullable=True)
    path_texto = db.Column(db.String(500), nullable=True)
    path_texto_resumen = db.Column(db.String(500), nullable=True)
    # Ruta del JSON de preguntas en un cuento; texto de las oraciones en una
    # oración. Es TEXT porque las oraciones no caben en VARCHAR(500).
    path_preguntas = db.Column(db.Text, nullable=False)
    # Application-side default. SQLAlchemy would render func.current_date()
    # as DEFAULT CURRENT_DATE for MySQL, which some managed MySQL versions
    # reject during schema creation.
    fecha_subido = db.Column(db.Date, nullable=False, default=date.today)
    # ID institucional de la docente (`idPersona` de CIMA, p.ej. "1000001"). No es
    # una FK real: la tabla `docente` vive en la API institucional, no en esta
    # base (ver bd_app.sql).
    fk_user = db.Column(db.String(50), nullable=False, index=True)
    # Nombre de la docente (ya normalizado, p.ej. "Docente Demo Uno")
    # tal como estaba en su sesión al crear el material. Copia para que la API
    # del robot pueda listar materiales por nombre (`?docente=`) y mostrarlo sin
    # volver a consultar a CIMA. Nulo en registros creados antes de la columna.
    fk_user_name = db.Column(db.String(255), nullable=True)
    # Bimestre académico del material. Obligatorio en producción: la app lo exige
    # al guardar (save_material) y en MySQL la columna es NOT NULL
    # (migrations/006, bd_app_mysql.sql). Se deja nullable a nivel ORM porque el
    # create_all() de SQLite en las pruebas siembra materiales directamente.
    id_periodo = db.Column(
        db.Integer,
        db.ForeignKey("periodo.id"),
        nullable=True,
        index=True,
    )
    # Tema al que la docente asignó este material. Obligatorio en producción
    # (misma nota que id_periodo) y debe pertenecer al mismo periodo que
    # `id_periodo` — la app lo valida al guardar, no hay trigger en la base.
    id_tema = db.Column(
        db.Integer,
        db.ForeignKey("tema.id"),
        nullable=True,
        index=True,
    )

    interacciones = db.relationship(
        "Interaccion",
        back_populates="material",
        lazy="selectin",
    )
    periodo = db.relationship("Periodo", back_populates="materiales")
    tema = db.relationship("Tema", back_populates="materiales")

    @property
    def es_oracion(self) -> bool:
        return self.tipo_material == TIPO_ORACION

    @property
    def es_oracion_imagen(self) -> bool:
        return self.tipo_material == TIPO_ORACION_IMAGEN

    @property
    def es_cuento(self) -> bool:
        return self.tipo_material == TIPO_CUENTO

    @property
    def es_bits(self) -> bool:
        return self.tipo_material == TIPO_BITS


class Interaccion(db.Model):
    """One question/answer turn between an identified student and MAXCIM.
    Mirrors `interaccion` in bd_app.sql exactly.

    Usually the turn is about a `material` the teacher prepared. When
    `id_material` is NULL the turn is a free conversation the student held
    with MAXCIM, not tied to any material; the teacher views label it
    "Conversación".
    """

    __tablename__ = "interaccion"

    id = db.Column(db.Integer, primary_key=True)
    id_material = db.Column(
        db.Integer,
        db.ForeignKey("material.id"),
        nullable=True,
        index=True,
    )
    # ID institucional del alumno, igual que `fk_user` en Material: no es una
    # FK real porque `alumno` también vive en la API institucional.
    fk_alumno = db.Column(db.String(50), nullable=False, index=True)
    fecha_hora = db.Column(db.DateTime, nullable=False, default=_utc_now)
    pregunta = db.Column(db.Text, nullable=False)
    respuesta = db.Column(db.Text, nullable=False)
    path_audio_rpta = db.Column(db.String(500), nullable=False)
    apreciacion_robot = db.Column(db.Text, nullable=False)
    rpta_correcta = db.Column(db.Boolean, nullable=False)
    # Bimestre académico de la interacción; nulo si queda fuera de todos los periodos definidos.
    id_periodo = db.Column(
        db.Integer,
        db.ForeignKey("periodo.id"),
        nullable=True,
        index=True,
    )

    material = db.relationship("Material", back_populates="interacciones")
    periodo = db.relationship("Periodo", back_populates="interacciones")
