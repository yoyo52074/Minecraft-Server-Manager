import requests
import os
import json

class ServerManager:
    def __init__(self, server_directory):
        self.server_directory = server_directory
        if not os.path.exists(self.server_directory):
            os.makedirs(self.server_directory)
        self.properties_path = os.path.join(self.server_directory, "server.properties")

    def get_available_cores(self):
        return ["Paper", "Vanilla (官方版)", "Forge"]

    def get_core_versions(self, core_name):
        try:
            if core_name == "Paper":
                response = requests.get("https://api.papermc.io/v2/projects/paper", timeout=15)
                response.raise_for_status()
                return response.json().get("versions", [])
            elif core_name == "Vanilla (官方版)":
                response = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=15)
                response.raise_for_status()
                return [v['id'] for v in response.json()['versions'] if v['type'] == 'release']
            elif core_name == "Forge":
                return ["1.20.1", "1.19.4", "1.18.2", "1.16.5"]
        except requests.RequestException as e:
            print(f"獲取 {core_name} 版本列表失敗: {e}")
            return []
        
    def download_server(self, core_name, version, progress_callback):
        try:
            if core_name == "Paper":
                return self._download_paper(version, progress_callback)
            elif core_name == "Vanilla (官方版)":
                return self._download_vanilla(version, progress_callback)
            elif core_name == "Forge":
                return self._download_forge(version, progress_callback)
        except Exception as e:
            return None, str(e)

    def _download_paper(self, version, progress_callback):
        builds_url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds"
        builds_res = requests.get(builds_url, timeout=15).json()
        latest_build = builds_res["builds"][-1]["build"]
        jar_name = builds_res["builds"][-1]["downloads"]["application"]["name"]
        download_url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest_build}/downloads/{jar_name}"
        return self._download_file(download_url, jar_name, progress_callback)
    
    def _download_vanilla(self, version, progress_callback):
        manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
        version_manifest = requests.get(manifest_url, timeout=15).json()
        version_info_url = next((v['url'] for v in version_manifest['versions'] if v['id'] == version), None)
        if not version_info_url: return None, "找不到該 Vanilla 版本"
        version_info = requests.get(version_info_url, timeout=15).json()
        download_url = version_info['downloads']['server']['url']
        jar_name = f"vanilla-{version}.jar"
        return self._download_file(download_url, jar_name, progress_callback)

    def _download_forge(self, version, progress_callback):
        forge_urls = {
            "1.20.1": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.2.0/forge-1.20.1-47.2.0-installer.jar",
            "1.19.4": "https://maven.minecraftforge.net/net/minecraftforge/forge/1.19.4-45.2.0/forge-1.19.4-45.2.0-installer.jar",
        }
        if version not in forge_urls: return None, f"Forge {version} 的範例下載連結不存在"
        download_url = forge_urls[version]
        jar_name = f"forge-{version}-installer.jar"
        return self._download_file(download_url, jar_name, progress_callback)

    def _download_file(self, url, filename, progress_callback):
        filepath = os.path.join(self.server_directory, filename)
        if os.path.exists(filepath): return filepath, f"檔案 '{os.path.basename(filename)}' 已存在。"
        
        with requests.get(url, stream=True, allow_redirects=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            with open(filepath, 'wb') as f:
                bytes_downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk); bytes_downloaded += len(chunk)
                    if total_size > 0: progress_callback((bytes_downloaded / total_size) * 100)
        return filepath, f"'{os.path.basename(filename)}' 下載完成。"

    def get_server_properties(self):
        properties = {}
        if not os.path.exists(self.properties_path): return {}
        with open(self.properties_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    properties[key] = value
        return properties

    def save_server_properties(self, properties):
        with open(self.properties_path, 'w', encoding='utf-8') as f:
            f.write("# Minecraft server properties\n")
            for key, value in properties.items():
                f.write(f"{key}={value}\n")