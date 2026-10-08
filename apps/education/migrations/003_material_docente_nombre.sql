-- `material.fk_user_name`: nombre de la docente al crear el material, como
-- copia para mostrarlo desde la API del robot sin volver a consultar a CIMA.
-- Aplicar una sola vez después de 002_interaccion_material_opcional.sql sobre
-- MySQL 8. El cambio es seguro para las filas existentes: la columna es NULL y
-- queda vacía en los materiales creados antes (o hasta que la docente los
-- vuelva a guardar / se haga un backfill).

ALTER TABLE `material`
  ADD COLUMN `fk_user_name` VARCHAR(255) NULL AFTER `fk_user`;

-- Rollback:
-- ALTER TABLE `material`
--   DROP COLUMN `fk_user_name`;
