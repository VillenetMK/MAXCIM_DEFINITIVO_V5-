-- Hace obligatorios `material.id_periodo` y `material.id_tema`: todo material
-- queda clasificado en un periodo y en un tema de ese periodo. La app ya lo
-- valida al guardar (save_material en app.py); esta migración alinea la base y
-- rellena los materiales antiguos que quedaron sin clasificar.
--
-- Aplicar una sola vez después de 005_tema.sql sobre MySQL 8. REVISA el
-- backfill antes de ejecutarlo en producción: crea un tema "Sin clasificar"
-- por cada (docente, periodo) que tenga material huérfano.

-- 1) Materiales sin periodo -> el periodo cuyo rango de fechas contiene
--    `fecha_subido`; si ninguno lo contiene, el periodo más antiguo.
UPDATE `material` m
LEFT JOIN `periodo` p
  ON m.`fecha_subido` BETWEEN p.`fecha_inicio` AND p.`fecha_fin`
SET m.`id_periodo` = COALESCE(
  p.`id`,
  (SELECT `id` FROM `periodo` ORDER BY `fecha_inicio`, `id` LIMIT 1)
)
WHERE m.`id_periodo` IS NULL;

-- 2) Un tema "Sin clasificar" por cada (docente, periodo) con material huérfano.
INSERT INTO `tema` (`nombre`, `fk_user`, `id_periodo`)
SELECT DISTINCT 'Sin clasificar', m.`fk_user`, m.`id_periodo`
FROM `material` m
WHERE m.`id_tema` IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM `tema` t
    WHERE t.`fk_user` = m.`fk_user`
      AND t.`id_periodo` = m.`id_periodo`
      AND t.`nombre` = 'Sin clasificar'
  );

-- 3) Asigna ese tema a los materiales huérfanos.
UPDATE `material` m
JOIN `tema` t
  ON t.`fk_user` = m.`fk_user`
 AND t.`id_periodo` = m.`id_periodo`
 AND t.`nombre` = 'Sin clasificar'
SET m.`id_tema` = t.`id`
WHERE m.`id_tema` IS NULL;

-- 4) Ya no debe quedar ningún NULL: fija las columnas como NOT NULL.
ALTER TABLE `material`
  MODIFY COLUMN `id_periodo` INT NOT NULL,
  MODIFY COLUMN `id_tema` INT NOT NULL;

-- Rollback:
-- ALTER TABLE `material`
--   MODIFY COLUMN `id_periodo` INT NULL,
--   MODIFY COLUMN `id_tema` INT NULL;
-- (los temas "Sin clasificar" creados por el backfill quedan; bórralos a mano
--  si de verdad los quieres deshacer.)
