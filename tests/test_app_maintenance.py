import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from web_panel.app import create_app


class FakeBridge:
    def __init__(self, controller):
        self.controller = controller

    def status(self):
        return {"running": False, "phase": "stopped"}

    def logs(self, limit=100):
        return []

    def start(self):
        return True, "started"

    def stop(self):
        return True, "stopped"

    def send_command(self, command):
        return True, "sent"

    def restart(self):
        return True, "restarted"


class FakeController:
    def __init__(self, directory):
        self.server_directory = directory
        self.server_process = None
        self.web_bridge = FakeBridge(self)


class MaintenanceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server_dir = os.path.join(self.temp.name, "server")
        os.makedirs(self.server_dir)
        with open(os.path.join(self.server_dir, "server.properties"), "w", encoding="utf-8") as f:
            f.write("motd=hello\n")
        self.app = create_app(FakeController(self.server_dir), os.path.join(self.temp.name, "panel.db"))
        user_id = self.app.state.db.create_user("admin", "A-strong-password-123", "admin")
        session_id, self.csrf = self.app.state.db.create_session(user_id)
        self.profile_id = self.app.state.profiles.list()[0]["id"]
        self.client = TestClient(self.app)
        self.client.cookies.set("mcm_session", session_id)
        self.client.cookies.set("mcm_csrf", self.csrf)

    def tearDown(self):
        self.client.close()
        self.app.state.scheduler.stop_event.set()
        self.app.state.monitor.stop()
        self.temp.cleanup()

    def test_history_monitor_and_backup_pages_render(self):
        for path in (f"/server/{self.profile_id}/history", f"/server/{self.profile_id}/monitor", f"/server/{self.profile_id}/backups"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.text[:500]))
            if path.endswith("/monitor"):
                self.assertIn("max-width:100%", response.text)
                self.assertIn('class="monitor-chart"', response.text)
                self.assertIn("伺服器配置上限", response.text)
                self.assertIn("disk-used", response.text)
                self.assertIn("整機總 CPU", response.text)

    def test_backup_retention_verify_and_restore_safety(self):
        headers = {"x-csrf-token": self.csrf}
        retention = self.client.put(f"/api/servers/{self.profile_id}/backups/retention", headers=headers, json={"retain_count": 2, "max_age_days": 30})
        self.assertEqual(retention.status_code, 200, retention.text)
        created = self.client.post(f"/api/servers/{self.profile_id}/backups", headers=headers, json={"label": "api-test"})
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["task_id"]
        for _ in range(100):
            job = self.client.get(f"/api/servers/{self.profile_id}/backups/jobs/{task_id}").json()
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "completed", job)
        name = job["filename"]
        verified = self.client.post(f"/api/servers/{self.profile_id}/backups/verify", headers=headers, json={"name": name})
        self.assertEqual(verified.status_code, 200, verified.text)
        restored = self.client.post(f"/api/servers/{self.profile_id}/backups/restore", headers=headers, json={"name": name})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["safety_backup"])

    def test_alert_threshold_permissions_and_history(self):
        headers = {"x-csrf-token": self.csrf}
        result = self.client.put(f"/api/servers/{self.profile_id}/alerts", headers=headers, json={"cpu_percent": 80, "memory_percent": 85, "disk_used_percent": 95})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["thresholds"]["cpu_percent"], 80)
        history = self.client.get(f"/api/servers/{self.profile_id}/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertTrue(any(row["action"] == "update_alert_thresholds" for row in history.json()["operations"]))

    def test_account_edit_password_reset_and_delete(self):
        managed_id = self.app.state.db.create_user("old-name", "Old-password-123", "viewer")
        managed_session, _ = self.app.state.db.create_session(managed_id)
        edited = self.client.post(f"/admin/users/{managed_id}/edit", data={
            "csrf": self.csrf, "username": "new-name", "role": "operator", "password": "New-password-456"
        }, follow_redirects=False)
        self.assertEqual(edited.status_code, 303)
        self.assertIsNone(self.app.state.db.get_session(managed_session))
        self.assertIsNone(self.app.state.db.authenticate("old-name", "Old-password-123"))
        self.assertEqual(self.app.state.db.authenticate("new-name", "New-password-456")["role"], "operator")
        deleted = self.client.post(f"/admin/users/{managed_id}/delete", data={"csrf": self.csrf}, follow_redirects=False)
        self.assertEqual(deleted.status_code, 303)
        self.assertNotIn(managed_id, [entry["id"] for entry in self.app.state.db.list_users()])

    def test_admin_user_page_and_chinese_permission_checkboxes(self):
        managed_id = self.app.state.db.create_user("helper", "Helper-password-123", "viewer")
        response = self.client.get("/admin/users")
        self.assertEqual(response.status_code, 200)
        for text in ("編輯帳號資料", "重設密碼", "永久刪除帳號", "檢視者"):
            self.assertIn(text, response.text)
        permissions = self.client.get(f"/admin/users/{managed_id}/permissions")
        self.assertEqual(permissions.status_code, 200)
        for text in ("查看伺服器狀態", "重新啟動伺服器", "全部勾選", "套用檢視者預設"):
            self.assertIn(text, permissions.text)
        self.assertRegex(permissions.text, r'value="server\.start"\s*(?!checked)')
        saved = self.client.post(f"/admin/users/{managed_id}/permissions", data={
            "csrf": self.csrf, "server_id": str(self.profile_id),
            "permissions": ["server.view", "server.start", "resources.view"]
        }, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(self.app.state.db.get_server_permissions(managed_id, self.profile_id),
                         {"server.view", "server.start", "resources.view"})

    def test_protect_last_admin_from_demotion_and_deletion(self):
        current_id = next(entry["id"] for entry in self.app.state.db.list_users() if entry["username"] == "admin")
        with self.assertRaisesRegex(ValueError, "最後一位"):
            self.app.state.db.set_user_role(current_id, "viewer")
        with self.assertRaisesRegex(ValueError, "最後一位"):
            self.app.state.db.set_user_enabled(current_id, False)
        with self.assertRaisesRegex(ValueError, "最後一位"):
            self.app.state.db.delete_user(current_id)


if __name__ == "__main__":
    unittest.main()
