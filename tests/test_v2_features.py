import hashlib
import os
import tempfile
import unittest
import zipfile

from backup_manager import BackupManager
from core_metadata import CoreMetadata
from diagnostics import ServerDiagnostics


class V2FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_backup_create_and_restore(self):
        os.makedirs(os.path.join(self.server_dir, "world"))
        with open(os.path.join(self.server_dir, "server.properties"), "w", encoding="utf-8") as file:
            file.write("motd=before\n")
        manager = BackupManager(self.server_dir)
        archive = manager.create_backup("test")
        self.assertTrue(os.path.exists(archive))
        with open(os.path.join(self.server_dir, "server.properties"), "w", encoding="utf-8") as file:
            file.write("motd=changed\n")
        manager.restore_backup(archive)
        with open(os.path.join(self.server_dir, "server.properties"), encoding="utf-8") as file:
            self.assertEqual(file.read(), "motd=before\n")

    def test_backup_rejects_path_traversal(self):
        archive = os.path.join(self.server_dir, "bad.zip")
        with zipfile.ZipFile(archive, "w") as file:
            file.writestr("../../outside.txt", "bad")
        with self.assertRaises(ValueError):
            BackupManager(self.server_dir).restore_backup(archive)

    def test_metadata_hash(self):
        jar = os.path.join(self.server_dir, "server.jar")
        with open(jar, "wb") as file:
            file.write(b"server")
        expected = hashlib.sha256(b"server").hexdigest()
        metadata = CoreMetadata(self.server_dir)
        metadata.save("Paper", "26.1", "server.jar", 25, expected)
        self.assertEqual(metadata.load()["java_required"], 25)
        self.assertEqual(metadata.verify(jar), (True, expected))

    def test_diagnostics_requires_eula(self):
        result = ServerDiagnostics(self.server_dir).check(25, 25, None, "2048")
        self.assertFalse(result["ok"])
        self.assertTrue(any("核心" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
