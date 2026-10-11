import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".com", ".scr"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_STAGED_UPLOADS = 20
MAX_STAGING_BYTES = 2 * 1024 * 1024 * 1024
UPLOAD_TTL_SECONDS = 24 * 60 * 60
MAX_ZIP_ENTRIES = 10000
MAX_ZIP_UNPACK_BYTES = 8 * 1024 * 1024 * 1024
MAX_FILE_VERSION_BYTES = 1024 * 1024 * 1024
MAX_FILE_VERSIONS = 50
MAX_TOTAL_VERSION_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_VERSION_COUNT = 1000
INTERNAL_DIRECTORIES = {".mcm_versions", ".mcm_uploads"}
WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _component_key(component):
    return unicodedata.normalize("NFKC", str(component)).casefold().rstrip(" .")


def _is_internal_component(component):
    return _component_key(component) in INTERNAL_DIRECTORIES


def _validate_path_components(parts):
    for part in parts:
        normalized = unicodedata.normalize("NFKC", str(part))
        if (not normalized or ":" in normalized or normalized != normalized.rstrip(" .")
                or normalized.split(".", 1)[0].casefold() in WINDOWS_DEVICE_NAMES
                or _is_internal_component(normalized)):
            raise PermissionError("路徑含有不允許的 Windows／檔案管理保留名稱")


def _is_link_or_reparse(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _open_regular_file(path):
    if _is_link_or_reparse(path):
        raise PermissionError("不允許讀取符號連結或 Windows reparse point")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("只允許讀取一般檔案")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _copy_regular_file(source, destination):
    source=Path(source);destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temp_name=tempfile.mkstemp(prefix=f".{destination.name}.",suffix=".copying",dir=destination.parent)
    temporary=Path(temp_name)
    try:
        with os.fdopen(descriptor,"wb") as output,_open_regular_file(source) as input_file:
            shutil.copyfileobj(input_file,output,length=1024*1024)
            output.flush();os.fsync(output.fileno())
        shutil.copystat(source,temporary,follow_symlinks=False)
        os.replace(temporary,destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination)


class SafeFileService:
    def __init__(self, profile_store):
        self.profile_store = profile_store
        self._upload_locks = [threading.Lock() for _ in range(64)]
        self._staging_locks = [threading.Lock() for _ in range(32)]

    def _upload_staging_dir(self, profile_id, create=False):
        root = self.profile_root(profile_id)
        staging = root / ".mcm_uploads"
        if _is_link_or_reparse(staging):
            raise PermissionError("上傳暫存目錄不可為符號連結或 Windows reparse point")
        if create:
            staging.mkdir(exist_ok=True)
        if staging.exists() and not staging.is_dir():
            raise NotADirectoryError("上傳暫存路徑不是資料夾")
        return staging

    def _cleanup_stale_uploads(self, staging):
        if not staging.exists():
            return
        now = time.time()
        for meta in staging.glob("*.json"):
            if _is_link_or_reparse(meta) or not meta.is_file():
                continue
            part = meta.with_suffix(".part")
            if now - meta.stat().st_mtime <= UPLOAD_TTL_SECONDS:
                continue
            if not _is_link_or_reparse(part):
                part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
        for part in staging.glob("*.part"):
            if _is_link_or_reparse(part) or not part.is_file():
                continue
            if now - part.stat().st_mtime > UPLOAD_TTL_SECONDS:
                part.unlink(missing_ok=True)

    @staticmethod
    def _staging_usage(staging):
        count = total_bytes = 0
        if not staging.exists():
            return count, total_bytes
        for meta in staging.glob("*.json"):
            if not _is_link_or_reparse(meta) and meta.is_file():
                count += 1
        for part in staging.glob("*.part"):
            if not _is_link_or_reparse(part) and part.is_file():
                total_bytes += part.stat().st_size
        return count, total_bytes

    def profile_root(self, profile_id):
        profile = self.profile_store.get(profile_id)
        if not profile:
            raise FileNotFoundError("找不到伺服器")
        root = Path(profile["directory"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def resolve(self, profile_id, relative=""):
        root = self.profile_root(profile_id)
        parts = PurePosixPath(str(relative).replace("\\", "/")).parts
        _validate_path_components(parts)
        cursor = root
        for component in parts:
            cursor = cursor / component
            if _is_link_or_reparse(cursor):
                raise PermissionError("檔案路徑不可經過符號連結或 Windows reparse point")
        target = root.joinpath(*parts).resolve()
        if target != root and root not in target.parents:
            raise PermissionError("檔案路徑超出伺服器目錄")
        return target

    def _version_folder(self, profile_id, relative):
        root=self.profile_root(profile_id);normalized=PurePosixPath(str(relative).replace("\\","/")).as_posix()
        key=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        version_root=root/".mcm_versions"
        if _is_link_or_reparse(version_root):raise PermissionError("檔案版本目錄不可為符號連結或 Windows reparse point")
        version_root.mkdir(exist_ok=True)
        folder=version_root/key
        if _is_link_or_reparse(folder):raise PermissionError("檔案版本 bucket 不可為符號連結或 Windows reparse point")
        folder.mkdir(exist_ok=True)
        return folder

    def snapshot(self, profile_id, relative):
        """Preserve a file atomically before it is replaced, moved or deleted."""
        target=self.resolve(profile_id,relative)
        if not target.is_file():return None
        size=target.stat().st_size
        if size>MAX_FILE_VERSION_BYTES:raise ValueError("檔案超過 1 GB 版本快照限制，為避免無備份修改已停止")
        folder=self._version_folder(profile_id,relative);version_id=f"{int(time.time()*1000)}-{uuid.uuid4().hex[:12]}"
        archive=folder/f"{version_id}.bin";part=folder/f"{version_id}.part";metadata=folder/f"{version_id}.json"
        descriptor,part_name=tempfile.mkstemp(prefix=".mcm-version-",suffix=".part",dir=folder);part=Path(part_name)
        try:
            with os.fdopen(descriptor,"wb") as output,_open_regular_file(target) as source:
                shutil.copyfileobj(source,output,length=1024*1024)
            digest=hashlib.sha256()
            with _open_regular_file(part) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
            os.replace(part,archive)
            meta={"version_id":version_id,"path":PurePosixPath(str(relative).replace("\\","/")).as_posix(),"name":target.name,"size":size,"modified_at":target.stat().st_mtime,"created_at":time.time(),"sha256":digest.hexdigest()}
            meta_fd,temp_name=tempfile.mkstemp(prefix=".mcm-meta-",suffix=".part",dir=folder);temp=Path(temp_name)
            with os.fdopen(meta_fd,"w",encoding="utf-8") as output:json.dump(meta,output,ensure_ascii=False)
            os.replace(temp,metadata)
        finally:part.unlink(missing_ok=True)
        versions=sorted((p for p in folder.glob("*.json") if not _is_link_or_reparse(p) and p.is_file()),key=lambda p:p.stat().st_mtime,reverse=True)
        for old in versions[MAX_FILE_VERSIONS:]:old.with_suffix(".bin").unlink(missing_ok=True);old.unlink(missing_ok=True)
        all_versions=sorted((p for p in (self.profile_root(profile_id)/".mcm_versions").glob("*/*.json") if not _is_link_or_reparse(p) and p.is_file()),key=lambda p:p.stat().st_mtime)
        total=sum((path.with_suffix(".bin").stat().st_size if path.with_suffix(".bin").exists() else 0) for path in all_versions)
        while all_versions and (len(all_versions)>MAX_TOTAL_VERSION_COUNT or total>MAX_TOTAL_VERSION_BYTES):
            old=all_versions.pop(0);archive=old.with_suffix(".bin");total-=archive.stat().st_size if archive.exists() else 0;archive.unlink(missing_ok=True);old.unlink(missing_ok=True)
        return meta

    def _snapshot_tree(self, profile_id, relative):
        target=self.resolve(profile_id,relative)
        if target.is_file():self.snapshot(profile_id,relative);return
        if target.is_dir():
            files=[]
            for current,dirs,names in os.walk(target,followlinks=False):
                dirs[:]=[name for name in dirs if not _is_link_or_reparse(Path(current)/name)]
                for name in names:
                    path=Path(current)/name
                    if not _is_link_or_reparse(path) and path.is_file():files.append(path)
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
        if _is_link_or_reparse(archive) or _is_link_or_reparse(meta_path):raise PermissionError("檔案版本不可為符號連結或 Windows reparse point")
        if not archive.is_file() or not meta_path.is_file():raise FileNotFoundError("找不到檔案版本")
        meta=json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("path")!=PurePosixPath(str(relative).replace("\\","/")).as_posix():raise PermissionError("版本不屬於指定檔案")
        digest=hashlib.sha256()
        with _open_regular_file(archive) as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        if digest.hexdigest()!=meta.get("sha256"):raise ValueError("版本快照完整性驗證失敗")
        if target.is_file():self.snapshot(profile_id,relative)
        target.parent.mkdir(parents=True,exist_ok=True)
        descriptor,temp_name=tempfile.mkstemp(prefix=f".{target.name}.",suffix=".restoring",dir=target.parent);temp=Path(temp_name)
        try:
            with os.fdopen(descriptor,"wb") as output,_open_regular_file(archive) as source:shutil.copyfileobj(source,output,length=1024*1024)
            os.replace(temp,target)
        finally:temp.unlink(missing_ok=True)
        return {"path":str(relative),"restored_version":version_id,"created_at":meta.get("created_at")}

    @staticmethod
    def _conflict_target(target, conflict="error"):
        if conflict not in {"error", "overwrite", "rename"}:
            raise ValueError("衝突策略只能是 error、rename 或 overwrite")
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
        visible=[path for path in directory.iterdir() if not _is_internal_component(path.name) and not _is_link_or_reparse(path)]
        for path in sorted(visible, key=lambda item: (not item.is_dir(), item.name.lower())):
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
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not _is_internal_component(name) and not _is_link_or_reparse(Path(current) / name)]
            for name in names:
                if len(results) >= min(max(int(limit), 1), 1000):break
                path = Path(current) / name
                try:
                    if _is_link_or_reparse(path) or not path.is_file():
                        continue
                    if any(_is_internal_component(part) for part in path.relative_to(self.profile_root(profile_id)).parts):
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
                        stat_result = path.stat()
                        results.append({"name": path.name, "path": relative_path, "size": stat_result.st_size,
                                        "modified_at": stat_result.st_mtime, "content_matches": snippets})
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
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".uploading", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
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
        lock = self._upload_locks[hash((int(profile_id), str(upload_id))) % len(self._upload_locks)]
        staging_lock = self._staging_locks[int(profile_id) % len(self._staging_locks)]
        with staging_lock, lock:
            return self._upload_chunk_locked(profile_id, upload_id, relative, offset, data, final, conflict, max_bytes)

    def _upload_chunk_locked(self, profile_id, upload_id, relative, offset, data, final=False, conflict="error", max_bytes=MAX_UPLOAD_BYTES):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        data = bytes(data)
        if len(data) > MAX_UPLOAD_CHUNK_BYTES:
            raise ValueError("單次上傳分段不可超過 8 MiB")
        target = self.resolve(profile_id, relative)
        if target.suffix.lower() in BLOCKED_EXTENSIONS:
            raise ValueError("不允許上傳可執行檔")
        root = self.profile_root(profile_id)
        staging = self._upload_staging_dir(profile_id, create=True)
        self._cleanup_stale_uploads(staging)
        part = staging / f"{upload_id}.part"
        meta = staging / f"{upload_id}.json"
        try:
            if conflict not in {"error", "overwrite", "rename"}:
                raise ValueError("衝突策略只能是 error、rename 或 overwrite")
            if final:
                self._conflict_target(target, conflict)
            if _is_link_or_reparse(part) or _is_link_or_reparse(meta):
                raise PermissionError("上傳暫存檔不可為符號連結或 Windows reparse point")
            normalized_relative = PurePosixPath(str(relative).replace("\\", "/")).as_posix()
            if meta.exists():
                saved = json.loads(meta.read_text(encoding="utf-8"))
                if saved.get("path") != normalized_relative or saved.get("conflict") != conflict:
                    raise ValueError("上傳工作目的地或衝突策略與原工作不符")
            else:
                active_count, _ = self._staging_usage(staging)
                if active_count >= MAX_STAGED_UPLOADS:
                    raise ValueError("此伺服器的未完成上傳數已達上限，請完成或取消既有工作")
                descriptor, meta_temp = tempfile.mkstemp(prefix=f"{upload_id}-", suffix=".meta.tmp", dir=staging)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                        json.dump({"path": normalized_relative, "conflict": conflict, "created_at": time.time()}, output)
                    os.replace(meta_temp, meta)
                finally:
                    if os.path.exists(meta_temp):
                        os.remove(meta_temp)
            expected = part.stat().st_size if part.exists() else 0
            if int(offset) != expected:
                raise ValueError(f"分段位置錯誤，伺服器目前已接收 {expected} bytes")
            _, staged_bytes = self._staging_usage(staging)
            if staged_bytes + len(data) > MAX_STAGING_BYTES:
                raise ValueError("此伺服器的上傳暫存空間已達 2 GiB 上限")
            if expected + len(data) > max_bytes:
                raise ValueError("檔案超過 512 MB 上傳限制")
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(part, flags, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("上傳暫存路徑不是一般檔案")
                with os.fdopen(descriptor, "ab") as output:
                    output.write(data)
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            received = part.stat().st_size
            if final:
                final_target = self._conflict_target(target, conflict)
                target.parent.mkdir(parents=True, exist_ok=True)
                if final_target.exists():
                    self.snapshot(profile_id, final_target.relative_to(root).as_posix())
                os.replace(part, final_target)
                meta.unlink(missing_ok=True)
                return {"complete": True, "received": received, "path": final_target.relative_to(root).as_posix()}
            return {"complete": False, "received": received}
        except Exception:
            if final:
                if not _is_link_or_reparse(part):
                    part.unlink(missing_ok=True)
                if not _is_link_or_reparse(meta):
                    meta.unlink(missing_ok=True)
            raise

    def cancel_upload(self, profile_id, upload_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        staging = self._upload_staging_dir(profile_id)
        (staging / f"{upload_id}.part").unlink(missing_ok=True)
        (staging / f"{upload_id}.json").unlink(missing_ok=True)

    def upload_status(self, profile_id, upload_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(upload_id)):
            raise ValueError("無效的上傳工作識別碼")
        staging = self._upload_staging_dir(profile_id)
        self._cleanup_stale_uploads(staging)
        meta = staging / f"{upload_id}.json"
        part = staging / f"{upload_id}.part"
        if _is_link_or_reparse(meta) or _is_link_or_reparse(part):
            raise PermissionError("上傳狀態檔不可為符號連結或 Windows reparse point")
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
        if Path(new_name).suffix.lower() in BLOCKED_EXTENSIONS:
            raise ValueError("不允許重新命名為系統執行檔")
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
        if target.is_dir():
            raise IsADirectoryError("不能以文字檔覆寫資料夾")
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".editing", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
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
            def ignore_reparse_points(folder, names):
                return [name for name in names if _is_link_or_reparse(Path(folder) / name)]
            shutil.copytree(source, destination, symlinks=False, ignore=ignore_reparse_points,
                            copy_function=_copy_regular_file)
        else:
            _copy_regular_file(source, destination)
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
        descriptor,temp_name=tempfile.mkstemp(prefix=f".{target.name}.",suffix=".creating",dir=target.parent)
        temp=Path(temp_name)
        try:
            with os.fdopen(descriptor,"w+b") as file_object,zipfile.ZipFile(file_object,"w",zipfile.ZIP_DEFLATED) as archive:
                if source.is_file():
                    paths=[source]
                else:
                    paths=[]
                    for current,dirs,names in os.walk(source,followlinks=False):
                        dirs[:]=[name for name in dirs if not _is_link_or_reparse(Path(current)/name)]
                        paths.extend(Path(current)/name for name in names if not _is_link_or_reparse(Path(current)/name))
                for path in paths:
                    if any(_is_internal_component(part) for part in path.relative_to(self.profile_root(profile_id)).parts):continue
                    if path.is_file() and not _is_link_or_reparse(path) and path != target and path != temp:
                        arcname=path.relative_to(source.parent).as_posix()
                        with _open_regular_file(path) as input_file,archive.open(arcname,"w") as output_file:
                            shutil.copyfileobj(input_file,output_file,length=1024*1024)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    def unzip(self, profile_id, relative, destination, conflict="error"):
        if conflict not in {"error", "rename", "overwrite"}:
            raise ValueError("衝突策略只能是 error、rename 或 overwrite")
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
            safe_members = []
            kinds = {}
            for member in members:
                normalized_name = member.filename.replace("\\", "/")
                posix = PurePosixPath(normalized_name)
                parts = posix.parts
                if not parts or posix.is_absolute() or ".." in parts or "\x00" in normalized_name:
                    raise ValueError("ZIP 含有不安全的路徑")
                try:
                    _validate_path_components(parts)
                except PermissionError as exc:
                    raise ValueError("ZIP 含有不安全的路徑") from exc
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("ZIP 不允許包含符號連結或特殊檔案")
                key = tuple(_component_key(part) for part in parts)
                if key in kinds:
                    raise ValueError("ZIP 含有 Windows 大小寫或名稱正規化後重複的路徑")
                kinds[key] = member.is_dir()
                candidate = root.joinpath(*parts).resolve()
                if candidate != root and root not in candidate.parents:
                    raise ValueError("ZIP 含有不安全的路徑")
                cursor = root
                for component in parts:
                    cursor = cursor / component
                    if _is_link_or_reparse(cursor):
                        raise ValueError("解壓目的地包含符號連結或 Windows reparse point")
                if candidate.exists() and conflict == "error":
                    raise FileExistsError(f"解壓目的地已有同名項目：{posix.name}")
                if candidate.exists() and conflict == "rename":
                    raise ValueError("ZIP 內檔名衝突；請選擇覆寫或其他空資料夾")
                if candidate.exists() and conflict == "overwrite" and candidate.is_dir() != member.is_dir():
                    raise ValueError("無法以檔案覆寫資料夾或反向操作")
                safe_members.append((member, parts, key))
            keys = set(kinds)
            for key, is_directory in kinds.items():
                for depth in range(1, len(key)):
                    if key[:depth] in kinds and not kinds[key[:depth]]:
                        raise ValueError("ZIP 含有檔案與子路徑衝突")
                if not is_directory and any(other[:len(key)] == key and len(other) > len(key) for other in keys):
                    raise ValueError("ZIP 含有檔案與子路徑衝突")

            with tempfile.TemporaryDirectory(prefix=".mcm-unzip-", dir=root) as temporary:
                staging = Path(temporary) / "staging"
                rollback = Path(temporary) / "rollback"
                staging.mkdir()
                rollback.mkdir()
                actual_total = 0
                for member, parts, _ in safe_members:
                    staged = staging.joinpath(*parts)
                    if member.is_dir():
                        staged.mkdir(parents=True, exist_ok=True)
                        continue
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source_stream, staged.open("xb") as output:
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not chunk:
                                break
                            actual_total += len(chunk)
                            if actual_total > MAX_ZIP_UNPACK_BYTES:
                                raise ValueError("ZIP 實際解壓縮容量超過 8 GB")
                            output.write(chunk)

                created_dirs = []
                installed = []
                moved_old = []
                try:
                    directories = sorted((item for item in safe_members if item[0].is_dir()), key=lambda item: len(item[1]))
                    files = sorted((item for item in safe_members if not item[0].is_dir()), key=lambda item: len(item[1]))
                    for member, parts, _ in directories:
                        destination_path = root.joinpath(*parts)
                        if _is_link_or_reparse(destination_path):
                            raise ValueError("解壓目的地包含符號連結或 Windows reparse point")
                        if not destination_path.exists():
                            destination_path.mkdir(parents=True, exist_ok=False)
                            created_dirs.append(destination_path)
                    for member, parts, _ in files:
                        destination_path = root.joinpath(*parts)
                        cursor = root
                        for component in parts:
                            cursor = cursor / component
                            if _is_link_or_reparse(cursor):
                                raise ValueError("解壓目的地包含符號連結或 Windows reparse point")
                        destination_path.parent.mkdir(parents=True, exist_ok=True)
                        if destination_path.exists():
                            if conflict != "overwrite" or destination_path.is_dir():
                                raise FileExistsError(f"解壓目的地已有同名項目：{destination_path.name}")
                            relative_path = destination_path.relative_to(self.profile_root(profile_id)).as_posix()
                            self.snapshot(profile_id, relative_path)
                            backup_path = rollback.joinpath(*parts)
                            backup_path.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(destination_path, backup_path)
                            moved_old.append((backup_path, destination_path))
                        os.replace(staging.joinpath(*parts), destination_path)
                        installed.append(destination_path)
                except Exception:
                    for installed_path in reversed(installed):
                        if not _is_link_or_reparse(installed_path):
                            installed_path.unlink(missing_ok=True)
                    for backup_path, destination_path in reversed(moved_old):
                        destination_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(backup_path, destination_path)
                    for directory in reversed(created_dirs):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
                    raise
        return target
