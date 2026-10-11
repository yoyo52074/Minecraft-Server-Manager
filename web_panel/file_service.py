import fnmatch
import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".com", ".scr"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 10000
MAX_ZIP_UNPACK_BYTES = 8 * 1024 * 1024 * 1024
MAX_FILE_VERSION_BYTES = 1024 * 1024 * 1024
MAX_FILE_VERSIONS = 50
MAX_TOTAL_VERSION_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_VERSION_COUNT = 1000


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
        parts=PurePosixPath(str(relative).replace("\\","/")).parts
        if any(part in {".mcm_versions",".mcm_uploads"} for part in parts):
            raise PermissionError("不允許直接存取檔案管理內部資料")
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise PermissionError("檔案路徑超出伺服器目錄")
        return target

    def _version_folder(self, profile_id, relative):
        root=self.profile_root(profile_id);normalized=PurePosixPath(str(relative).replace("\\","/")).as_posix()
        key=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        folder=root/".mcm_versions"/key;folder.mkdir(parents=True,exist_ok=True)
        return folder

    def snapshot(self, profile_id, relative):
        """Preserve a file atomically before it is replaced, moved or deleted."""
        target=self.resolve(profile_id,relative)
        if not target.is_file():return None
        size=target.stat().st_size
        if size>MAX_FILE_VERSION_BYTES:raise ValueError("檔案超過 1 GB 版本快照限制，為避免無備份修改已停止")
        folder=self._version_folder(profile_id,relative);version_id=f"{int(time.time()*1000)}-{uuid.uuid4().hex[:12]}"
        archive=folder/f"{version_id}.bin";part=folder/f"{version_id}.part";metadata=folder/f"{version_id}.json"
        try:
            shutil.copy2(target,part);digest=hashlib.sha256()
            with part.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
            os.replace(part,archive)
            meta={"version_id":version_id,"path":PurePosixPath(str(relative).replace("\\","/")).as_posix(),"name":target.name,"size":size,"modified_at":target.stat().st_mtime,"created_at":time.time(),"sha256":digest.hexdigest()}
            temp=metadata.with_suffix(".json.part");temp.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8");os.replace(temp,metadata)
        finally:part.unlink(missing_ok=True)
        versions=sorted(folder.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)
        for old in versions[MAX_FILE_VERSIONS:]:old.with_suffix(".bin").unlink(missing_ok=True);old.unlink(missing_ok=True)
        all_versions=sorted((self.profile_root(profile_id)/".mcm_versions").glob("*/*.json"),key=lambda p:p.stat().st_mtime)
        total=sum((path.with_suffix(".bin").stat().st_size if path.with_suffix(".bin").exists() else 0) for path in all_versions)
        while all_versions and (len(all_versions)>MAX_TOTAL_VERSION_COUNT or total>MAX_TOTAL_VERSION_BYTES):
            old=all_versions.pop(0);archive=old.with_suffix(".bin");total-=archive.stat().st_size if archive.exists() else 0;archive.unlink(missing_ok=True);old.unlink(missing_ok=True)
        return meta

    def _snapshot_tree(self, profile_id, relative):
        target=self.resolve(profile_id,relative)
        if target.is_file():self.snapshot(profile_id,relative);return
        if target.is_dir():
            files=[path for path in target.rglob("*") if path.is_file() and not path.is_symlink()]
            if len(files)>2000:raise ValueError("資料夾包含超過 2,000 個檔案，已停止異動以避免漏做版本快照")
            for path in files:
                if path.stat().st_size>MAX_FILE_VERSION_BYTES:raise ValueError(f"檔案 {path.name} 超過 1 GB 版本快照限制")
            root=self.profile_root(profile_id)
            for path in files:self.snapshot(profile_id,path.relative_to(root).as_posix())

    def versions(self, profile_id, relative):
        target=self.resolve(profile_id,relative);folder=self._version_folder(profile_id,relative);output=[]
        for path in sorted(folder.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:MAX_FILE_VERSIONS]:
            try:
                meta=json.loads(path.read_text(encoding="utf-8"))
                if meta.get("path")==PurePosixPath(str(relative).replace("\\","/")).as_posix() and path.with_suffix(".bin").is_file():output.append(meta)
            except (OSError,json.JSONDecodeError):continue
        return {"path":str(relative),"exists":target.is_file(),"versions":output}

    def restore_version(self, profile_id, relative, version_id):
        if not re.fullmatch(r"[0-9]{10,16}-[a-f0-9]{12}",str(version_id)):raise ValueError("無效的版本識別碼")
        target=self.resolve(profile_id,relative);folder=self._version_folder(profile_id,relative);archive=folder/f"{version_id}.bin";meta_path=folder/f"{version_id}.json"
        if not archive.is_file() or not meta_path.is_file():raise FileNotFoundError("找不到檔案版本")
        meta=json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("path")!=PurePosixPath(str(relative).replace("\\","/")).as_posix():raise PermissionError("版本不屬於指定檔案")
        digest=hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        if digest.hexdigest()!=meta.get("sha256"):raise ValueError("版本快照完整性驗證失敗")
        if target.is_file():self.snapshot(profile_id,relative)
        target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(target.name+".restoring")
        try:shutil.copy2(archive,temp);os.replace(temp,target)
        finally:temp.unlink(missing_ok=True)
        return {"path":str(relative),"restored_version":version_id,"created_at":meta.get("created_at")}

    @staticmethod
    def _conflict_target(target, conflict="error"):
        if not target.exists():
            return target
        if conflict == "error":
            raise FileExistsError(f"目的地已存在：{target.name}")
        if conflict == "overwrite":
            if target.is_dir():
                raise IsADirectoryError("不支援直接覆寫資料夾")
            return target
        if conflict == "rename":
            stem, suffix = target.stem, target.suffix
            index = 1
            while True:
                candidate = target.with_name(f"{stem} ({index}){suffix}")
                if not candidate.exists():
                    return candidate
                index += 1
        raise ValueError("衝突策略只能是 error、rename 或 overwrite")

    def list(self, profile_id, relative=""):
        directory = self.resolve(profile_id, relative)
        if not directory.is_dir():
            raise NotADirectoryError("不是資料夾")
        items = []
        for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if path.name in {".mcm_versions",".mcm_uploads"}:continue
            stat = path.stat()
            items.append({"name": path.name, "path": path.relative_to(self.profile_root(profile_id)).as_posix(),
                          "is_directory": path.is_dir(), "size": stat.st_size if path.is_file() else 0,
                          "modified_at": stat.st_mtime})
        return items

    def search(self, profile_id, query, relative="", content=False, limit=500, max_file_bytes=2 * 1024 * 1024):
        query = str(query).strip()
        if not query:
            return []
        root = self.resolve(profile_id, relative)
        if not root.is_dir():
            raise NotADirectoryError("搜尋起點不是資料夾")
        needle = query.casefold()
        results = []
        for path in root.rglob("*"):
            if len(results) >= min(max(int(limit), 1), 1000):
                break
            try:
                if any(part in {".mcm_versions",".mcm_uploads"} for part in path.relative_to(self.profile_root(profile_id)).parts):continue
                if path.is_symlink() or not path.is_file():
                    continue
                relative_path = path.relative_to(self.profile_root(profile_id)).as_posix()
                name_match = needle in path.name.casefold() or fnmatch.fnmatch(path.name.casefold(), needle)
                snippets = []
                if content and path.stat().st_size <= max_file_bytes and path.suffix.lower() in {".txt", ".log", ".json", ".properties", ".yml", ".yaml", ".toml", ".cfg", ".conf", ".md", ".xml"}:
                    try:
                        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                            if needle in line.casefold():
                                snippets.append({"line": line_no, "text": line[:300]})
                                if len(snippets) >= 3:
                                    break
                    except (UnicodeError, OSError):
                        pass
                if name_match or snippets:
                    stat = path.stat()
                    results.append({"name": path.name, "path": relative_path, "size": stat.st_size,
                                    "modified_at": stat.st_mtime, "content_matches": snippets})
            except OSError:
                continue
        return results

    def write_upload(self, profile_id, relative, upload_file, max_bytes=MAX_UPLOAD_BYTES, conflict="error"):
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS:
            raise ValueError("不允許上傳可執行檔")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._conflict_target(target, conflict)
        if target.exists():self.snapshot(profile_id,target.relative_to(self.profile_root(profile_id)).as_posix())
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

    def upload_chunk(self, profile_id, upload_id, relative, offset, data, final=False, conflict="error", max_bytes=MAX_UPLOAD_BYTES):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS:
            raise ValueError("不允許上傳可執行檔")
        root = self.profile_root(profile_id)
        staging = root / ".mcm_uploads"
        staging.mkdir(exist_ok=True)
        part = staging / f"{upload_id}.part"
        meta = staging / f"{upload_id}.json"
        if meta.exists():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("path") != Path(relative).as_posix() or saved.get("conflict") != conflict:
                raise ValueError("上傳工作目的地或衝突策略與原工作不符")
        else:
            meta.write_text(json.dumps({"path": Path(relative).as_posix(), "conflict": conflict}), encoding="utf-8")
        expected = part.stat().st_size if part.exists() else 0
        if int(offset) != expected:
            raise ValueError(f"分段位置錯誤，伺服器目前已接收 {expected} bytes")
        data = bytes(data)
        if expected + len(data) > max_bytes:
            part.unlink(missing_ok=True); meta.unlink(missing_ok=True)
            raise ValueError("檔案超過 512 MB 上傳限制")
        with part.open("ab") as output:
            output.write(data)
        received = part.stat().st_size
        if final:
            target.parent.mkdir(parents=True, exist_ok=True)
            final_target = self._conflict_target(target, conflict)
            if final_target.exists():self.snapshot(profile_id,final_target.relative_to(root).as_posix())
            os.replace(part, final_target)
            meta.unlink(missing_ok=True)
            return {"complete": True, "received": received, "path": final_target.relative_to(root).as_posix()}
        return {"complete": False, "received": received}

    def cancel_upload(self, profile_id, upload_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        staging = self.profile_root(profile_id) / ".mcm_uploads"
        (staging / f"{upload_id}.part").unlink(missing_ok=True)
        (staging / f"{upload_id}.json").unlink(missing_ok=True)

    def upload_status(self, profile_id, upload_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        staging = self.profile_root(profile_id) / ".mcm_uploads"
        meta = staging / f"{upload_id}.json"
        part = staging / f"{upload_id}.part"
        if not meta.exists():
            return {"exists": False, "received": 0}
        try:
            saved = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("上傳工作狀態損毀") from exc
        return {"exists": True, "path": saved.get("path", ""), "received": part.stat().st_size if part.exists() else 0,
                "conflict": saved.get("conflict", "error")}

    def mkdir(self, profile_id, relative):
        target = self.resolve(profile_id, relative)
        target.mkdir(parents=False, exist_ok=False)
        return target

    def delete(self, profile_id, relative):
        target = self.resolve(profile_id, relative)
        if not target.exists() or target == self.profile_root(profile_id):
            raise FileNotFoundError("找不到檔案或不允許刪除根目錄")
        self._snapshot_tree(profile_id,relative)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def delete_many(self, profile_id, relatives):
        if not relatives or len(relatives) > 500:
            raise ValueError("批次操作需選擇 1 至 500 個項目")
        targets = [self.resolve(profile_id, item) for item in relatives]
        root = self.profile_root(profile_id)
        if len(set(targets)) != len(targets) or any(path == root for path in targets):
            raise ValueError("批次清單含重複項目或根目錄")
        for target in targets:
            if not target.exists():
                raise FileNotFoundError(f"找不到：{target.name}")
        for relative in relatives:self._snapshot_tree(profile_id,relative)
        for target in targets:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        return len(targets)

    def rename(self, profile_id, relative, new_name, conflict="error"):
        if not new_name or new_name in {".", ".."} or Path(new_name).name != new_name:
            raise ValueError("無效的新檔名")
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, str(Path(relative).parent / new_name))
        target = self._conflict_target(target, conflict)
        self._snapshot_tree(profile_id,relative)
        if target.exists():self._snapshot_tree(profile_id,target.relative_to(self.profile_root(profile_id)).as_posix())
        source.rename(target)
        return target

    def read_text(self, profile_id, relative, max_bytes=4 * 1024 * 1024):
        target = self.resolve(profile_id, relative)
        if not target.is_file() or target.stat().st_size > max_bytes:
            raise ValueError("檔案不存在或超過 4 MB 編輯限制")
        return target.read_text(encoding="utf-8")

    def write_text(self, profile_id, relative, content, conflict="overwrite"):
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS or len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("不允許編輯此檔案或內容超過 4 MB")
        if conflict != "overwrite":
            target = self._conflict_target(target, conflict)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():self.snapshot(profile_id,relative)
        temp = target.with_name(target.name + ".editing")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
        return target

    def copy_or_move(self, profile_id, source_relative, destination_relative, move=False, conflict="error"):
        source = self.resolve(profile_id, source_relative)
        destination = self.resolve(profile_id, destination_relative)
        root = self.profile_root(profile_id)
        if not source.exists() or source == root:
            raise FileNotFoundError("找不到來源")
        if source == destination or (source.is_dir() and source in destination.parents):
            raise ValueError("不能將資料夾複製或移動到自身內部")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self._conflict_target(destination, conflict)
        if move:self._snapshot_tree(profile_id,source_relative)
        if destination.exists():self._snapshot_tree(profile_id,destination.relative_to(root).as_posix())
        if move:
            shutil.move(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(source, destination, symlinks=False)
        else:
            shutil.copy2(source, destination)
        return destination

    def batch_copy_move(self, profile_id, sources, destination_directory, move=False, conflict="rename"):
        if not sources or len(sources) > 500:
            raise ValueError("批次操作需選擇 1 至 500 個項目")
        directory = self.resolve(profile_id, destination_directory)
        if not directory.is_dir():
            raise NotADirectoryError("目的地必須是已存在的資料夾")
        output = []
        for source in sources:
            name = Path(source).name
            target = directory / name
            done = self.copy_or_move(profile_id, source, target.relative_to(self.profile_root(profile_id)).as_posix(), move, conflict)
            output.append(done.relative_to(self.profile_root(profile_id)).as_posix())
        return output

    def zip_folder(self, profile_id, relative, destination, conflict="error"):
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, destination)
        if not source.is_dir() and not source.is_file():
            raise FileNotFoundError("找不到壓縮來源")
        if target.suffix.lower() != ".zip":
            raise ValueError("壓縮檔必須使用 .zip 副檔名")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._conflict_target(target, conflict)
        if target.exists():self.snapshot(profile_id,target.relative_to(self.profile_root(profile_id)).as_posix())
        temp = target.with_name(target.name + ".creating")
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                paths = [source] if source.is_file() else source.rglob("*")
                for path in paths:
                    if any(part in {".mcm_versions",".mcm_uploads"} for part in path.relative_to(self.profile_root(profile_id)).parts):continue
                    if path.is_file() and not path.is_symlink() and path != target and path != temp:
                        archive.write(path, path.relative_to(source.parent if source.is_file() else source.parent))
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    def unzip(self, profile_id, relative, destination, conflict="error"):
        source = self.resolve(profile_id, relative)
        target = self.resolve(profile_id, destination)
        if source.suffix.lower() != ".zip" or not source.is_file():
            raise ValueError("只能解壓縮 ZIP 檔案")
        if not target.exists():
            target.mkdir(parents=True)
        if not target.is_dir():
            raise NotADirectoryError("解壓目的地必須是資料夾")
        root = target.resolve()
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES or sum(member.file_size for member in members) > MAX_ZIP_UNPACK_BYTES:
                raise ValueError("ZIP 項目過多或解壓縮容量超過 8 GB")
            for member in members:
                posix = PurePosixPath(member.filename)
                if posix.is_absolute() or ".." in posix.parts or any(part in {".mcm_versions",".mcm_uploads"} for part in posix.parts):
                    raise ValueError("ZIP 含有不安全的路徑")
                candidate = (root / Path(*posix.parts)).resolve()
                if candidate != root and root not in candidate.parents:
                    raise ValueError("ZIP 含有不安全的路徑")
                if candidate.exists() and conflict == "error":
                    raise FileExistsError(f"解壓目的地已有同名項目：{posix.name}")
                if candidate.exists() and conflict == "rename":
                    raise ValueError("ZIP 內檔名衝突；請選擇覆寫或其他空資料夾")
                if candidate.exists() and conflict == "overwrite" and candidate.is_dir() != member.is_dir():
                    raise ValueError("無法以檔案覆寫資料夾或反向操作")
                if candidate.is_file() and conflict=="overwrite":self.snapshot(profile_id,candidate.relative_to(self.profile_root(profile_id)).as_posix())
            archive.extractall(root)
        return target
