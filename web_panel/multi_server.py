import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from core_metadata import java_requirement_for_core, java_version_compatible
from world_lock import WorldLockProbeError, is_world_locked

try:
    import psutil
except ImportError:  # pragma: no cover - optional until installed
    psutil = None


CORE_TYPES = {"Vanilla", "Paper", "Forge", "Fabric"}
EULA_REQUIRED_MESSAGE = (
    "首次啟動前必須閱讀並同意 Minecraft EULA（https://aka.ms/MinecraftEULA）；"
    "請確認同意後再啟動。"
)
WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class CoreAdapter:
    def __init__(self, core_type: str):
        if core_type not in CORE_TYPES:
            raise ValueError("不支援的伺服器核心")
        self.core_type = core_type

    def candidate_launchers(self):
        if self.core_type == "Fabric":
            return ["fabric-server-launch.jar", "server.jar"]
        if self.core_type == "Forge":
            return ["run.bat", "run.sh", "forge-server.jar"]
        if self.core_type == "Paper":
            return ["paper.jar", "server.jar"]
        return ["server.jar", "minecraft_server.jar"]

    def find_launcher(self, directory: Path, configured: str | None = None, allow_scripts=True):
        directory = Path(directory).resolve()
        if self.core_type == "Forge":
            candidates = []
            if allow_scripts:
                candidates.extend(name for name in (configured,) if name in {"run.bat", "run.sh"})
                candidates.extend(["run.bat", "run.sh"])
            candidates.append("forge-server.jar")
            candidates.extend(
                path.name for path in sorted(directory.glob("forge-*.jar"))
                if "installer" not in path.name.casefold()
            )
            if configured and (allow_scripts or Path(configured).suffix.lower() not in {".bat", ".sh"}):
                candidates.append(configured)
            candidates.append("server.jar")
        else:
            candidates = [configured] if configured and (allow_scripts or Path(configured).suffix.lower() not in {".bat", ".sh"}) else []
            candidates.extend(self.candidate_launchers())

        def safe_candidate(name):
            raw = str(name or "")
            if (not re.fullmatch(r"[A-Za-z0-9._+-]{1,180}", raw)
                    or PurePosixPath(raw).name != raw or PureWindowsPath(raw).name != raw):
                return None
            candidate = directory / raw
            if candidate.is_symlink():
                return None
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(directory)
            except (OSError, ValueError):
                return None
            return resolved if resolved.is_file() else None

        for name in candidates:
            candidate = safe_candidate(name)
            if candidate:
                return candidate
        jars = sorted(directory.glob("*.jar"))
        if self.core_type == "Forge":
            forge_jars = [path for path in jars
                          if "forge" in path.name.casefold() and "installer" not in path.name.casefold()]
            if forge_jars:
                jars = forge_jars
        return safe_candidate(jars[0].name) if jars else None

    def command(self, profile: dict, java_path=None, allow_scripts=True):
        directory = Path(profile["directory"])
        launcher = self.find_launcher(directory, profile.get("launcher"), allow_scripts=allow_scripts)
        if not launcher:
            raise FileNotFoundError("找不到伺服器啟動檔，請先上傳或安裝核心")
        if not allow_scripts and launcher.suffix.lower() in {".bat", ".sh"}:
            raise PermissionError("非管理員不可執行伺服器資料夾中的 shell／批次啟動腳本")
        if launcher.suffix.lower() == ".bat":
            if os.name != "nt":
                raise ValueError("Windows 批次啟動檔只能在 Windows 上執行")
            # The basename is validated by find_launcher; pass only that safe token to cmd.
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", f'call "{launcher.name}"']
        if launcher.suffix.lower() == ".sh":
            if os.name == "nt":
                raise ValueError("POSIX shell 啟動檔不能在 Windows 上執行")
            return ["/bin/sh", launcher.name]
        java = java_path or profile.get("java_path") or "java"
        return [
            java,
            f"-Xms{int(profile.get('min_memory') or 1024)}M",
            f"-Xmx{int(profile.get('max_memory') or 2048)}M",
            "-jar",
            launcher.name,
            "nogui",
        ]

    @staticmethod
    def required_java_version(profile, launcher=None):
        directory = Path(profile["directory"])
        metadata = {}
        try:
            with (directory / "core-info.json").open(encoding="utf-8") as file:
                metadata = json.load(file)
        except (OSError, ValueError, TypeError):
            pass
        version = profile.get("minecraft_version") or metadata.get("minecraft_version")
        if not version or not re.search(r"\d+\.\d+", str(version)):
            launcher = launcher or CoreAdapter(profile["core_type"]).find_launcher(
                directory, profile.get("launcher")
            )
            if launcher:
                match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)", launcher.name)
                if match:
                    version = match.group(1)
        required = java_requirement_for_core(profile.get("core_type"), version)
        return required, str(version or "未知版本")

    @staticmethod
    def java_major_version(executable):
        result = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        match = re.search(r'version\s+"(\d+)(?:\.(\d+))?', output, re.IGNORECASE)
        if match:
            major = int(match.group(1))
            return int(match.group(2)) if major == 1 and match.group(2) else major
        match = re.search(r"(?:openjdk|java)\s+(\d+)", output, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    @classmethod
    def resolve_java(cls, profile, required_version, minecraft_version):
        configured = str(profile.get("java_path") or "java").strip().strip('"')
        candidates = [configured]
        path_java = shutil.which("java")
        if path_java:
            candidates.append(path_java)
        checked = set()
        found = []
        for candidate in candidates:
            executable = shutil.which(candidate) or candidate
            if executable in checked:
                continue
            checked.add(executable)
            try:
                major = cls.java_major_version(executable)
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
            if major:
                found.append((executable, major))
                if java_version_compatible(required_version, major):
                    return executable, major
        requirement = (f"Minecraft {minecraft_version} 需要 Java {required_version}（舊版需相同主版本）。"
                       if required_version <= 16 else
                       f"Minecraft {minecraft_version} 需要 Java {required_version} 或更新版本。")
        if found:
            detected = ", ".join(f"Java {major}（{executable}）" for executable, major in found)
            raise ValueError(
                f"{requirement}目前找到不相容的 Java：{detected}。系統會嘗試自動準備 Java {required_version}。"
            )
        raise ValueError(
            f"{requirement}找不到可執行的 Java。請安裝 Java {required_version}，或使用「伺服器設定 → Java 路徑」自動搜尋與安裝。"
        )


class ServerProfileStore:
    def __init__(self, db, default_directory=None):
        self.db = db
        self.default_directory = Path(default_directory or Path(db.path).resolve().parent).expanduser().resolve()
        self._init_schema()

    def _default_server_directory(self, name):
        folder = unicodedata.normalize("NFKC", str(name))
        folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", folder).strip(" .")
        if not folder:
            folder = "Minecraft Server"
        if folder.split(".", 1)[0].casefold() in WINDOWS_DEVICE_NAMES:
            folder = "_" + folder
        used = {os.path.normcase(str(Path(item["directory"]).resolve())) for item in self.list()}
        candidate_name = folder
        suffix = 2
        candidate = self.default_directory / candidate_name
        while os.path.lexists(candidate) or os.path.normcase(str(candidate.resolve())) in used:
            candidate_name = f"{folder} ({suffix})"
            candidate = self.default_directory / candidate_name
            suffix += 1
        return candidate

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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(server_profiles)").fetchall()}
            for column in ("playit_primary_address", "playit_backup_address"):
                if column in columns:
                    conn.execute(f"ALTER TABLE server_profiles DROP COLUMN {column}")
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
        if self.db.legacy_server_suppressed(directory):
            return None
        return self.create("既有伺服器", directory, "Paper")

    def create(self, name, directory, core_type, **values):
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("伺服器名稱必須為 1 到 80 個字元")
        if not str(directory or "").strip():
            directory = self._default_server_directory(name)
        directory = str(Path(directory).expanduser().resolve())
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
        self.db.suppress_legacy_server(directory, False)
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
        fields = ("name", "directory", "core_type", "minecraft_version", "java_path", "min_memory",
                  "max_memory", "server_port", "launcher", "auto_restart", "enabled")
        parameters = []
        for field in fields:
            parameters.extend((int(field in values), values.get(field)))
        parameters.extend((values["updated_at"], profile_id))
        with self.db._connect() as conn:
            conn.execute("""UPDATE server_profiles SET
                name=CASE WHEN ? THEN ? ELSE name END,
                directory=CASE WHEN ? THEN ? ELSE directory END,
                core_type=CASE WHEN ? THEN ? ELSE core_type END,
                minecraft_version=CASE WHEN ? THEN ? ELSE minecraft_version END,
                java_path=CASE WHEN ? THEN ? ELSE java_path END,
                min_memory=CASE WHEN ? THEN ? ELSE min_memory END,
                max_memory=CASE WHEN ? THEN ? ELSE max_memory END,
                server_port=CASE WHEN ? THEN ? ELSE server_port END,
                launcher=CASE WHEN ? THEN ? ELSE launcher END,
                auto_restart=CASE WHEN ? THEN ? ELSE auto_restart END,
                enabled=CASE WHEN ? THEN ? ELSE enabled END,
                updated_at=? WHERE id=?""", parameters)
        return self.get(profile_id)

    def delete(self, profile_id: int, delete_files=False):
        profile = self.get(profile_id)
        if not profile:
            raise KeyError("找不到伺服器")
        if delete_files:
            shutil.rmtree(profile["directory"], ignore_errors=False)
        self.db.suppress_legacy_server(profile["directory"], True)
        with self.db._connect() as conn:
            conn.execute("DELETE FROM server_permissions WHERE server_id = ?", (profile_id,))
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
        self._io_samples = {}
        self._cpu_samples = {}
        self._cpu_lock = threading.Lock()
        self._core_updates = set()

    def _with_io_rates(self, profile_id, metrics):
        now=time.monotonic();read_bytes=float(metrics.pop("disk_read_bytes",0) or 0);write_bytes=float(metrics.pop("disk_write_bytes",0) or 0)
        previous=self._io_samples.get(profile_id);read_rate=write_rate=0.0
        if previous:
            elapsed=max(now-previous[0],0.001)
            if read_bytes>=previous[1]:read_rate=(read_bytes-previous[1])/1024**2/elapsed
            if write_bytes>=previous[2]:write_rate=(write_bytes-previous[2])/1024**2/elapsed
        self._io_samples[profile_id]=(now,read_bytes,write_bytes)
        metrics["disk_read_mb_s"]=round(read_rate,3);metrics["disk_write_mb_s"]=round(write_rate,3)
        return metrics

    def _sample_cpu_percent(self, profile_id, process):
        if not process or psutil is None:
            with self._cpu_lock:
                self._cpu_samples.pop(profile_id, None)
            return 0.0
        try:
            if process.poll() is not None:
                with self._cpu_lock:
                    self._cpu_samples.pop(profile_id, None)
                return 0.0
            pid = int(process.pid)
        except (AttributeError, OSError, TypeError, ValueError):
            return 0.0

        now = time.monotonic()
        with self._cpu_lock:
            sample = self._cpu_samples.get(profile_id)
            if sample and sample["pid"] == pid:
                if now - sample["sampled_at"] < 1.0:
                    return sample["percent"]
                sampler = sample["process"]
            else:
                try:
                    sampler = psutil.Process(pid)
                    sampler.cpu_percent(interval=None)  # establish a baseline for the next sample
                except Exception:
                    self._cpu_samples.pop(profile_id, None)
                    return 0.0
                self._cpu_samples[profile_id] = {
                    "pid": pid,
                    "process": sampler,
                    "sampled_at": now,
                    "percent": 0.0,
                }
                return 0.0

            try:
                percent = max(0.0, float(sampler.cpu_percent(interval=None)))
            except Exception:
                self._cpu_samples.pop(profile_id, None)
                return 0.0
            sample.update(sampled_at=now, percent=percent)
            return percent

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
            process = self.legacy_bridge.controller.server_process
            cpu_percent = self._sample_cpu_percent(profile_id, process)
            result["metrics"] = self._with_io_rates(
                profile_id,
                self._metrics(process, profile["directory"], profile.get("max_memory"), cpu_percent),
            )
            return result
        process = self._processes.get(profile_id)
        running = bool(process and process.poll() is None)
        if not running:
            self._processes.pop(profile_id, None)
            self._started_at.pop(profile_id, None)
            if self._phases.get(profile_id) not in {"error", "stopped"}:
                self._phases[profile_id] = "error" if process and process.returncode not in (None, 0) else "stopped"
        cpu_percent = self._sample_cpu_percent(profile_id, process)
        metrics = self._with_io_rates(
            profile_id,
            self._metrics(process, profile["directory"], profile.get("max_memory"), cpu_percent),
        )
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
    def _metrics(process, directory=None, memory_limit_mb=None, sampled_cpu_percent=None):
        disk = {"disk_used_percent": 0.0, "disk_free_gb": 0.0, "disk_used_gb": 0.0, "disk_total_gb": 0.0, "net_sent_mb": 0.0, "net_recv_mb": 0.0,"disk_read_bytes":0,"disk_write_bytes":0}
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
            try:
                io=proc.io_counters();disk_read_bytes=int(io.read_bytes);disk_write_bytes=int(io.write_bytes)
            except Exception:disk_read_bytes=disk_write_bytes=0
            cpu_count = psutil.cpu_count(logical=True) or 1
            raw_cpu_percent = (
                proc.cpu_percent(interval=None)
                if sampled_cpu_percent is None
                else sampled_cpu_percent
            )
            return {
                # Process.cpu_percent() can exceed 100 on multi-core hosts; normalize to total-machine capacity.
                "cpu_percent": round(raw_cpu_percent / cpu_count, 1),
                "memory_mb": memory_mb,
                "memory_percent": round(proc.memory_percent(), 1),
                "memory_limit_mb": memory_limit_mb,
                "memory_limit_percent": round(memory_mb / memory_limit_mb * 100, 1) if memory_limit_mb else 0.0,
                "disk_read_bytes":disk_read_bytes,"disk_write_bytes":disk_write_bytes,
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

    def requires_eula(self, profile_id):
        profile = self.store.get(profile_id)
        if not profile:
            return False
        adapter = CoreAdapter(profile["core_type"])
        if not adapter.find_launcher(Path(profile["directory"]), profile.get("launcher")):
            return False
        path = Path(profile["directory"]) / "eula.txt"
        try:
            return not any(
                line.strip().casefold() == "eula=true"
                for line in path.read_text(encoding="utf-8-sig").splitlines()
            )
        except OSError:
            return True

    @staticmethod
    def _record_eula_acceptance(profile):
        path = Path(profile["directory"]) / "eula.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("eula=true\n", encoding="utf-8")

    def start(self, profile_id, accept_eula=False, allow_scripts=False):
        profile = self.store.get(profile_id)
        if not profile:
            return False, "找不到伺服器"
        if self._is_legacy(profile):
            if not allow_scripts:
                return False, "此舊版桌面伺服器尚未隔離啟動環境，只有管理員可以啟動"
            if self.requires_eula(profile_id):
                if not accept_eula:
                    return False, EULA_REQUIRED_MESSAGE
                try:
                    self._record_eula_acceptance(profile)
                except OSError as exc:
                    return False, f"無法寫入 eula.txt：{exc}"
            ok, message = self.legacy_bridge.start()
            if ok:
                self._phases[profile_id] = "starting"
                self._phase_at[profile_id] = time.time()
            return ok, message
        with self._lock:
            if profile_id in self._core_updates:
                return False, "伺服器核心更新中，完成後才能啟動"
            current = self._processes.get(profile_id)
            if current and current.poll() is None:
                return False, "伺服器已經在運行中"
            try:
                if is_world_locked(profile["directory"]):
                    return False, "世界存檔目前已被鎖定，可能已有另一個 Java 伺服器程序正在使用。請先正常停止該程序；不要刪除 session.lock。"
            except WorldLockProbeError as exc:
                return False, str(exc)
            adapter = CoreAdapter(profile["core_type"])
            launcher = adapter.find_launcher(Path(profile["directory"]), profile.get("launcher"), allow_scripts=allow_scripts)
            if not launcher:
                return False, "找不到伺服器啟動檔，請先上傳或安裝核心"
            required_java, minecraft_version = adapter.required_java_version(profile, launcher)
            try:
                java_path, java_major = adapter.resolve_java(profile, required_java, minecraft_version)
            except ValueError as exc:
                return False, str(exc)
            if self.requires_eula(profile_id):
                if not accept_eula:
                    return False, EULA_REQUIRED_MESSAGE
                try:
                    self._record_eula_acceptance(profile)
                except OSError as exc:
                    return False, f"無法寫入 eula.txt：{exc}"
            command = adapter.command(profile, java_path, allow_scripts=allow_scripts)
            java_bin = Path(java_path).resolve().parent
            process_env = os.environ.copy()
            if java_bin.is_dir():
                process_env["JAVA_HOME"] = str(java_bin.parent)
                process_env["PATH"] = str(java_bin) + os.pathsep + process_env.get("PATH", "")
            process = subprocess.Popen(
                command,
                cwd=profile["directory"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=process_env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._processes[profile_id] = process
            self._started_at[profile_id] = time.time()
            self._phases[profile_id] = "running"
            self._phase_at[profile_id] = time.time()
            self._logs.setdefault(profile_id, []).append(f"使用 Java {java_major}：{java_path}")
            self._logs[profile_id].append("---------- 伺服器正在啟動 ----------")
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
                return True, "已強制結束伺服器程序"
            else:
                process.stdin.write("stop\n")
                process.stdin.flush()
                return True, "已送出正常停止要求；等待世界存檔與 Java 程序完全結束"
        except (OSError, BrokenPipeError) as exc:
            return False, f"無法送出停止指令：{exc}；伺服器仍可能在運行，請確認狀態後再操作"

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
