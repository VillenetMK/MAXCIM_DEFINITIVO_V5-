-- `tema`: unidades temáticas que cada docente usa para organizar su propio
-- material dentro de un periodo (p.ej. "Animales", "La familia"). No es
-- compartida entre docentes ni entre periodos: cada tema pertenece a una
-- docente y a un bimestre concretos. Aplicar una sola vez después de
-- 004_periodo.sql sobre MySQL 8.

CREATE TABLE `tema` (
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
  -- periodo (case/acento-insensible, misma collation que `periodo`).
  UNIQUE KEY `uq_tema_docente_periodo_nombre` (`fk_user`, `id_periodo`, `nombre`),
  CONSTRAINT `tema_id_periodo_foreign`
    FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `material`
  ADD COLUMN `id_tema` INT NULL,
  ADD KEY `ix_material_tema` (`id_tema`),
  -- Sin ON DELETE CASCADE/SET NULL a propósito: la app bloquea el borrado de
  -- un tema con material asignado (ver DELETE /api/temas/<id> en app.py) y
  -- devuelve un 409 legible antes de que la fila llegue a chocar con esta
  -- FK; RESTRICT queda como red de seguridad si algo se salta esa capa.
  ADD CONSTRAINT `material_id_tema_foreign`
    FOREIGN KEY (`id_tema`) REFERENCES `tema` (`id`);

-- Rollback:
-- ALTER TABLE `material`
--   DROP FOREIGN KEY `material_id_tema_foreign`,
--   DROP INDEX `ix_material_tema`,
--   DROP COLUMN `id_tema`;
-- DROP TABLE `tema`;
