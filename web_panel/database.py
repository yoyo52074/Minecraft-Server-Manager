import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from .permissions import ALL_PERMISSIONS


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
                    server_id INTEGER,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS server_permissions (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    server_id INTEGER NOT NULL,
                    permission_key TEXT NOT NULL,
                    allowed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, server_id, permission_key)
                );
                CREATE TABLE IF NOT EXISTS resource_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    recorded_at REAL NOT NULL,
                    cpu_percent REAL NOT NULL,
                    memory_mb REAL NOT NULL,
                    memory_percent REAL NOT NULL,
                    memory_limit_percent REAL NOT NULL DEFAULT 0,
                    memory_limit_mb REAL NOT NULL DEFAULT 0,
                    disk_used_percent REAL NOT NULL,
                    disk_free_gb REAL NOT NULL,
                    disk_used_gb REAL NOT NULL DEFAULT 0,
                    disk_total_gb REAL NOT NULL DEFAULT 0,
                    net_sent_mb REAL NOT NULL,
                    net_recv_mb REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_settings (
                    server_id INTEGER PRIMARY KEY,
                    cpu_percent REAL NOT NULL DEFAULT 90,
                    memory_percent REAL NOT NULL DEFAULT 90,
                    disk_used_percent REAL NOT NULL DEFAULT 90,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    alert_key TEXT NOT NULL,
                    message TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    resolved_at REAL
                );
                CREATE TABLE IF NOT EXISTS backup_settings (
                    server_id INTEGER PRIMARY KEY,
                    retain_count INTEGER NOT NULL DEFAULT 10,
                    max_age_days INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(audit_logs)")}
            if "server_id" not in columns:
                db.execute("ALTER TABLE audit_logs ADD COLUMN server_id INTEGER")
            metric_columns = {row[1] for row in db.execute("PRAGMA table_info(resource_metrics)")}
            for name in ("memory_limit_percent", "memory_limit_mb", "disk_used_gb", "disk_total_gb"):
                if name not in metric_columns:
                    db.execute(f"ALTER TABLE resource_metrics ADD COLUMN {name} REAL NOT NULL DEFAULT 0")
            normalized = db.execute("SELECT value FROM system_metadata WHERE key='cpu_percent_machine_normalized'").fetchone()
            if not normalized:
                cpu_count = max(1, os.cpu_count() or 1)
                db.execute("UPDATE resource_metrics SET cpu_percent=MIN(cpu_percent / ?, 100)", (cpu_count,))
                db.execute("INSERT INTO system_metadata(key,value) VALUES('cpu_percent_machine_normalized','1')")

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
            current = db.execute("SELECT role,enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if current and current["role"] == "admin" and current["enabled"] and not enabled:
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                    raise ValueError("不能停用最後一位啟用中的管理員")
            db.execute("UPDATE users SET enabled = ?, updated_at = ? WHERE id = ?", (int(enabled), self._now(), user_id))

    def set_user_role(self, user_id: int, role: str):
        if role not in {"admin", "operator", "viewer"}:
            raise ValueError("無效的角色")
        with self._connect() as db:
            current = db.execute("SELECT role,enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if current and current["role"] == "admin" and current["enabled"] and role != "admin":
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                    raise ValueError("不能變更最後一位啟用中管理員的角色")
            db.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, self._now(), user_id))

    def update_user(self, user_id: int, username: str, password: str = "", role: str | None = None):
        username = username.strip()
        if not username or len(username) > 64:
            raise ValueError("帳號長度必須為 1 到 64 個字元")
        if password and len(password) < 12:
            raise ValueError("新密碼至少需要 12 個字元")
        if role is not None and role not in {"admin", "operator", "viewer"}:
            raise ValueError("無效的角色")
        now = self._now()
        password_hash = self.password_hasher.hash(password) if password else None
        with self._connect() as db:
            current = db.execute("SELECT role,enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if not current:
                raise ValueError("找不到使用者")
            if role is not None and current["role"] == "admin" and current["enabled"] and role != "admin":
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                    raise ValueError("不能變更最後一位啟用中管理員的角色")
            if password_hash:
                db.execute("UPDATE users SET username=?,role=COALESCE(?,role),password_hash=?,updated_at=? WHERE id=?", (username, role, password_hash, now, user_id))
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            else:
                db.execute("UPDATE users SET username=?,role=COALESCE(?,role),updated_at=? WHERE id=?", (username, role, now, user_id))
        return username

    def delete_user(self, user_id: int):
        with self._connect() as db:
            user = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("找不到使用者")
            if user["role"] == "admin" and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                raise ValueError("不能刪除最後一位啟用中的管理員")
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def audit(self, user_id, action: str, detail: str = "", server_id=None):
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_logs(user_id, server_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, server_id, action, detail[:1000], self._now()),
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

    def server_audit_logs(self, server_id, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT a.*, u.username FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id "
                "WHERE a.server_id=? ORDER BY a.id DESC LIMIT ?", (server_id, limit)
            ).fetchall()]

    def schedule_runs(self, schedule_id=None, limit=100):
        with self._connect() as db:
            query = "SELECT r.*, s.server_id, s.name AS schedule_name, s.action FROM schedule_runs r LEFT JOIN schedules s ON s.id=r.schedule_id"
            args = []
            if schedule_id is not None:
                query += " WHERE r.schedule_id=?"
                args.append(schedule_id)
            query += " ORDER BY r.id DESC LIMIT ?"
            args.append(limit)
            return [dict(row) for row in db.execute(query, args).fetchall()]

    def alert_thresholds(self, server_id):
        defaults = {"server_id": server_id, "cpu_percent": 90.0, "memory_percent": 90.0, "disk_used_percent": 90.0}
        with self._connect() as db:
            row = db.execute("SELECT * FROM alert_settings WHERE server_id=?", (server_id,)).fetchone()
        return dict(row) if row else defaults

    def set_alert_thresholds(self, server_id, values):
        keys = ("cpu_percent", "memory_percent", "disk_used_percent")
        normalized = {key: float(values.get(key, self.alert_thresholds(server_id)[key])) for key in keys}
        if any(value < 1 or value > 100 for value in normalized.values()):
            raise ValueError("告警門檻必須介於 1 到 100")
        with self._connect() as db:
            db.execute("INSERT INTO alert_settings(server_id,cpu_percent,memory_percent,disk_used_percent,updated_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(server_id) DO UPDATE SET cpu_percent=excluded.cpu_percent,memory_percent=excluded.memory_percent,disk_used_percent=excluded.disk_used_percent,updated_at=excluded.updated_at",
                       (server_id, normalized["cpu_percent"], normalized["memory_percent"], normalized["disk_used_percent"], self._now()))
        return self.alert_thresholds(server_id)

    def evaluate_metric_alerts(self, server_id, metrics):
        thresholds = self.alert_thresholds(server_id)
        memory_load = float(metrics.get("memory_limit_percent") or metrics.get("memory_percent", 0))
        checks = {
            "cpu": ("CPU", float(metrics.get("cpu_percent", 0)), thresholds["cpu_percent"], "%"),
            "memory": ("記憶體配置上限使用率", memory_load, thresholds["memory_percent"], "%"),
            "disk": ("磁碟使用率", float(metrics.get("disk_used_percent", 0)), thresholds["disk_used_percent"], "%"),
        }
        now = __import__("time").time()
        with self._connect() as db:
            for key, (label, value, threshold, unit) in checks.items():
                active = value >= threshold
                previous = db.execute("SELECT id FROM alert_events WHERE server_id=? AND alert_key=? AND active=1 ORDER BY id DESC LIMIT 1", (server_id, key)).fetchone()
                if active and not previous:
                    db.execute("INSERT INTO alert_events(server_id,alert_key,message,active,created_at) VALUES(?,?,?,?,?)", (server_id, key, f"{label}達到 {value:.1f}{unit}（門檻 {threshold:.1f}{unit}）", 1, now))
                elif not active and previous:
                    db.execute("UPDATE alert_events SET active=0,resolved_at=? WHERE id=?", (now, previous["id"]))

    def evaluate_server_phase(self, server_id, phase, timeout_phase=None):
        now = __import__("time").time()
        with self._connect() as db:
            if timeout_phase:
                timeout = db.execute("SELECT id FROM alert_events WHERE server_id=? AND alert_key='server_timeout' AND active=1 ORDER BY id DESC LIMIT 1", (server_id,)).fetchone()
                if not timeout:
                    db.execute("INSERT INTO alert_events(server_id,alert_key,message,active,created_at) VALUES(?,?,?,?,?)", (server_id, "server_timeout", f"伺服器{('啟動' if timeout_phase == 'starting' else '停止')}逾時", 1, now))
            previous = db.execute("SELECT id FROM alert_events WHERE server_id=? AND alert_key='server_state' AND active=1 ORDER BY id DESC LIMIT 1", (server_id,)).fetchone()
            if phase == "error" and not previous:
                db.execute("INSERT INTO alert_events(server_id,alert_key,message,active,created_at) VALUES(?,?,?,?,?)", (server_id, "server_state", "伺服器進入異常狀態，可能是啟動失敗、程序非預期結束或停止逾時", 1, now))
            elif phase in {"running", "stopped"} and previous:
                db.execute("UPDATE alert_events SET active=0,resolved_at=? WHERE id=?", (now, previous["id"]))
            if phase in {"running", "stopped"} and not timeout_phase:
                db.execute("UPDATE alert_events SET active=0,resolved_at=? WHERE server_id=? AND alert_key='server_timeout' AND active=1", (now, server_id))

    def alert_events(self, server_id, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM alert_events WHERE server_id=? ORDER BY id DESC LIMIT ?", (server_id, limit)).fetchall()]

    def backup_retention(self, server_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM backup_settings WHERE server_id=?", (server_id,)).fetchone()
        return dict(row) if row else {"server_id": server_id, "retain_count": 10, "max_age_days": 0}

    def set_backup_retention(self, server_id, retain_count, max_age_days=0):
        retain_count, max_age_days = int(retain_count), int(max_age_days)
        if not 1 <= retain_count <= 1000 or not 0 <= max_age_days <= 36500:
            raise ValueError("保留數量須介於 1 至 1000；保存天數須介於 0 至 36500")
        with self._connect() as db:
            db.execute("INSERT INTO backup_settings(server_id,retain_count,max_age_days,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(server_id) DO UPDATE SET retain_count=excluded.retain_count,max_age_days=excluded.max_age_days,updated_at=excluded.updated_at",
                       (server_id, retain_count, max_age_days, self._now()))
        return self.backup_retention(server_id)

    def get_server_permissions(self, user_id: int, server_id: int):
        with self._connect() as db:
            rows = db.execute(
                "SELECT permission_key, allowed FROM server_permissions WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            ).fetchall()
        if rows:
            return {row["permission_key"] for row in rows if row["allowed"]}
        return set()

    def has_server_permission(self, user: dict, server_id: int, permission_key: str) -> bool:
        if user["role"] == "admin":
            return True
        return permission_key in self.get_server_permissions(user["user_id"], server_id)

    def set_server_permissions(self, user_id: int, server_id: int, permissions):
        selected = set(permissions) & set(ALL_PERMISSIONS)
        now = self._now()
        with self._connect() as db:
            db.execute("DELETE FROM server_permissions WHERE user_id = ? AND server_id = ?", (user_id, server_id))
            db.executemany(
                "INSERT INTO server_permissions(user_id, server_id, permission_key, allowed, updated_at) VALUES (?, ?, ?, 1, ?)",
                [(user_id, server_id, permission, now) for permission in selected],
            )
        return selected

    def record_metrics(self, server_id: int, metrics: dict):
        with self._connect() as db:
            db.execute("INSERT INTO resource_metrics(server_id,recorded_at,cpu_percent,memory_mb,memory_percent,memory_limit_percent,memory_limit_mb,disk_used_percent,disk_free_gb,disk_used_gb,disk_total_gb,net_sent_mb,net_recv_mb) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (server_id, __import__('time').time(), metrics.get('cpu_percent',0), metrics.get('memory_mb',0), metrics.get('memory_percent',0), metrics.get('memory_limit_percent',0), metrics.get('memory_limit_mb',0), metrics.get('disk_used_percent',0), metrics.get('disk_free_gb',0), metrics.get('disk_used_gb',0), metrics.get('disk_total_gb',0), metrics.get('net_sent_mb',0), metrics.get('net_recv_mb',0)))
            db.execute("DELETE FROM resource_metrics WHERE recorded_at < ?", (__import__('time').time()-86400,))

    def recent_metrics(self, server_id: int, limit=120):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM resource_metrics WHERE server_id=? ORDER BY id DESC LIMIT ?", (server_id, limit)).fetchall()][::-1]
