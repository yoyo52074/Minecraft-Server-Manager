import hashlib
import json
import os


class CoreMetadata:
    FILENAME = "core-info.json"

    def __init__(self, server_directory):
        self.path = os.path.join(server_directory, self.FILENAME)

    def save(self, core, version, jar_name, java_required, sha256=None):
        data = {
            "core": core,
            "minecraft_version": version,
            "jar": jar_name,
            "java_required": java_required,
            "sha256": sha256,
        }
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return data

    def load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def sha256(path, chunk_size=1024 * 1024):
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def verify(self, jar_path):
        expected = self.load().get("sha256")
        if not expected:
            return True, "未提供 SHA-256，略過驗證"
        actual = self.sha256(jar_path)
        return actual.lower() == expected.lower(), actual


__all__ = ["CoreMetadata"]
