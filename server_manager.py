import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


class ServerManager:
    PAPER_API_BASE = "https://fill.papermc.io/v3"
    USER_AGENT = "Minecraft-Server-Manager/1.7 (https://github.com/yoyo52074/Minecraft-Server-Manager)"
    ALLOWED_DOWNLOAD_HOSTS = {
        "fill.papermc.io", "api.papermc.io", "cdn.papermc.io",
        "launchermeta.mojang.com", "piston-meta.mojang.com",
        "piston-data.mojang.com", "launcher.mojang.com", "maven.minecraftforge.net",
    }
    MAX_CORE_DOWNLOAD_BYTES = 1024 * 1024 * 1024

    def __init__(self, server_directory):
        self.server_directory = os.path.realpath(server_directory)
        os.makedirs(self.server_directory, exist_ok=True)
        self.properties_path = os.path.join(self.server_directory, "server.properties")

    def _validate_properties_path(self):
        try:
            info = os.lstat(self.properties_path)
        except FileNotFoundError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & reparse_flag:
            raise ValueError("server.properties 不可為符號連結或 Windows reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("server.properties 必須是一般檔案")
        return True

    def get_available_cores(self):
        return ["Paper", "Vanilla (官方版)", "Forge"]

    def get_core_versions(self, core_name):
        try:
            if core_name == "Paper":
                response = requests.get(f"{self.PAPER_API_BASE}/projects/paper", headers={"User-Agent": self.USER_AGENT}, timeout=15)
                response.raise_for_status()
                grouped_versions = response.json().get("versions", {})
                return [version for versions in grouped_versions.values() for version in versions]
            if core_name == "Vanilla (官方版)":
                response = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=15)
                response.raise_for_status()
                return [item["id"] for item in response.json()["versions"] if item["type"] == "release"]
            if core_name == "Forge":
                return ["1.20.1", "1.19.4", "1.18.2", "1.16.5"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"獲取 {core_name} 版本列表失敗: {exc}")
        return []

    def download_server(self, core_name, version, progress_callback):
        try:
            if core_name == "Paper":
                return self._download_paper(version, progress_callback)
            if core_name == "Vanilla (官方版)":
                return self._download_vanilla(version, progress_callback)
            if core_name == "Forge":
                return self._download_forge(version, progress_callback)
            return None, f"不支援的伺服器核心：{core_name}"
        except Exception as exc:
            return None, str(exc)

    def _download_paper(self, version, progress_callback):
        response = self._safe_get(f"{self.PAPER_API_BASE}/projects/paper/versions/{version}/builds", headers={"User-Agent": self.USER_AGENT}, timeout=15)
        response.raise_for_status()
        stable_builds = [build for build in response.json() if build.get("channel") == "STABLE"]
        if not stable_builds:
            return None, f"Paper {version} 沒有可用的穩定版本"
        download = stable_builds[0].get("downloads", {}).get("server:default")
        if not download or not download.get("url") or not download.get("name"):
            return None, f"Paper {version} 的下載資訊格式不正確"
        return self._download_file(download["url"], download["name"], progress_callback)

    def _download_vanilla(self, version, progress_callback):
        manifest_response = self._safe_get("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=15)
        manifest_response.raise_for_status()
        version_info_url = next((item["url"] for item in manifest_response.json()["versions"] if item["id"] == version), None)
        if not version_info_url:
            return None, "找不到該 Vanilla 版本"
        version_response = self._safe_get(version_info_url, timeout=15)
        version_response.raise_for_status()
        download_url = version_response.json().get("downloads", {}).get("server", {}).get("url")
        if not download_url:
            return None, f"Vanilla {version} 沒有可用的伺服器下載檔"
        return self._download_file(download_url, f"vanilla-{version}.jar", progress_callback)

    def _download_forge(self, version, progress_callback):
        forge_urls = {
            "1.20.1": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.2.0/forge-1.20.1-47.2.0-installer.jar",
            "1.19.4": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.19.4-45.2.0/forge-1.19.4-45.2.0-installer.jar",
            "1.18.2": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.18.2-40.2.1/forge-1.18.2-40.2.1-installer.jar",
            "1.16.5": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.16.5-36.2.39/forge-1.16.5-36.2.39-installer.jar",
        }
        if version not in forge_urls:
            return None, f"Forge {version} 的下載連結不存在"
        return self._download_file(forge_urls[version], f"forge-{version}-installer.jar", progress_callback)

    def _download_file(self, url, filename, progress_callback):
        filename = str(filename or "")
        if (not re.fullmatch(r"[A-Za-z0-9._+-]{1,180}", filename)
                or Path(filename).name != filename or filename in {".", ".."}):
            raise ValueError("核心下載檔名不安全，已停止寫入")
        root = Path(self.server_directory).resolve()
        target = root / filename
        if target.is_symlink() or target.parent.resolve() != root:
            raise ValueError("核心下載目的地超出伺服器資料夾")
        filepath = str(target)
        if target.exists():
            return filepath, f"檔案 '{os.path.basename(filename)}' 已存在。"
        fd, temp_path = tempfile.mkstemp(prefix=f".{filename}.", suffix=".part", dir=root)
        try:
            with self._safe_get(url, stream=True, timeout=30) as response:
                response.raise_for_status()
                total_size = int(response.headers.get("content-length", 0))
                if total_size > self.MAX_CORE_DOWNLOAD_BYTES:
                    raise ValueError("核心下載檔超過 1 GiB，已停止下載")
                bytes_downloaded = 0
                with os.fdopen(fd, "wb") as output:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        if bytes_downloaded + len(chunk) > self.MAX_CORE_DOWNLOAD_BYTES:
                            raise ValueError("核心下載檔超過 1 GiB，已停止下載")
                        output.write(chunk)
                        bytes_downloaded += len(chunk)
                        if total_size > 0:
                            progress_callback((bytes_downloaded / total_size) * 100)
            os.replace(temp_path, filepath)
            return filepath, f"'{os.path.basename(filename)}' 下載完成。"
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    @classmethod
    def _safe_get(cls, url, **kwargs):
        current = str(url)
        timeout = kwargs.pop("timeout", (20, 30))
        for _ in range(6):
            parsed = urlparse(current)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.hostname.casefold() not in cls.ALLOWED_DOWNLOAD_HOSTS
                    or parsed.username or parsed.password):
                raise ValueError("核心下載 URL 指向不受信任的 HTTPS 主機")
            response = requests.get(current, allow_redirects=False, timeout=timeout, **kwargs)
            if response.is_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise ValueError("核心下載重新導向缺少目的網址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            return response
        raise ValueError("核心下載重新導向次數過多")

    def get_server_properties(self):
        properties = {}
        if not self._validate_properties_path():
            return properties
        try:
            with open(self.properties_path, "r", encoding="utf-8") as file:
                for line in file:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    properties[key] = value
        except OSError as exc:
            print(f"讀取 server.properties 時發生錯誤: {exc}")
        return properties

    def save_server_properties(self, properties):
        self._validate_properties_path()
        descriptor, temporary = tempfile.mkstemp(prefix=".server-properties-", suffix=".tmp", dir=self.server_directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                file.write("# Minecraft server properties\n")
                file.write(f"# {datetime.now().astimezone().strftime('%a %b %d %H:%M:%S %Z %Y')}\n")
                for key, value in properties.items():
                    file.write(f"{key}={value}\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.properties_path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


__all__ = ["ServerManager"]
