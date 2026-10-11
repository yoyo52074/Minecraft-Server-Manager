import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from starlette.websockets import WebSocketDisconnect

from fastapi.testclient import TestClient
from web_panel.app import create_app
from web_panel.java_runtime import JavaRuntimeJobs


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
        self.session_id = session_id
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
                self.assertIn("配置上限", response.text)
                self.assertIn("disk-used", response.text)
                self.assertIn("整機總運算能力", response.text)
                self.assertIn("TPS", response.text)
                self.assertIn("MSPT", response.text)
                self.assertIn("Java Heap", response.text)

    def test_server_shortcuts_are_in_header_and_java_scanner_is_removed(self):
        response=self.client.get(f"/server/{self.profile_id}")
        self.assertEqual(response.status_code,200,response.text[:500])
        header=response.text.split("</nav>",1)[0]
        for path in (f"/server/{self.profile_id}/backups",f"/server/{self.profile_id}/players",f"/server/{self.profile_id}/schedules",f"/server/{self.profile_id}/settings"):
            self.assertIn(f'href="{path}"',header)
        self.assertNotIn("Java 管理",response.text)
        self.assertEqual(self.client.get("/java").status_code,404)
        self.assertEqual(self.client.get("/api/java").status_code,404)

    def test_dashboard_has_safe_server_remove_action(self):
        response=self.client.get("/")
        self.assertEqual(response.status_code,200)
        self.assertNotIn("Windows 多伺服器管理面板",response.text)
        self.assertNotIn("支援 Vanilla、Paper、Forge、Fabric",response.text)
        self.assertIn('id="live"',response.text)
        self.assertIn("移除伺服器",response.text)
        self.assertIn("世界檔案會保留",response.text)
        self.assertIn("delete_files:false",response.text)

    def test_delete_server_profile_preserves_server_directory(self):
        directory=Path(self.temp.name)/"removable"
        (directory/"world").mkdir(parents=True)
        (directory/"world"/"level.dat").write_bytes(b"world-data")
        profile=self.app.state.profiles.create("Removable",directory,"Paper")
        response=self.client.request("DELETE",f"/api/servers/{profile['id']}",headers={"x-csrf-token":self.csrf},json={"delete_files":False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.app.state.profiles.get(profile["id"]))
        self.assertEqual((directory/"world"/"level.dat").read_bytes(),b"world-data")

    def test_removed_legacy_server_stays_removed_until_manually_readded(self):
        response=self.client.request("DELETE",f"/api/servers/{self.profile_id}",headers={"x-csrf-token":self.csrf},json={"delete_files":False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.app.state.profiles.ensure_legacy(self.server_dir))
        self.assertTrue(Path(self.server_dir,"server.properties").is_file())
        recreated=self.app.state.profiles.create("重新新增",self.server_dir,"Paper")
        self.assertEqual(self.app.state.profiles.ensure_legacy(self.server_dir)["id"],recreated["id"])

    def test_settings_page_has_real_core_version_update_controls(self):
        response=self.client.get(f"/server/{self.profile_id}/settings")
        self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn("尋找並自動套用 Java",response.text)
        self.assertIn("自動下載、驗證並安裝 Temurin Java",response.text)
        self.assertIn("變更 Minecraft 版本",response.text)
        self.assertIn("/core-update",response.text)
        self.assertIn("目標 Minecraft 版本",response.text)
        self.assertIn("backups/cores",response.text)

    def test_java_auto_search_applies_matching_runtime_and_logs_it(self):
        self.app.state.profiles.update(self.profile_id,{"minecraft_version":"26.2"})
        with patch.object(JavaRuntimeJobs,"discover",return_value=[{"path":r"C:\Java\jdk-25\bin\java.exe","version":25}]):
            response=self.client.post(f"/api/servers/{self.profile_id}/java/auto",headers={"x-csrf-token":self.csrf})
            self.assertEqual(response.status_code,200,response.text)
            for _ in range(100):
                job=self.client.get(f"/api/servers/{self.profile_id}/java/auto/{response.json()['job_id']}").json()["job"]
                if job["status"] in {"completed","failed"}:break
                time.sleep(.01)
        self.assertEqual(job["status"],"completed",job)
        self.assertEqual(job["required_version"],25)
        self.assertEqual(self.app.state.profiles.get(self.profile_id)["java_path"],r"C:\Java\jdk-25\bin\java.exe")
        self.assertTrue(any(row["action"]=="auto_configure_java" for row in self.app.state.db.server_audit_logs(self.profile_id,20)))

    def test_java_auto_search_downloads_when_no_compatible_runtime_is_found(self):
        self.app.state.profiles.update(self.profile_id,{"minecraft_version":"26.2"})
        expected=r"C:\Users\Test\AppData\Roaming\MinecraftServerManager\runtimes\jdk-25\bin\java.exe"
        with patch.object(JavaRuntimeJobs,"discover",return_value=[]), patch.object(JavaRuntimeJobs,"_download_and_install",return_value=expected), patch.object(JavaRuntimeJobs,"_version",return_value=25):
            response=self.client.post(f"/api/servers/{self.profile_id}/java/auto",headers={"x-csrf-token":self.csrf})
            self.assertEqual(response.status_code,200,response.text)
            for _ in range(100):
                job=self.client.get(f"/api/servers/{self.profile_id}/java/auto/{response.json()['job_id']}").json()["job"]
                if job["status"] in {"completed","failed"}:break
                time.sleep(.01)
        self.assertEqual(job["status"],"completed",job)
        self.assertEqual(job["java_path"],expected)
        self.assertEqual(self.app.state.profiles.get(self.profile_id)["java_path"],expected)

    def test_start_failure_auto_searches_java_and_retries_start(self):
        self.app.state.profiles.update(self.profile_id,{"minecraft_version":"26.2"})
        java_path=r"C:\Java\jdk-25\bin\java.exe"
        with patch.object(self.app.state.multi,"start",return_value=(False,"Minecraft 26.2 需要 Java 25 或更新版本。目前找到的 Java 版本不足：Java 21。")), patch.object(JavaRuntimeJobs,"discover",return_value=[{"path":java_path,"version":25}]), patch.object(self.app.state.java_runtime,"start_server",return_value=(True,"已送出啟動要求")) as retry_start:
            response=self.client.post(f"/api/servers/{self.profile_id}/start",headers={"x-csrf-token":self.csrf})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn("java_setup_job_id",response.json())
            for _ in range(100):
                job=self.client.get(f"/api/servers/{self.profile_id}/java/auto/{response.json()['java_setup_job_id']}").json()["job"]
                if job["status"] in {"completed","failed"}:break
                time.sleep(.01)
        self.assertEqual(job["status"],"completed",job)
        self.assertTrue(job["start_after"])
        self.assertTrue(job["start_result"]["ok"])
        retry_start.assert_called_once_with(self.profile_id)
        self.assertEqual(self.app.state.profiles.get(self.profile_id)["java_path"],java_path)

    def test_java_auto_search_requires_csrf(self):
        self.client.cookies.delete("mcm_csrf")
        response=self.client.post(f"/api/servers/{self.profile_id}/java/auto")
        self.assertEqual(response.status_code,403)

    def test_core_update_api_rejects_unsupported_core_and_running_server(self):
        url=f"/api/servers/{self.profile_id}/core-update"
        headers={"x-csrf-token":self.csrf}
        unsupported=self.client.post(url,headers=headers,json={"core_type":"Forge","version":"1.21.4"})
        self.assertEqual(unsupported.status_code,400)
        self.assertIn("Paper 與 Vanilla",unsupported.json()["detail"])
        with patch.object(self.app.state.multi,"status",return_value={"running":True,"phase":"running"}):
            running=self.client.post(url,headers=headers,json={"core_type":"Paper","version":"1.21.4"})
        self.assertEqual(running.status_code,400)
        self.assertIn("停止伺服器",running.json()["detail"])

    def test_responsive_layout_styles_are_shared_across_management_pages(self):
        paths=("/",f"/server/{self.profile_id}",f"/server/{self.profile_id}/files",f"/server/{self.profile_id}/backups",f"/server/{self.profile_id}/players",f"/server/{self.profile_id}/schedules",f"/server/{self.profile_id}/monitor")
        for path in paths:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,(path,response.text[:300]))
            self.assertIn('name="viewport" content="width=device-width, initial-scale=1"',response.text)
            self.assertIn("@media(max-width:640px)",response.text)
            self.assertIn("overflow-x:auto",response.text)

    def test_start_api_explains_java_25_requirement_before_launch(self):
        directory=Path(self.temp.name)/"minecraft-26-1"
        profile=self.app.state.profiles.create("Minecraft 26.1",directory,"Paper",minecraft_version="26.1",java_path="java")
        (directory/"server.jar").write_bytes(b"placeholder")
        with patch("web_panel.multi_server.CoreAdapter.java_major_version",return_value=21), patch("web_panel.multi_server.subprocess.Popen") as popen, patch.object(JavaRuntimeJobs,"discover",return_value=[]):
            response=self.client.post(f"/api/servers/{profile['id']}/start",headers={"x-csrf-token":self.csrf})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()["ok"])
        self.assertIn("Minecraft 26.1",response.json()["message"])
        self.assertIn("Java 25",response.json()["message"])
        self.assertIn("Java 21",response.json()["message"])
        popen.assert_not_called()

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

    def test_delete_backup_requires_csrf_token(self):
        backup_dir=Path(self.server_dir)/"backups"
        backup_dir.mkdir(exist_ok=True)
        backup=backup_dir/"csrf-check.zip"
        with zipfile.ZipFile(backup,"w") as archive:
            archive.writestr("server.properties","motd=test\n")
        url=f"/api/servers/{self.profile_id}/backups/{backup.name}"
        denied=self.client.delete(url,headers={"x-csrf-token":"invalid-token"})
        self.assertEqual(denied.status_code,403)
        self.assertTrue(backup.is_file())
        allowed=self.client.delete(url,headers={"x-csrf-token":self.csrf})
        self.assertEqual(allowed.status_code,200,allowed.text)
        self.assertFalse(backup.exists())

    def test_legacy_status_and_logs_require_server_permissions(self):
        user_id=self.app.state.db.create_user("restricted-viewer","Viewer-password-123","viewer")
        session_id,_=self.app.state.db.create_session(user_id)
        client=TestClient(self.app)
        client.cookies.set("mcm_session",session_id)
        try:
            self.assertEqual(client.get("/api/status").status_code,403)
            self.assertEqual(client.get("/api/logs").status_code,403)
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/logs") as socket:
                    socket.send_text("poll")
                    socket.receive_text()
        finally:
            client.close()

    def test_servers_websocket_revalidates_revoked_sessions(self):
        with self.client.websocket_connect("/ws/servers",headers={"origin":"http://testserver"}) as socket:
            first=socket.receive_json()
            self.assertEqual(first["type"],"servers")
            self.app.state.db.delete_session(self.session_id)
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()

    def test_deleted_legacy_profile_invalidates_api_and_log_websocket(self):
        viewer_id=self.app.state.db.create_user("legacy-viewer","Viewer-password-123","viewer")
        self.app.state.db.set_server_permissions(viewer_id,self.profile_id,{"console.view"})
        viewer_session,_=self.app.state.db.create_session(viewer_id)
        client=TestClient(self.app)
        client.cookies.set("mcm_session",viewer_session)
        database_path=str(Path(self.temp.name)/"panel.db")
        try:
            with client.websocket_connect("/ws/logs",headers={"origin":"http://testserver"}) as socket:
                self.app.state.profiles.delete(self.profile_id)
                socket.send_text("poll")
                with self.assertRaises(WebSocketDisconnect):
                    socket.receive_text()
            self.assertEqual(client.get("/api/status").status_code,404)
            self.assertEqual(client.get("/api/logs").status_code,404)
            with self.app.state.db._connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM server_permissions WHERE server_id=?",(self.profile_id,)).fetchone()[0],0)
        finally:
            client.close()
        restarted=create_app(FakeController(self.server_dir),database_path)
        restarted_client=TestClient(restarted)
        try:
            self.assertEqual(restarted_client.get("/api/status").status_code,404)
            self.assertEqual(restarted_client.get("/api/logs").status_code,404)
            with self.assertRaises(WebSocketDisconnect):
                with restarted_client.websocket_connect("/ws/logs",headers={"origin":"http://testserver"}):
                    pass
        finally:
            restarted_client.close()
            restarted.state.scheduler.stop_event.set()
            restarted.state.monitor.stop()

    def test_chunk_upload_http_rejects_oversized_stream(self):
        url=f"/api/servers/{self.profile_id}/files/upload/chunk?upload_id={'a'*32}&path=plugins%2Ftest.jar&offset=0&final=true"
        response=self.client.post(url,content=b"x"*(8*1024*1024+1),headers={"x-csrf-token":self.csrf,"content-type":"application/octet-stream"})
        self.assertEqual(response.status_code,413,response.text)

    def test_first_admin_setup_rejects_cross_site_request(self):
        empty_dir=Path(self.temp.name)/"empty-server"
        empty_app=create_app(FakeController(str(empty_dir)),str(Path(self.temp.name)/"empty-panel.db"))
        remote=TestClient(empty_app,client=("203.0.113.7",51000))
        local=TestClient(empty_app,client=("127.0.0.1",51001))
        try:
            denied_remote=remote.post("/setup",data={"username":"attacker","password":"Attacker-password-123"},headers={"origin":"http://testserver","sec-fetch-site":"same-origin"})
            self.assertEqual(denied_remote.status_code,403)
            denied_cross=local.post("/setup",data={"username":"attacker","password":"Attacker-password-123"},headers={"origin":"https://attacker.example","sec-fetch-site":"cross-site"})
            self.assertEqual(denied_cross.status_code,403)
            self.assertEqual(empty_app.state.db.user_count(),0)
            allowed=local.post("/setup",data={"username":"first-admin","password":"Secure-password-123"},headers={"origin":"http://testserver","sec-fetch-site":"same-origin"},follow_redirects=False)
            self.assertEqual(allowed.status_code,303,allowed.text)
            self.assertEqual(empty_app.state.db.user_count(),1)
            with self.assertRaisesRegex(ValueError,"已初始化"):
                empty_app.state.db.create_first_admin("second-admin","Another-password-123")
        finally:
            remote.close()
            local.close()
            empty_app.state.scheduler.stop_event.set()
            empty_app.state.monitor.stop()

    def test_alert_threshold_permissions_and_history(self):
        headers = {"x-csrf-token": self.csrf}
        result = self.client.put(f"/api/servers/{self.profile_id}/alerts", headers=headers, json={"cpu_percent": 80, "memory_percent": 85, "disk_used_percent": 95})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["thresholds"]["cpu_percent"], 80)
        history = self.client.get(f"/api/servers/{self.profile_id}/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertTrue(any(row["action"] == "update_alert_thresholds" for row in history.json()["operations"]))

    def test_backup_scope_jobs_and_settings_snapshot_retention(self):
        headers={"x-csrf-token":self.csrf};profile=self.app.state.profiles.get(self.profile_id)
        os.makedirs(os.path.join(profile["directory"],"world"),exist_ok=True)
        with open(os.path.join(profile["directory"],"world","level.dat"),"w") as f:f.write("world")
        retention=self.client.put(f"/api/servers/{self.profile_id}/backups/settings-retention",headers=headers,json={"retain_count":3,"max_age_days":30})
        self.assertEqual(retention.status_code,200,retention.text)
        created=self.client.post(f"/api/servers/{self.profile_id}/backups",headers=headers,json={"label":"world-test","scope":"worlds"})
        self.assertEqual(created.status_code,200,created.text)
        task_id=created.json()["task_id"]
        for _ in range(100):
            state=self.client.get(f"/api/servers/{self.profile_id}/backups/jobs/{task_id}").json()
            if state["status"] in {"completed","failed"}:break
            time.sleep(.02)
        self.assertEqual(state["status"],"completed")
        history=self.client.get(f"/api/servers/{self.profile_id}/backups/jobs?limit=20")
        self.assertEqual(history.status_code,200,history.text)
        self.assertEqual(history.json()["jobs"][0]["scope"],"worlds")

    def test_schedule_advanced_api_and_player_history_api(self):
        headers={"x-csrf-token":self.csrf}
        created=self.client.post(f"/api/servers/{self.profile_id}/schedules",headers=headers,json={"name":"每日存檔","action":"command_save_all","schedule_type":"daily","run_time":"04:10","ready_timeout_seconds":0,"retry_count":1})
        self.assertEqual(created.status_code,200,created.text);schedule_id=created.json()["schedule"]["id"]
        schedules=self.client.get(f"/api/servers/{self.profile_id}/schedules")
        self.assertEqual(schedules.status_code,200);self.assertEqual(schedules.json()["schedules"][0]["schedule_type"],"daily")
        updated=self.client.put(f"/api/schedules/{schedule_id}",headers=headers,json={"name":"每日安全存檔","action":"command_save_all","schedule_type":"daily","run_time":"04:20","ready_timeout_seconds":0,"retry_count":0})
        self.assertEqual(updated.status_code,200,updated.text)
        run=self.client.post(f"/api/schedules/{schedule_id}/run",headers=headers)
        self.assertEqual(run.status_code,200,run.text);self.assertEqual(run.json()["result"],"queued")
        for _ in range(100):
            runs=self.client.get(f"/api/servers/{self.profile_id}/schedule-runs").json()["runs"]
            if runs:break
            time.sleep(.02)
        self.assertTrue(runs);self.assertEqual(runs[0]["result"],"ok")
        self.assertEqual(self.client.get(f"/api/servers/{self.profile_id}/players/history").status_code,200)
        self.assertIn("玩家名稱",self.client.get(f"/api/servers/{self.profile_id}/players/export.csv").text)

    def test_file_version_api_restores_saved_content(self):
        headers={"x-csrf-token":self.csrf};path="server.properties"
        edited=self.client.put(f"/api/servers/{self.profile_id}/files/text",headers=headers,json={"path":path,"content":"motd=changed\n"})
        self.assertEqual(edited.status_code,200,edited.text)
        versions=self.client.get(f"/api/servers/{self.profile_id}/files/versions",params={"path":path})
        self.assertEqual(versions.status_code,200,versions.text);self.assertEqual(len(versions.json()["versions"]),1)
        version_id=versions.json()["versions"][0]["version_id"]
        restored=self.client.post(f"/api/servers/{self.profile_id}/files/versions/restore",headers=headers,json={"path":path,"version_id":version_id})
        self.assertEqual(restored.status_code,200,restored.text)
        content=self.client.get(f"/api/servers/{self.profile_id}/files/text",params={"path":path})
        self.assertIn("motd=hello",content.json()["content"])
        self.assertTrue(any(row["action"]=="restore_file_version" for row in self.app.state.db.server_audit_logs(self.profile_id,20)))

    def test_alert_notification_webhook_is_masked_in_api(self):
        headers={"x-csrf-token":self.csrf}
        saved=self.client.put(f"/api/servers/{self.profile_id}/alert-notifications",headers=headers,json={"webhook_url":"https://discord.com/api/webhooks/123/secret","cooldown_seconds":600})
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertTrue(saved.json()["webhook_configured"])
        self.assertNotIn("webhook_url",saved.json())
        fetched=self.client.get(f"/api/servers/{self.profile_id}/alert-notifications")
        self.assertEqual(fetched.status_code,200);self.assertNotIn("secret",fetched.text)

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

    def test_role_change_and_disable_revoke_existing_sessions(self):
        user_id=self.app.state.db.create_user("revoked-user","Revoked-password-123","operator")
        session_id,_=self.app.state.db.create_session(user_id)
        self.assertIsNotNone(self.app.state.db.get_session(session_id))
        self.app.state.db.set_user_role(user_id,"viewer")
        self.assertIsNone(self.app.state.db.get_session(session_id))
        session_id,_=self.app.state.db.create_session(user_id)
        self.app.state.db.set_user_enabled(user_id,False)
        self.assertIsNone(self.app.state.db.get_session(session_id))

    def test_java_download_rejects_untrusted_redirect_host(self):
        from unittest.mock import Mock
        response=Mock()
        response.is_redirect=True
        response.headers={"location":"https://127.0.0.1/private.zip"}
        with patch("web_panel.java_runtime.requests.get",return_value=response) as get:
            with self.assertRaisesRegex(RuntimeError,"不受信任"):
                JavaRuntimeJobs._download_response("https://api.adoptium.net/v3/binary/latest/25/ga/windows/x64/jdk/hotspot/normal/eclipse")
        self.assertEqual(get.call_count,1)
        response.close.assert_called_once()

    def test_login_rejects_cross_site_origin(self):
        client=TestClient(self.app)
        try:
            response=client.post("/login",data={"username":"admin","password":"A-strong-password-123"},headers={"origin":"https://attacker.example","sec-fetch-site":"cross-site"},follow_redirects=False)
            self.assertEqual(response.status_code,403)
            self.assertNotIn("mcm_session",client.cookies)
        finally:
            client.close()

    def test_login_rejects_cross_scheme_same_host_origin_and_missing_metadata(self):
        for headers in (
            {"origin":"http://testserver","sec-fetch-site":"same-site"},
            {},
        ):
            client=TestClient(self.app,base_url="https://testserver")
            try:
                response=client.post("/login",data={"username":"admin","password":"A-strong-password-123"},headers=headers,follow_redirects=False)
                self.assertEqual(response.status_code,403,response.text)
                self.assertNotIn("mcm_session",client.cookies)
            finally:
                client.close()

    def test_logout_requires_csrf_and_then_revokes_session(self):
        denied=self.client.post("/logout",follow_redirects=False)
        self.assertEqual(denied.status_code,403)
        self.assertIsNotNone(self.app.state.db.get_session(self.session_id))
        allowed=self.client.post("/logout",data={"csrf":self.csrf},follow_redirects=False)
        self.assertEqual(allowed.status_code,303)
        self.assertIsNone(self.app.state.db.get_session(self.session_id))


if __name__ == "__main__":
    unittest.main()
