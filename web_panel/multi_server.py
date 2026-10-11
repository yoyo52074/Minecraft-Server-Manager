import os
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - optional until installed
    psutil = None


CORE_TYPES = {"Vanilla", "Paper", "Forge", "Fabric"}


class CoreAdapter:
    def __init__(self, core_type: str):
        if core_type not in CORE_TYPES:
            raise ValueError("不支援的伺服器核心")
        self.core_type = core_type

    def candidate_launchers(self):
        if self.core_type == "Fabric":
            return ["fabric-server-launch.jar", "server.jar"]
        if self.core_type == "Forge":
            return ["run.bat", "run.sh", "server.jar", "forge-server.jar"]
        if self.core_type == "Paper":
            return ["paper.jar", "server.jar"]
        return ["server.jar", "minecraft_server.jar"]

    def find_launcher(self, directory: Path, configured: str | None = None):
        candidates = [configured] if configured else []
        candidates.extend(self.candidate_launchers())
        for name in candidates:
            if name and (directory / name).is_file():
                return directory / name
        jars = sorted(directory.glob("*.jar"))
        return jars[0] if jars else None

    def command(self, profile: dict):
        directory = Path(profile["directory"])
        launcher = self.find_launcher(directory, profile.get("launcher"))
        if not launcher:
            raise FileNotFoundError("找不到伺服器啟動檔，請先上傳或安裝核心")
        if launcher.suffix.lower() == ".bat":
            return [str(launcher)], True
        java = profile.get("java_path") or "java"
        return [
            java,
            f"-Xms{int(profile.get('min_memory') or 1024)}M",
            f"-Xmx{int(profile.get('max_memory') or 2048)}M",
            "-jar",
            launcher.name,
            "nogui",
        ], False


class ServerProfileStore:
    def __init__(self, db):
        self.db = db
        self._init_schema()

    def _init_schema(self):
        with self.db._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS server_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    directory TEXT NOT NULL UNIQUE,
                    core_type TEXT NOT NULL CHECK(core_type IN ('Vanilla','Paper','Forge','Fabric')),
                    minecraft_version TEXT NOT NULL DEFAULT '',
                    java_path TEXT NOT NULL DEFAULT 'java',
                    min_memory INTEGER NOT NULL DEFAULT 1024,
                    max_memory INTEGER NOT NULL DEFAULT 2048,
                    server_port INTEGER NOT NULL DEFAULT 25565,
                    launcher TEXT NOT NULL DEFAULT '',
                    auto_restart INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Upgrade pre-existing history using each profile's configured memory limit.
            conn.execute("""UPDATE resource_metrics
                SET memory_limit_mb=COALESCE((SELECT max_memory FROM server_profiles WHERE id=resource_metrics.server_id),0),
                    memory_limit_percent=CASE WHEN COALESCE((SELECT max_memory FROM server_profiles WHERE id=resource_metrics.server_id),0)>0
                        THEN ROUND(memory_mb*100.0/(SELECT max_memory FROM server_profiles WHERE id=resource_metrics.server_id),1) ELSE 0 END
                WHERE memory_limit_percent=0 AND memory_mb>0 AND server_id IN (SELECT id FROM server_profiles)""")
            for profile in conn.execute("SELECT id,directory FROM server_profiles").fetchall():
                try:
                    disk_total_gb = shutil.disk_usage(profile["directory"]).total / 1024**3
                except OSError:
                    continue
                conn.execute("UPDATE resource_metrics SET disk_total_gb=?,disk_used_gb=MAX(0,?-disk_free_gb) WHERE server_id=? AND disk_total_gb=0",
                             (disk_total_gb, disk_total_gb, profile["id"]))

    def list(self):
        with self.db._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM server_profiles ORDER BY id").fetchall()]

    def get(self, profile_id: int):
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM server_profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else None

    def ensure_legacy(self, directory: str):
        directory = str(Path(directory).resolve())
        existing = next((item for item in self.list() if str(Path(item["directory"]).resolve()) == directory), None)
        if existing:
            return existing
        return self.create("既有伺服器", directory, "Paper")

    def create(self, name, directory, core_type, **values):
        name = name.strip()
        directory = str(Path(directory).expanduser().resolve())
        if not name or len(name) > 80:
            raise ValueError("伺服器名稱必須為 1 到 80 個字元")
        if core_type not in CORE_TYPES:
            raise ValueError("不支援的伺服器核心")
        Path(directory).mkdir(parents=True, exist_ok=True)
        now = self.db._now()
        fields = {
            "minecraft_version": str(values.get("minecraft_version", "")),
            "java_path": str(values.get("java_path", "java")),
            "min_memory": max(256, int(values.get("min_memory", 1024))),
            "max_memory": max(512, int(values.get("max_memory", 2048))),
            "server_port": int(values.get("server_port", 25565)),
            "launcher": str(values.get("launcher", "")),
            "auto_restart": int(bool(values.get("auto_restart", False))),
        }
        if fields["min_memory"] > fields["max_memory"]:
            raise ValueError("最小記憶體不可大於最大記憶體")
        with self.db._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO server_profiles
                (name, directory, core_type, minecraft_version, java_path, min_memory,
                 max_memory, server_port, launcher, auto_restart, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, directory, core_type, fields["minecraft_version"], fields["java_path"],
                 fields["min_memory"], fields["max_memory"], fields["server_port"],
                 fields["launcher"], fields["auto_restart"], now, now),
            )
            profile_id = cursor.lastrowid
        return self.get(profile_id)

    def update(self, profile_id: int, values: dict):
        current = self.get(profile_id)
        if not current:
            raise KeyError("找不到伺服器")
        allowed = {"name", "directory", "core_type", "minecraft_version", "java_path", "min_memory", "max_memory", "server_port", "launcher", "auto_restart", "enabled"}
        values = {key: value for key, value in values.items() if key in allowed}
        if "core_type" in values and values["core_type"] not in CORE_TYPES:
            raise ValueError("不支援的伺服器核心")
        if "directory" in values:
            values["directory"] = str(Path(values["directory"]).expanduser().resolve())
            Path(values["directory"]).mkdir(parents=True, exist_ok=True)
        if "min_memory" in values or "max_memory" in values:
            low = int(values.get("min_memory", current["min_memory"]))
            high = int(values.get("max_memory", current["max_memory"]))
            if low > high:
                raise ValueError("最小記憶體不可大於最大記憶體")
        if not values:
            return current
        values["updated_at"] = self.db._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.db._connect() as conn:
            conn.execute(f"UPDATE server_profiles SET {assignments} WHERE id = ?", (*values.values(), profile_id))
        return self.get(profile_id)

    def delete(self, profile_id: int, delete_files=False):
        profile = self.get(profile_id)
        if not profile:
            raise KeyError("找不到伺服器")
        if delete_files:
            shutil.rmtree(profile["directory"], ignore_errors=False)
        with self.db._connect() as conn:
            conn.execute("DELETE FROM server_profiles WHERE id = ?", (profile_id,))


class MultiServerManager:
    def __init__(self, store, legacy_bridge=None):
        self.store = store
        self.db = store.db
        self.legacy_bridge = legacy_bridge
        self._lock = threading.RLock()
        self._processes = {}
        self._logs = {}
        self._started_at = {}
        self._phases = {}
        self._phase_at = {}

    def _is_legacy(self, profile):
        return self.legacy_bridge and str(Path(profile["directory"]).resolve()) == str(Path(self.legacy_bridge.controller.server_directory).resolve())

    def status(self, profile_id):
        profile = self.store.get(profile_id)
        if not profile:
            return None
        if self._is_legacy(profile):
            result = self.legacy_bridge.status()
            phase = "running" if result.get("running") else self._phases.get(profile_id, "stopped")
            timeout_phase = None
            if not result.get("running") and phase in {"starting", "stopping"}:
                timeout = 30 if phase == "starting" else 15
                if time.time() - self._phase_at.get(profile_id, 0) > timeout:
                    timeout_phase = phase
                    phase = "error" if phase == "starting" else "stopped"
                    self._phases[profile_id] = phase
            result.update({"id": profile_id, "name": profile["name"], "core_type": profile["core_type"], "directory": profile["directory"], "server_port": profile["server_port"], "logs": self.legacy_bridge.logs(100), "phase": phase, "phase_timeout": timeout_phase})
            result["metrics"] = self._metrics(self.legacy_bridge.controller.server_process, profile["directory"], profile.get("max_memory"))
            return result
        process = self._processes.get(profile_id)
        running = bool(process and process.poll() is None)
        if not running:
            self._processes.pop(profile_id, None)
            self._started_at.pop(profile_id, None)
            if self._phases.get(profile_id) not in {"error", "stopped"}:
                self._phases[profile_id] = "error" if process and process.returncode not in (None, 0) else "stopped"
        metrics = self._metrics(process, profile["directory"], profile.get("max_memory"))
        return {
            "id": profile_id, "name": profile["name"], "core_type": profile["core_type"],
            "directory": profile["directory"], "server_port": profile["server_port"],
            "running": running,
            "phase": self._phases.get(profile_id, "running" if running else "stopped"),
            "uptime_seconds": int(time.time() - self._started_at[profile_id]) if running and profile_id in self._started_at else 0,
            "logs": self._logs.get(profile_id, [])[-100:],
            "metrics": metrics,
        }

    @staticmethod
    def _metrics(process, directory=None, memory_limit_mb=None):
        disk = {"disk_used_percent": 0.0, "disk_free_gb": 0.0, "disk_used_gb": 0.0, "disk_total_gb": 0.0, "net_sent_mb": 0.0, "net_recv_mb": 0.0}
        available = psutil is not None
        if psutil is not None:
            try:
                usage = psutil.disk_usage(str(directory or Path.cwd()))
                net = psutil.net_io_counters()
                disk = {"disk_used_percent": round(usage.percent, 1), "disk_free_gb": round(usage.free / 1024 / 1024 / 1024, 2), "disk_used_gb": round(usage.used / 1024 / 1024 / 1024, 2), "disk_total_gb": round(usage.total / 1024 / 1024 / 1024, 2), "net_sent_mb": round(net.bytes_sent / 1024 / 1024, 1), "net_recv_mb": round(net.bytes_recv / 1024 / 1024, 1)}
            except (psutil.Error, OSError):
                pass
        else:
            try:
                usage = shutil.disk_usage(str(directory or Path.cwd()))
                disk.update({"disk_used_percent": round((usage.used / usage.total) * 100, 1), "disk_free_gb": round(usage.free / 1024 / 1024 / 1024, 2), "disk_used_gb": round(usage.used / 1024 / 1024 / 1024, 2), "disk_total_gb": round(usage.total / 1024 / 1024 / 1024, 2)})
            except OSError:
                pass
        memory_limit_mb = max(float(memory_limit_mb or 0), 0)
        if not process or process.poll() is not None or psutil is None:
            return {"available": available, "cpu_percent": 0.0, "memory_mb": 0.0, "memory_percent": 0.0, "memory_limit_mb": memory_limit_mb, "memory_limit_percent": 0.0, **disk}
        try:
            proc = psutil.Process(process.pid)
            memory_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
            cpu_count = psutil.cpu_count(logical=True) or 1
            return {
                # Process.cpu_percent() can exceed 100 on multi-core hosts; normalize to total-machine capacity.
                "cpu_percent": round(proc.cpu_percent(interval=None) / cpu_count, 1),
                "memory_mb": memory_mb,
                "memory_percent": round(proc.memory_percent(), 1),
                "memory_limit_mb": memory_limit_mb,
                "memory_limit_percent": round(memory_mb / memory_limit_mb * 100, 1) if memory_limit_mb else 0.0,
                "available": available,
                **disk,
            }
        except (psutil.Error, OSError):
            return {"available": available, "cpu_percent": 0.0, "memory_mb": 0.0, "memory_percent": 0.0, "memory_limit_mb": memory_limit_mb, "memory_limit_percent": 0.0, **disk}

    def statuses(self):
        return [self.status(item["id"]) for item in self.store.list()]

    def logs(self, profile_id, limit=200):
        if self._is_legacy(self.store.get(profile_id)):
            return self.legacy_bridge.logs(limit)
        return self._logs.get(profile_id, [])[-max(1, min(limit, 500)):]

    def start(self, profile_id):
        profile = self.store.get(profile_id)
        if not profile:
            return False, "找不到伺服器"
        if self._is_legacy(profile):
            ok, message = self.legacy_bridge.start()
            if ok:
                self._phases[profile_id] = "starting"
                self._phase_at[profile_id] = time.time()
            return ok, message
        with self._lock:
            current = self._processes.get(profile_id)
            if current and current.poll() is None:
                return False, "伺服器已經在運行中"
            command, shell = CoreAdapter(profile["core_type"]).command(profile)
            process = subprocess.Popen(
                command,
                cwd=profile["directory"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=shell,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._processes[profile_id] = process
            self._started_at[profile_id] = time.time()
            self._phases[profile_id] = "running"
            self._phase_at[profile_id] = time.time()
            self._logs.setdefault(profile_id, []).append("---------- 伺服器正在啟動 ----------")
            threading.Thread(target=self._read_output, args=(profile_id, process), daemon=True).start()
            return True, "已送出啟動要求"

    def _read_output(self, profile_id, process):
        try:
            for line in iter(process.stdout.readline, ""):
                if line:
                    self._logs.setdefault(profile_id, []).append(line.rstrip())
                    self._logs[profile_id] = self._logs[profile_id][-500:]
        finally:
            self._logs.setdefault(profile_id, []).append("伺服器程序已停止")
            self._phases[profile_id] = "error" if process.returncode not in (None, 0) else "stopped"

    def stop(self, profile_id, force=False):
        profile = self.store.get(profile_id)
        if not profile:
            return False, "找不到伺服器"
        if self._is_legacy(profile):
            ok, message = self.legacy_bridge.stop()
            if ok:
                self._phases[profile_id] = "stopping"
                self._phase_at[profile_id] = time.time()
            return ok, message
        process = self._processes.get(profile_id)
        if not process or process.poll() is not None:
            return False, "伺服器目前沒有運行"
        self._phases[profile_id] = "stopping"
        self._phase_at[profile_id] = time.time()
        try:
            if force:
                process.kill()
            else:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        return True, "已送出停止要求"

    def send_command(self, profile_id, command):
        profile = self.store.get(profile_id)
        if self._is_legacy(profile):
            return self.legacy_bridge.send_command(command)
        process = self._processes.get(profile_id)
        if not process or process.poll() is not None:
            return False, "伺服器目前沒有運行"
        if not command.strip() or len(command) > 512:
            return False, "指令不可為空或超過 512 個字元"
        if not process.stdin:
            return False, "伺服器主控台輸入串流不可用"
        try:
            process.stdin.write(command.strip() + "\n")
            process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            return False, f"指令發送失敗：{exc}"
        return True, "指令已送出"
