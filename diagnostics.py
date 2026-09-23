import os
import socket


class ServerDiagnostics:
    def __init__(self, server_directory):
        self.server_directory = server_directory

    def check(self, java_version, required_java, jar_path, ram_mb, port=25565, process_running=False):
        errors = []
        warnings = []
        if process_running:
            errors.append("伺服器已在運行中")
        if not jar_path or not os.path.isfile(jar_path):
            errors.append("找不到伺服器核心 .jar 檔案")
        elif os.path.getsize(jar_path) < 1024 * 1024:
            warnings.append("伺服器核心檔案大小異常，可能需要重新下載")
        if java_version < required_java:
            errors.append(f"目前 Java {java_version or '未安裝'}，需要 Java {required_java} 或更新版本")
        try:
            ram = int(ram_mb)
            if ram < 1024:
                errors.append("記憶體至少需要 1024 MB")
        except (TypeError, ValueError):
            errors.append("記憶體必須是有效的數字")
        try:
            port = int(port)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if sock.connect_ex(("127.0.0.1", port)) == 0 and not process_running:
                    warnings.append(f"連接埠 {port} 目前可能已被占用")
        except (TypeError, ValueError):
            errors.append("伺服器連接埠必須是有效的數字")
        eula_path = os.path.join(self.server_directory, "eula.txt")
        if not os.path.exists(eula_path):
            errors.append("尚未找到 eula.txt")
        else:
            with open(eula_path, "r", encoding="utf-8") as file:
                if not any(line.strip().lower() == "eula=true" for line in file):
                    errors.append("尚未同意 Minecraft EULA")
        return {"ok": not errors, "errors": errors, "warnings": warnings}


__all__ = ["ServerDiagnostics"]
