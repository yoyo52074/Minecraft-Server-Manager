import os
import json
import shutil
import stat
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath


class BackupCancelledError(Exception):
    """Raised when a backup operation is cancelled by its caller."""


class BackupManager:
    BACKUP_DIR_NAME = "backups"
    INCLUDED_FILES = {
        "server.properties", "eula.txt", "ops.json", "whitelist.json",
        "banned-players.json", "banned-ips.json", "core-info.json",
    }
    INCLUDED_DIRS = {
        "world", "world_nether", "world_the_end", "plugins", "mods", "config",
        "resourcepacks", "datapacks", "logs", "crash-reports",
    }

    def __init__(self, server_directory):
        self.server_directory = os.path.abspath(server_directory)
        self.backup_directory = os.path.join(self.server_directory, self.BACKUP_DIR_NAME)
        os.makedirs(self.backup_directory, exist_ok=True)
        self.last_pre_restore_backup = None

    def _included_files(self):
        files = []
        for name in sorted(self.INCLUDED_FILES):
            path = os.path.join(self.server_directory, name)
            if os.path.isfile(path):
                files.append((path, name))
        for directory in sorted(self.INCLUDED_DIRS):
            base = os.path.join(self.server_directory, directory)
            if not os.path.isdir(base):
                continue
            for root, dirs, names in os.walk(base):
                dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
                for name in sorted(names):
                    path = os.path.join(root, name)
                    if os.path.isfile(path) and not os.path.islink(path):
                        files.append((path, os.path.relpath(path, self.server_directory)))
        return files

    def create_backup(self, label="manual", *, progress_callback=None, cancel_event=None):
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label))[:64] or "backup"
        archive_path = os.path.join(self.backup_directory, f"backup-{timestamp}-{safe_label}.zip")
        part_path = archive_path + ".part"
        files = self._included_files()
        total = len(files)
        try:
            with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for index, (file_path, arcname) in enumerate(files, 1):
                    if cancel_event and cancel_event.is_set():
                        raise BackupCancelledError("備份已取消")
                    archive.write(file_path, arcname)
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
        return sorted(
            (os.path.join(self.backup_directory, name) for name in os.listdir(self.backup_directory)
             if name.lower().endswith(".zip") and os.path.isfile(os.path.join(self.backup_directory, name))),
            key=os.path.getmtime,
            reverse=True,
        )

    def create_settings_backup(self, profile_snapshot=None):
        """Create a small, timestamped rollback archive before settings edits."""
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
        settings_dir = os.path.join(self.backup_directory, "settings")
        os.makedirs(settings_dir, exist_ok=True)
        archive_path = os.path.join(settings_dir, f"settings-{timestamp}.zip")
        part_path = archive_path + ".part"
        try:
            with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
                properties = os.path.join(self.server_directory, "server.properties")
                if os.path.isfile(properties):
                    archive.write(properties, "server.properties")
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

    @staticmethod
    def _validate_member(member):
        raw = member.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        mode = member.external_attr >> 16
        if (not raw or raw.startswith("/") or path.is_absolute() or ".." in path.parts
                or (len(raw) >= 2 and raw[1] == ":") or stat.S_ISLNK(mode)):
            raise ValueError("備份檔包含不安全的路徑")
        return raw

    def verify_backup(self, archive_path):
        archive_path = os.path.abspath(archive_path)
        if not os.path.isfile(archive_path):
            raise FileNotFoundError(archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.infolist():
                self._validate_member(member)
            bad = archive.testzip()
            if bad:
                raise zipfile.BadZipFile(f"備份完整性檢查失敗：{bad}")
        return True

    def restore_backup(self, archive_path, *, create_safety_backup=True):
        archive_path = os.path.abspath(archive_path)
        root = os.path.abspath(self.server_directory)
        backup_root = os.path.abspath(self.backup_directory)
        if os.path.commonpath((root, archive_path)) != root:
            raise ValueError("備份檔必須位於伺服器資料夾內")
        if os.path.commonpath((backup_root, archive_path)) != backup_root:
            raise ValueError("只能還原備份目錄中的檔案")
        self.verify_backup(archive_path)
        if create_safety_backup:
            self.last_pre_restore_backup = self.create_backup("pre-restore")
        with zipfile.ZipFile(archive_path, "r") as archive:
            # All names and CRCs are checked before extraction; extract only after backup succeeds.
            for member in archive.infolist():
                name = self._validate_member(member)
                target = os.path.abspath(os.path.join(root, *PurePosixPath(name).parts))
                if os.path.commonpath((root, target)) != root:
                    raise ValueError("備份檔包含不安全的路徑")
            archive.extractall(root)
        return archive_path


__all__ = ["BackupManager", "BackupCancelledError"]


class BackupJobManager:
    """In-process background backup jobs with progress and cancellation."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.RLock()

    def start(self, server_id, server_directory, label="manual", retain_count=10, max_age_days=0):
        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        job = {"id": job_id, "server_id": server_id, "status": "queued", "completed": 0,
               "total": 0, "current_file": "", "filename": "", "error": "",
               "created_at": time.time(), "started_at": None, "finished_at": None,
               "_cancel": cancel_event}
        with self._lock:
            cutoff = time.time() - 86400
            for old_id, old in list(self._jobs.items()):
                if old.get("finished_at") and old["finished_at"] < cutoff:
                    self._jobs.pop(old_id, None)
            finished = sorted((old for old in self._jobs.values() if old.get("finished_at")), key=lambda item: item["finished_at"])
            for old in finished[:-200]:
                self._jobs.pop(old["id"], None)
            self._jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id, server_directory, label, retain_count, max_age_days), daemon=True).start()
        return job_id

    def _run(self, job_id, server_directory, label, retain_count, max_age_days):
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = time.time()
        manager = BackupManager(server_directory)
        def progress(done, total, filename):
            with self._lock:
                job.update({"completed": done, "total": total, "current_file": filename})
        try:
            path = manager.create_backup(label, progress_callback=progress, cancel_event=job["_cancel"])
            manager.cleanup_retention(retain_count, max_age_days or None)
            with self._lock:
                job.update({"status": "completed", "filename": os.path.basename(path), "completed": job["total"], "finished_at": time.time()})
        except BackupCancelledError:
            with self._lock:
                job.update({"status": "cancelled", "finished_at": time.time()})
        except Exception as exc:
            with self._lock:
                job.update({"status": "failed", "error": str(exc), "finished_at": time.time()})

    @staticmethod
    def _public(job):
        return {key: value for key, value in job.items() if key != "_cancel"}

    def get(self, job_id, server_id=None):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or (server_id is not None and job["server_id"] != server_id):
                return None
            return self._public(job)

    def cancel(self, job_id, server_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["server_id"] != server_id:
                return None
            if job["status"] in {"queued", "running"}:
                job["_cancel"].set()
                job["status"] = "cancelling"
            return self._public(job)


__all__.append("BackupJobManager")
