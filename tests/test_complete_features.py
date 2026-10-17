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

    def test_player_list_replays_join_leave_after_latest_snapshot(self):
        multi=Mock();multi.store.get.return_value={'directory':self.root}
        multi.logs.return_value=[
            '[00:00:00] [Server thread/INFO]: There are 0/20 players online:',
            '[00:00:10] [Server thread/INFO]: Steve joined the game',
            '[00:00:11] [Server thread/INFO]: Alex joined the game',
        ]
        manager=PlayerManager(multi,self.db)
        self.assertCountEqual([item['name'] for item in manager.list(4)],['Steve','Alex'])
        self.assertTrue(manager.last_snapshot_reliable[4])
        self.assertEqual(sum(bool(item['is_online']) for item in self.db.player_history(4)['players']),2)
        multi.logs.return_value=[
            '[00:00:20] [Server thread/INFO]: There are 2/20 players online: Steve, Alex',
            '[00:00:21] [Server thread/INFO]: Steve left the game',
        ]
        self.assertEqual([item['name'] for item in manager.list(4)],['Alex'])

    def test_player_inventory_queries_fresh_server_list(self):
        class LiveMulti:
            def __init__(inner_self):
                inner_self.store=Mock();inner_self.store.get.return_value={'directory':self.root}
                inner_self.lines=[];inner_self.cursor=0;inner_self.commands=[]
            def log_cursor(inner_self,profile_id):return inner_self.cursor
            def logs_since(inner_self,profile_id,cursor):return inner_self.lines[cursor:]
            def send_command(inner_self,profile_id,command):
                inner_self.commands.append(command)
                inner_self.lines.append('[00:00:30] [Server thread/INFO] [minecraft/DedicatedServer]: There are 2/20 players online: Steve, Alex')
                inner_self.cursor+=1
                return True,'指令已送出'

        multi=LiveMulti();manager=PlayerManager(multi,self.db)
        inventory=manager.inventory(4,refresh=True)
        self.assertEqual(multi.commands,['list'])
        self.assertEqual(inventory['online_count'],2)
        self.assertTrue(inventory['online_reliable'])
        self.assertTrue(inventory['online_names_reliable'])
        self.assertCountEqual([player['name'] for player in inventory['online']],['Steve','Alex'])
        self.assertEqual(sum(bool(player['is_online']) for player in self.db.player_history(4)['players']),2)

    def test_missing_player_log_evidence_does_not_mark_all_players_offline(self):
        self.db.record_player_snapshot(4,[{'name':'Steve','uuid':'s'},{'name':'Alex','uuid':'a'}],now=100)
        multi=Mock();multi.store.get.return_value={'directory':self.root};multi.logs.return_value=[]
        manager=PlayerManager(multi,self.db)
        self.assertCountEqual([player['name'] for player in manager.list(4)],['Steve','Alex'])
        self.assertFalse(manager.last_snapshot_reliable[4])
        self.assertIsNone(manager.online_counts[4])
        self.assertEqual(sum(bool(player['is_online']) for player in self.db.player_history(4)['players']),2)

    def test_live_query_does_not_replay_old_zero_snapshot_as_current(self):
        class SilentMulti:
            def __init__(inner_self):
                inner_self.store=Mock();inner_self.store.get.return_value={'directory':self.root}
                inner_self.lines=['[00:00:01] [Server thread/INFO]: There are 0/20 players online:']
                inner_self.cursor=1;inner_self.commands=[]
            def log_cursor(inner_self,profile_id):return inner_self.cursor
            def logs_since(inner_self,profile_id,cursor):return inner_self.lines[cursor:]
            def send_command(inner_self,profile_id,command):
                inner_self.commands.append(command)
                return True,'指令已送出'

        multi=SilentMulti();manager=PlayerManager(multi,self.db);manager.live_query_timeout_seconds=0
        inventory=manager.inventory(4,refresh=True)
        self.assertEqual(multi.commands,['list'])
        self.assertFalse(inventory['online_reliable'])
        self.assertIsNone(inventory['online_count'])
        self.assertEqual(inventory['online'],[])

    def test_player_reliability_persists_between_incremental_log_batches(self):
        class EventMulti:
            def __init__(inner_self):
                inner_self.store=Mock();inner_self.store.get.return_value={'directory':self.root}
                inner_self.lines=['[00:00:10] [Server thread/INFO]: Steve joined the game'];inner_self.cursor=1
            def log_cursor(inner_self,profile_id):return inner_self.cursor
            def logs_since(inner_self,profile_id,cursor):return inner_self.lines[cursor:]

        manager=PlayerManager(EventMulti(),self.db)
        self.assertEqual([player['name'] for player in manager.list(4)],['Steve'])
        self.assertTrue(manager.last_snapshot_reliable[4])
        self.assertEqual([player['name'] for player in manager.list(4)],['Steve'])
        self.assertTrue(manager.last_snapshot_reliable[4])
        self.assertEqual(manager.online_counts[4],1)

    def test_online_count_survives_incomplete_server_list_names(self):
        self.db.record_player_snapshot(4,[{'name':'Steve','uuid':'s'},{'name':'Alex','uuid':'a'}],now=100)

        class LiveMulti:
            def __init__(inner_self):
                inner_self.store=Mock();inner_self.store.get.return_value={'directory':self.root}
                inner_self.lines=[];inner_self.cursor=0
            def log_cursor(inner_self,profile_id):return inner_self.cursor
            def logs_since(inner_self,profile_id,cursor):return inner_self.lines[cursor:]
            def send_command(inner_self,profile_id,command):
                inner_self.lines.append('[00:00:31] [Server thread/INFO]: There are 2/20 players online: Steve')
                inner_self.cursor+=1
                return True,'指令已送出'

        inventory=PlayerManager(LiveMulti(),self.db).inventory(4,refresh=True)
        self.assertEqual(inventory['online_count'],2)
        self.assertTrue(inventory['online_reliable'])
        self.assertFalse(inventory['online_names_reliable'])
        self.assertEqual(inventory['online'],[])
        self.assertEqual(sum(bool(player['is_online']) for player in self.db.player_history(4)['players']),2)

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
