-- `interaccion.id_material` pasa a ser opcional.
-- Aplicar una sola vez después de 001_tipos_material.sql sobre MySQL 8.
-- Una interacción con `id_material` NULL es una conversación libre del alumno
-- con MAXCIM, sin material asociado (las vistas de la docente la rotulan
-- "Conversación"). El cambio es seguro para las filas existentes: solo relaja
-- la nulabilidad; la FK a `material(id)` se conserva (admite NULL).

ALTER TABLE `interaccion`
  MODIFY COLUMN `id_material` INT NULL;

-- Rollback (solo si antes se borran o reasignan las filas con id_material NULL):
-- ALTER TABLE `interaccion`
--   MODIFY COLUMN `id_material` INT NOT NULL;
