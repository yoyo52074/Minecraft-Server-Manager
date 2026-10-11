import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from web_panel.database import WebDatabase
from web_panel.file_service import SafeFileService
from web_panel.multi_server import CoreAdapter, ServerProfileStore


class WebPanelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = WebDatabase(Path(self.temp.name) / "panel.db")
        self.store = ServerProfileStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_fabric_profile_and_launcher_detection(self):
        directory = Path(self.temp.name) / "fabric"
        profile = self.store.create("Fabric", directory, "Fabric", minecraft_version="1.21.1")
        (directory / "fabric-server-launch.jar").write_bytes(b"jar")
        launcher = CoreAdapter("Fabric").find_launcher(directory)
        self.assertEqual(launcher.name, "fabric-server-launch.jar")
        self.assertEqual(profile["core_type"], "Fabric")

    def test_file_service_rejects_path_traversal(self):
        profile = self.store.create("Paper", Path(self.temp.name) / "paper", "Paper")
        service = SafeFileService(self.store)
        with self.assertRaises(PermissionError):
            service.resolve(profile["id"], "../../outside.txt")

    def test_file_search_batch_and_conflict_policies(self):
        profile = self.store.create("Files", Path(self.temp.name) / "files", "Paper")
        service = SafeFileService(self.store)
        root = Path(profile["directory"])
        (root / "config.yml").write_text("motd: welcome\n", encoding="utf-8")
        (root / "other.txt").write_text("nothing here\n", encoding="utf-8")
        self.assertEqual([item["name"] for item in service.search(profile["id"], "config")], ["config.yml"])
        content_matches = service.search(profile["id"], "welcome", content=True)
        self.assertEqual(content_matches[0]["content_matches"][0]["line"], 1)
        service.mkdir(profile["id"], "target")
        copied = service.batch_copy_move(profile["id"], ["config.yml"], "target", conflict="rename")
        self.assertEqual(copied, ["target/config.yml"])
        (root / "target/config.yml").write_text("existing", encoding="utf-8")
        renamed = service.copy_or_move(profile["id"], "other.txt", "target/config.yml", conflict="rename")
        self.assertEqual(renamed.name, "config (1).yml")
        with self.assertRaises(FileExistsError):
            service.copy_or_move(profile["id"], "other.txt", "target/config.yml", conflict="error")
        self.assertEqual(service.delete_many(profile["id"], ["other.txt"]), 1)

    def test_file_versions_snapshot_before_edit_delete_and_restore(self):
        profile=self.store.create("Versions",Path(self.temp.name)/"versions","Paper");service=SafeFileService(self.store)
        root=Path(profile["directory"]);target=root/"server.properties";target.write_text("motd=old\n",encoding="utf-8")
        service.write_text(profile["id"],"server.properties","motd=new\n")
        versions=service.versions(profile["id"],"server.properties")["versions"]
        self.assertEqual(len(versions),1);self.assertEqual((root/"server.properties").read_text(encoding="utf-8"),"motd=new\n")
        service.restore_version(profile["id"],"server.properties",versions[0]["version_id"])
        self.assertEqual(target.read_text(encoding="utf-8"),"motd=old\n")
        self.assertEqual(len(service.versions(profile["id"],"server.properties")["versions"]),2)
        service.delete(profile["id"],"server.properties")
        self.assertFalse(target.exists());self.assertEqual(len(service.versions(profile["id"],"server.properties")["versions"]),3)
        with self.assertRaises(PermissionError):service.resolve(profile["id"],".mcm_versions")
        self.assertNotIn(".mcm_versions",[entry["name"] for entry in service.list(profile["id"])])

    def test_chunk_upload_resume_and_cancel(self):
        profile = self.store.create("Upload", Path(self.temp.name) / "upload", "Paper")
        service = SafeFileService(self.store)
        upload_id = uuid.uuid4().hex
        first = service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 0, b"abc", False, "error")
        self.assertEqual(first["received"], 3)
        with self.assertRaisesRegex(ValueError, "位置錯誤"):
            service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 0, b"x", False, "error")
        status = service.upload_status(profile["id"], upload_id)
        self.assertEqual(status["received"], 3)
        final = service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 3, b"def", True, "error")
        self.assertTrue(final["complete"])
        self.assertEqual((Path(profile["directory"]) / "plugins/test.jar").read_bytes(), b"abcdef")
        service.upload_chunk(profile["id"], upload_id, "cancel.bin", 0, b"x", False, "error")
        service.cancel_upload(profile["id"], upload_id)
        self.assertFalse(service.upload_status(profile["id"], upload_id)["exists"])

    def test_zip_extraction_rejects_traversal(self):
        profile = self.store.create("Zip", Path(self.temp.name) / "zip", "Paper")
        service = SafeFileService(self.store)
        root = Path(profile["directory"])
        with zipfile.ZipFile(root / "bad.zip", "w") as archive:
            archive.writestr("../../outside.txt", "bad")
        with self.assertRaisesRegex(ValueError, "不安全"):
            service.unzip(profile["id"], "bad.zip", "extract")
        self.assertFalse((Path(self.temp.name) / "outside.txt").exists())

    def test_profile_update_and_delete(self):
        profile = self.store.create("Old", Path(self.temp.name) / "old", "Vanilla")
        updated = self.store.update(profile["id"], {"name": "New", "core_type": "Forge"})
        self.assertEqual(updated["name"], "New")
        self.assertEqual(updated["core_type"], "Forge")
        self.store.delete(profile["id"])
        self.assertIsNone(self.store.get(profile["id"]))

    def test_server_permissions_default_deny_and_assign(self):
        profile = self.store.create("Protected", Path(self.temp.name) / "protected", "Paper")
        user_id = self.db.create_user("limited", "l" * 12, "viewer")
        user = {"user_id": user_id, "role": "viewer"}
        self.assertFalse(self.db.has_server_permission(user, profile["id"], "server.view"))
        self.db.set_server_permissions(user_id, profile["id"], {"server.view", "files.view"})
        self.assertTrue(self.db.has_server_permission(user, profile["id"], "server.view"))
        self.assertFalse(self.db.has_server_permission(user, profile["id"], "server.stop"))


if __name__ == "__main__":
    unittest.main()
