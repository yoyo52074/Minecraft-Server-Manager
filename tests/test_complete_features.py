import os
import tempfile
import time
import unittest
import zipfile
from unittest.mock import Mock

from backup_manager import BackupJobManager, BackupManager
from web_panel.database import WebDatabase
from web_panel.operations import MonitorService, PlayerManager, Scheduler


class CompleteFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.db = WebDatabase(os.path.join(self.root, 'panel.sqlite'))

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_scope_and_settings_snapshot_retention(self):
        root = os.path.join(self.root, 'server')
        os.makedirs(os.path.join(root, 'world')); os.makedirs(os.path.join(root, 'plugins')); os.makedirs(os.path.join(root, 'config'))
        for name in ['world/level.dat','plugins/a.jar','config/server.toml','server.properties']:
            path=os.path.join(root,name);os.makedirs(os.path.dirname(path),exist_ok=True)
            with open(path,'w') as stream:stream.write(name)
        manager=BackupManager(root)
        archive=manager.create_backup('world-only',scope='worlds')
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.namelist(),['world/level.dat'])
        settings=[manager.create_settings_backup({'name':'server'}) for _ in range(3)]
        removed=manager.cleanup_settings_retention(2,0)
        self.assertEqual(len(removed),1)
        self.assertEqual(len([x for x in os.listdir(os.path.join(manager.backup_directory,'settings')) if x.endswith('.zip')]),2)
        self.assertTrue(os.path.isfile(settings[-1]))

    def test_backup_job_persists_completed_state(self):
        root=os.path.join(self.root,'persist-server');os.makedirs(root)
        with open(os.path.join(root,'server.properties'),'w') as f:f.write('motd=test')
        manager=BackupJobManager(self.db);job_id=manager.start(9,root,scope='config')
        deadline=time.time()+4
        while time.time()<deadline:
            job=manager.get(job_id,9)
            if job and job['status'] in {'completed','failed'}:break
            time.sleep(.02)
        self.assertEqual(job['status'],'completed')
        restarted=BackupJobManager(self.db)
        self.assertEqual(restarted.get(job_id,9)['status'],'completed')
        self.assertEqual(self.db.backup_jobs(9)[0]['scope'],'config')

    def test_player_join_leave_history_and_session_duration(self):
        self.db.record_player_snapshot(4,[{'name':'Steve','uuid':'abc'}],now=100)
        self.db.record_player_snapshot(4,[{'name':'Steve','uuid':'abc'}],now=120)
        self.db.record_player_snapshot(4,[],now=160)
        history=self.db.player_history(4,'ste',10,0)
        self.assertEqual(history['total'],1)
        record=history['players'][0]
        self.assertFalse(record['is_online'])
        self.assertEqual(record['total_online_seconds'],60)
        self.assertEqual([e['action'] for e in self.db.player_events(4)],['leave','join'])
        self.assertEqual(self.db.player_sessions(4)[0]['left_at'],160)

    def test_schedule_frequency_validation_and_webhook_masking(self):
        multi=Mock()
        multi.status.return_value={'running':False,'phase':'stopped','logs':[]}
        scheduler=Scheduler(self.db,multi,lambda _:BackupManager(self.root),poll_seconds=60)
        try:
            weekly=scheduler.create(7,'週末備份','backup',3600,schedule_type='weekly',weekdays=[5,6],run_time='04:30',retry_count=2)
            self.assertEqual(weekly['schedule_type'],'weekly')
            self.assertNotIn('notify_webhook',weekly)
            self.assertEqual(weekly['retry_count'],2)
            self.assertGreater(weekly['next_run'],time.time())
            with self.assertRaises(ValueError):scheduler.create(7,'錯誤','backup',60,schedule_type='weekly',weekdays=[])
            with self.assertRaises(ValueError):scheduler.create(7,'錯誤','backup',60,notify_webhook='http://127.0.0.1/hook')
            updated=scheduler.update(weekly['id'],{'name':'更新後','retry_count':1,'notify_webhook':'','clear_notify_webhook':False})
            self.assertEqual(updated['name'],'更新後')
        finally:scheduler.stop()

    def test_monitor_log_parser_and_advanced_alert_thresholds(self):
        tps,mspt=MonitorService._parse_logs(['[INFO]: TPS from last 1m, 5m, 15m: 12.5, 18.0, 19.0','[INFO]: MSPT from last 1m, 5m, 15m: 52.0, 35.0, 20.0'])
        self.assertEqual(tps,12.5);self.assertEqual(mspt,52.0)
        self.db.set_alert_thresholds(8,{'tps_min':18,'mspt_max':50,'heap_percent':90})
        self.db.evaluate_metric_alerts(8,{'tps':12,'mspt':52,'heap_percent':30})
        keys={event['alert_key'] for event in self.db.alert_events(8)}
        self.assertEqual(keys,{'tps_low','mspt_high'})

    def test_notification_settings_are_masked_and_quiet_hours_validate(self):
        saved=self.db.set_alert_notification_settings(11,'https://discord.com/api/webhooks/123/secret',900,'22:00','07:00')
        self.assertTrue(saved['webhook_configured'])
        self.assertNotIn('webhook_url',saved)
        raw=self.db.alert_notification_settings(11,reveal=True)
        self.assertIn('secret',raw['webhook_url'])
        with self.assertRaises(ValueError):self.db.set_alert_notification_settings(11,'http://example.com/hook')
        with self.assertRaises(ValueError):self.db.set_alert_notification_settings(11,None,900,'25:00','07:00')


if __name__ == '__main__':
    unittest.main()
