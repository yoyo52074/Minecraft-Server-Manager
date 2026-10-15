import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import requests

from core_metadata import java_version_compatible


class JavaRuntimeJobs:
    """Find an installed Java runtime or install Temurin for a server."""

    DOWNLOAD_HOSTS = {
        "api.adoptium.net", "github.com", "objects.githubusercontent.com",
        "release-assets.githubusercontent.com", "github-releases.githubusercontent.com",
    }
    MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
    MAX_EXTRACT_BYTES = 4 * 1024 * 1024 * 1024
    MAX_ARCHIVE_ENTRIES = 200000

    def __init__(self, profiles, database, runtime_root, start_server=None):
        self.profiles = profiles
        self.database = database
        self.runtime_root = Path(runtime_root)
        self.start_server = start_server
        self._lock = threading.RLock()
        self._jobs = {}
        self._active_by_server = {}
        self._latest_by_server = {}

    @staticmethod
    def _creation_flags():
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    @classmethod
    def _version(cls, executable):
        try:
            result = subprocess.run(
                [str(executable), "-version"], capture_output=True, text=True,
                timeout=8, check=False, creationflags=cls._creation_flags(),
            )
            output = f"{result.stdout}\n{result.stderr}"
            match = re.search(r'version\s+"(\d+)(?:\.(\d+))?', output, re.I)
            if match:
                major = int(match.group(1))
                minor = int(match.group(2) or 0)
                return minor if major == 1 and minor else major
            match = re.search(r"openjdk\s+(\d+)", output, re.I)
            return int(match.group(1)) if match else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0

    @classmethod
    def discover(cls):
        """Return installed Java executables from JAVA_HOME, PATH and common JDK roots."""
        candidates = set()
        executable_name = "java.exe" if os.name == "nt" else "java"

        def add(value):
            if not value:
                return
            path = Path(os.path.expandvars(str(value).strip().strip('"')))
            if path.name.lower() in {"java.exe", "java"}:
                candidates.add(path)
            else:
                candidates.add(path / "bin" / executable_name)

        add(os.environ.get("JAVA_HOME"))
        found_on_path = shutil.which("java")
        add(found_on_path)
        if os.name == "nt":
            where_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "where.exe")
            try:
                result = subprocess.run(
                    [where_exe, "java"], capture_output=True, text=True, timeout=8,
                    check=False, creationflags=cls._creation_flags(),
                )
                for line in result.stdout.splitlines():
                    add(line.strip())
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                import winreg
                registry_roots = (
                    r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE",
                    r"SOFTWARE\Eclipse Adoptium", r"SOFTWARE\Microsoft\JDK",
                    r"SOFTWARE\Azul Systems\Zulu", r"SOFTWARE\Amazon\Corretto",
                )
                hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
                views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
                for hive in hives:
                    for view in views:
                        for key_path in registry_roots:
                            try:
                                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as root_key:
                                    for candidate_key in (root_key,):
                                        try:
                                            add(winreg.QueryValueEx(candidate_key, "JavaHome")[0])
                                        except OSError:
                                            pass
                                    index = 0
                                    while True:
                                        try:
                                            subkey_name = winreg.EnumKey(root_key, index)
                                        except OSError:
                                            break
                                        index += 1
                                        try:
                                            with winreg.OpenKey(root_key, subkey_name, 0, winreg.KEY_READ | view) as subkey:
                                                add(winreg.QueryValueEx(subkey, "JavaHome")[0])
                                        except OSError:
                                            continue
                            except OSError:
                                continue
            except ImportError:
                pass

        roots = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(variable)
            if value:
                base = Path(value)
                roots.extend([
                    base / "Java", base / "Eclipse Adoptium", base / "Adoptium",
                    base / "Microsoft", base / "Zulu", base / "Amazon Corretto",
                    base / "BellSoft", base / "Programs" / "Eclipse Adoptium",
                    base / "Programs" / "Microsoft",
                ])
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            roots.append(Path(userprofile) / ".jdks")
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for java_path in root.glob(f"*/bin/{executable_name}"):
                    candidates.add(java_path)
                for java_path in root.glob(f"*/*/bin/{executable_name}"):
                    candidates.add(java_path)
            except OSError:
                continue

        installed = []
        visited = set()
        for candidate in candidates:
            try:
                normalized = str(candidate.resolve()).casefold()
            except OSError:
                normalized = str(candidate).casefold()
            if normalized in visited or not candidate.is_file():
                continue
            visited.add(normalized)
            major = cls._version(candidate)
            if major:
                installed.append({"path": str(candidate), "version": major})
        installed.sort(key=lambda item: (item["version"], item["path"].casefold()), reverse=True)
        return installed

    def start(self, server_id, required_major, actor_id=None, start_after=False):
        profile = self.profiles.get(server_id)
        if not profile:
            raise ValueError("找不到伺服器")
        required_major = int(required_major)
        if required_major not in {8, 11, 16, 17, 21, 25}:
            raise ValueError("不支援的 Java 相容版本")
        with self._lock:
            active_id = self._active_by_server.get(server_id)
            if active_id and self._jobs.get(active_id, {}).get("status") in {"queued", "running"}:
                return active_id
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "id": job_id, "server_id": server_id, "status": "queued",
                "stage": "search", "message": f"正在搜尋與 Minecraft 相容的 Java {required_major}…",
                "required_version": required_major, "java_path": "", "java_version": 0,
                "progress": 0, "downloaded_bytes": 0, "total_bytes": 0,
                "error": "", "start_after": bool(start_after), "start_result": None,
                "created_at": time.time(), "finished_at": None,
            }
            self._active_by_server[server_id] = job_id
            self._latest_by_server[server_id] = job_id
        threading.Thread(target=self._run, args=(job_id, dict(profile), actor_id, bool(start_after)), daemon=True).start()
        return job_id

    def _update(self, job_id, **values):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(values)

    def _apply(self, job_id, profile, path, major, source, actor_id, start_after):
        self.profiles.update(profile["id"], {"java_path": str(path)})
        self.database.audit(
            actor_id, "auto_configure_java",
            f"{source} Java {major}：{path}", server_id=profile["id"],
        )
        message = f"已找到並套用相容 Java {major}：{path}"
        start_result = None
        if start_after and self.start_server:
            ok, start_message = self.start_server(profile["id"], actor_id)
            start_result = {"ok": bool(ok), "message": str(start_message)}
            message += f"；{'已重試啟動' if ok else 'Java 已修復，但啟動仍未成功'}：{start_message}"
        self._update(job_id, status="completed", stage="complete", java_path=str(path),
                     java_version=major, start_result=start_result, message=message, finished_at=time.time())

    @classmethod
    def _download_response(cls, url):
        current = url
        for _ in range(6):
            parsed = urlparse(current)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.hostname.casefold() not in cls.DOWNLOAD_HOSTS
                    or parsed.username or parsed.password):
                raise RuntimeError("Java 下載重新導向至不受信任的 HTTPS 主機，已停止下載。")
            response = requests.get(current, stream=True, allow_redirects=False, timeout=(20, 90))
            if response.is_redirect:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise RuntimeError("Java 下載回應缺少重新導向位置。")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("Java 下載重新導向次數過多。")

    def _run(self, job_id, profile, actor_id, start_after):
        required = self._jobs[job_id]["required_version"]
        self._update(job_id, status="running")
        try:
            installed = self.discover()
            suitable = next((item for item in installed if java_version_compatible(required, item["version"])), None)
            if suitable:
                self._apply(job_id, profile, suitable["path"], suitable["version"], "自動偵測", actor_id, start_after)
                return
            self._update(job_id, stage="download", message=f"找不到相容的 Java {required}，正在自動下載 Temurin Java {required}…")
            path = self._download_and_install(required, job_id)
            major = self._version(path)
            if not java_version_compatible(required, major):
                raise RuntimeError(f"下載的 Java 版本不符：取得 Java {major}，此 Minecraft 版本需使用 Java {required}。")
            self._apply(job_id, profile, path, major, "自動下載並安裝", actor_id, start_after)
        except Exception as exc:
            self._update(job_id, status="failed", stage="failed", error=str(exc),
                         message="Java 搜尋／自動安裝失敗。可手動輸入 java.exe 路徑後再試。", finished_at=time.time())

    def _download_and_install(self, major, job_id=None):
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        final_dir = self.runtime_root / f"jdk-{major}"
        executable = final_dir / "bin" / ("java.exe" if os.name == "nt" else "java")
        if executable.is_file() and java_version_compatible(major, self._version(executable)):
            return executable
        if os.name != "nt":
            raise RuntimeError("自動下載功能目前支援 Windows x64；請在此系統手動安裝 Java。")
        url = f"https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk"
        work = Path(tempfile.mkdtemp(prefix=f"mcm-jdk-{major}-", dir=str(self.runtime_root)))
        archive_path = work / "temurin.zip.part"
        extract_dir = work / "extract"
        staged_dir = work / "runtime"
        try:
            with self._download_response(url) as response:
                try:
                    total_bytes = max(0, int(response.headers.get("content-length", 0)))
                except (TypeError, ValueError):
                    total_bytes = 0
                if total_bytes > self.MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Java 安裝檔超過 1 GiB，已停止下載。")
                if job_id:
                    self._update(job_id, stage="download", progress=0, downloaded_bytes=0,
                                 total_bytes=total_bytes, message=f"正在下載 Temurin Java {major}…")
                downloaded_bytes = 0
                with archive_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            if downloaded_bytes + len(chunk) > self.MAX_ARCHIVE_BYTES:
                                raise RuntimeError("Java 安裝檔超過 1 GiB，已停止下載。")
                            output.write(chunk)
                            downloaded_bytes += len(chunk)
                            if job_id:
                                progress = min(100, int(downloaded_bytes * 100 / total_bytes)) if total_bytes else 0
                                self._update(job_id, progress=progress, downloaded_bytes=downloaded_bytes,
                                             total_bytes=total_bytes,
                                             message=f"正在下載 Temurin Java {major}…")
            extract_dir.mkdir()
            if job_id:
                self._update(job_id, stage="verify", progress=0,
                             message="下載完成，正在驗證 Java 安裝檔…")
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > self.MAX_ARCHIVE_ENTRIES:
                    raise RuntimeError("Java 安裝檔項目過多，已停止解壓。")
                extract_total = sum(item.file_size for item in members if not item.is_dir())
                if extract_total > self.MAX_EXTRACT_BYTES:
                    raise RuntimeError("Java 安裝檔展開後超過 4 GiB，已停止解壓。")
                for member in members:
                    member_path = PurePosixPath(member.filename.replace("\\", "/"))
                    mode = member.external_attr >> 16
                    if (member_path.is_absolute() or re.match(r"^[A-Za-z]:", member.filename)
                            or ".." in member_path.parts or stat.S_ISLNK(mode)
                            or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
                        raise RuntimeError("Java 安裝壓縮檔含有不安全路徑，已停止解壓。")
                damaged_member = archive.testzip()
                if damaged_member:
                    raise RuntimeError(f"Java 安裝檔 CRC 驗證失敗：{damaged_member}")
                extract_bytes = 0
                if job_id:
                    self._update(job_id, stage="install", progress=0,
                                 message=f"Java 安裝檔驗證完成，正在解壓 Java {major}…")
                for member in members:
                    member_path = PurePosixPath(member.filename.replace("\\", "/"))
                    target = extract_dir.joinpath(*member_path.parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source, target.open("wb") as output:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            if extract_bytes + len(chunk) > self.MAX_EXTRACT_BYTES:
                                raise RuntimeError("Java 安裝檔展開後超過 4 GiB，已停止解壓。")
                            output.write(chunk)
                            extract_bytes += len(chunk)
                            if job_id:
                                progress = min(100, int(extract_bytes * 100 / extract_total)) if extract_total else 100
                                self._update(job_id, progress=progress,
                                             message=f"正在解壓 Java {major}…")
            executables = list(extract_dir.glob("*/bin/java.exe"))
            if not executables:
                raise RuntimeError("下載內容未包含有效的 Java 執行檔。")
            extracted_root = executables[0].parent.parent
            shutil.move(str(extracted_root), str(staged_dir))
            staged_java = staged_dir / "bin" / "java.exe"
            if job_id:
                self._update(job_id, stage="verify", progress=100,
                             message="解壓完成，正在確認 Java 版本…")
            detected = self._version(staged_java)
            if not java_version_compatible(major, detected):
                raise RuntimeError(f"下載檔案版本驗證失敗：Java {detected}，此 Minecraft 版本需使用 Java {major}。")
            if final_dir.exists():
                shutil.rmtree(final_dir)
            os.replace(staged_dir, final_dir)
            return final_dir / "bin" / "java.exe"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def get(self, job_id, server_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job and job["server_id"] == server_id else None

    def latest(self, server_id):
        with self._lock:
            job_id = self._latest_by_server.get(server_id)
            job = self._jobs.get(job_id) if job_id else None
            return dict(job) if job else None
