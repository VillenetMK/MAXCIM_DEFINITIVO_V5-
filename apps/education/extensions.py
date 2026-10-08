from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine


db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores FOREIGN KEY constraints unless told otherwise per
    connection — MySQL (producción) siempre las aplica, así que sin esto los
    FK de `material.id_periodo`/`id_tema` e `interaccion.id_material` son
    solo decorativos en las pruebas y en DEMO_MODE (que también usa SQLite),
    dejando pasar referencias huérfanas que MySQL rechazaría."""
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
