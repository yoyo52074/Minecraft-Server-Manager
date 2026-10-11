import os
import tempfile
import time
import unittest
import zipfile

from backup_manager import BackupCancelledError, BackupManager
from web_panel.database import WebDatabase
from web_panel.operations import MonitorService


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


if __name__ == "__main__":
    unittest.main()
