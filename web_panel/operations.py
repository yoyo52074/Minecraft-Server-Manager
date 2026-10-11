import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

class JavaManager:
    def scan(self):
        candidates=[]
        seen=set()
        for raw in [os.environ.get('JAVA_HOME','')]+os.environ.get('PATH','').split(os.pathsep):
            p=Path(raw)
            if p.name.lower()=='bin': p=p.parent
            exe=p/'bin'/'java.exe' if os.name=='nt' else p/'bin'/'java'
            if exe.exists(): candidates.append(exe)
        for base in [Path('C:/Program Files/Java'),Path('C:/Program Files/Eclipse Adoptium'),Path('C:/Program Files/Microsoft')]:
            if base.exists():
                for exe in base.glob('**/bin/java.exe'): candidates.append(exe)
        for exe in candidates:
            key=str(exe.resolve())
            if key in seen: continue
            seen.add(key); version='未知'; ok=False
            try:
                r=subprocess.run([str(exe),'-version'],capture_output=True,text=True,timeout=5)
                text=(r.stderr or '')+'\n'+(r.stdout or '')
                m=re.search(r'version "([^"]+)',text); version=m.group(1) if m else '未知'; ok=r.returncode==0
            except (OSError,subprocess.SubprocessError): pass
            candidates[-1:] = candidates[-1:] if False else candidates[-1:]
            # replace by a serializable record
            yield {'path':key,'version':version,'available':ok}
    def list(self): return list(self.scan())

class PlayerManager:
    def __init__(self, multi): self.multi=multi
    def list(self, profile_id):
        logs=self.multi.logs(profile_id,500)
        names=[]
        for line in logs:
            m=re.search(r'There are \d+.*?: (.*)',line)
            if m: names=[x.strip() for x in m.group(1).split(',') if x.strip()]
        return [{'name':n} for n in names]
    def command(self, profile_id, action, player):
        commands={'kick':f'kick {player}','ban':f'ban {player}','pardon':f'pardon {player}','op':f'op {player}','deop':f'deop {player}','whitelist_add':f'whitelist add {player}','whitelist_remove':f'whitelist remove {player}'}
        if action not in commands: return False,'不支援的玩家操作'
        return self.multi.send_command(profile_id,commands[action])

class Scheduler:
    def __init__(self, db, multi, backup_factory):
        self.db=db; self.multi=multi; self.backup_factory=backup_factory; self.stop_event=threading.Event(); self._init(); threading.Thread(target=self._loop,daemon=True).start()
    def _init(self):
        with self.db._connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, name TEXT NOT NULL, action TEXT NOT NULL, interval_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, next_run REAL NOT NULL, last_result TEXT NOT NULL DEFAULT ""); CREATE TABLE IF NOT EXISTS schedule_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, server_id INTEGER, schedule_name TEXT, action TEXT, started_at REAL, result TEXT, detail TEXT);''')
            columns={row[1] for row in c.execute('PRAGMA table_info(schedule_runs)')}
            for name,kind in [('server_id','INTEGER'),('schedule_name','TEXT'),('action','TEXT')]:
                if name not in columns:c.execute(f'ALTER TABLE schedule_runs ADD COLUMN {name} {kind}')
    def list(self, server_id=None):
        with self.db._connect() as c:
            q='SELECT * FROM schedules'; args=[]
            if server_id is not None:q+=' WHERE server_id=?';args=[server_id]
            return [dict(x) for x in c.execute(q+' ORDER BY id',args)]
    def get(self, sid):
        with self.db._connect() as c:
            row=c.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone()
            return dict(row) if row else None
    def create(self, server_id,name,action,interval_seconds):
        if action not in {'start','stop','restart','backup','command_save_all'}: raise ValueError('不支援的排程動作')
        interval=max(60,int(interval_seconds)); now=time.time()
        with self.db._connect() as c:c.execute('INSERT INTO schedules(server_id,name,action,interval_seconds,next_run) VALUES(?,?,?,?,?)',(server_id,name[:80],action,interval,now+interval))
    def delete(self,sid):
        with self.db._connect() as c:
            c.execute('UPDATE schedule_runs SET schedule_id=NULL WHERE schedule_id=?',(sid,))
            c.execute('DELETE FROM schedules WHERE id=?',(sid,))
    def toggle(self,sid):
        with self.db._connect() as c:c.execute('UPDATE schedules SET enabled=1-enabled WHERE id=?',(sid,))
    def _loop(self):
        while not self.stop_event.wait(5):
            now=time.time()
            for item in self.list():
                if not item['enabled'] or item['next_run']>now: continue
                result='ok'; detail=''
                try:
                    if item['action']=='backup':
                        manager=self.backup_factory(item['server_id']);manager.create_backup('scheduled')
                        settings=self.db.backup_retention(item['server_id'])
                        manager.cleanup_retention(settings['retain_count'],settings['max_age_days'] or None)
                    elif item['action']=='command_save_all': self.multi.send_command(item['server_id'],'save-all')
                    else: getattr(self.multi,item['action'])(item['server_id'])
                except Exception as exc: result='error';detail=str(exc)
                with self.db._connect() as c:
                    c.execute('UPDATE schedules SET next_run=?,last_result=? WHERE id=?',(now+item['interval_seconds'],result,item['id']))
                    c.execute('INSERT INTO schedule_runs(schedule_id,server_id,schedule_name,action,started_at,result,detail) VALUES(?,?,?,?,?,?,?)',(item['id'],item['server_id'],item['name'],item['action'],now,result,detail))


class MonitorService:
    """Collect metrics and evaluate alert transitions independently of UI polling."""
    def __init__(self, profiles, multi, db, interval_seconds=5):
        self.profiles=profiles;self.multi=multi;self.db=db
        self.interval_seconds=max(1,int(interval_seconds));self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._loop,daemon=True,name='mcm-monitor')
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        while not self.stop_event.is_set():
            for profile in self.profiles.list():
                if self.stop_event.is_set():break
                try:
                    status=self.multi.status(profile['id'])
                    if not status:continue
                    self.db.record_metrics(profile['id'],status.get('metrics',{}))
                    self.db.evaluate_metric_alerts(profile['id'],status.get('metrics',{}))
                    self.db.evaluate_server_phase(profile['id'],status.get('phase'),status.get('phase_timeout'))
                except Exception:
                    # One unavailable server must not terminate collection for the others.
                    continue
            self.stop_event.wait(self.interval_seconds)
