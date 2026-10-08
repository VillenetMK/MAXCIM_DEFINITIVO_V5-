-- Esquema MySQL 8 vigente de MAXCIM App.
-- Import completo para una base nueva: incluye todas las tablas que el código
-- actual usa (periodo, tema, material, interaccion) con las columnas añadidas
-- por migrations/001..005 ya integradas. Aplicar este archivo equivale a
-- correr esas cinco migraciones en orden sobre una base vacía; no hace falta
-- ejecutarlas aparte.
--
-- Las tablas de migraciones obsoletas anteriores al refactor (pregunta,
-- sesion_interaccion, turno_conversacion, etc.) no se incluyen. Docentes,
-- aulas y alumnos provienen de la API institucional y nunca se persisten
-- localmente.

CREATE DATABASE IF NOT EXISTS `maxcim_app`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE `maxcim_app`;

-- `periodo`: bimestres académicos usados para clasificar materiales e
-- interacciones por sus fechas. Ver migrations/004_periodo.sql.
CREATE TABLE IF NOT EXISTS `periodo` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `nombre` VARCHAR(50) NOT NULL,
  `anio` INT NOT NULL,
  `fecha_inicio` DATE NOT NULL,
  `fecha_fin` DATE NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_periodo_anio_nombre` (`anio`, `nombre`),
  CONSTRAINT `chk_periodo_fechas` CHECK (`fecha_fin` >= `fecha_inicio`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO `periodo` (`id`, `nombre`, `anio`, `fecha_inicio`, `fecha_fin`) VALUES
  (1, 'I BIMESTRE', 2026, '2026-03-02', '2026-05-08'),
  (2, 'II BIMESTRE', 2026, '2026-05-11', '2026-07-24'),
  (3, 'III BIMESTRE', 2026, '2026-08-03', '2026-10-09'),
  (4, 'IV BIMESTRE', 2026, '2026-10-12', '2026-12-18');

-- `tema`: unidades temáticas que cada docente usa para organizar su propio
-- material dentro de un periodo. No se comparte entre docentes ni periodos.
-- Ver migrations/005_tema.sql.
CREATE TABLE IF NOT EXISTS `tema` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `nombre` VARCHAR(120) NOT NULL,
  -- ID institucional de la docente (`idPersona` de CIMA), igual que
  -- `material.fk_user`: no es una FK real, la tabla `docente` vive en la API
  -- institucional, no en esta base.
  `fk_user` VARCHAR(50) NOT NULL,
  `id_periodo` INT NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_tema_docente` (`fk_user`),
  KEY `ix_tema_periodo` (`id_periodo`),
  -- Evita que una misma docente repita el nombre de un tema dentro del mismo
  -- periodo (case/acento-insensible por la collation utf8mb4_0900_ai_ci).
  UNIQUE KEY `uq_tema_docente_periodo_nombre` (`fk_user`, `id_periodo`, `nombre`),
  CONSTRAINT `tema_id_periodo_foreign`
    FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `material` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `nombre_material` VARCHAR(255) NOT NULL,
  -- Valores permitidos: 'cuento', 'oracion' y 'oracion_imagen' (oraciones con
  -- dos sustantivos concretos que luego se reemplazan por imágenes). No hay
  -- CHECK: lo valida la app (TIPOS_MATERIAL en models.py).
  `tipo_material` VARCHAR(255) NOT NULL,
  `path_audio` VARCHAR(500) NULL,
  `path_texto` VARCHAR(500) NULL,
  `path_texto_resumen` VARCHAR(500) NULL,
  -- Para un cuento guarda la ruta del JSON de preguntas; para una oración
  -- guarda el texto de las oraciones. Por eso es TEXT y no VARCHAR(500).
  `path_preguntas` TEXT NOT NULL,
  `fecha_subido` DATE NOT NULL,
  -- ID institucional de la docente (`idPersona` de CIMA). No es una FK real: la
  -- tabla `docente` vive en la API institucional, no en esta base.
  `fk_user` VARCHAR(50) NOT NULL,
  -- Nombre de la docente al crear el material; copia para que el robot lo
  -- muestre sin volver a consultar a CIMA. NULL en registros antiguos.
  `fk_user_name` VARCHAR(255) NULL,
  -- Bimestre académico del material. Obligatorio: todo material se clasifica
  -- en un periodo (y en un tema de ese periodo).
  `id_periodo` INT NOT NULL,
  -- Tema que la docente asignó al material. Obligatorio y debe pertenecer al
  -- mismo periodo que `id_periodo` (lo valida la app, sin trigger).
  `id_tema` INT NOT NULL,
  PRIMARY KEY (`id`),
  INDEX `ix_material_docente` (`fk_user`),
  INDEX `ix_material_periodo` (`id_periodo`),
  INDEX `ix_material_tema` (`id_tema`),
  CONSTRAINT `material_id_periodo_foreign`
    FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`),
  -- Sin ON DELETE CASCADE/SET NULL a propósito: la app bloquea el borrado de
  -- un tema con material asignado (DELETE /api/temas/<id> en app.py) y devuelve
  -- un 409 legible; RESTRICT queda como red de seguridad.
  CONSTRAINT `material_id_tema_foreign`
    FOREIGN KEY (`id_tema`) REFERENCES `tema` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Una interacción = un turno de pregunta/respuesta entre un alumno y MAXCIM.
-- Normalmente es sobre un material puntual; cuando `id_material` es NULL el
-- turno es una conversación libre del alumno con MAXCIM, sin material asociado
-- (las vistas de la docente lo rotulan "Conversación").
CREATE TABLE IF NOT EXISTS `interaccion` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `id_material` INT NULL,
  -- ID institucional del alumno, igual que `fk_user` en `material`: no es una
  -- FK real porque la tabla `alumno` también vive en la API institucional.
  `fk_alumno` VARCHAR(50) NOT NULL,
  `fecha_hora` DATETIME NOT NULL,
  `pregunta` TEXT NOT NULL,
  `respuesta` TEXT NOT NULL,
  `path_audio_rpta` VARCHAR(500) NOT NULL,
  `apreciacion_robot` TEXT NOT NULL COMMENT 'Campo en el cual el robot dirá su "crítica" sobre la respuesta del alumno.',
  `rpta_correcta` BOOLEAN NOT NULL,
  -- Bimestre académico de la interacción; NULL si queda fuera de los periodos.
  `id_periodo` INT NULL,
  PRIMARY KEY (`id`),
  INDEX `ix_interaccion_alumno` (`fk_alumno`),
  INDEX `ix_interaccion_material` (`id_material`),
  INDEX `ix_interaccion_periodo` (`id_periodo`),
  CONSTRAINT `interaccion_id_material_foreign` FOREIGN KEY (`id_material`) REFERENCES `material` (`id`),
  CONSTRAINT `interaccion_id_periodo_foreign` FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
