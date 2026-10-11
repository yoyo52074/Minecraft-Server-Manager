import ipaddress
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import requests


def _allowed_webhook_url(url):
    parsed = urlparse(str(url))
    try:
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    allowed_path = ((host == "discord.com" and parsed.path.startswith("/api/webhooks/"))
                    or (host == "hooks.slack.com" and parsed.path.startswith("/services/")))
    return (parsed.scheme == "https" and allowed_path and not parsed.username and not parsed.password
            and port in (None, 443) and len(str(url)) <= 500)


def _post_webhook(url, payload):
    if not _allowed_webhook_url(url):
        return False
    try:
        with requests.post(url, json=payload, headers={"User-Agent": "MinecraftServerManager/1.0"},
                           timeout=8, allow_redirects=False) as response:
            if response.is_redirect:
                return False
            response.raise_for_status()
        return True
    except (OSError, requests.RequestException, ValueError):
        return False


class PlayerManager:
    def __init__(self,multi,db=None):self.multi=multi;self.db=db;self.last_snapshot_reliable={}
    def _profile_directory(self,profile_id):
        profile=self.multi.store.get(profile_id)
        if not profile:raise FileNotFoundError('找不到伺服器')
        return Path(profile['directory'])
    @staticmethod
    def _json_file(path):
        try:
            value=json.loads(path.read_text(encoding='utf-8-sig'));return value if isinstance(value,list) else []
        except (OSError,ValueError):return []
    def inventory(self,profile_id):
        directory=self._profile_directory(profile_id);cache=self._json_file(directory/'usercache.json')
        whitelist=self._json_file(directory/'whitelist.json');ops=self._json_file(directory/'ops.json');banned=self._json_file(directory/'banned-players.json');banned_ips=self._json_file(directory/'banned-ips.json')
        online=self.list(profile_id);history=self.db.player_history(profile_id,limit=500)['players'] if self.db else [];known={}
        for item in cache:
            if isinstance(item,dict) and item.get('name'):known[str(item['name']).casefold()]={'name':item['name'],'uuid':item.get('uuid',''),'expires_on':item.get('expiresOn',''),'source':'usercache'}
        for item in history:known[item['name'].casefold()]={**known.get(item['name'].casefold(),{}),**item,'source':'history'}
        return {'online':online,'known':list(known.values()),'whitelist':whitelist,'ops':ops,'banned_players':banned,'banned_ips':banned_ips}
    def list(self,profile_id):
        logs=self.multi.logs(profile_id,500);names=[];found=False
        for line in logs:
            match=re.search(r'(?:There are\s+\d+.*?online:\s*|\d+\s+players?\s+online:\s*)(.*)$',line,re.I)
            if match:names=[name.strip() for name in match.group(1).split(',') if name.strip() and name.strip().lower() not in {'none','no one'}];found=True
        if self.db and not found:
            previous=self.db.player_history(profile_id,limit=500)['players'];online={item['name'].casefold():item['name'] for item in previous if item['is_online']};saw_event=False
            for line in logs:
                joined=re.search(r'\]:\s*([A-Za-z0-9_]{1,16}) joined the game',line,re.I);left=re.search(r'\]:\s*([A-Za-z0-9_]{1,16}) left the game',line,re.I)
                if joined:online[joined.group(1).casefold()]=joined.group(1);saw_event=True
                if left:online.pop(left.group(1).casefold(),None);saw_event=True
            if saw_event:names=list(online.values());found=True
        self.last_snapshot_reliable[profile_id]=found;directory=self._profile_directory(profile_id)
        cache={str(item.get('name','')).casefold():item.get('uuid','') for item in self._json_file(directory/'usercache.json') if isinstance(item,dict)}
        players=[{'name':name,'uuid':cache.get(name.casefold(),'')} for name in names]
        if self.db and found:self.db.record_player_snapshot(profile_id,players)
        return players
    def command(self,profile_id,action,player):
        player=str(player).strip();commands={'kick':f'kick {player}','ban':f'ban {player}','pardon':f'pardon {player}','op':f'op {player}','deop':f'deop {player}','whitelist_add':f'whitelist add {player}','whitelist_remove':f'whitelist remove {player}'}
        if action not in commands:return False,'不支援的玩家操作'
        if not re.fullmatch(r'[A-Za-z0-9_]{1,16}',player):return False,'玩家名稱格式錯誤'
        return self.multi.send_command(profile_id,commands[action])
    def ip_command(self,profile_id,action,address):
        try:address=str(ipaddress.ip_address(str(address).strip()))
        except ValueError:return False,'IP 位址格式錯誤'
        commands={'ban_ip':f'ban-ip {address}','pardon_ip':f'pardon-ip {address}'}
        if action not in commands:return False,'不支援的 IP 操作'
        return self.multi.send_command(profile_id,commands[action])


class Scheduler:
    ACTIONS={'start','stop','restart','backup','command_save_all','stop_when_empty'};TYPES={'interval','daily','weekly'}
    def __init__(self,db,multi,backup_factory,players=None,poll_seconds=5):
        self.db=db;self.multi=multi;self.backup_factory=backup_factory;self.players=players;self.stop_event=threading.Event();self.poll_seconds=max(1,int(poll_seconds));self._active_runs=set();self._run_lock=threading.Lock();self._init()
        self.thread=threading.Thread(target=self._loop,daemon=True,name='mcm-scheduler');self.thread.start()
    def stop(self):self.stop_event.set()
    def _init(self):
        with self.db._connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT,server_id INTEGER NOT NULL,name TEXT NOT NULL,action TEXT NOT NULL,interval_seconds INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,next_run REAL NOT NULL,last_result TEXT NOT NULL DEFAULT '',schedule_type TEXT NOT NULL DEFAULT 'interval',weekdays TEXT NOT NULL DEFAULT '[]',run_time TEXT NOT NULL DEFAULT '',retry_count INTEGER NOT NULL DEFAULT 0,retry_delay_seconds INTEGER NOT NULL DEFAULT 60,ready_timeout_seconds INTEGER NOT NULL DEFAULT 300,notify_webhook TEXT NOT NULL DEFAULT '');CREATE TABLE IF NOT EXISTS schedule_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,schedule_id INTEGER,server_id INTEGER,schedule_name TEXT,action TEXT,started_at REAL,result TEXT,detail TEXT,attempts INTEGER NOT NULL DEFAULT 1);''')
            columns={row[1] for row in c.execute('PRAGMA table_info(schedules)')}
            for name,kind in [('schedule_type',"TEXT NOT NULL DEFAULT 'interval'"),('weekdays',"TEXT NOT NULL DEFAULT '[]'"),('run_time',"TEXT NOT NULL DEFAULT ''"),('retry_count','INTEGER NOT NULL DEFAULT 0'),('retry_delay_seconds','INTEGER NOT NULL DEFAULT 60'),('ready_timeout_seconds','INTEGER NOT NULL DEFAULT 300'),('notify_webhook',"TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:c.execute(f'ALTER TABLE schedules ADD COLUMN {name} {kind}')
            run_columns={row[1] for row in c.execute('PRAGMA table_info(schedule_runs)')}
            for name,kind in [('server_id','INTEGER'),('schedule_name','TEXT'),('action','TEXT'),('attempts','INTEGER NOT NULL DEFAULT 1')]:
                if name not in run_columns:c.execute(f'ALTER TABLE schedule_runs ADD COLUMN {name} {kind}')
    @staticmethod
    def _validated_webhook(url):
        url=str(url or '').strip()
        if not url:return ''
        if not _allowed_webhook_url(url):raise ValueError('通知只接受 Discord 或 Slack 的 HTTPS Webhook URL（443 port、無帳密資訊、最多 500 字元）')
        return url
    @staticmethod
    def _next_run(now,kind,interval,run_time='',weekdays=None):
        if kind=='interval':return now+interval
        try:hour,minute=map(int,run_time.split(':'))
        except (ValueError,AttributeError):raise ValueError('請使用 HH:MM 格式的每日時間')
        if not(0<=hour<=23 and 0<=minute<=59):raise ValueError('每日時間無效')
        days=set(weekdays or range(7));base=datetime.fromtimestamp(now)
        for offset in range(8):
            candidate=(base+timedelta(days=offset)).replace(hour=hour,minute=minute,second=0,microsecond=0)
            if candidate.timestamp()>now and candidate.weekday() in days:return candidate.timestamp()
        raise ValueError('找不到下一次排程時間')
    def list(self,server_id=None):
        with self.db._connect() as c:
            q='SELECT * FROM schedules';args=[]
            if server_id is not None:q+=' WHERE server_id=?';args=[server_id]
            records=[dict(row) for row in c.execute(q+' ORDER BY id',args)]
        for record in records:record['notify_webhook_configured']=bool(record.get('notify_webhook'));record.pop('notify_webhook',None)
        return records
    def get(self,sid):
        with self.db._connect() as c:
            row=c.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone();return dict(row) if row else None
    def _preview(self,server_id,name,action,interval_seconds,**options):
        if action not in self.ACTIONS:raise ValueError('不支援的排程動作')
        name=str(name).strip();kind=options.get('schedule_type','interval')
        if not name or len(name)>80:raise ValueError('排程名稱必須為 1 至 80 個字元')
        if kind not in self.TYPES:raise ValueError('排程頻率只能是間隔、每日或每週')
        interval=max(60,min(int(interval_seconds),31536000));days=sorted(set(int(x) for x in options.get('weekdays',[]) if 0<=int(x)<=6))
        if kind=='weekly' and not days:raise ValueError('每週排程至少要選擇一個星期')
        run_time=str(options.get('run_time','00:00'));retries=int(options.get('retry_count',0));delay=int(options.get('retry_delay_seconds',60));ready=int(options.get('ready_timeout_seconds',300))
        if not 0<=retries<=10 or not 1<=delay<=3600:raise ValueError('重試次數須 0–10 次，延遲須 1–3600 秒')
        if not 0<=ready<=3600:raise ValueError('就緒等待時間須介於 0–3600 秒')
        webhook=self._validated_webhook(options.get('notify_webhook',''));now=time.time()
        return {'server_id':server_id,'name':name,'action':action,'interval_seconds':interval,'next_run':self._next_run(now,kind,interval,run_time,days if kind=='weekly' else None),'schedule_type':kind,'weekdays':json.dumps(days),'run_time':run_time,'retry_count':retries,'retry_delay_seconds':delay,'ready_timeout_seconds':ready,'notify_webhook':webhook}
    def create(self,server_id,name,action,interval_seconds=3600,**options):
        item=self._preview(server_id,name,action,interval_seconds,**options)
        with self.db._connect() as c:c.execute('INSERT INTO schedules(server_id,name,action,interval_seconds,next_run,schedule_type,weekdays,run_time,retry_count,retry_delay_seconds,ready_timeout_seconds,notify_webhook) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(item['server_id'],item['name'],item['action'],item['interval_seconds'],item['next_run'],item['schedule_type'],item['weekdays'],item['run_time'],item['retry_count'],item['retry_delay_seconds'],item['ready_timeout_seconds'],item['notify_webhook']))
        return self.list(server_id)[-1]
    def update(self,sid,values):
        old=self.get(sid)
        if not old:raise KeyError('找不到排程')
        merged={**old,**values}
        if values.get('clear_notify_webhook'):merged['notify_webhook']=''
        elif not values.get('notify_webhook'):merged['notify_webhook']=old.get('notify_webhook','')
        days=merged.get('weekdays',[])
        if isinstance(days,str):
            try:days=json.loads(days)
            except ValueError:days=[]
        item=self._preview(old['server_id'],merged['name'],merged['action'],merged['interval_seconds'],schedule_type=merged.get('schedule_type','interval'),weekdays=days,run_time=merged.get('run_time','00:00'),retry_count=merged.get('retry_count',0),retry_delay_seconds=merged.get('retry_delay_seconds',60),ready_timeout_seconds=merged.get('ready_timeout_seconds',300),notify_webhook=merged.get('notify_webhook',''))
        with self.db._connect() as c:c.execute('UPDATE schedules SET name=?,action=?,interval_seconds=?,next_run=?,schedule_type=?,weekdays=?,run_time=?,retry_count=?,retry_delay_seconds=?,ready_timeout_seconds=?,notify_webhook=? WHERE id=?',(item['name'],item['action'],item['interval_seconds'],item['next_run'],item['schedule_type'],item['weekdays'],item['run_time'],item['retry_count'],item['retry_delay_seconds'],item['ready_timeout_seconds'],item['notify_webhook'],sid))
        return self.get(sid)
    def delete(self,sid):
        with self.db._connect() as c:c.execute('UPDATE schedule_runs SET schedule_id=NULL WHERE schedule_id=?',(sid,));c.execute('DELETE FROM schedules WHERE id=?',(sid,))
    def toggle(self,sid):
        with self.db._connect() as c:c.execute('UPDATE schedules SET enabled=1-enabled WHERE id=?',(sid,))
    def run_now(self,sid):
        item=self.get(sid)
        if not item:raise KeyError('找不到排程')
        if not self._launch(item):raise RuntimeError('此排程目前正在執行')
        return {'result':'queued','detail':'已加入背景執行佇列','attempts':0}
    def _launch(self,item):
        sid=item['id']
        with self._run_lock:
            if sid in self._active_runs:return False
            self._active_runs.add(sid)
        def worker():
            try:self._run_item(item)
            finally:
                with self._run_lock:self._active_runs.discard(sid)
        threading.Thread(target=worker,daemon=True,name=f'mcm-schedule-{sid}').start()
        return True
    def _wait_ready(self,sid,timeout):
        if timeout<=0:return
        deadline=time.time()+timeout
        while time.time()<deadline and not self.stop_event.is_set():
            status=self.multi.status(sid)
            if not status or status.get('phase') in {'error','stopped'}:raise RuntimeError('伺服器啟動失敗或程序已停止')
            logs=status.get('logs') or self.multi.logs(sid,100)
            if any(re.search(r'\bDone \(',line) or 'For help, type' in line for line in logs):return
            self.stop_event.wait(min(2,max(0,deadline-time.time())))
        raise TimeoutError(f'等待伺服器完成啟動逾時（{timeout} 秒）')
    def _execute(self,item):
        sid=item['server_id'];action=item['action']
        if action=='backup':
            manager=self.backup_factory(sid);path=manager.create_backup('scheduled');settings=self.db.backup_retention(sid)
            manager.cleanup_retention(settings['retain_count'],settings['max_age_days'] or None);manager.cleanup_settings_retention(settings['settings_retain_count'],settings['settings_max_age_days']);return f'備份完成：{Path(path).name}'
        if action=='command_save_all':
            ok,message=self.multi.send_command(sid,'save-all')
            if not ok:raise RuntimeError(message)
            return message
        if action=='stop_when_empty':
            if not self.players:raise RuntimeError('玩家監控服務尚未啟用')
            online=self.players.list(sid)
            if not self.players.last_snapshot_reliable.get(sid):raise RuntimeError('無法確認玩家清單；已略過自動停止以避免誤停')
            if online:raise RuntimeError(f'仍有 {len(online)} 位玩家在線；略過停止')
            ok,message=self.multi.stop(sid)
            if not ok:raise RuntimeError(message)
            return '玩家全數離線，已送出安全停止'
        if action=='start':
            ok,message=self.multi.start(sid)
            if not ok:raise RuntimeError(message)
            self._wait_ready(sid,int(item.get('ready_timeout_seconds',300)));return '伺服器已啟動並就緒'
        if action=='stop':
            ok,message=self.multi.stop(sid)
            if not ok:raise RuntimeError(message)
            return message
        if action=='restart':
            status=self.multi.status(sid)
            if status and status.get('running'):
                ok,message=self.multi.stop(sid)
                if not ok:raise RuntimeError(message)
                deadline=time.time()+30
                while time.time()<deadline and self.multi.status(sid).get('running'):self.stop_event.wait(1)
                if self.multi.status(sid).get('running'):raise TimeoutError('停止伺服器逾時')
            ok,message=self.multi.start(sid)
            if not ok:raise RuntimeError(message)
            self._wait_ready(sid,int(item.get('ready_timeout_seconds',300)));return '伺服器已重啟並就緒'
        raise ValueError('不支援的排程動作')
    def _notify(self,url,text):
        if not url:return
        payload={'content':text} if urlparse(url).hostname=='discord.com' else {'text':text}
        _post_webhook(url,payload)
    def _run_item(self,item):
        started=time.time();attempts=0;result='error';detail='';maximum=int(item.get('retry_count',0))+1
        for attempt in range(maximum):
            attempts=attempt+1
            try:detail=self._execute(item);result='ok';break
            except Exception as exc:
                detail=str(exc)
                if attempt+1<maximum and self.stop_event.wait(int(item.get('retry_delay_seconds',60))):break
        now=time.time();kind=item.get('schedule_type','interval')
        try:days=json.loads(item.get('weekdays','[]'))
        except (TypeError,ValueError):days=[]
        next_run=now+int(item['interval_seconds']) if kind=='interval' else self._next_run(now,kind,int(item['interval_seconds']),item.get('run_time','00:00'),days if kind=='weekly' else None)
        with self.db._connect() as c:
            c.execute('UPDATE schedules SET next_run=?,last_result=? WHERE id=?',(next_run,result,item['id']))
            c.execute('INSERT INTO schedule_runs(schedule_id,server_id,schedule_name,action,started_at,result,detail,attempts) VALUES(?,?,?,?,?,?,?,?)',(item['id'],item['server_id'],item['name'],item['action'],started,result,detail,attempts))
        self._notify(item.get('notify_webhook',''),f"[{result.upper()}] {item['name']} ({item['action']}) — {detail}；嘗試 {attempts} 次")
        return {'result':result,'detail':detail,'attempts':attempts}
    def _loop(self):
        while not self.stop_event.wait(self.poll_seconds):
            now=time.time()
            for item in self.list():
                if self.stop_event.is_set():break
                if not item['enabled'] or item['next_run']>now:continue
                with self._run_lock:
                    if item['id'] in self._active_runs:continue
                kind=item.get('schedule_type','interval')
                try:days=json.loads(item.get('weekdays','[]'))
                except (TypeError,ValueError):days=[]
                next_run=now+int(item['interval_seconds']) if kind=='interval' else self._next_run(now,kind,int(item['interval_seconds']),item.get('run_time','00:00'),days if kind=='weekly' else None)
                with self.db._connect() as c:
                    changed=c.execute('UPDATE schedules SET next_run=? WHERE id=? AND enabled=1 AND next_run<=?',(next_run,item['id'],now)).rowcount
                if changed:self._launch(item)


class MonitorService:
    """Collect advanced server metrics and evaluate alert transitions independently of UI polling."""
    def __init__(self,profiles,multi,db,interval_seconds=5,players=None):
        self.profiles=profiles;self.multi=multi;self.db=db;self.players=players;self.interval_seconds=max(1,int(interval_seconds));self.stop_event=threading.Event()
        self._last_query={};self._last_jvm={};self._jvm_cache={};self.thread=threading.Thread(target=self._loop,daemon=True,name='mcm-monitor');self.thread.start()
    def stop(self):self.stop_event.set()
    @staticmethod
    def _parse_logs(logs):
        tps=mspt=0.0
        for line in reversed(logs):
            if not tps:
                m=re.search(r'TPS(?:\s+from\s+last\s+[^:]+)?:\s*([0-9.]+)',line,re.I)
                if m:tps=float(m.group(1))
            if not mspt:
                m=re.search(r'(?:MSPT(?:\s+from\s+last\s+[^:]+)?|Mean tick time):\s*([0-9.]+)',line,re.I)
                if m:mspt=float(m.group(1))
            if tps and mspt:break
        return tps,mspt
    def _query_console(self,profile,status,now):
        sid=profile['id']
        if not status.get('running') or now-self._last_query.get(sid,0)<60:return
        self._last_query[sid]=now
        if self.players:self.multi.send_command(sid,'list')
        core=str(profile.get('core_type','')).casefold()
        if any(name in core for name in ('paper','purpur','folia')):
            self.multi.send_command(sid,'tps');self.multi.send_command(sid,'mspt')
    def _jvm_metrics(self,profile):
        sid=profile['id'];now=time.time()
        cached=self._jvm_cache.get(sid,{})
        if now-self._last_jvm.get(sid,0)<60:return cached
        self._last_jvm[sid]=now
        proc=getattr(self.multi,'_processes',{}).get(sid)
        if proc is None:
            bridge=getattr(self.multi,'legacy_bridge',None);controller=getattr(bridge,'controller',None);proc=getattr(controller,'server_process',None)
        if not proc or proc.poll() is not None:
            self._jvm_cache.pop(sid,None)
            return {'heap_used_mb':0,'heap_max_mb':0,'heap_percent':0,'gc_count':0,'gc_time_ms':0}
        java=str(profile.get('java_path') or 'java');base=Path(java)
        candidates=[]
        if base.is_file():candidates.append(base.with_name('jcmd.exe' if os.name=='nt' else 'jcmd'))
        which=shutil.which('jcmd')
        if which:candidates.append(Path(which))
        jcmd=next((str(path) for path in candidates if path.is_file()),None)
        result=dict(cached)
        if jcmd:
            try:
                output=subprocess.run([jcmd,str(proc.pid),'GC.heap_info'],capture_output=True,text=True,timeout=4,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).stdout
                match=re.search(r'(?:total\s+([0-9]+)K,\s*used\s+([0-9]+)K|used\s+([0-9]+)K.*?committed\s+([0-9]+)K)',output,re.I|re.S)
                if match:
                    if match.group(1):total,used=float(match.group(1)),float(match.group(2))
                    else:used,total=float(match.group(3)),float(match.group(4))
                    result.update(heap_used_mb=round(used/1024,1),heap_max_mb=round(total/1024,1),heap_percent=round(used/total*100,1) if total else 0)
            except (OSError,subprocess.SubprocessError):pass
        java_path=Path(java);jstat_candidates=[java_path.with_name('jstat.exe' if os.name=='nt' else 'jstat')] if java_path.is_file() else []
        jstat_path=shutil.which('jstat')
        if jstat_path:jstat_candidates.append(Path(jstat_path))
        jstat=next((str(path) for path in jstat_candidates if path.is_file()),None)
        if jstat:
            try:
                output=subprocess.run([jstat,'-gc',str(proc.pid)],capture_output=True,text=True,timeout=4,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).stdout.splitlines()
                if len(output)>1:
                    headers=output[0].split();values=output[-1].split();data=dict(zip(headers,values))
                    gc_count=sum(float(data.get(key,0)) for key in ('YGC','FGC','CGC'));gc_time=sum(float(data.get(key,0)) for key in ('YGCT','FGCT','CGCT'))*1000
                    result.update(gc_count=int(gc_count),gc_time_ms=round(gc_time,1))
            except (OSError,ValueError,subprocess.SubprocessError):pass
        if result:self._jvm_cache[sid]=result
        return result
    def _max_players(self,profile):
        try:
            for line in (Path(profile['directory'])/'server.properties').read_text(encoding='utf-8',errors='replace').splitlines():
                if line.strip().startswith('max-players='):return max(0,int(line.split('=',1)[1].strip()))
        except (OSError,ValueError,KeyError):pass
        return 0
    def _notify_new_alerts(self,server_id):
        events=[event for event in self.db.alert_events(server_id,100) if event['active']]
        if not events:return
        latest=max(events,key=lambda event:event['id']);url=self.db.claim_alert_notification(server_id,latest['id'])
        if not url:return
        messages='\n'.join('• '+event['message'] for event in events)
        payload={'content':f'Minecraft Server Manager 告警\n{messages}'} if urlparse(url).hostname=='discord.com' else {'text':f'Minecraft Server Manager 告警\n{messages}'}
        _post_webhook(url,payload)
    def _loop(self):
        while not self.stop_event.is_set():
            for profile in self.profiles.list():
                if self.stop_event.is_set():break
                try:
                    sid=profile['id'];status=self.multi.status(sid)
                    if not status:continue
                    metrics=dict(status.get('metrics',{}));now=time.time();self._query_console(profile,status,now)
                    logs=self.multi.logs(sid,500) if hasattr(self.multi,'logs') else [];tps,mspt=self._parse_logs(logs);metrics.update(tps=tps,mspt=mspt,max_players=self._max_players(profile))
                    if self.players:
                        online=self.players.list(sid)
                        metrics['online_players']=len(online) if self.players.last_snapshot_reliable.get(sid) else -1
                    metrics.update(self._jvm_metrics(profile))
                    self.db.record_metrics(sid,metrics);self.db.evaluate_metric_alerts(sid,metrics)
                    self.db.evaluate_server_phase(sid,status.get('phase'),status.get('phase_timeout'));self._notify_new_alerts(sid)
                except Exception:continue
            self.stop_event.wait(self.interval_seconds)
