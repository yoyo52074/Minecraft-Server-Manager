import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import zipfile

from backup_manager import BackupCancelledError, BackupManager
from web_panel.database import WebDatabase
from web_panel.operations import MonitorService, Scheduler, _post_webhook
from web_panel.multi_server import MultiServerManager


class BackupMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        os.makedirs(os.path.join(self.root, "world"))
        with open(os.path.join(self.root, "world", "level.dat"), "wb") as f:
            f.write(b"world-state")
        with open(os.path.join(self.root, "server.properties"), "w", encoding="utf-8") as f:
            f.write("motd=before\n")
        self.manager = BackupManager(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_restore_creates_pre_restore_safety_backup(self):
        archive = self.manager.create_backup("known-good")
        with open(os.path.join(self.root, "server.properties"), "w", encoding="utf-8") as f:
            f.write("motd=changed\n")
        self.manager.restore_backup(archive)
        self.assertTrue(self.manager.last_pre_restore_backup)
        self.assertTrue(os.path.isfile(self.manager.last_pre_restore_backup))
        self.assertIn("pre-restore", os.path.basename(self.manager.last_pre_restore_backup))
        with open(os.path.join(self.root, "server.properties"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "motd=before\n")

    def test_backup_crc_verification_and_retention(self):
        paths = [self.manager.create_backup(f"b{i}") for i in range(3)]
        self.assertTrue(self.manager.verify_backup(paths[-1]))
        removed = self.manager.cleanup_retention(retain=2)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(self.manager.list_backups()), 2)

    def test_cancelled_backup_leaves_no_partial_archive(self):
        import threading
        event = threading.Event()
        event.set()
        with self.assertRaises(BackupCancelledError):
            self.manager.create_backup("cancel", cancel_event=event)
        self.assertFalse(any(name.endswith(".part") for name in os.listdir(self.manager.backup_directory)))

    def test_windows_style_path_traversal_is_rejected(self):
        bad = os.path.join(self.manager.backup_directory, "bad.zip")
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("..\\..\\outside.txt", "bad")
        with self.assertRaises(ValueError):
            self.manager.restore_backup(bad)

    def test_backup_skips_top_level_symlink_to_external_data(self):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        with open(os.path.join(external.name, "secret.txt"), "w", encoding="utf-8") as file:
            file.write("outside server root")
        link = os.path.join(self.root, "plugins")
        try:
            os.symlink(external.name, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("目前平台不允許建立符號連結")
        archive = self.manager.create_backup("symlink-check")
        with zipfile.ZipFile(archive) as backup:
            self.assertNotIn("plugins/secret.txt", backup.namelist())

    def test_restore_rejects_existing_symlink_parent(self):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        outside_file = os.path.join(external.name, "owned.txt")
        with open(outside_file, "w", encoding="utf-8") as file:
            file.write("safe")
        link = os.path.join(self.root, "world")
        import shutil
        shutil.rmtree(link)
        try:
            os.symlink(external.name, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("目前平台不允許建立符號連結")
        archive = os.path.join(self.manager.backup_directory, "symlink-restore.zip")
        with zipfile.ZipFile(archive, "w") as backup:
            backup.writestr("world/owned.txt", "overwritten")
        with self.assertRaisesRegex(ValueError, "不安全的路徑|符號連結"):
            self.manager.restore_backup(archive)
        with open(outside_file, encoding="utf-8") as file:
            self.assertEqual(file.read(), "safe")

    def test_restore_rejects_windows_casefold_duplicate_members(self):
        archive=os.path.join(self.manager.backup_directory,"case-duplicate.zip")
        with zipfile.ZipFile(archive,"w") as backup:
            backup.writestr("world/case.txt","first")
            backup.writestr("world/CASE.TXT","second")
        with self.assertRaisesRegex(ValueError,"大小寫|正規化後重複"):
            self.manager.restore_backup(archive,create_safety_backup=False)
        with open(os.path.join(self.root,"world","level.dat"),"rb") as file:
            self.assertEqual(file.read(),b"world-state")

    def test_restore_commit_failure_rolls_back_replaced_files(self):
        world=os.path.join(self.root,"world")
        first=os.path.join(world,"a.txt")
        second=os.path.join(world,"b.txt")
        with open(first,"w",encoding="utf-8") as file:file.write("old-a")
        with open(second,"w",encoding="utf-8") as file:file.write("old-b")
        archive=os.path.join(self.manager.backup_directory,"rollback.zip")
        with zipfile.ZipFile(archive,"w") as backup:
            backup.writestr("world/a.txt","new-a")
            backup.writestr("world/b.txt","new-b")
        original_replace=os.replace
        def fail_second_staged_replace(source,destination):
            if str(source).replace("\\","/").endswith("/staging/world/b.txt"):
                raise OSError("simulated disk failure")
            return original_replace(source,destination)
        with patch("backup_manager.os.replace",side_effect=fail_second_staged_replace):
            with self.assertRaisesRegex(OSError,"simulated disk failure"):
                self.manager.restore_backup(archive,create_safety_backup=False)
        with open(first,encoding="utf-8") as file:self.assertEqual(file.read(),"old-a")
        with open(second,encoding="utf-8") as file:self.assertEqual(file.read(),"old-b")

    def test_settings_backup_rejects_symlinked_properties_and_settings_directory(self):
        external=tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        outside_properties=os.path.join(external.name,"outside.properties")
        with open(outside_properties,"w",encoding="utf-8") as file:file.write("motd=private\n")
        properties=os.path.join(self.root,"server.properties")
        os.unlink(properties)
        try:
            os.symlink(outside_properties,properties)
        except (OSError,NotImplementedError):
            self.skipTest("目前平台不允許建立符號連結")
        with self.assertRaisesRegex(ValueError,"符號連結"):
            self.manager.create_settings_backup({"name":"test"})
        os.unlink(properties)
        settings=os.path.join(self.manager.backup_directory,"settings")
        if os.path.isdir(settings) and not os.listdir(settings):
            os.rmdir(settings)
        outside_zip=os.path.join(external.name,"settings-outside.zip")
        with open(outside_zip,"wb") as file:file.write(b"keep")
        try:
            os.symlink(external.name,settings,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest("目前平台不允許建立符號連結")
        with self.assertRaisesRegex(ValueError,"符號連結"):
            self.manager.create_settings_backup({"name":"test"})
        with self.assertRaisesRegex(ValueError,"符號連結"):
            self.manager.cleanup_settings_retention(retain=1)
        with open(outside_zip,"rb") as file:
            self.assertEqual(file.read(),b"keep")


class HistoryAndAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = WebDatabase(os.path.join(self.temp.name, "panel.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_audit_history_scoped_to_server(self):
        user_id = self.db.create_user("history-user", "A-strong-password-123", "admin")
        self.db.audit(user_id, "player_kick", "Steve", server_id=7)
        self.db.audit(user_id, "player_ban", "Alex", server_id=8)
        rows = self.db.server_audit_logs(7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "player_kick")

    def test_metric_alert_deduplicates_and_resolves(self):
        high = {"cpu_percent": 96, "memory_percent": 30, "disk_used_percent": 50}
        low = {"cpu_percent": 20, "memory_percent": 30, "disk_used_percent": 50}
        self.db.evaluate_metric_alerts(3, high)
        self.db.evaluate_metric_alerts(3, high)
        self.assertEqual(len(self.db.alert_events(3)), 1)
        self.db.evaluate_metric_alerts(3, low)
        events = self.db.alert_events(3)
        self.assertEqual(events[0]["active"], 0)
        self.db.evaluate_metric_alerts(3, high)
        self.assertEqual(len(self.db.alert_events(3)), 2)

    def test_alert_threshold_validation_and_phase_alert(self):
        with self.assertRaises(ValueError):
            self.db.set_alert_thresholds(1, {"cpu_percent": 101})
        self.db.evaluate_server_phase(1, "error")
        self.db.evaluate_server_phase(1, "error")
        self.assertEqual(len(self.db.alert_events(1)), 1)
        self.db.evaluate_server_phase(1, "running")
        self.assertEqual(self.db.alert_events(1)[0]["active"], 0)

    def test_start_timeout_is_recorded_and_resolved(self):
        self.db.evaluate_server_phase(1, "error", "starting")
        events = self.db.alert_events(1)
        self.assertEqual(len(events), 2)
        self.assertTrue(any("啟動" in event["message"] and "逾時" in event["message"] for event in events))
        self.db.evaluate_server_phase(1, "stopped")
        self.assertFalse(any(event["active"] for event in self.db.alert_events(1)))

    def test_monitor_service_collects_without_websocket(self):
        class Profiles:
            def list(self): return [{"id": 42}]
        class Multi:
            def status(self, profile_id):
                return {"phase": "running", "metrics": {"cpu_percent": 95, "memory_percent": 20, "disk_used_percent": 40}}
        monitor = MonitorService(Profiles(), Multi(), self.db, interval_seconds=1)
        try:
            deadline = time.time() + 3
            while time.time() < deadline and not self.db.recent_metrics(42):
                time.sleep(0.02)
            self.assertTrue(self.db.recent_metrics(42))
            self.assertTrue(any(event["alert_key"] == "cpu" for event in self.db.alert_events(42)))
        finally:
            monitor.stop()

    def test_metric_percentages_have_explicit_denominators(self):
        process = SimpleNamespace(pid=123, poll=lambda: None)
        proc = SimpleNamespace(cpu_percent=lambda interval=None: 200.0,
                               memory_info=lambda: SimpleNamespace(rss=1024 * 1024 * 1024),
                               memory_percent=lambda: 4.0)
        with patch("web_panel.multi_server.psutil") as fake_psutil:
            fake_psutil.cpu_count.return_value = 8
            fake_psutil.Process.return_value = proc
            fake_psutil.disk_usage.return_value = SimpleNamespace(percent=65.0, free=350 * 1024**3,
                                                                   used=650 * 1024**3, total=1000 * 1024**3)
            fake_psutil.net_io_counters.return_value = SimpleNamespace(bytes_sent=0, bytes_recv=0)
            metrics = MultiServerManager._metrics(process, "/server", memory_limit_mb=2048)
        self.assertEqual(metrics["cpu_percent"], 25.0)
        self.assertEqual(metrics["memory_mb"], 1024.0)
        self.assertEqual(metrics["memory_percent"], 4.0)
        self.assertEqual(metrics["memory_limit_percent"], 50.0)
        self.assertEqual(metrics["disk_used_percent"], 65.0)
        self.assertEqual(metrics["disk_used_gb"], 650.0)
        self.assertEqual(metrics["disk_total_gb"], 1000.0)

    def test_cpu_sampler_reuses_process_and_caches_subsecond_queries(self):
        manager = MultiServerManager(SimpleNamespace(db=None))
        process = SimpleNamespace(pid=123, poll=lambda: None)
        samples = iter((0.0, 64.0))
        sampler = SimpleNamespace(cpu_percent=lambda interval=None: next(samples))
        with patch("web_panel.multi_server.psutil") as fake_psutil, \
             patch("web_panel.multi_server.time.monotonic", side_effect=(10.0, 10.5, 11.1)):
            fake_psutil.Process.return_value = sampler
            self.assertEqual(manager._sample_cpu_percent(7, process), 0.0)
            self.assertEqual(manager._sample_cpu_percent(7, process), 0.0)
            self.assertEqual(manager._sample_cpu_percent(7, process), 64.0)
        fake_psutil.Process.assert_called_once_with(123)

    def test_memory_alert_uses_server_allocation_ratio(self):
        self.db.evaluate_metric_alerts(55, {"cpu_percent": 1, "memory_percent": 7.2,
                                             "memory_limit_percent": 95, "disk_used_percent": 10})
        events = self.db.alert_events(55)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["alert_key"], "memory")
        self.assertIn("配置上限", events[0]["message"])

    def test_webhook_sender_rejects_redirects_and_nonstandard_ports(self):
        response=MagicMock()
        response.is_redirect=True
        response.__enter__.return_value=response
        response.__exit__.return_value=False
        with patch("web_panel.operations.requests.post",return_value=response) as post:
            self.assertFalse(_post_webhook("https://discord.com/api/webhooks/123/token",{"content":"test"}))
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            self.assertFalse(_post_webhook("https://discord.com:8443/api/webhooks/123/token",{"content":"test"}))
            self.assertEqual(post.call_count,1)

    def test_schedule_webhook_validation_matches_sender_allowlist(self):
        self.assertEqual(Scheduler._validated_webhook("https://discord.com/api/webhooks/123/token"),"https://discord.com/api/webhooks/123/token")
        for url in ("https://user:pass@discord.com/api/webhooks/123/token",
                    "https://discord.com:8443/api/webhooks/123/token",
                    "https://discord.com.evil.test/api/webhooks/123/token",
                    "https://hooks.slack.com:bad/services/token"):
            with self.assertRaisesRegex(ValueError,"Discord 或 Slack"):
                Scheduler._validated_webhook(url)


if __name__ == "__main__":
    unittest.main()
