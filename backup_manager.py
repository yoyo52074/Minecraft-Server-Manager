import os
import json
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath


def _is_link_or_reparse(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _open_regular_file(path):
    if _is_link_or_reparse(path):
        raise ValueError("檔案不可為符號連結或 Windows reparse point")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("只允許讀取一般檔案")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


class BackupCancelledError(Exception):
    """Raised when a backup operation is cancelled by its caller."""


class BackupManager:
    BACKUP_DIR_NAME = "backups"
    MAX_RESTORE_ENTRIES = 10000
    MAX_RESTORE_BYTES = 8 * 1024 * 1024 * 1024
    INCLUDED_FILES = {
        "server.properties", "eula.txt", "ops.json", "whitelist.json",
        "banned-players.json", "banned-ips.json", "core-info.json",
    }
    INCLUDED_DIRS = {
        "world", "world_nether", "world_the_end", "plugins", "mods", "config",
        "resourcepacks", "datapacks", "logs", "crash-reports",
    }
    WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

    def __init__(self, server_directory):
        self.server_directory = os.path.realpath(server_directory)
        self.backup_directory = os.path.join(self.server_directory, self.BACKUP_DIR_NAME)
        if _is_link_or_reparse(self.backup_directory):
            raise ValueError("備份目錄不可為符號連結或 Windows reparse point")
        os.makedirs(self.backup_directory, exist_ok=True)
        self.last_pre_restore_backup = None

    def _included_files(self, scope="all"):
        allowed = {"all", "worlds", "plugins", "mods", "config"}
        if scope not in allowed:
            raise ValueError("不支援的備份範圍")
        files = []
        server_root = os.path.realpath(self.server_directory)

        def contained(path):
            try:
                return os.path.commonpath((server_root, os.path.realpath(path))) == server_root
            except ValueError:
                return False

        selected_files = self.INCLUDED_FILES if scope in {"all", "config"} else set()
        selected_dirs = self.INCLUDED_DIRS if scope == "all" else ({"world", "world_nether", "world_the_end"} if scope == "worlds" else {scope} if scope in {"plugins", "mods", "config"} else set())
        for name in sorted(selected_files):
            path = os.path.join(self.server_directory, name)
            if not _is_link_or_reparse(path) and contained(path) and os.path.isfile(path):
                files.append((path, name))
        for directory in sorted(selected_dirs):
            base = os.path.join(self.server_directory, directory)
            if _is_link_or_reparse(base) or not contained(base) or not os.path.isdir(base):
                continue
            for root, dirs, names in os.walk(base):
                dirs[:] = sorted(d for d in dirs if not _is_link_or_reparse(os.path.join(root, d)))
                for name in sorted(names):
                    path = os.path.join(root, name)
                    if os.path.isfile(path) and not _is_link_or_reparse(path) and contained(path):
                        files.append((path, os.path.relpath(path, self.server_directory)))
        return files

    def create_backup(self, label="manual", *, scope="all", progress_callback=None, cancel_event=None):
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label))[:64] or "backup"
        archive_path = os.path.join(self.backup_directory, f"backup-{timestamp}-{safe_label}-{uuid.uuid4().hex[:10]}.zip")
        descriptor, part_path = tempfile.mkstemp(prefix=".mcm-backup-", suffix=".part", dir=self.backup_directory)
        os.close(descriptor)
        files = self._included_files(scope)
        total = len(files)
        try:
            with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for index, (file_path, arcname) in enumerate(files, 1):
                    if cancel_event and cancel_event.is_set():
                        raise BackupCancelledError("備份已取消")
                    with _open_regular_file(file_path) as source, archive.open(arcname, "w") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    if progress_callback:
                        progress_callback(index, total, arcname)
            if cancel_event and cancel_event.is_set():
                raise BackupCancelledError("備份已取消")
            with zipfile.ZipFile(part_path, "r") as archive:
                bad = archive.testzip()
                if bad:
                    raise zipfile.BadZipFile(f"備份完整性檢查失敗：{bad}")
            os.replace(part_path, archive_path)
            return archive_path
        except Exception:
            try:
                os.remove(part_path)
            except FileNotFoundError:
                pass
            raise

    def list_backups(self):
        if _is_link_or_reparse(self.backup_directory):
            raise ValueError("備份目錄不可為符號連結或 Windows reparse point")
        return sorted(
            (os.path.join(self.backup_directory, name) for name in os.listdir(self.backup_directory)
             if name.lower().endswith(".zip") and not _is_link_or_reparse(os.path.join(self.backup_directory, name))
             and os.path.isfile(os.path.join(self.backup_directory, name))),
            key=os.path.getmtime,
            reverse=True,
        )

    def create_settings_backup(self, profile_snapshot=None):
        """Create a small, timestamped rollback archive before settings edits."""
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
        settings_dir = os.path.join(self.backup_directory, "settings")
        if _is_link_or_reparse(self.backup_directory):
            raise ValueError("備份目錄不可為符號連結或 Windows reparse point")
        if _is_link_or_reparse(settings_dir):
            raise ValueError("設定備份目錄不可為符號連結或 Windows reparse point")
        os.makedirs(settings_dir, exist_ok=True)
        if os.path.commonpath((os.path.realpath(self.backup_directory), os.path.realpath(settings_dir))) != os.path.realpath(self.backup_directory):
            raise ValueError("設定備份目錄不可跳出備份資料夾")
        archive_path = os.path.join(settings_dir, f"settings-{timestamp}-{uuid.uuid4().hex[:10]}.zip")
        descriptor, part_path = tempfile.mkstemp(prefix=".mcm-settings-", suffix=".part", dir=settings_dir)
        os.close(descriptor)
        try:
            with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
                properties = os.path.join(self.server_directory, "server.properties")
                if os.path.lexists(properties):
                    with _open_regular_file(properties) as source:
                        if os.fstat(source.fileno()).st_size > 4 * 1024 * 1024:
                            raise ValueError("server.properties 超過 4 MiB 設定備份上限")
                        with archive.open("server.properties", "w") as output:
                            shutil.copyfileobj(source, output, length=64 * 1024)
                archive.writestr("profile.json", json.dumps(profile_snapshot or {}, ensure_ascii=False, indent=2))
            os.replace(part_path, archive_path)
            return archive_path
        except Exception:
            try:
                os.remove(part_path)
            except FileNotFoundError:
                pass
            raise

    def cleanup_retention(self, retain=10, max_age_days=None):
        retain = max(1, int(retain))
        backups = self.list_backups()
        cutoff = None
        if max_age_days is not None:
            cutoff = datetime.now().timestamp() - max(0, int(max_age_days)) * 86400
        removed = []
        for index, path in enumerate(backups):
            expired = cutoff is not None and os.path.getmtime(path) < cutoff
            if index >= retain or expired:
                os.remove(path)
                removed.append(path)
        return removed

    def cleanup_settings_retention(self, retain=50, max_age_days=180):
        settings_dir = os.path.join(self.backup_directory, "settings")
        if _is_link_or_reparse(self.backup_directory) or _is_link_or_reparse(settings_dir):
            raise ValueError("設定備份目錄不可為符號連結或 Windows reparse point")
        if not os.path.isdir(settings_dir):
            return []
        backup_root = os.path.realpath(self.backup_directory)
        settings_root = os.path.realpath(settings_dir)
        if os.path.commonpath((backup_root, settings_root)) != backup_root:
            raise ValueError("設定備份目錄不可跳出備份資料夾")
        retain = max(1, int(retain))
        cutoff = time.time() - max(0, int(max_age_days)) * 86400 if max_age_days else None
        snapshots = sorted((os.path.join(settings_dir, name) for name in os.listdir(settings_dir)
                            if name.lower().endswith(".zip") and not _is_link_or_reparse(os.path.join(settings_dir, name))
                            and os.path.isfile(os.path.join(settings_dir, name))),
                           key=os.path.getmtime, reverse=True)
        removed = []
        for index, path in enumerate(snapshots):
            if index >= retain or (cutoff is not None and os.path.getmtime(path) < cutoff):
                os.remove(path)
                removed.append(path)
        return removed

    @staticmethod
    def _validate_member(member):
        raw = member.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if (not raw or raw == "." or "\x00" in raw or raw.startswith("/") or path.is_absolute() or ".." in path.parts
                or ":" in raw or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}):
            raise ValueError("備份檔包含不安全的路徑")
        for component in path.parts:
            normalized = unicodedata.normalize("NFKC", component)
            if normalized != normalized.rstrip(" .") or normalized.split(".", 1)[0].casefold() in BackupManager.WINDOWS_DEVICE_NAMES:
                raise ValueError("備份檔包含 Windows 不安全的路徑名稱")
        return raw

    def verify_backup(self, archive_path):
        if _is_link_or_reparse(archive_path):
            raise ValueError("備份檔不可為符號連結或 Windows reparse point")
        archive_path = os.path.realpath(archive_path)
        if not os.path.isfile(archive_path):
            raise FileNotFoundError(archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            if len(members) > self.MAX_RESTORE_ENTRIES or sum(item.file_size for item in members) > self.MAX_RESTORE_BYTES:
                raise ValueError("備份項目過多或解壓縮容量超過 8 GB")
            for member in members:
                self._validate_member(member)
            bad = archive.testzip()
            if bad:
                raise zipfile.BadZipFile(f"備份完整性檢查失敗：{bad}")
        return True

    def restore_backup(self, archive_path, *, create_safety_backup=True):
        if _is_link_or_reparse(archive_path) or _is_link_or_reparse(self.backup_directory):
            raise ValueError("備份檔或備份目錄不可為符號連結或 Windows reparse point")
        root = os.path.realpath(self.server_directory)
        backup_root = os.path.realpath(self.backup_directory)
        archive_path = os.path.realpath(archive_path)
        if os.path.commonpath((backup_root, archive_path)) != backup_root:
            raise ValueError("備份檔必須位於伺服器資料夾內")
        if os.path.commonpath((root, archive_path)) != root:
            raise ValueError("只能還原備份目錄中的檔案")
        self.verify_backup(archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            safe_members = []
            kinds = {}
            allowed_roots = {unicodedata.normalize("NFKC", item).casefold().rstrip(" .") for item in self.INCLUDED_FILES | self.INCLUDED_DIRS}
            # Preflight every destination, including existing parent symlinks, before writing.
            for member in members:
                name = self._validate_member(member)
                relative = PurePosixPath(name)
                parts = relative.parts
                key = tuple(unicodedata.normalize("NFKC", part).casefold().rstrip(" .") for part in parts)
                if not parts or key[0] not in allowed_roots:
                    raise ValueError("備份檔包含不允許還原的項目")
                if key in kinds:
                    raise ValueError("備份檔包含 Windows 大小寫或名稱正規化後重複的路徑")
                kinds[key] = member.is_dir()
                target = os.path.join(root, *parts)
                if os.path.commonpath((root, os.path.realpath(target))) != root:
                    raise ValueError("備份檔包含不安全的路徑")
                parent = root
                for component in parts[:-1]:
                    parent = os.path.join(parent, component)
                    if _is_link_or_reparse(parent):
                        raise ValueError("還原目的地包含符號連結或 Windows reparse point，已停止操作")
                    if os.path.exists(parent) and not os.path.isdir(parent):
                        raise ValueError("還原目的地檔案與資料夾類型不符")
                if _is_link_or_reparse(target):
                    raise ValueError("還原目的地是符號連結或 Windows reparse point，已停止操作")
                if os.path.exists(target) and member.is_dir() != os.path.isdir(target):
                    raise ValueError("還原目的地檔案與資料夾類型不符")
                safe_members.append((member, parts, key))

            keys = set(kinds)
            for key, is_directory in kinds.items():
                if any(prefix in kinds and not kinds[prefix] for prefix in (key[:depth] for depth in range(1, len(key)))):
                    raise ValueError("備份檔包含檔案與子路徑衝突")
                if not is_directory and any(other[:len(key)] == key and len(other) > len(key) for other in keys):
                    raise ValueError("備份檔包含檔案與子路徑衝突")

            if create_safety_backup:
                self.last_pre_restore_backup = self.create_backup("pre-restore")

            with tempfile.TemporaryDirectory(prefix=".mcm-restore-", dir=backup_root) as temporary:
                staging = os.path.join(temporary, "staging")
                rollback = os.path.join(temporary, "rollback")
                os.makedirs(staging)
                os.makedirs(rollback)
                extracted = 0
                for member, parts, _ in safe_members:
                    staged = os.path.join(staging, *parts)
                    if member.is_dir():
                        os.makedirs(staged, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(staged), exist_ok=True)
                    with archive.open(member, "r") as source, open(staged, "wb") as output:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            extracted += len(chunk)
                            if extracted > self.MAX_RESTORE_BYTES:
                                raise ValueError("備份解壓縮容量超過 8 GB")
                            output.write(chunk)

                for member, parts, _ in safe_members:
                    target = os.path.join(root, *parts)
                    parent = os.path.dirname(target)
                    cursor = root
                    for component in parts[:-1]:
                        cursor = os.path.join(cursor, component)
                        if _is_link_or_reparse(cursor):
                            raise ValueError("還原目的地包含符號連結或 Windows reparse point，已停止操作")
                    if _is_link_or_reparse(target):
                        raise ValueError("還原目的地包含符號連結或 Windows reparse point，已停止操作")
                    if member.is_dir():
                        if os.path.exists(target) and not os.path.isdir(target):
                            raise ValueError("還原目的地檔案與資料夾類型不符")
                    elif os.path.isdir(target):
                        raise ValueError("還原目的地檔案與資料夾類型不符")
                created_dirs = []
                installed = []
                moved_old = []

                def ensure_directory(path):
                    missing = []
                    current = path
                    while current != root and not os.path.exists(current):
                        missing.append(current)
                        current = os.path.dirname(current)
                    for directory in reversed(missing):
                        os.mkdir(directory)
                        created_dirs.append(directory)

                try:
                    ordered = sorted(safe_members, key=lambda item: (not item[0].is_dir(), len(item[1])))
                    for member, parts, _ in ordered:
                        target = os.path.join(root, *parts)
                        staged = os.path.join(staging, *parts)
                        if member.is_dir():
                            if _is_link_or_reparse(target):
                                raise ValueError("還原目的地包含符號連結或 Windows reparse point")
                            ensure_directory(target)
                            continue
                        ensure_directory(os.path.dirname(target))
                        if _is_link_or_reparse(os.path.dirname(target)) or _is_link_or_reparse(target):
                            raise ValueError("還原目的地包含符號連結或 Windows reparse point")
                        if os.path.exists(target):
                            if os.path.isdir(target):
                                raise ValueError("還原目的地檔案與資料夾類型不符")
                            rollback_path = os.path.join(rollback, *parts)
                            os.makedirs(os.path.dirname(rollback_path), exist_ok=True)
                            os.replace(target, rollback_path)
                            moved_old.append((rollback_path, target))
                        os.replace(staged, target)
                        installed.append(target)
                except Exception:
                    for target in reversed(installed):
                        if not _is_link_or_reparse(target):
                            try:
                                os.remove(target)
                            except FileNotFoundError:
                                pass
                    for rollback_path, target in reversed(moved_old):
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        os.replace(rollback_path, target)
                    for directory in reversed(created_dirs):
                        try:
                            os.rmdir(directory)
                        except OSError:
                            pass
                    raise
        return archive_path


__all__ = ["BackupManager", "BackupCancelledError"]


class BackupJobManager:
    """In-process background backup jobs with progress and cancellation."""

    def __init__(self, db=None):
        self.db = db
        self._jobs = {}
        self._lock = threading.RLock()

    def _persist(self, job):
        if self.db:
            try:
                self.db.save_backup_job(self._public(job))
            except Exception:
                pass

    def start(self, server_id, server_directory, label="manual", retain_count=10, max_age_days=0, scope="all"):
        if scope not in {"all", "worlds", "plugins", "mods", "config"}:
            raise ValueError("不支援的備份範圍")
        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        job = {"id": job_id, "server_id": server_id, "status": "queued", "completed": 0,
               "total": 0, "current_file": "", "filename": "", "error": "",
               "created_at": time.time(), "started_at": None, "finished_at": None,
               "scope": scope, "_cancel": cancel_event}
        with self._lock:
            cutoff = time.time() - 86400
            for old_id, old in list(self._jobs.items()):
                if old.get("finished_at") and old["finished_at"] < cutoff:
                    self._jobs.pop(old_id, None)
            finished = sorted((old for old in self._jobs.values() if old.get("finished_at")), key=lambda item: item["finished_at"])
            for old in finished[:-200]:
                self._jobs.pop(old["id"], None)
            self._jobs[job_id] = job
        self._persist(job)
        threading.Thread(target=self._run, args=(job_id, server_directory, label, retain_count, max_age_days, scope), daemon=True).start()
        return job_id

    def _run(self, job_id, server_directory, label, retain_count, max_age_days, scope):
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = time.time()
            self._persist(job)
        manager = BackupManager(server_directory)
        def progress(done, total, filename):
            with self._lock:
                job.update({"completed": done, "total": total, "current_file": filename})
                self._persist(job)
        try:
            path = manager.create_backup(label, scope=scope, progress_callback=progress, cancel_event=job["_cancel"])
            manager.cleanup_retention(retain_count, max_age_days or None)
            with self._lock:
                job.update({"status": "completed", "filename": os.path.basename(path), "completed": job["total"], "finished_at": time.time()})
                self._persist(job)
        except BackupCancelledError:
            with self._lock:
                job.update({"status": "cancelled", "finished_at": time.time()})
                self._persist(job)
        except Exception as exc:
            with self._lock:
                job.update({"status": "failed", "error": str(exc), "finished_at": time.time()})
                self._persist(job)

    @staticmethod
    def _public(job):
        return {key: value for key, value in job.items() if key != "_cancel"}

    def get(self, job_id, server_id=None):
        with self._lock:
            job = self._jobs.get(job_id)
            if job and (server_id is None or job["server_id"] == server_id):
                return self._public(job)
        return self.db.backup_job(job_id, server_id) if self.db else None

    def cancel(self, job_id, server_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["server_id"] != server_id:
                return None
            if job["status"] in {"queued", "running"}:
                job["_cancel"].set()
                job["status"] = "cancelling"
                self._persist(job)
            return self._public(job)


__all__.append("BackupJobManager")
