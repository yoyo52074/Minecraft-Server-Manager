import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web_panel.app import create_app
from web_panel.multi_server import CoreAdapter


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


class FakeController:
    def __init__(self, directory):
        self.server_directory = directory
        self.server_process = None
        self.web_bridge = FakeBridge(self)


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server_dir = Path(self.temp.name) / "server"
        self.server_dir.mkdir()
        (self.server_dir / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
        self.app = create_app(FakeController(str(self.server_dir)), str(Path(self.temp.name) / "panel.db"))
        user_id = self.app.state.db.create_user("admin", "Strong-password-1234", "admin")
        self.admin_id = user_id
        session_id, self.csrf = self.app.state.db.create_session(user_id)
        self.profile_id = self.app.state.profiles.list()[0]["id"]
        self.client = TestClient(self.app, headers={"origin": "http://testserver"})
        self.client.cookies.set("mcm_session", session_id)
        self.client.cookies.set("mcm_csrf", self.csrf)

    def tearDown(self):
        self.client.close()
        self.app.state.scheduler.stop_event.set()
        self.app.state.scheduler.thread.join(timeout=1)
        self.app.state.monitor.stop()
        self.temp.cleanup()

    def _client_for(self, user_id):
        session_id, csrf = self.app.state.db.create_session(user_id)
        client = TestClient(self.app, headers={"origin": "http://testserver"})
        client.cookies.set("mcm_session", session_id)
        client.cookies.set("mcm_csrf", csrf)
        return client, csrf

    def test_csrf_cookie_alone_is_not_accepted(self):
        url = f"/api/servers/{self.profile_id}/files/mkdir"
        denied = self.client.post(url, json={"path": "cookie-only"})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertFalse((self.server_dir / "cookie-only").exists())

        accepted = self.client.post(url, json={"path": "header-token"}, headers={"x-csrf-token": self.csrf})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue((self.server_dir / "header-token").is_dir())

    def test_json_body_limit_rejects_oversized_request(self):
        body = b'{"path":"' + (b"x" * (12 * 1024 * 1024)) + b'"}'
        response = self.client.post(
            f"/api/servers/{self.profile_id}/files/mkdir",
            content=body,
            headers={"content-type": "application/json", "x-csrf-token": self.csrf},
        )
        self.assertEqual(response.status_code, 413, response.text)

    def test_same_site_sibling_origin_is_rejected(self):
        response = self.client.post(
            f"/api/servers/{self.profile_id}/files/mkdir",
            json={"path": "sibling-origin"},
            headers={
                "origin": "http://sibling.testserver",
                "sec-fetch-site": "same-site",
                "x-csrf-token": self.csrf,
            },
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertFalse((self.server_dir / "sibling-origin").exists())

    def test_non_admin_cannot_change_profile_root_with_settings_edit(self):
        user_id = self.app.state.db.create_user("settings-user", "Settings-password-123", "viewer")
        self.app.state.db.set_server_permissions(user_id, self.profile_id, {"settings.edit"})
        client, csrf = self._client_for(user_id)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        original = self.app.state.profiles.get(self.profile_id)["directory"]
        try:
            response = client.put(
                f"/api/servers/{self.profile_id}/settings",
                json={"profile": {"directory": str(outside)}},
                headers={"x-csrf-token": csrf},
            )
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(self.app.state.profiles.get(self.profile_id)["directory"], original)
        finally:
            client.close()

    def test_server_view_only_dashboard_hides_logs_metrics_and_path(self):
        user_id = self.app.state.db.create_user("status-viewer", "Viewer-password-1234", "viewer")
        self.app.state.db.set_server_permissions(user_id, self.profile_id, {"server.view"})
        client, _ = self._client_for(user_id)
        secret_path = str(Path(self.temp.name) / "private-server-path")
        self.app.state.multi.statuses = lambda: [{
            "id": self.profile_id,
            "name": "Test Server",
            "core_type": "Vanilla",
            "directory": secret_path,
            "server_port": 25565,
            "running": False,
            "logs": ["PRIVATE_LOG_SENTINEL"],
            "metrics": {"cpu_percent": "CPU_METRIC_PRIVATE_SENTINEL", "memory_mb": "MEMORY_METRIC_PRIVATE_SENTINEL", "disk_free_gb": 12},
        }]
        try:
            response = client.get("/")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("PRIVATE_LOG_SENTINEL", response.text)
            self.assertNotIn(secret_path, response.text)
            self.assertNotIn("MEMORY_METRIC_PRIVATE_SENTINEL", response.text)
            self.assertNotIn("CPU_METRIC_PRIVATE_SENTINEL", response.text)
        finally:
            client.close()

    def test_scheduler_rechecks_creator_permission_before_action(self):
        user_id = self.app.state.db.create_user("scheduler", "Scheduler-password-123", "viewer")
        self.app.state.db.set_server_permissions(
            user_id, self.profile_id, {"schedule.create", "schedule.run", "server.stop"}
        )
        client, csrf = self._client_for(user_id)
        try:
            response = client.post(
                f"/api/servers/{self.profile_id}/schedules",
                json={"name": "Stop test", "action": "stop", "interval_seconds": 3600},
                headers={"x-csrf-token": csrf},
            )
            self.assertEqual(response.status_code, 200, response.text)
            schedule_id = response.json()["schedule"]["id"]
            item = self.app.state.scheduler.get(schedule_id)
            self.assertEqual(item["created_by"], user_id)

            self.app.state.db.set_server_permissions(user_id, self.profile_id, {"schedule.create", "schedule.run"})
            with patch.object(self.app.state.multi, "stop") as stop:
                with self.assertRaisesRegex(PermissionError, "已不具備"):
                    self.app.state.scheduler._execute(item)
                stop.assert_not_called()

            denied = client.post(
                f"/api/schedules/{schedule_id}/run",
                headers={"x-csrf-token": csrf},
            )
            self.assertEqual(denied.status_code, 403, denied.text)
        finally:
            client.close()

    def test_non_admin_start_cannot_execute_forge_shell_launcher(self):
        forge_dir = Path(self.temp.name) / "forge"
        forge_dir.mkdir()
        marker = Path(self.temp.name) / "script-ran"
        (forge_dir / "run.sh").write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        profile = self.app.state.profiles.create(
            "Script Forge", str(forge_dir), "Forge", minecraft_version="1.12.2", launcher="run.sh"
        )
        adapter = CoreAdapter("Forge")

        self.assertEqual(adapter.find_launcher(forge_dir).name, "run.sh")
        self.assertIsNone(adapter.find_launcher(forge_dir, "run.sh", allow_scripts=False))
        user_id = self.app.state.db.create_user("script-operator", "Script-password-1234", "operator")
        self.app.state.db.set_server_permissions(user_id, profile["id"], {"server.start"})
        client, csrf = self._client_for(user_id)
        try:
            response = client.post(
                f"/api/servers/{profile['id']}/start",
                headers={"x-csrf-token": csrf},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["ok"])
            self.assertFalse(marker.exists())
        finally:
            client.close()

    def test_login_attempts_are_rate_limited(self):
        responses = []
        for _ in range(5):
            responses.append(self.client.post(
                "/login",
                data={"username": "missing-user", "password": "incorrect-password"},
            ))
        self.assertEqual([response.status_code for response in responses[:4]], [200] * 4)
        self.assertEqual(responses[4].status_code, 429, responses[4].text)
        self.assertGreater(int(responses[4].headers.get("retry-after", "0")), 0)
        still_limited = self.client.post(
            "/login",
            data={"username": "missing-user", "password": "incorrect-password"},
        )
        self.assertEqual(still_limited.status_code, 429)


if __name__ == "__main__":
    unittest.main()
