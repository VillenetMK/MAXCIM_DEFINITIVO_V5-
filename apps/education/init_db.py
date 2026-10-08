"""Creates the current MAXCIM schema on a fresh production database."""

from app import app
from extensions import db


with app.app_context():
    db.create_all()
