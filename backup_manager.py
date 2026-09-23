import os
import shutil
import zipfile
from datetime import datetime


class BackupManager:
    BACKUP_DIR_NAME = "backups"
    INCLUDED_FILES = {
        "server.properties", "eula.txt", "ops.json", "whitelist.json",
        "banned-players.json", "banned-ips.json", "core-info.json",
    }
    INCLUDED_DIRS = {"world", "world_nether", "world_the_end", "plugins"}

    def __init__(self, server_directory):
        self.server_directory = os.path.abspath(server_directory)
        self.backup_directory = os.path.join(self.server_directory, self.BACKUP_DIR_NAME)
        os.makedirs(self.backup_directory, exist_ok=True)

    def create_backup(self, label="manual"):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
        archive_path = os.path.join(self.backup_directory, f"backup-{timestamp}-{safe_label}.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(self.INCLUDED_FILES):
                path = os.path.join(self.server_directory, name)
                if os.path.isfile(path):
                    archive.write(path, name)
            for directory in sorted(self.INCLUDED_DIRS):
                path = os.path.join(self.server_directory, directory)
                if os.path.isdir(path):
                    for root, _, files in os.walk(path):
                        for filename in files:
                            file_path = os.path.join(root, filename)
                            archive.write(file_path, os.path.relpath(file_path, self.server_directory))
        return archive_path

    def list_backups(self):
        return sorted(
            (os.path.join(self.backup_directory, name) for name in os.listdir(self.backup_directory)),
            key=os.path.getmtime,
            reverse=True,
        )

    def restore_backup(self, archive_path):
        archive_path = os.path.abspath(archive_path)
        if not os.path.isfile(archive_path):
            raise FileNotFoundError(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            root = os.path.abspath(self.server_directory)
            for member in archive.infolist():
                target = os.path.abspath(os.path.join(root, member.filename))
                if os.path.commonpath((root, target)) != root:
                    raise ValueError("備份檔包含不安全的路徑")
            archive.extractall(root)
        return archive_path


__all__ = ["BackupManager"]
