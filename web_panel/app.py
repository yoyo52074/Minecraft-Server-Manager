import asyncio
import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backup_manager import BackupJobManager, BackupManager
from server_manager import ServerManager
from .database import WebDatabase
from .file_service import SafeFileService
from .multi_server import CORE_TYPES, MultiServerManager, ServerProfileStore
from .permissions import ALL_PERMISSIONS, PERMISSION_GROUPS
from .operations import JavaManager, MonitorService, PlayerManager, Scheduler

ROLES = {"admin", "operator", "viewer"}


def create_app(controller, database_path):
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.scheduler.stop_event.set()
        app.state.monitor.stop()

    app = FastAPI(title="Minecraft Server Manager Web Panel", lifespan=lifespan)
    db = WebDatabase(database_path)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    profiles = ServerProfileStore(db)
    legacy_profile = profiles.ensure_legacy(controller.server_directory)
    multi = MultiServerManager(profiles, controller.web_bridge)
    files = SafeFileService(profiles)
    app.state.db = db
    app.state.bridge = controller.web_bridge
    app.state.profiles = profiles
    app.state.multi = multi
    app.state.files = files
    app.state.java = JavaManager()
    app.state.players = PlayerManager(multi)
    app.state.backup_jobs = BackupJobManager()
    app.state.scheduler = Scheduler(db, multi, lambda server_id: BackupManager(profiles.get(server_id)["directory"]))
    app.state.monitor = MonitorService(profiles, multi, db)

    def current_user(request: Request):
        return db.get_session(request.cookies.get("mcm_session"))

    def require_user(request: Request, roles=None):
        user = current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="請先登入")
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="權限不足")
        return user

    def require_server_permission(request: Request, profile_id: int, permission: str):
        user = require_user(request)
        if not profiles.get(profile_id):
            raise HTTPException(status_code=404, detail="找不到伺服器")
        if not db.has_server_permission(user, profile_id, permission):
            raise HTTPException(status_code=403, detail=f"缺少權限：{permission}")
        return user

    def visible_servers(user):
        return [item for item in multi.statuses() if user["role"] == "admin" or db.has_server_permission(user, item["id"], "server.view")]

    def render(request, template, **context):
        context.update({"user": current_user(request), "setup_required": db.user_count() == 0})
        return templates.TemplateResponse(request=request, name=template, context=context)

    def check_csrf(request: Request, user):
        token = request.headers.get("x-csrf-token") or request.cookies.get("mcm_csrf")
        if not token or token != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return render(request, "dashboard.html", status=app.state.bridge.status(), logs=app.state.bridge.logs(), servers=visible_servers(user))

    @app.get("/server/{profile_id}", response_class=HTMLResponse)
    def server_page(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "server.view")
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return render(request, "server.html", profile=profile, status=multi.status(profile_id), csrf=current_user(request)["csrf_token"])

    @app.get("/server/{profile_id}/settings", response_class=HTMLResponse)
    def settings_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.view")
        profile = profiles.get(profile_id)
        return render(request, "settings.html", profile=profile, properties=ServerManager(profile["directory"]).get_server_properties(), csrf=user["csrf_token"])

    @app.get("/server/{profile_id}/history", response_class=HTMLResponse)
    def server_history_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "server.view")
        return render(request, "history.html", profile=profiles.get(profile_id), csrf=user["csrf_token"])

    @app.get("/server/{profile_id}/monitor", response_class=HTMLResponse)
    def monitor_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "resources.view")
        profile = profiles.get(profile_id)
        status = multi.status(profile_id) or {}
        return render(request, "monitor_v2.html", profile=profile, csrf=user["csrf_token"], thresholds=db.alert_thresholds(profile_id), metrics=db.recent_metrics(profile_id, 120), alerts=db.alert_events(profile_id), current=status.get("metrics", {}), can_edit_alerts=db.has_server_permission(user, profile_id, "settings.edit"))

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", error=None)

    @app.post("/setup")
    def setup(request: Request, username: str = Form(...), password: str = Form(...)):
        if db.user_count() > 0:
            raise HTTPException(status_code=403, detail="管理員已初始化")
        try:
            user_id = db.create_user(username, password, "admin")
        except ValueError as exc:
            return render(request, "login.html", error=str(exc))
        db.audit(user_id, "setup_admin", "建立第一位管理員")
        session_id, csrf = db.create_session(user_id)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("mcm_session", session_id, httponly=True, samesite="lax", max_age=86400)
        response.set_cookie("mcm_csrf", csrf, httponly=False, samesite="lax", max_age=86400)
        return response

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        user = db.authenticate(username, password)
        if not user:
            return render(request, "login.html", error="帳號或密碼錯誤")
        session_id, csrf = db.create_session(user["id"])
        db.audit(user["id"], "login")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("mcm_session", session_id, httponly=True, samesite="lax", max_age=86400)
        response.set_cookie("mcm_csrf", csrf, httponly=False, samesite="lax", max_age=86400)
        return response

    @app.post("/logout")
    def logout(request: Request):
        session_id = request.cookies.get("mcm_session")
        if session_id:
            db.delete_session(session_id)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("mcm_session")
        response.delete_cookie("mcm_csrf")
        return response

    @app.get("/api/status")
    def api_status(request: Request):
        require_user(request)
        return app.state.bridge.status()

    @app.get("/api/logs")
    def api_logs(request: Request, limit: int = 100):
        require_user(request)
        return {"logs": app.state.bridge.logs(limit)}

    @app.get("/api/servers")
    def list_servers(request: Request):
        user = require_user(request)
        return {"servers": visible_servers(user), "core_types": sorted(CORE_TYPES)}

    @app.post("/api/servers")
    async def create_server(request: Request):
        user = require_user(request, {"admin"})
        check_csrf(request, user)
        payload = await request.json()
        try:
            profile = profiles.create(
                payload.get("name", ""), payload.get("directory", ""), payload.get("core_type", "Paper"),
                minecraft_version=payload.get("minecraft_version", ""), java_path=payload.get("java_path", "java"),
                min_memory=payload.get("min_memory", 1024), max_memory=payload.get("max_memory", 2048),
                server_port=payload.get("server_port", 25565), launcher=payload.get("launcher", ""),
                auto_restart=payload.get("auto_restart", False),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "create_server", str(profile["id"]), server_id=profile["id"])
        return profile

    @app.get("/api/servers/{profile_id}")
    def get_server(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "server.view")
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        profile["status"] = multi.status(profile_id)
        return profile

    @app.patch("/api/servers/{profile_id}")
    async def update_server(profile_id: int, request: Request):
        user = require_user(request, {"admin"})
        check_csrf(request, user)
        try:
            profile = profiles.update(profile_id, await request.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "update_server", str(profile_id), server_id=profile_id)
        return profile

    @app.delete("/api/servers/{profile_id}")
    async def delete_server(profile_id: int, request: Request):
        user = require_user(request, {"admin"})
        check_csrf(request, user)
        if multi.status(profile_id) and multi.status(profile_id)["running"]:
            raise HTTPException(status_code=409, detail="請先停止伺服器")
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        try:
            profiles.delete(profile_id, bool(payload.get("delete_files", False)))
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "delete_server", str(profile_id))
        return {"ok": True}

    @app.post("/api/servers/{profile_id}/schedules")
    async def create_schedule_before_action(profile_id: int, request: Request):
        user=require_server_permission(request, profile_id, "schedule.create")
        check_csrf(request, user)
        payload=await request.json()
        app.state.scheduler.create(profile_id, str(payload.get("name", "排程")), str(payload.get("action", "backup")), int(payload.get("interval_seconds", 3600)))
        return {"ok": True}

    @app.get("/api/servers/{profile_id}/logs")
    def server_logs(profile_id: int, request: Request, limit: int = 200):
        require_server_permission(request, profile_id, "console.view")
        if not profiles.get(profile_id):
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return {"logs": multi.logs(profile_id, limit)}

    @app.get("/api/servers/{profile_id}/metrics")
    def metrics_history(profile_id: int, request: Request, limit: int = 120):
        require_server_permission(request, profile_id, "resources.view")
        return {"metrics": app.state.db.recent_metrics(profile_id, min(max(limit, 1), 1440))}

    @app.get("/api/servers/{profile_id}/history")
    def server_history_api(profile_id: int, request: Request, limit: int = 100):
        user = require_server_permission(request, profile_id, "server.view")
        limit = min(max(limit, 1), 500)
        visible_operations = []
        for entry in db.server_audit_logs(profile_id, limit * 3):
            action = entry["action"]
            permission = ("players.view" if action.startswith("player_") else
                          "backups.view" if "backup" in action else
                          "settings.view" if "settings" in action else
                          "files.view" if action.startswith(("file_", "upload_file", "edit_file", "delete_file")) else
                          "console.view" if action.startswith("console_") else "server.view")
            if db.has_server_permission(user, profile_id, permission):
                visible_operations.append(entry)
            if len(visible_operations) >= limit:
                break
        runs = []
        if db.has_server_permission(user, profile_id, "schedule.view"):
            with db._connect() as conn:
                runs = [dict(row) for row in conn.execute("SELECT r.*,COALESCE(r.server_id,s.server_id) AS server_id,COALESCE(r.schedule_name,s.name) AS schedule_name,COALESCE(r.action,s.action) AS action FROM schedule_runs r LEFT JOIN schedules s ON s.id=r.schedule_id WHERE COALESCE(r.server_id,s.server_id)=? ORDER BY r.id DESC LIMIT ?", (profile_id, limit)).fetchall()]
        return {"operations": visible_operations, "schedule_runs": runs}

    @app.get("/api/servers/{profile_id}/alerts")
    def get_alerts(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "resources.view")
        return {"thresholds": db.alert_thresholds(profile_id), "alerts": db.alert_events(profile_id)}

    @app.put("/api/servers/{profile_id}/alerts")
    async def update_alerts(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.edit")
        check_csrf(request, user)
        try:
            thresholds = db.set_alert_thresholds(profile_id, await request.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "update_alert_thresholds", str(thresholds), server_id=profile_id)
        return {"thresholds": thresholds}

    @app.post("/api/servers/{profile_id}/alerts/evaluate")
    def evaluate_alerts(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "resources.view")
        status = multi.status(profile_id)
        if status:
            db.evaluate_metric_alerts(profile_id, status["metrics"])
            db.evaluate_server_phase(profile_id, status.get("phase"), status.get("phase_timeout"))
        return {"alerts": db.alert_events(profile_id)}

    @app.post("/api/servers/{profile_id}/console")
    async def server_console(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "console.send_command")
        check_csrf(request, user)
        payload = await request.json()
        ok, message = multi.send_command(profile_id, str(payload.get("command", "")))
        db.audit(user["user_id"], "console_command", f"{profile_id}: {payload.get('command', '')}", server_id=profile_id)
        return {"ok": ok, "message": message}

    @app.get("/api/servers/{profile_id}/settings")
    def server_settings(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "settings.view")
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return {"profile": profile, "properties": ServerManager(profile["directory"]).get_server_properties()}

    @app.put("/api/servers/{profile_id}/settings")
    async def update_server_settings(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.edit")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        payload = await request.json()
        properties = payload.get("properties", payload)
        profile_values = payload.get("profile", {})
        if not isinstance(profile_values, dict):
            raise HTTPException(status_code=400, detail="伺服器設定格式錯誤")
        if any(key in profile_values for key in ("java_path", "min_memory", "max_memory")):
            require_server_permission(request, profile_id, "settings.edit_java")
        if any(key in profile_values for key in ("launcher", "auto_restart")):
            require_server_permission(request, profile_id, "settings.edit_startup")
        if not isinstance(properties, dict):
            raise HTTPException(status_code=400, detail="設定格式錯誤")
        if "server-port" in properties:
            try:
                port = int(properties["server-port"])
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="伺服器連接埠必須介於 1 到 65535") from exc
        before_properties = ServerManager(profile["directory"]).get_server_properties()
        before_profile = {key: profile.get(key) for key in profile_values}
        try:
            snapshot = BackupManager(profile["directory"]).create_settings_backup(profile)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"設定安全快照建立失敗，未儲存設定：{exc}") from exc
        # Save only after the rollback snapshot exists.
        try:
            ServerManager(profile["directory"]).save_server_properties({str(k): str(v) for k, v in properties.items()})
            if profile_values:
                profiles.update(profile_id, profile_values)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changes = {"properties": {key: {"from": before_properties.get(key), "to": str(value)} for key, value in properties.items() if before_properties.get(key) != str(value)},
                   "profile": {key: {"from": before_profile.get(key), "to": value} for key, value in profile_values.items() if before_profile.get(key) != value},
                   "snapshot": Path(snapshot).name}
        db.audit(user["user_id"], "update_settings", json.dumps(changes, ensure_ascii=False), server_id=profile_id)
        return {"ok": True, "properties": ServerManager(profile["directory"]).get_server_properties()}

    @app.get("/java", response_class=HTMLResponse)
    def java_page(request: Request):
        user = require_user(request, {"admin"})
        return render(request, "java.html", java_versions=app.state.java.list(), csrf=user["csrf_token"])

    @app.get("/api/java")
    def java_scan(request: Request):
        require_user(request, {"admin"})
        return {"java": app.state.java.list()}

    @app.get("/server/{profile_id}/players", response_class=HTMLResponse)
    def players_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "players.view")
        profile = profiles.get(profile_id)
        return render(request, "players.html", profile=profile, players=app.state.players.list(profile_id), csrf=user["csrf_token"])

    @app.get("/api/servers/{profile_id}/players")
    def players_api(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "players.view")
        return {"players": app.state.players.list(profile_id)}

    @app.post("/api/servers/{profile_id}/players/{action}")
    async def player_action(profile_id: int, action: str, request: Request):
        user = require_server_permission(request, profile_id, "players.view")
        check_csrf(request, user); payload=await request.json(); player=str(payload.get("player", "")).strip()
        if action in {"kick","ban","pardon","op","deop","whitelist_add","whitelist_remove"}:
            perm = "players.unban" if action=="pardon" else "players.whitelist" if action.startswith("whitelist") else f"players.{action}"
            require_server_permission(request, profile_id, perm)
        ok,message=app.state.players.command(profile_id,action,player); db.audit(user["user_id"],"player_"+action,f"{profile_id}:{player}",server_id=profile_id); return {"ok":ok,"message":message}

    @app.get("/server/{profile_id}/schedules", response_class=HTMLResponse)
    def schedules_page(profile_id: int, request: Request):
        user=require_server_permission(request,profile_id,"schedule.view"); return render(request,"schedules.html",profile=profiles.get(profile_id),schedules=app.state.scheduler.list(profile_id),csrf=user["csrf_token"])

    @app.get("/api/servers/{profile_id}/schedules")
    def schedules_api(profile_id:int,request:Request): require_server_permission(request,profile_id,"schedule.view"); return {"schedules":app.state.scheduler.list(profile_id)}

    @app.post("/api/servers/{profile_id}/schedules")
    async def create_schedule(profile_id:int,request:Request):
        user=require_server_permission(request,profile_id,"schedule.create");check_csrf(request,user);p=await request.json();app.state.scheduler.create(profile_id,str(p.get("name","排程")),str(p.get("action","backup")),int(p.get("interval_seconds",3600)));return {"ok":True}

    @app.post("/api/schedules/{schedule_id}/toggle")
    def toggle_schedule(schedule_id:int,request:Request):
        item=app.state.scheduler.get(schedule_id)
        if not item: raise HTTPException(status_code=404,detail="找不到排程")
        user=require_server_permission(request,item["server_id"],"schedule.edit");check_csrf(request,user);app.state.scheduler.toggle(schedule_id);return {"ok":True}

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id:int,request:Request):
        item=app.state.scheduler.get(schedule_id)
        if not item: raise HTTPException(status_code=404,detail="找不到排程")
        user=require_server_permission(request,item["server_id"],"schedule.delete");check_csrf(request,user);app.state.scheduler.delete(schedule_id);return {"ok":True}

    @app.get("/api/servers/{profile_id}/backups")
    def list_backups(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "backups.view")
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        manager = BackupManager(profile["directory"])
        return {"backups": [{"name": Path(path).name, "size": Path(path).stat().st_size, "modified_at": Path(path).stat().st_mtime} for path in manager.list_backups()]}

    @app.get("/api/servers/{profile_id}/backups/retention")
    def get_backup_retention(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "backups.view")
        return db.backup_retention(profile_id)

    @app.put("/api/servers/{profile_id}/backups/retention")
    async def put_backup_retention(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.create")
        check_csrf(request, user)
        payload = await request.json()
        try:
            result = db.set_backup_retention(profile_id, payload.get("retain_count", 10), payload.get("max_age_days", 0))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.post("/api/servers/{profile_id}/backups/cleanup")
    def cleanup_backups(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.delete")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        settings = db.backup_retention(profile_id)
        removed = BackupManager(profile["directory"]).cleanup_retention(settings["retain_count"], settings["max_age_days"] or None)
        db.audit(user["user_id"], "cleanup_backups", f"{profile_id}:removed={len(removed)}", server_id=profile_id)
        return {"removed": [Path(path).name for path in removed]}

    @app.post("/api/servers/{profile_id}/backups/verify")
    async def verify_backup(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "backups.view")
        payload = await request.json()
        profile = profiles.get(profile_id)
        root = (Path(profile["directory"]) / "backups").resolve()
        target = (root / str(payload.get("name", ""))).resolve()
        if target.parent != root or target.suffix.lower() != ".zip":
            raise HTTPException(status_code=400, detail="無效的備份檔")
        try:
            BackupManager(profile["directory"]).verify_backup(str(target))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/server/{profile_id}/backups", response_class=HTMLResponse)
    def backups_page(profile_id:int,request:Request):
        user=require_server_permission(request,profile_id,"backups.view"); profile=profiles.get(profile_id); manager=BackupManager(profile["directory"])
        items=[{"name":Path(p).name,"size":Path(p).stat().st_size,"modified_at":Path(p).stat().st_mtime} for p in manager.list_backups()]
        return render(request,"backups_v2.html",profile=profile,backups=items,csrf=user["csrf_token"])

    @app.get("/api/servers/{profile_id}/backups/{name}/download")
    def download_backup(profile_id:int,name:str,request:Request):
        require_server_permission(request,profile_id,"backups.download");profile=profiles.get(profile_id);root=(Path(profile["directory"])/"backups").resolve();target=(root/name).resolve()
        if target.parent!=root or not target.is_file(): raise HTTPException(status_code=404,detail="找不到備份")
        return FileResponse(target,filename=target.name)

    @app.delete("/api/servers/{profile_id}/backups/{name}")
    def delete_backup(profile_id:int,name:str,request:Request):
        user=require_server_permission(request,profile_id,"backups.delete");profile=profiles.get(profile_id);root=(Path(profile["directory"])/"backups").resolve();target=(root/name).resolve()
        if target.parent!=root or target.suffix.lower()!='.zip': raise HTTPException(status_code=400,detail="無效的備份")
        target.unlink(missing_ok=False);db.audit(user["user_id"],"delete_backup",f"{profile_id}:{name}");return {"ok":True}

    @app.post("/api/servers/{profile_id}/backups")
    async def create_backup(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.create")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        status = multi.status(profile_id)
        if status and status["running"]:
            raise HTTPException(status_code=409, detail="建立備份前建議先停止伺服器")
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        settings = db.backup_retention(profile_id)
        task_id = app.state.backup_jobs.start(profile_id, profile["directory"], str(payload.get("label", "manual")), settings["retain_count"], settings["max_age_days"])
        db.audit(user["user_id"], "start_backup", f"{profile_id}:task={task_id}", server_id=profile_id)
        return {"ok": True, "task_id": task_id, "status": "queued"}

    @app.get("/api/servers/{profile_id}/backups/jobs/{task_id}")
    def get_backup_job(profile_id: int, task_id: str, request: Request):
        require_server_permission(request, profile_id, "backups.view")
        job = app.state.backup_jobs.get(task_id, profile_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到備份工作")
        return job

    @app.post("/api/servers/{profile_id}/backups/jobs/{task_id}/cancel")
    def cancel_backup_job(profile_id: int, task_id: str, request: Request):
        user = require_server_permission(request, profile_id, "backups.create")
        check_csrf(request, user)
        job = app.state.backup_jobs.cancel(task_id, profile_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到備份工作")
        db.audit(user["user_id"], "cancel_backup", f"{profile_id}:task={task_id}", server_id=profile_id)
        return job

    @app.post("/api/servers/{profile_id}/backups/restore")
    async def restore_backup(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.restore")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        status = multi.status(profile_id)
        if status and status["running"]:
            raise HTTPException(status_code=409, detail="還原備份前必須先停止伺服器")
        payload = await request.json()
        backup = Path(profile["directory"]) / "backups" / str(payload.get("name", ""))
        root = (Path(profile["directory"]) / "backups").resolve()
        if backup.resolve().parent != root or backup.suffix.lower() != ".zip":
            raise HTTPException(status_code=400, detail="無效的備份檔")
        manager = BackupManager(profile["directory"])
        try:
            path = manager.restore_backup(str(backup))
        except (OSError, ValueError, FileNotFoundError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        safety_name = Path(manager.last_pre_restore_backup or "").name
        db.audit(user["user_id"], "restore_backup", f"{profile_id}:{Path(path).name}; safety={safety_name}", server_id=profile_id)
        return {"ok": True, "safety_backup": safety_name}

    @app.get("/api/servers/{profile_id}/files")
    def list_files(profile_id: int, request: Request, path: str = ""):
        require_server_permission(request, profile_id, "files.view")
        try:
            return {"path": path, "items": files.list(profile_id, path)}
        except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/servers/{profile_id}/files/download")
    def download_file(profile_id: int, request: Request, path: str):
        require_server_permission(request, profile_id, "files.download")
        try:
            target = files.resolve(profile_id, path)
        except (PermissionError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=400, detail="不是檔案")
        return FileResponse(target, filename=target.name)

    @app.post("/api/servers/{profile_id}/files/upload")
    async def upload_file(profile_id: int, request: Request, file: UploadFile = File(...), path: str = Form("")):
        user = require_server_permission(request, profile_id, "files.upload")
        check_csrf(request, user)
        relative = str(Path(path) / (file.filename or "upload.bin"))
        try:
            target = files.write_upload(profile_id, relative, file)
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "upload_file", f"{profile_id}:{relative}", server_id=profile_id)
        return {"ok": True, "path": target.name}

    @app.post("/api/servers/{profile_id}/files/mkdir")
    async def make_directory(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "files.create_folder")
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.mkdir(profile_id, payload.get("path", ""))
        except (PermissionError, FileExistsError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/servers/{profile_id}/files/rename")
    async def rename_file(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "files.rename")
        check_csrf(request, user)
        payload = await request.json()
        try:
            target = files.rename(profile_id, payload.get("path", ""), payload.get("new_name", ""))
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "path": target.name}

    @app.get("/api/servers/{profile_id}/files/text")
    def read_text_file(profile_id: int, request: Request, path: str):
        require_server_permission(request, profile_id, "files.view")
        try:
            return {"path": path, "content": files.read_text(profile_id, path)}
        except (PermissionError, FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/servers/{profile_id}/files/text")
    async def write_text_file(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "files.edit")
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.write_text(profile_id, payload.get("path", ""), str(payload.get("content", "")))
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "edit_file", f"{profile_id}:{payload.get('path', '')}", server_id=profile_id)
        return {"ok": True}

    @app.post("/api/servers/{profile_id}/files/{operation}")
    async def file_operation(profile_id: int, operation: str, request: Request):
        permission = {"copy": "files.copy", "move": "files.move", "zip": "files.compress", "unzip": "files.extract"}.get(operation)
        if not permission:
            raise HTTPException(status_code=404, detail="未知的檔案操作")
        user = require_server_permission(request, profile_id, permission)
        check_csrf(request, user)
        payload = await request.json()
        try:
            if operation in {"copy", "move"}:
                target = files.copy_or_move(profile_id, payload.get("source", ""), payload.get("destination", ""), operation == "move")
            elif operation == "zip":
                target = files.zip_folder(profile_id, payload.get("source", ""), payload.get("destination", "archive.zip"))
            elif operation == "unzip":
                target = files.unzip(profile_id, payload.get("source", ""), payload.get("destination", ""))
            else:
                raise ValueError("未知的檔案操作")
        except (PermissionError, ValueError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], f"file_{operation}", f"{profile_id}:{payload}", server_id=profile_id)
        return {"ok": True, "path": str(target)}

    @app.delete("/api/servers/{profile_id}/files")
    async def delete_file(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "files.delete")
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.delete(profile_id, payload.get("path", ""))
        except (PermissionError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "delete_file", f"{profile_id}:{payload.get('path', '')}", server_id=profile_id)
        return {"ok": True}

    @app.post("/api/server/{action}")
    def legacy_server_action(action: str, request: Request):
        permission = {"start": "server.start", "stop": "server.stop", "restart": "server.restart"}.get(action)
        if not permission:
            raise HTTPException(status_code=404, detail="未知的操作")
        user = require_server_permission(request, legacy_profile["id"], permission)
        check_csrf(request, user)
        actions = {"start": app.state.bridge.start, "stop": app.state.bridge.stop, "restart": app.state.bridge.restart}
        ok, message = actions[action]()
        db.audit(user["user_id"], action, message)
        return {"ok": ok, "message": message}

    @app.post("/api/console")
    async def legacy_console(request: Request):
        user = require_server_permission(request, legacy_profile["id"], "console.send_command")
        check_csrf(request, user)
        payload = await request.json()
        ok, message = app.state.bridge.send_command(str(payload.get("command", "")))
        db.audit(user["user_id"], "console_command", str(payload.get("command", "")))
        return {"ok": ok, "message": message}

    @app.get("/admin/users", response_class=HTMLResponse)
    def admin_users(request: Request):
        user = require_user(request, {"admin"})
        return render(request, "admin_users.html", users=db.list_users(), csrf=user["csrf_token"], error=None)

    @app.post("/admin/users")
    def create_managed_user(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form(...), csrf: str = Form(...)):
        user = require_user(request, {"admin"})
        if csrf != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        try:
            new_id = db.create_user(username, password, role)
            db.audit(user["user_id"], "create_user", username)
        except Exception as exc:
            return render(request, "admin_users.html", users=db.list_users(), csrf=user["csrf_token"], error=str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/admin/users/{user_id}/permissions", response_class=HTMLResponse)
    def permissions_page(user_id: int, request: Request):
        admin = require_user(request, {"admin"})
        matches = [item for item in db.list_users() if item["id"] == user_id]
        if not matches:
            raise HTTPException(status_code=404, detail="找不到使用者")
        server_rows = [{"profile": profile, "selected": db.get_server_permissions(user_id, profile["id"])} for profile in profiles.list()]
        return render(request, "permissions.html", managed_user=matches[0], servers=server_rows, groups=PERMISSION_GROUPS, csrf=admin["csrf_token"])

    @app.post("/admin/users/{user_id}/permissions")
    async def save_permissions(user_id: int, request: Request):
        admin = require_user(request, {"admin"})
        form = await request.form()
        if form.get("csrf") != admin["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        try:
            server_id = int(form.get("server_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="伺服器選擇錯誤") from exc
        selected = form.getlist("permissions")
        db.set_server_permissions(user_id, server_id, selected)
        db.audit(admin["user_id"], "set_server_permissions", f"user={user_id},server={server_id},count={len(selected)}")
        return RedirectResponse(f"/admin/users/{user_id}/permissions?server_id={server_id}", status_code=303)

    @app.post("/admin/users/{user_id}/toggle")
    def toggle_user(user_id: int, request: Request, csrf: str = Form(...)):
        user = require_user(request, {"admin"})
        if csrf != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        if user_id == user["user_id"]:
            raise HTTPException(status_code=400, detail="不能停用目前登入的管理員")
        matches = [item for item in db.list_users() if item["id"] == user_id]
        if matches:
            db.set_user_enabled(user_id, not bool(matches[0]["enabled"]))
            db.audit(user["user_id"], "toggle_user", str(user_id))
        return RedirectResponse("/admin/users", status_code=303)

    # Keep the catch-all action route after all explicit /api/servers routes.
    @app.post("/api/servers/{profile_id}/{action}")
    def server_action(profile_id: int, action: str, request: Request):
        permission = {"start": "server.start", "stop": "server.stop", "force-stop": "server.force_stop"}.get(action)
        if not permission:
            raise HTTPException(status_code=404, detail="未知的操作")
        user = require_server_permission(request, profile_id, permission)
        check_csrf(request, user)
        actions = {"start": multi.start, "stop": multi.stop, "force-stop": lambda value: multi.stop(value, True)}
        ok, message = actions[action](profile_id)
        db.audit(user["user_id"], f"server_{action}", str(profile_id), server_id=profile_id)
        return {"ok": ok, "message": message}

    @app.websocket("/ws/logs")
    async def logs_socket(websocket: WebSocket):
        session = db.get_session(websocket.cookies.get("mcm_session"))
        if not session:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            last = len(app.state.bridge.logs())
            while True:
                await websocket.receive_text()
                logs = app.state.bridge.logs()
                if len(logs) > last:
                    for line in logs[last:]:
                        await websocket.send_json({"line": line})
                    last = len(logs)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/servers")
    async def servers_socket(websocket: WebSocket):
        session = db.get_session(websocket.cookies.get("mcm_session"))
        if not session:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                snapshot = visible_servers(session)
                await websocket.send_json({"type": "servers", "servers": snapshot, "timestamp": asyncio.get_running_loop().time()})
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

    return app
