"""Persistencia de equipo. Los datos educativos se conservan en su servicio propio."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path

ROLES = {"admin", "engineer", "teacher", "observer"}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()
    return f"pbkdf2_sha256$310000${salt}${digest}"


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != 310000:
            return False
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = password_hash("nonexistent-account-placeholder")


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, role TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1);
          CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, actor TEXT NOT NULL,
            event TEXT NOT NULL, detail TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 1,
            author TEXT NOT NULL, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY, author TEXT NOT NULL, body TEXT NOT NULL,
            timestamp REAL NOT NULL);
        """)

    def create_user(self, username, password, role):
        if not isinstance(username, str) or not username.strip() or len(username) > 80:
            raise ValueError("Nombre de usuario inválido")
        if not isinstance(password, str) or not 12 <= len(password) <= 256 or role not in ROLES:
            raise ValueError("Contraseña de 12 a 256 caracteres y rol válido requeridos")
        with self.db:
            self.db.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                            (username.strip(), password_hash(password), role))

    def login(self, username, password):
        user = self.db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        # Calcula un hash incluso si la cuenta no existe.
        encoded = user["password"] if user else DUMMY_HASH
        if not verify_password(password, encoded) or not user:
            return None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (key, user["id"], csrf, time.time()+8*3600))
        return token, csrf

    def session(self, token):
        if not token or len(token) > 100:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute("""SELECT u.id,u.username,u.role,s.csrf,s.token FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1""",
                              (key, time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, key):
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (key,))

    def audit(self, actor, event, detail=None):
        with self.db:
            self.db.execute("INSERT INTO audit(timestamp,actor,event,detail) VALUES(?,?,?,?)",
                            (time.time(), actor, event, json.dumps(detail or {}, ensure_ascii=False)))

    def audit_rows(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]

    def tasks(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY id DESC")]

    def create_task(self, author, title, owner):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Título de tarea inválido")
        if not isinstance(owner, str) or len(owner) > 80:
            raise ValueError("Responsable inválido")
        with self.db:
            cur = self.db.execute("INSERT INTO tasks(title,owner,author,updated) VALUES(?,?,?,?)",
                                  (title.strip(), owner, author, time.time()))
        return cur.lastrowid

    def update_task(self, task_id, state, version):
        if state not in {"pending", "working", "done"} or type(version) is not int:
            raise ValueError("Estado o versión inválidos")
        with self.db:
            cur = self.db.execute("UPDATE tasks SET state=?,version=version+1,updated=? WHERE id=? AND version=?",
                                  (state, time.time(), task_id, version))
        return cur.rowcount == 1

    def notes(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM notes ORDER BY id DESC LIMIT 50")]

    def add_note(self, author, body):
        if not isinstance(body, str) or not body.strip() or len(body) > 4000:
            raise ValueError("Nota de 1 a 4000 caracteres requerida")
        with self.db:
            self.db.execute("INSERT INTO notes(author,body,timestamp) VALUES(?,?,?)", (author, body.strip(), time.time()))

    def close(self):
        self.db.close()
