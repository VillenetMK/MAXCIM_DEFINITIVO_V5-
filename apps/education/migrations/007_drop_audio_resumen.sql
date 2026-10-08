-- Elimina `material.path_audio_resumen`: MAXCIM ya no genera ni guarda el audio
-- del resumen (el robot reproduce el cuento por escenas y el texto del resumen
-- se conserva en `path_texto_resumen`).
--
-- Aplicar una sola vez después de 006_material_tema_obligatorio.sql sobre
-- MySQL 8. Es opcional: la columna es NULL y la app ya no la lee ni la
-- escribe, así que puede quedarse sin causar errores. Al eliminarla se pierden
-- las rutas guardadas; los archivos `audio_resumen.wav` de `uploads/<id>/` no
-- se tocan (se borran junto con el material).

ALTER TABLE `material`
  DROP COLUMN `path_audio_resumen`;
