-- `periodo`: bimestres académicos usados para clasificar materiales e
-- interacciones por sus fechas. Aplicar una sola vez después de
-- 003_material_docente_nombre.sql sobre MySQL 8. Las columnas foráneas son
-- NULL para conservar filas históricas y fechas fuera de los periodos.

CREATE TABLE `periodo` (
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

ALTER TABLE `material`
  ADD COLUMN `id_periodo` INT NULL,
  ADD KEY `ix_material_periodo` (`id_periodo`),
  ADD CONSTRAINT `material_id_periodo_foreign`
    FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`);

ALTER TABLE `interaccion`
  ADD COLUMN `id_periodo` INT NULL,
  ADD KEY `ix_interaccion_periodo` (`id_periodo`),
  ADD CONSTRAINT `interaccion_id_periodo_foreign`
    FOREIGN KEY (`id_periodo`) REFERENCES `periodo` (`id`);

UPDATE `material` AS `m`
JOIN `periodo` AS `p`
  ON `m`.`fecha_subido` BETWEEN `p`.`fecha_inicio` AND `p`.`fecha_fin`
SET `m`.`id_periodo` = `p`.`id`;

UPDATE `interaccion` AS `i`
JOIN `periodo` AS `p`
  ON DATE(`i`.`fecha_hora`) BETWEEN `p`.`fecha_inicio` AND `p`.`fecha_fin`
SET `i`.`id_periodo` = `p`.`id`;

-- Rollback:
-- ALTER TABLE `interaccion`
--   DROP FOREIGN KEY `interaccion_id_periodo_foreign`,
--   DROP INDEX `ix_interaccion_periodo`,
--   DROP COLUMN `id_periodo`;
-- ALTER TABLE `material`
--   DROP FOREIGN KEY `material_id_periodo_foreign`,
--   DROP INDEX `ix_material_periodo`,
--   DROP COLUMN `id_periodo`;
-- DROP TABLE `periodo`;
