import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from backup_manager import BackupManager
from core_metadata import CoreMetadata, minimum_java_version
from server_manager import ServerManager
from .multi_server import CoreAdapter


DOWNLOAD_CORE_NAMES = {"Paper": "Paper", "Vanilla": "Vanilla (官方版)"}


class CoreUpdateJobs:
    """Run safe, asynchronous Paper/Vanilla core updates per server."""

    def __init__(self, profiles, multi):
        self.profiles = profiles
        self.multi = multi
        self._lock = threading.RLock()
        self._jobs = {}
        self._active_by_server = {}
        self._latest_by_server = {}

    def start(self, server_id, core_type, version):
        profile = self.profiles.get(server_id)
        if not profile:
            raise ValueError("找不到伺服器")
        if core_type not in DOWNLOAD_CORE_NAMES:
            raise ValueError("網頁版核心更新目前支援 Paper 與 Vanilla；Forge/Fabric 請使用原安裝流程更新")
        version = str(version or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,40}", version):
            raise ValueError("請輸入有效的 Minecraft 版本號")

        with self._lock:
            active_id = self._active_by_server.get(server_id)
            if active_id and self._jobs.get(active_id, {}).get("status") in {"queued", "running"}:
                raise ValueError("此伺服器已有核心更新工作進行中")
            with self.multi._lock:
                if server_id in self.multi._core_updates:
                    raise ValueError("此伺服器已有核心更新工作進行中")
                status = self.multi.status(server_id)
                if status and (status.get("running") or status.get("phase") in {"starting", "stopping"}):
                    raise ValueError("請先停止伺服器，再更新核心版本")
                self.multi._core_updates.add(server_id)

            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "server_id": server_id,
                "status": "queued",
                "stage": "queued",
                "core_type": core_type,
                "version": version,
                "progress": 0,
                "message": "更新工作已排入佇列",
                "error": "",
                "backup": "",
                "created_at": time.time(),
                "finished_at": None,
            }
            self._jobs[job_id] = job
            self._active_by_server[server_id] = job_id
            self._latest_by_server[server_id] = job_id
            self._prune_locked()

        threading.Thread(target=self._run, args=(job_id, dict(profile)), daemon=True).start()
        return job_id

    def _prune_locked(self):
        finished = sorted(
            (job for job in self._jobs.values() if job["finished_at"]),
            key=lambda job: job["finished_at"],
        )
        for job in finished[:-100]:
            self._jobs.pop(job["id"], None)

    def _update(self, job_id, **values):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(values)

    def _run(self, job_id, profile):
        directory = Path(profile["directory"])
        core_type = self._jobs[job_id]["core_type"]
        version = self._jobs[job_id]["version"]
        download_path = None
        initial_jars = {path.resolve() for path in directory.glob("*.jar")}
        profile_updated = False
        metadata_path = directory / CoreMetadata.FILENAME
        previous_metadata = metadata_path.read_bytes() if metadata_path.is_file() else None
        try:
            self._update(job_id, status="running", stage="backup", message="正在建立更新前完整備份…")
            backup_path = BackupManager(directory).create_backup("before-core-update")
            self._update(job_id, backup=os.path.basename(backup_path), stage="download", message=f"正在下載 {core_type} {version}…")

            def progress(percent):
                self._update(job_id, progress=max(0, min(100, int(percent))), message=f"正在下載 {core_type} {version}…")

            manager = ServerManager(str(directory))
            download_core = DOWNLOAD_CORE_NAMES[core_type]
            download_path, message = manager.download_server(download_core, version, progress)
            if not download_path or not os.path.isfile(download_path):
                raise RuntimeError(message or "核心下載失敗")
            download_path = Path(download_path).resolve()
            with zipfile.ZipFile(download_path, "r") as archive:
                damaged_member = archive.testzip()
                if damaged_member:
                    raise zipfile.BadZipFile(f"核心檔案驗證失敗：{damaged_member}")

            self._update(job_id, stage="switch", progress=100, message="下載完成，正在封存舊核心並套用版本…")
            archive_dir = directory / BackupManager.BACKUP_DIR_NAME / "cores"
            old_jars = [path for path in directory.glob("*.jar") if path.resolve() != download_path]
            archived = []
            if old_jars:
                archive_dir.mkdir(parents=True, exist_ok=True)
                for old_jar in old_jars:
                    archived_path = archive_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{old_jar.name}"
                    shutil.copy2(old_jar, archived_path)
                    archived.append((old_jar, archived_path))

            metadata = {
                "core": download_core,
                "minecraft_version": version,
                "jar": download_path.name,
                "java_required": minimum_java_version(version),
                "sha256": CoreMetadata.sha256(download_path),
            }
            temp_metadata = metadata_path.with_name(f"{metadata_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temp_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp_metadata, metadata_path)
                self.profiles.update(profile["id"], {
                    "core_type": core_type,
                    "minecraft_version": version,
                    "launcher": download_path.name,
                })
                profile_updated = True
            except Exception:
                try:
                    temp_metadata.unlink(missing_ok=True)
                    if previous_metadata is None:
                        metadata_path.unlink(missing_ok=True)
                    else:
                        metadata_path.write_bytes(previous_metadata)
                except OSError:
                    pass
                raise

            warnings = []
            for old_jar, archived_path in archived:
                try:
                    old_jar.unlink()
                except OSError as exc:
                    warnings.append(f"舊核心已封存至 {archived_path.name}，但無法清除原檔：{exc}")
            detail = f"已更新為 {core_type} {version}。更新前備份：{os.path.basename(backup_path)}"
            if warnings:
                detail += "；注意：" + "；".join(warnings)
            self._update(job_id, status="completed", stage="complete", message=detail, finished_at=time.time())
        except Exception as exc:
            if download_path and download_path.exists() and download_path.resolve() not in initial_jars and not profile_updated:
                try:
                    archive_dir = directory / BackupManager.BACKUP_DIR_NAME / "cores"
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(download_path), str(archive_dir / f"failed-{uuid.uuid4().hex[:8]}-{download_path.name}"))
                except OSError:
                    pass
            self._update(job_id, status="failed", stage="failed", error=str(exc), message="核心更新失敗；伺服器設定仍維持原版本。", finished_at=time.time())
        finally:
            with self.multi._lock:
                self.multi._core_updates.discard(profile["id"])

    def get(self, job_id, server_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job and job["server_id"] == server_id else None

    def latest(self, server_id):
        with self._lock:
            job_id = self._latest_by_server.get(server_id)
            job = self._jobs.get(job_id) if job_id else None
            return dict(job) if job else None
