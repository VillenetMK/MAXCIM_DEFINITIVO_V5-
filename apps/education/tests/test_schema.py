from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from extensions import db
from models import Material


def test_material_schema_is_compatible_with_managed_mysql():
    ddl = str(CreateTable(Material.__table__).compile(dialect=mysql.dialect()))

    assert "fecha_subido DATE NOT NULL" in ddl
    assert "DEFAULT CURRENT_DATE" not in ddl


def test_oracion_material_saves_with_only_path_preguntas(app):
    with app.app_context():
        material = Material(
            nombre_material="Oraciones de práctica",
            tipo_material="oracion",
            path_preguntas="La luna brilla. El río canta.",
            fk_user="DOC-TEST-1",
        )
        db.session.add(material)
        db.session.commit()

        saved = db.session.get(Material, material.id)
        assert saved.path_preguntas == "La luna brilla. El río canta."
        assert saved.path_texto is None
        assert saved.path_texto_resumen is None
        assert saved.path_audio is None


def test_material_type_helpers_express_the_distinction():
    oracion = Material(tipo_material="oracion")
    cuento = Material(tipo_material="cuento")

    assert oracion.es_oracion is True
    assert oracion.es_cuento is False
    assert cuento.es_cuento is True
    assert cuento.es_oracion is False
