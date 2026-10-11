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

    @staticmethod
    def _legacy_suppression_key(directory):
        return f"legacy_server_suppressed:{Path(directory).expanduser().resolve()}"

    def legacy_server_suppressed(self, directory):
        key = self._legacy_suppression_key(directory)
        with self._connect() as db:
            row = db.execute("SELECT value FROM system_metadata WHERE key=?", (key,)).fetchone()
        return bool(row and row["value"] == "1")

    def suppress_legacy_server(self, directory, suppressed=True):
        key = self._legacy_suppression_key(directory)
        with self._connect() as db:
            if suppressed:
                db.execute("INSERT OR REPLACE INTO system_metadata(key,value) VALUES(?, '1')", (key,))
            else:
                db.execute("DELETE FROM system_metadata WHERE key=?", (key,))

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
                    net_recv_mb REAL NOT NULL,
                    tps REAL NOT NULL DEFAULT 0, mspt REAL NOT NULL DEFAULT 0,
                    online_players INTEGER NOT NULL DEFAULT -1, max_players INTEGER NOT NULL DEFAULT 0,
                    heap_used_mb REAL NOT NULL DEFAULT 0, heap_max_mb REAL NOT NULL DEFAULT 0, heap_percent REAL NOT NULL DEFAULT 0,
                    gc_count INTEGER NOT NULL DEFAULT 0, gc_time_ms REAL NOT NULL DEFAULT 0,
                    disk_read_mb_s REAL NOT NULL DEFAULT 0, disk_write_mb_s REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS alert_settings (
                    server_id INTEGER PRIMARY KEY,
                    cpu_percent REAL NOT NULL DEFAULT 90,
                    memory_percent REAL NOT NULL DEFAULT 90,
                    disk_used_percent REAL NOT NULL DEFAULT 90,
                    tps_min REAL NOT NULL DEFAULT 18, mspt_max REAL NOT NULL DEFAULT 50, heap_percent REAL NOT NULL DEFAULT 90,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_notification_settings (
                    server_id INTEGER PRIMARY KEY, webhook_url TEXT NOT NULL DEFAULT '', cooldown_seconds INTEGER NOT NULL DEFAULT 900,
                    mute_start TEXT NOT NULL DEFAULT '', mute_end TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_notification_state (
                    server_id INTEGER PRIMARY KEY, last_sent_at REAL NOT NULL DEFAULT 0, last_event_id INTEGER NOT NULL DEFAULT 0
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
                    settings_retain_count INTEGER NOT NULL DEFAULT 50,
                    settings_max_age_days INTEGER NOT NULL DEFAULT 180,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backup_jobs (
                    id TEXT PRIMARY KEY, server_id INTEGER NOT NULL, status TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    current_file TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, started_at REAL, finished_at REAL, scope TEXT NOT NULL DEFAULT 'all'
                );
                CREATE TABLE IF NOT EXISTS player_records (
                    server_id INTEGER NOT NULL, player_key TEXT NOT NULL, name TEXT NOT NULL,
                    uuid TEXT NOT NULL DEFAULT '', first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    last_login REAL, is_online INTEGER NOT NULL DEFAULT 0, total_online_seconds REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(server_id,player_key)
                );
                CREATE TABLE IF NOT EXISTS player_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, player_key TEXT NOT NULL,
                    name TEXT NOT NULL, uuid TEXT NOT NULL DEFAULT '', joined_at REAL NOT NULL, left_at REAL
                );
                CREATE TABLE IF NOT EXISTS player_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, player_key TEXT NOT NULL,
                    name TEXT NOT NULL, uuid TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, created_at REAL NOT NULL
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
            for name in ("memory_limit_percent", "memory_limit_mb", "disk_used_gb", "disk_total_gb", "tps", "mspt", "online_players", "max_players", "heap_used_mb", "heap_max_mb", "heap_percent", "gc_count", "gc_time_ms", "disk_read_mb_s", "disk_write_mb_s"):
                if name not in metric_columns:
                    default = -1 if name == "online_players" else 0
                    db.execute(f"ALTER TABLE resource_metrics ADD COLUMN {name} REAL NOT NULL DEFAULT {default}")
            alert_columns={row[1] for row in db.execute("PRAGMA table_info(alert_settings)")}
            for name,default in (("tps_min",18),("mspt_max",50),("heap_percent",90)):
                if name not in alert_columns:db.execute(f"ALTER TABLE alert_settings ADD COLUMN {name} REAL NOT NULL DEFAULT {default}")
            backup_columns = {row[1] for row in db.execute("PRAGMA table_info(backup_settings)")}
            for name, default in (("settings_retain_count", 50), ("settings_max_age_days", 180)):
                if name not in backup_columns:
                    db.execute(f"ALTER TABLE backup_settings ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}")
            normalized = db.execute("SELECT value FROM system_metadata WHERE key='cpu_percent_machine_normalized'").fetchone()
            if not normalized:
                cpu_count = max(1, os.cpu_count() or 1)
                db.execute("UPDATE resource_metrics SET cpu_percent=MIN(cpu_percent / ?, 100)", (cpu_count,))
                db.execute("INSERT INTO system_metadata(key,value) VALUES('cpu_percent_machine_normalized','1')")
            db.execute("UPDATE backup_jobs SET status='interrupted',error='程式重新啟動時工作仍在執行；請重新建立備份',finished_at=? WHERE status IN ('queued','running','cancelling')", (__import__('time').time(),))
            now = __import__('time').time()
            db.execute("UPDATE player_records SET is_online=0 WHERE is_online=1")
            db.execute("UPDATE player_sessions SET left_at=? WHERE left_at IS NULL", (now,))

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
        defaults = {"server_id": server_id, "cpu_percent": 90.0, "memory_percent": 90.0, "disk_used_percent": 90.0,"tps_min":18.0,"mspt_max":50.0,"heap_percent":90.0}
        with self._connect() as db:
            row = db.execute("SELECT * FROM alert_settings WHERE server_id=?", (server_id,)).fetchone()
        return dict(row) if row else defaults

    def set_alert_thresholds(self, server_id, values):
        keys = ("cpu_percent", "memory_percent", "disk_used_percent", "tps_min", "mspt_max", "heap_percent")
        normalized = {key: float(values.get(key, self.alert_thresholds(server_id)[key])) for key in keys}
        if any(normalized[key] < 1 or normalized[key] > 100 for key in ("cpu_percent","memory_percent","disk_used_percent","heap_percent")):
            raise ValueError("CPU、記憶體、磁碟與 Heap 門檻必須介於 1 到 100")
        if not 1<=normalized['tps_min']<=20 or not 1<=normalized['mspt_max']<=10000:
            raise ValueError("TPS 門檻須 1–20；MSPT 門檻須 1–10000")
        with self._connect() as db:
            db.execute("INSERT INTO alert_settings(server_id,cpu_percent,memory_percent,disk_used_percent,tps_min,mspt_max,heap_percent,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(server_id) DO UPDATE SET cpu_percent=excluded.cpu_percent,memory_percent=excluded.memory_percent,disk_used_percent=excluded.disk_used_percent,tps_min=excluded.tps_min,mspt_max=excluded.mspt_max,heap_percent=excluded.heap_percent,updated_at=excluded.updated_at",
                       (server_id, normalized["cpu_percent"], normalized["memory_percent"], normalized["disk_used_percent"],normalized['tps_min'],normalized['mspt_max'],normalized['heap_percent'], self._now()))
        return self.alert_thresholds(server_id)

    def evaluate_metric_alerts(self, server_id, metrics):
        thresholds = self.alert_thresholds(server_id)
        configured_memory = metrics.get("memory_limit_percent")
        memory_load = float(configured_memory if configured_memory is not None else metrics.get("memory_percent", 0))
        checks = {
            "cpu": ("CPU", float(metrics.get("cpu_percent", 0)), thresholds["cpu_percent"], "%"),
            "memory": ("記憶體配置上限使用率", memory_load, thresholds["memory_percent"], "%"),
            "disk": ("磁碟使用率", float(metrics.get("disk_used_percent", 0)), thresholds["disk_used_percent"], "%"),
        }
        if float(metrics.get('tps') or 0)>0:
            checks['tps_low']=("TPS",float(metrics['tps']),thresholds['tps_min'],"",'low')
        if float(metrics.get('mspt') or 0)>0:
            checks['mspt_high']=("MSPT",float(metrics['mspt']),thresholds['mspt_max']," ms",'high')
        if float(metrics.get('heap_percent') or 0)>0:
            checks['heap']=("Java Heap 使用率",float(metrics['heap_percent']),thresholds['heap_percent'],"%")
        now = __import__("time").time()
        with self._connect() as db:
            for key, check in checks.items():
                label,value,threshold,unit=check[:4]
                direction=check[4] if len(check)>4 else 'high'
                active = value <= threshold if direction=='low' else value >= threshold
                previous = db.execute("SELECT id FROM alert_events WHERE server_id=? AND alert_key=? AND active=1 ORDER BY id DESC LIMIT 1", (server_id, key)).fetchone()
                if active and not previous:
                    operator='低於' if direction=='low' else '達到'
                    db.execute("INSERT INTO alert_events(server_id,alert_key,message,active,created_at) VALUES(?,?,?,?,?)", (server_id, key, f"{label}{operator} {value:.1f}{unit}（門檻 {threshold:.1f}{unit}）", 1, now))
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
        return dict(row) if row else {"server_id": server_id, "retain_count": 10, "max_age_days": 0, "settings_retain_count": 50, "settings_max_age_days": 180}

    def set_backup_retention(self, server_id, retain_count, max_age_days=0):
        retain_count, max_age_days = int(retain_count), int(max_age_days)
        if not 1 <= retain_count <= 1000 or not 0 <= max_age_days <= 36500:
            raise ValueError("保留數量須介於 1 至 1000；保存天數須介於 0 至 36500")
        with self._connect() as db:
            db.execute("INSERT INTO backup_settings(server_id,retain_count,max_age_days,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(server_id) DO UPDATE SET retain_count=excluded.retain_count,max_age_days=excluded.max_age_days,updated_at=excluded.updated_at",
                       (server_id, retain_count, max_age_days, self._now()))
        return self.backup_retention(server_id)

    def set_settings_backup_retention(self, server_id, retain_count, max_age_days):
        retain_count, max_age_days = int(retain_count), int(max_age_days)
        if not 1 <= retain_count <= 1000 or not 0 <= max_age_days <= 36500:
            raise ValueError("設定快照保留數量須介於 1 至 1000；保存天數須介於 0 至 36500")
        with self._connect() as db:
            db.execute("INSERT INTO backup_settings(server_id,settings_retain_count,settings_max_age_days,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(server_id) DO UPDATE SET settings_retain_count=excluded.settings_retain_count,settings_max_age_days=excluded.settings_max_age_days,updated_at=excluded.updated_at",
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
            names=('server_id','recorded_at','cpu_percent','memory_mb','memory_percent','memory_limit_percent','memory_limit_mb','disk_used_percent','disk_free_gb','disk_used_gb','disk_total_gb','net_sent_mb','net_recv_mb','tps','mspt','online_players','max_players','heap_used_mb','heap_max_mb','heap_percent','gc_count','gc_time_ms','disk_read_mb_s','disk_write_mb_s')
            values=[server_id,__import__('time').time()]+[metrics.get(name,-1 if name=='online_players' else 0) for name in names[2:]]
            db.execute(f"INSERT INTO resource_metrics({','.join(names)}) VALUES({','.join('?' for _ in names)})",values)
            db.execute("DELETE FROM resource_metrics WHERE recorded_at < ?", (__import__('time').time()-86400,))

    def alert_notification_settings(self, server_id, reveal=False):
        with self._connect() as db:row=db.execute('SELECT * FROM alert_notification_settings WHERE server_id=?',(server_id,)).fetchone()
        value=dict(row) if row else {'server_id':server_id,'webhook_url':'','cooldown_seconds':900,'mute_start':'','mute_end':''}
        if not reveal:value['webhook_configured']=bool(value.pop('webhook_url',''))
        return value

    def set_alert_notification_settings(self, server_id, webhook_url=None, cooldown_seconds=900, mute_start='', mute_end='', clear=False):
        current=self.alert_notification_settings(server_id,reveal=True)
        webhook='' if clear else (str(webhook_url or '').strip() or current['webhook_url'])
        cooldown_seconds=int(cooldown_seconds)
        if not 60<=cooldown_seconds<=86400:raise ValueError('通知冷卻時間須介於 60 到 86400 秒')
        for value in (mute_start,mute_end):
            if value and not __import__('re').fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(value)):raise ValueError('靜音時段請使用 HH:MM 格式')
        parsed=__import__('urllib.parse',fromlist=['urlparse']).urlparse(webhook)
        if webhook and not (parsed.scheme=='https' and ((parsed.hostname=='discord.com' and parsed.path.startswith('/api/webhooks/')) or (parsed.hostname=='hooks.slack.com' and parsed.path.startswith('/services/')))):
            raise ValueError('通知只接受 Discord 或 Slack 的 HTTPS Webhook URL')
        with self._connect() as db:
            db.execute('INSERT INTO alert_notification_settings(server_id,webhook_url,cooldown_seconds,mute_start,mute_end,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(server_id) DO UPDATE SET webhook_url=excluded.webhook_url,cooldown_seconds=excluded.cooldown_seconds,mute_start=excluded.mute_start,mute_end=excluded.mute_end,updated_at=excluded.updated_at',(server_id,webhook,cooldown_seconds,mute_start,mute_end,self._now()))
        return self.alert_notification_settings(server_id)

    def claim_alert_notification(self, server_id, event_id, now=None):
        now=now or __import__('time').time();settings=self.alert_notification_settings(server_id,reveal=True)
        if not settings['webhook_url']:return None
        def minutes(value):return int(value[:2])*60+int(value[3:]) if value else None
        start,end=minutes(settings['mute_start']),minutes(settings['mute_end']);current=__import__('datetime').datetime.now().hour*60+__import__('datetime').datetime.now().minute
        muted=(start is not None and end is not None and (start<=current<end if start<end else current>=start or current<end))
        if muted:return None
        with self._connect() as db:
            state=db.execute('SELECT * FROM alert_notification_state WHERE server_id=?',(server_id,)).fetchone()
            if state and (event_id<=state['last_event_id'] or now-state['last_sent_at']<settings['cooldown_seconds']):return None
            db.execute('INSERT INTO alert_notification_state(server_id,last_sent_at,last_event_id) VALUES(?,?,?) ON CONFLICT(server_id) DO UPDATE SET last_sent_at=excluded.last_sent_at,last_event_id=excluded.last_event_id',(server_id,now,event_id))
        return settings['webhook_url']

    def save_backup_job(self, job):
        with self._connect() as db:
            db.execute("""INSERT INTO backup_jobs(id,server_id,status,completed,total,current_file,filename,error,created_at,started_at,finished_at,scope)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,completed=excluded.completed,total=excluded.total,
                current_file=excluded.current_file,filename=excluded.filename,error=excluded.error,started_at=excluded.started_at,finished_at=excluded.finished_at,scope=excluded.scope""",
                (job['id'],job['server_id'],job['status'],job.get('completed',0),job.get('total',0),job.get('current_file',''),job.get('filename',''),job.get('error',''),job['created_at'],job.get('started_at'),job.get('finished_at'),job.get('scope','all')))
            if job.get('status') in {'completed','failed','cancelled','interrupted'}:
                db.execute("DELETE FROM backup_jobs WHERE id IN (SELECT id FROM backup_jobs WHERE server_id=? AND status IN ('completed','failed','cancelled','interrupted') ORDER BY created_at DESC LIMIT -1 OFFSET 500)",(job['server_id'],))

    def backup_job(self, job_id, server_id=None):
        with self._connect() as db:
            if server_id is None:
                row=db.execute("SELECT * FROM backup_jobs WHERE id=?",(job_id,)).fetchone()
            else:
                row=db.execute("SELECT * FROM backup_jobs WHERE id=? AND server_id=?",(job_id,server_id)).fetchone()
        return dict(row) if row else None

    def backup_jobs(self, server_id, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM backup_jobs WHERE server_id=? ORDER BY created_at DESC LIMIT ?",(server_id,min(max(int(limit),1),500))).fetchall()]

    def record_player_snapshot(self, server_id, players, now=None):
        now = now or __import__('time').time()
        current = {}
        for player in players:
            name = str(player.get('name', '')).strip()
            if not name:
                continue
            player_uuid = str(player.get('uuid', '') or '')
            key = player_uuid.lower() if player_uuid else name.casefold()
            current[key] = {'name': name, 'uuid': player_uuid}
        with self._connect() as db:
            old = {row['player_key']: dict(row) for row in db.execute('SELECT * FROM player_records WHERE server_id=?', (server_id,)).fetchall()}
            for key, player in current.items():
                previous = old.get(key)
                if previous:
                    if not previous['is_online']:
                        db.execute('INSERT INTO player_sessions(server_id,player_key,name,uuid,joined_at) VALUES(?,?,?,?,?)', (server_id,key,player['name'],player['uuid'],now))
                        db.execute('INSERT INTO player_events(server_id,player_key,name,uuid,action,created_at) VALUES(?,?,?,?,?,?)', (server_id,key,player['name'],player['uuid'],'join',now))
                    db.execute('UPDATE player_records SET name=?,uuid=?,last_seen=?,is_online=1,last_login=CASE WHEN is_online=0 THEN ? ELSE last_login END WHERE server_id=? AND player_key=?', (player['name'],player['uuid'] or previous['uuid'],now,now,server_id,key))
                else:
                    db.execute('INSERT INTO player_records(server_id,player_key,name,uuid,first_seen,last_seen,last_login,is_online) VALUES(?,?,?,?,?,?,?,1)', (server_id,key,player['name'],player['uuid'],now,now,now))
                    db.execute('INSERT INTO player_sessions(server_id,player_key,name,uuid,joined_at) VALUES(?,?,?,?,?)', (server_id,key,player['name'],player['uuid'],now))
                    db.execute('INSERT INTO player_events(server_id,player_key,name,uuid,action,created_at) VALUES(?,?,?,?,?,?)', (server_id,key,player['name'],player['uuid'],'join',now))
            for key, previous in old.items():
                if previous['is_online'] and key not in current:
                    opened = db.execute('SELECT id,joined_at FROM player_sessions WHERE server_id=? AND player_key=? AND left_at IS NULL ORDER BY id DESC LIMIT 1', (server_id,key)).fetchone()
                    duration = max(0,now-opened['joined_at']) if opened else 0
                    if opened:
                        db.execute('UPDATE player_sessions SET left_at=? WHERE id=?', (now,opened['id']))
                    db.execute('UPDATE player_records SET is_online=0,last_seen=?,total_online_seconds=total_online_seconds+? WHERE server_id=? AND player_key=?', (now,duration,server_id,key))
                    db.execute('INSERT INTO player_events(server_id,player_key,name,uuid,action,created_at) VALUES(?,?,?,?,?,?)', (server_id,key,previous['name'],previous['uuid'],'leave',now))

    def player_history(self, server_id, query='', limit=100, offset=0):
        query = str(query).strip().casefold()
        limit = min(max(int(limit),1),500); offset = max(int(offset),0)
        where = 'server_id=?'; args = [server_id]
        if query:
            where += ' AND (LOWER(name) LIKE ? OR LOWER(uuid) LIKE ?)'; args.extend([f'%{query}%',f'%{query}%'])
        with self._connect() as db:
            total = db.execute(f'SELECT COUNT(*) FROM player_records WHERE {where}',args).fetchone()[0]
            players = [dict(row) for row in db.execute(f'SELECT * FROM player_records WHERE {where} ORDER BY last_seen DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()]
        return {'players':players,'total':total,'limit':limit,'offset':offset}

    def player_sessions(self, server_id, player_key=None, limit=500):
        query='SELECT * FROM player_sessions WHERE server_id=?'; args=[server_id]
        if player_key:
            query+=' AND player_key=?'; args.append(player_key)
        query+=' ORDER BY joined_at DESC LIMIT ?'; args.append(min(max(int(limit),1),2000))
        with self._connect() as db:
            return [dict(row) for row in db.execute(query,args).fetchall()]

    def player_events(self, server_id, player_key=None, limit=500):
        query='SELECT * FROM player_events WHERE server_id=?'; args=[server_id]
        if player_key:
            query+=' AND player_key=?'; args.append(player_key)
        query+=' ORDER BY id DESC LIMIT ?'; args.append(min(max(int(limit),1),2000))
        with self._connect() as db:
            return [dict(row) for row in db.execute(query,args).fetchall()]

    def recent_metrics(self, server_id: int, limit=120):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM resource_metrics WHERE server_id=? ORDER BY id DESC LIMIT ?", (server_id, limit)).fetchall()][::-1]
