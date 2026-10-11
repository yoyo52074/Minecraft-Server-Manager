import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


class WebDatabase:
    """Small SQLite repository for local web-panel users and sessions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.password_hasher = PasswordHasher()
        self._init_schema()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self):
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'operator', 'viewer')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def user_count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str = "viewer"):
        username = username.strip()
        if not username or len(username) > 64:
            raise ValueError("帳號長度必須為 1 到 64 個字元")
        if len(password) < 12:
            raise ValueError("密碼至少需要 12 個字元")
        if role not in {"admin", "operator", "viewer"}:
            raise ValueError("無效的角色")
        now = self._now()
        password_hash = self.password_hasher.hash(password)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO users(username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, role, now, now),
            )
            return cursor.lastrowid

    def authenticate(self, username: str, password: str):
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        if not row or not row["enabled"]:
            return None
        try:
            self.password_hasher.verify(row["password_hash"], password)
        except (VerifyMismatchError, ValueError):
            return None
        return dict(row)

    def create_session(self, user_id: int, days: int = 1):
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + days * 86400, timezone.utc
        ).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(id, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, csrf_token, expires_at),
            )
        return session_id, csrf_token

    def get_session(self, session_id: str):
        if not session_id:
            return None
        with self._connect() as db:
            row = db.execute(
                """
                SELECT s.id, s.csrf_token, s.expires_at, u.id AS user_id,
                       u.username, u.role, u.enabled
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row or not row["enabled"]:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            self.delete_session(session_id)
            return None
        return dict(row)

    def delete_session(self, session_id: str):
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def list_users(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, username, role, enabled, created_at FROM users ORDER BY id"
            ).fetchall()]

    def set_user_enabled(self, user_id: int, enabled: bool):
        with self._connect() as db:
            db.execute("UPDATE users SET enabled = ?, updated_at = ? WHERE id = ?", (int(enabled), self._now(), user_id))

    def set_user_role(self, user_id: int, role: str):
        if role not in {"admin", "operator", "viewer"}:
            raise ValueError("無效的角色")
        with self._connect() as db:
            db.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, self._now(), user_id))

    def delete_user(self, user_id: int):
        with self._connect() as db:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def audit(self, user_id, action: str, detail: str = ""):
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_logs(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (user_id, action, detail[:1000], self._now()),
            )

    def list_audit_logs(self, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT a.*, u.username FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.id DESC LIMIT ?
                """, (limit,)
            ).fetchall()]
