import hashlib
import json
import os
import re


def minimum_java_version(minecraft_version):
    """Return the recommended compatible Java major for this Minecraft release family."""
    if not minecraft_version:
        return 17
    numbers = [int(value) for value in re.findall(r"\d+", str(minecraft_version))[:3]]
    if not numbers:
        return 17
    major = numbers[0]
    minor = numbers[1] if len(numbers) > 1 else 0
    patch = numbers[2] if len(numbers) > 2 else 0

    # Use the PaperMC compatibility guidance for legacy releases and the Java
    # generation floors introduced by newer Minecraft releases. Legacy targets
    # are pinned to their matching major below to avoid unsupported mod stacks.
    if major > 26 or (major == 26 and minor >= 1) or (major == 1 and minor >= 22):
        return 25
    if major == 26:
        return 21
    if major == 1:
        if minor >= 21:
            return 21
        if minor == 20 and patch >= 5:
            return 21
        if minor == 20:
            return 17
        if minor >= 17:
            return 17
        if minor == 16 and patch >= 5:
            return 16
        if minor >= 12:
            return 11
        return 8
    return 17


def java_version_compatible(required_major, installed_major):
    """Legacy server families need their matching runtime; modern versions accept newer Java."""
    try:
        required_major = int(required_major)
        installed_major = int(installed_major)
    except (TypeError, ValueError):
        return False
    return installed_major == required_major if required_major <= 16 else installed_major >= required_major


def java_requirement_for_core(core_type, minecraft_version):
    """Return the runtime target for a core, including legacy Forge's Java 8 constraint."""
    numbers = [int(value) for value in re.findall(r"\d+", str(minecraft_version or ""))[:2]]
    if str(core_type or "").casefold() == "forge" and len(numbers) >= 2:
        if numbers[0] == 1 and numbers[1] <= 12:
            return 8
    return minimum_java_version(minecraft_version)


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


__all__ = ["CoreMetadata", "minimum_java_version", "java_version_compatible", "java_requirement_for_core"]
