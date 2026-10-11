import os
import shutil
import zipfile
from pathlib import Path


BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".com", ".scr"}


class SafeFileService:
    def __init__(self, profile_store):
        self.profile_store = profile_store

    def profile_root(self, profile_id):
        profile = self.profile_store.get(profile_id)
        if not profile:
            raise FileNotFoundError("找不到伺服器")
        root = Path(profile["directory"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def resolve(self, profile_id, relative=""):
        root = self.profile_root(profile_id)
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise PermissionError("檔案路徑超出伺服器目錄")
        return target

    def list(self, profile_id, relative=""):
        directory = self.resolve(profile_id, relative)
        if not directory.is_dir():
            raise NotADirectoryError("不是資料夾")
        items = []
        for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            stat = path.stat()
            items.append({
                "name": path.name,
                "path": path.relative_to(self.profile_root(profile_id)).as_posix(),
                "is_directory": path.is_dir(),
                "size": stat.st_size if path.is_file() else 0,
                "modified_at": stat.st_mtime,
            })
        return items

    def write_upload(self, profile_id, relative, upload_file, max_bytes=512 * 1024 * 1024):
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS:
            raise ValueError("不允許上傳可執行檔")
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        temp = target.with_name(target.name + ".uploading")
        try:
            with temp.open("wb") as output:
                while True:
                    chunk = upload_file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("檔案超過 512 MB 上傳限制")
                    output.write(chunk)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
        return target

    def mkdir(self, profile_id, relative):
        target = self.resolve(profile_id, relative)
        target.mkdir(parents=False, exist_ok=False)
        return target

    def delete(self, profile_id, relative):
        target = self.resolve(profile_id, relative)
        if not target.exists():
            raise FileNotFoundError("找不到檔案")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def rename(self, profile_id, relative, new_name):
        if not new_name or new_name in {".", ".."} or Path(new_name).name != new_name:
            raise ValueError("無效的新檔名")
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, str(Path(relative).parent / new_name))
        source.rename(target)
        return target

    def read_text(self, profile_id, relative, max_bytes=4 * 1024 * 1024):
        target = self.resolve(profile_id, relative)
        if not target.is_file() or target.stat().st_size > max_bytes:
            raise ValueError("檔案不存在或超過 4 MB 編輯限制")
        return target.read_text(encoding="utf-8")

    def write_text(self, profile_id, relative, content):
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS or len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("不允許編輯此檔案或內容超過 4 MB")
        target.write_text(content, encoding="utf-8")

    def copy_or_move(self, profile_id, source_relative, destination_relative, move=False):
        source = self.resolve(profile_id, source_relative)
        destination = self.resolve(profile_id, destination_relative)
        if not source.exists() or source == self.profile_root(profile_id):
            raise FileNotFoundError("找不到來源")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=False)
        else:
            shutil.copy2(source, destination)
        return destination

    def zip_folder(self, profile_id, relative, destination):
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, destination)
        if not source.is_dir():
            raise NotADirectoryError("只能壓縮資料夾")
        if target.suffix.lower() != ".zip":
            raise ValueError("壓縮檔必須使用 .zip 副檔名")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(source.parent))
        return target

    def unzip(self, profile_id, relative, destination):
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, destination)
        if source.suffix.lower() != ".zip":
            raise ValueError("只能解壓縮 ZIP")
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                candidate = (root / member.filename).resolve()
                if candidate != root and root not in candidate.parents:
                    raise ValueError("ZIP 含有不安全的路徑")
            archive.extractall(root)
        return target
