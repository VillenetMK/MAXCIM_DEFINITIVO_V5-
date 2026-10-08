-- Compatibilidad de `material` con cuentos y oraciones.
-- Aplicar una sola vez después de bd_app.sql sobre la misma base MySQL 8.
-- El cambio es seguro para las filas existentes: solo relaja restricciones
-- de nulabilidad y amplía `path_preguntas` de VARCHAR(500) a TEXT.

ALTER TABLE `material`
  MODIFY COLUMN `path_texto` VARCHAR(500) NULL,
  MODIFY COLUMN `path_texto_resumen` VARCHAR(500) NULL,
  MODIFY COLUMN `path_audio` VARCHAR(500) NULL,
  MODIFY COLUMN `path_audio_resumen` VARCHAR(500) NULL,
  MODIFY COLUMN `path_preguntas` TEXT NOT NULL;

-- Rollback (solo si antes se eliminan los NULL y se garantiza que
-- `path_preguntas` no supera 500 caracteres):
-- ALTER TABLE `material`
--   MODIFY COLUMN `path_texto` VARCHAR(500) NOT NULL,
--   MODIFY COLUMN `path_texto_resumen` VARCHAR(500) NOT NULL,
--   MODIFY COLUMN `path_audio` VARCHAR(500) NOT NULL,
--   MODIFY COLUMN `path_audio_resumen` VARCHAR(500) NOT NULL,
--   MODIFY COLUMN `path_preguntas` VARCHAR(500) NOT NULL;
