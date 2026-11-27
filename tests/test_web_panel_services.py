import tempfile
import unittest
from pathlib import Path

from web_panel.bridge import ServerBridge
from web_panel.database import WebDatabase
from web_panel.file_service import SafeFileService
from web_panel.multi_server import CoreAdapter, MultiServerManager, ServerProfileStore
from web_panel.operations import PlayerManager


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

    def test_command_and_player_injection_rejected(self):
        class DummyController:
            server_process = None
            server_directory = self.temp.name
            web_panel_port = 8765
            def log(self, message, tag_override=None):
                pass

        bridge = ServerBridge(DummyController())
        ok, msg = bridge.send_command("say hi\nop hacker")
        self.assertFalse(ok)
        self.assertIn("控制字元", msg)

        multi = MultiServerManager(self.store, bridge)
        ok, msg = multi.send_command(9999, "list")
        self.assertFalse(ok)
        self.assertIn("找不到伺服器", msg)

        players = PlayerManager(multi)
        ok, msg = players.command(1, "kick", "bad\nop evil")
        self.assertFalse(ok)
        self.assertIn("無效的玩家名稱", msg)


if __name__ == "__main__":
    unittest.main()
