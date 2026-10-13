import asyncio
import csv
import ipaddress
import io
import json
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from backup_manager import BackupJobManager, BackupManager
from server_manager import ServerManager
from .database import WebDatabase
from .file_service import MAX_UPLOAD_CHUNK_BYTES, SafeFileService
from .core_update import CoreUpdateJobs
from .multi_server import (
    CORE_TYPES,
    EULA_REQUIRED_MESSAGE,
    CoreAdapter,
    MultiServerManager,
    ServerProfileStore,
)
from .permissions import ALL_PERMISSIONS, PERMISSION_GROUPS, PERMISSION_LABELS, ROLE_DEFAULTS
from .operations import MonitorService, PlayerManager, Scheduler
from .node_resources import NodeResourceService
from .java_runtime import JavaRuntimeJobs

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
    java_runtime = JavaRuntimeJobs(profiles, db, Path(database_path).resolve().parent / "runtimes", start_server=multi.start)
    core_updates = CoreUpdateJobs(profiles, multi, java_runtime)
    app.state.db = db
    app.state.bridge = controller.web_bridge
    app.state.profiles = profiles
    app.state.multi = multi
    app.state.files = files
    app.state.core_updates = core_updates
    app.state.java_runtime = java_runtime
    app.state.players = PlayerManager(multi, db)
    app.state.backup_jobs = BackupJobManager(db)
    app.state.scheduler = Scheduler(db, multi, lambda server_id: BackupManager(profiles.get(server_id)["directory"]), app.state.players)
    app.state.monitor = MonitorService(profiles, multi, db, players=app.state.players)
    app.state.node_resources = NodeResourceService()

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

    def validated_server_port(value):
        if isinstance(value, bool):
            raise ValueError("伺服器連接埠必須介於 1 到 65535")
        try:
            port = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("伺服器連接埠必須介於 1 到 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("伺服器連接埠必須介於 1 到 65535")
        return port

    def sync_server_port(profile, properties=None):
        if not profile:
            return profile, properties
        if properties is None:
            properties = ServerManager(profile["directory"]).get_server_properties()
        raw_port = properties.get("server-port")
        if raw_port is None:
            properties_path = Path(profile["directory"]) / "server.properties"
            if not properties_path.is_file() or properties_path.is_symlink():
                return profile, properties
            port = 25565  # Minecraft's default when server-port is omitted.
        else:
            try:
                port = validated_server_port(raw_port)
            except ValueError:
                return profile, properties
        if int(profile.get("server_port") or 25565) != port:
            profile = profiles.update(profile["id"], {"server_port": port})
        return profile, properties

    def visible_servers(user):
        visible = []
        for item in multi.statuses():
            if user["role"] != "admin" and not db.has_server_permission(user, item["id"], "server.view"):
                continue
            profile, _ = sync_server_port(profiles.get(item["id"]))
            if profile:
                item["server_port"] = profile["server_port"]
            visible.append(item)
        return visible

    def render(request, template, **context):
        context.update({"user": current_user(request), "setup_required": db.user_count() == 0})
        return templates.TemplateResponse(request=request, name=template, context=context)

    def check_csrf(request: Request, user):
        token = request.headers.get("x-csrf-token") or request.cookies.get("mcm_csrf")
        if not token or token != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")

    def same_origin(headers, expected_scheme=None, expected_host=None):
        site = headers.get("sec-fetch-site", "").lower()
        if site == "cross-site":
            return False
        origin = headers.get("origin")
        if not origin:
            return site in {"same-origin", "none"}
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        host = (expected_host or headers.get("host", "")).rstrip(".").casefold()
        origin_host = parsed.netloc.rstrip(".").casefold()
        if parsed.scheme not in {"http", "https"} or not origin_host:
            return False
        scheme = expected_scheme or ("https" if headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower() == "https" else "http")
        if parsed.scheme != scheme:
            return False
        # Fetch Metadata may accommodate a proxy-rewritten host, but never a scheme mismatch.
        return origin_host == host or site == "same-origin"

    def require_same_origin(request: Request):
        if not same_origin(request.headers, request.url.scheme, request.url.netloc):
            raise HTTPException(status_code=403, detail="跨來源請求已拒絕")

    def current_legacy_profile():
        if not legacy_profile:
            return None
        profile = profiles.get(legacy_profile["id"])
        if not profile:
            return None
        if Path(profile["directory"]).resolve() != Path(controller.server_directory).resolve():
            return None
        return profile

    def set_auth_cookies(response, request: Request, session_id: str, csrf_token: str):
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        secure = request.url.scheme == "https" or forwarded_proto == "https"
        response.set_cookie("mcm_session", session_id, httponly=True, samesite="lax", max_age=86400, secure=secure, path="/")
        response.set_cookie("mcm_csrf", csrf_token, httponly=False, samesite="lax", max_age=86400, secure=secure, path="/")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        node_resources = app.state.node_resources.snapshot(profiles.list()) if user["role"] == "admin" else None
        return render(request, "dashboard.html", status=app.state.bridge.status(), logs=app.state.bridge.logs(), servers=visible_servers(user), node_resources=node_resources)

    @app.get("/server/{profile_id}", response_class=HTMLResponse)
    def server_page(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "server.view")
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        profile, _ = sync_server_port(profile)
        return render(request, "server.html", profile=profile, status=multi.status(profile_id), csrf=current_user(request)["csrf_token"])

    @app.get("/server/{profile_id}/settings", response_class=HTMLResponse)
    def settings_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.view")
        profile = profiles.get(profile_id)
        profile, properties = sync_server_port(profile)
        can_edit_core = db.has_server_permission(user, profile_id, "settings.edit_startup")
        can_edit_java = db.has_server_permission(user, profile_id, "settings.edit_java")
        return render(request, "settings.html", profile=profile, properties=properties, csrf=user["csrf_token"], can_edit_core=can_edit_core, can_edit_java=can_edit_java)

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
        peer = request.client.host if request.client else ""
        try:
            peer_ip = ipaddress.ip_address(peer)
            local_peer = peer_ip.is_loopback
            if isinstance(peer_ip, ipaddress.IPv6Address) and peer_ip.ipv4_mapped:
                local_peer = local_peer or peer_ip.ipv4_mapped.is_loopback
        except ValueError:
            local_peer = False
        if not local_peer:
            raise HTTPException(status_code=403, detail="首次初始化僅允許本機連線，請在執行伺服器的電腦上開啟管理面板")
        require_same_origin(request)
        try:
            user_id = db.create_first_admin(username, password)
        except ValueError as exc:
            if db.user_count() > 0:
                raise HTTPException(status_code=403, detail="管理員已初始化") from exc
            return render(request, "login.html", error=str(exc))
        db.audit(user_id, "setup_admin", "建立第一位管理員")
        session_id, csrf = db.create_session(user_id)
        response = RedirectResponse("/", status_code=303)
        set_auth_cookies(response, request, session_id, csrf)
        return response

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        require_same_origin(request)
        user = db.authenticate(username, password)
        if not user:
            return render(request, "login.html", error="帳號或密碼錯誤")
        session_id, csrf = db.create_session(user["id"])
        db.audit(user["id"], "login")
        response = RedirectResponse("/", status_code=303)
        set_auth_cookies(response, request, session_id, csrf)
        return response

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        user = current_user(request)
        if user and csrf != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        session_id = request.cookies.get("mcm_session")
        if session_id:
            db.delete_session(session_id)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("mcm_session")
        response.delete_cookie("mcm_csrf")
        return response

    @app.get("/api/status")
    def api_status(request: Request):
        profile = current_legacy_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="找不到既有伺服器")
        require_server_permission(request, profile["id"], "server.view")
        return app.state.bridge.status()

    @app.get("/api/logs")
    def api_logs(request: Request, limit: int = 100):
        profile = current_legacy_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="找不到既有伺服器")
        require_server_permission(request, profile["id"], "console.view")
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
            requested_port = validated_server_port(payload.get("server_port", 25565))
            profile = profiles.create(
                payload.get("name", ""), payload.get("directory", ""), payload.get("core_type", "Paper"),
                minecraft_version=payload.get("minecraft_version", ""), java_path=payload.get("java_path", "java"),
                min_memory=payload.get("min_memory", 1024), max_memory=payload.get("max_memory", 2048),
                server_port=requested_port, launcher=payload.get("launcher", ""),
                auto_restart=payload.get("auto_restart", False),
            )
            manager = ServerManager(profile["directory"])
            properties = manager.get_server_properties()
            if "server-port" in properties:
                profile, _ = sync_server_port(profile, properties)
            else:
                properties["server-port"] = str(requested_port)
                manager.save_server_properties(properties)
                profile = profiles.update(profile["id"], {"server_port": requested_port})
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
        profile, _ = sync_server_port(profile)
        profile["status"] = multi.status(profile_id)
        return profile

    @app.patch("/api/servers/{profile_id}")
    async def update_server(profile_id: int, request: Request):
        user = require_user(request, {"admin"})
        check_csrf(request, user)
        if profile_id in multi._core_updates:
            raise HTTPException(status_code=409, detail="核心更新進行中，請稍後再修改伺服器資料")
        try:
            values = await request.json()
            if not isinstance(values, dict):
                raise ValueError("伺服器設定格式錯誤")
            if "server_port" in values:
                port = validated_server_port(values["server_port"])
                current = profiles.get(profile_id)
                directory = str(Path(values.get("directory", current["directory"])).expanduser().resolve())
                Path(directory).mkdir(parents=True, exist_ok=True)
                manager = ServerManager(directory)
                properties = manager.get_server_properties()
                properties["server-port"] = str(port)
                manager.save_server_properties(properties)
                values["server_port"] = port
            profile = profiles.update(profile_id, values)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "update_server", str(profile_id), server_id=profile_id)
        return profile

    @app.delete("/api/servers/{profile_id}")
    async def delete_server(profile_id: int, request: Request):
        user = require_user(request, {"admin"})
        check_csrf(request, user)
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        try:
            with multi._lock:
                current = multi.status(profile_id)
                if profile_id in multi._core_updates:
                    raise HTTPException(status_code=409, detail="核心更新進行中，請稍後再移除伺服器")
                if current and current["running"]:
                    raise HTTPException(status_code=409, detail="請先停止伺服器")
                profiles.delete(profile_id, bool(payload.get("delete_files", False)))
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "delete_server", str(profile_id))
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
                          "settings.view" if "settings" in action or action.startswith("auto_configure_java") else
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

    @app.get("/api/servers/{profile_id}/alert-notifications")
    def get_alert_notifications(profile_id:int,request:Request):
        require_server_permission(request,profile_id,"resources.view")
        return db.alert_notification_settings(profile_id)

    @app.put("/api/servers/{profile_id}/alert-notifications")
    async def put_alert_notifications(profile_id:int,request:Request):
        user=require_server_permission(request,profile_id,"settings.edit");check_csrf(request,user);payload=await request.json()
        try:
            settings=db.set_alert_notification_settings(profile_id,payload.get("webhook_url"),payload.get("cooldown_seconds",900),payload.get("mute_start",""),payload.get("mute_end",""),bool(payload.get("clear_webhook",False)))
        except (ValueError,TypeError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
        db.audit(user["user_id"],"update_alert_notifications",f"configured={settings['webhook_configured']}",server_id=profile_id)
        return settings

    @app.post("/api/servers/{profile_id}/alerts/evaluate")
    def evaluate_alerts(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "resources.view")
        check_csrf(request, user)
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
        profile, properties = sync_server_port(profile)
        return {"profile": profile, "properties": properties}

    @app.put("/api/servers/{profile_id}/settings")
    async def update_server_settings(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.edit")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        if profile_id in multi._core_updates:
            raise HTTPException(status_code=409, detail="核心更新進行中，請稍後再修改設定")
        payload = await request.json()
        properties = payload.get("properties", payload)
        profile_values = payload.get("profile", {})
        if not isinstance(profile_values, dict):
            raise HTTPException(status_code=400, detail="伺服器設定格式錯誤")
        profile_values = dict(profile_values)
        if any(key in profile_values for key in ("java_path", "min_memory", "max_memory")):
            require_server_permission(request, profile_id, "settings.edit_java")
        if any(key in profile_values for key in ("launcher", "auto_restart")):
            require_server_permission(request, profile_id, "settings.edit_startup")
        if not isinstance(properties, dict):
            raise HTTPException(status_code=400, detail="設定格式錯誤")
        try:
            before_properties = ServerManager(profile["directory"]).get_server_properties()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        properties = {**before_properties, **properties}
        try:
            port = validated_server_port(properties.get("server-port", profile.get("server_port", 25565)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        properties["server-port"] = str(port)
        profile_values["server_port"] = port
        before_profile = {key: profile.get(key) for key in profile_values}
        backup_manager = BackupManager(profile["directory"])
        try:
            snapshot = backup_manager.create_settings_backup(profile)
            retention = db.backup_retention(profile_id)
            backup_manager.cleanup_settings_retention(retention["settings_retain_count"], retention["settings_max_age_days"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"設定安全快照遭安全檢查拒絕，未儲存設定：{exc}") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"設定安全快照建立失敗，未儲存設定：{exc}") from exc
        # Save only after the rollback snapshot exists.
        try:
            ServerManager(profile["directory"]).save_server_properties({str(k): str(v) for k, v in properties.items()})
            if profile_values:
                profiles.update(profile_id, profile_values)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        profile = profiles.get(profile_id)
        changes = {"properties": {key: {"from": before_properties.get(key), "to": str(value)} for key, value in properties.items() if before_properties.get(key) != str(value)},
                   "profile": {key: {"from": before_profile.get(key), "to": value} for key, value in profile_values.items() if before_profile.get(key) != value},
                   "snapshot": Path(snapshot).name}
        db.audit(user["user_id"], "update_settings", json.dumps(changes, ensure_ascii=False), server_id=profile_id)
        return {"ok": True, "profile": profile, "properties": ServerManager(profile["directory"]).get_server_properties()}

    @app.post("/api/servers/{profile_id}/java/auto")
    def auto_configure_java(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.edit_java")
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        required, minecraft_version = CoreAdapter.required_java_version(profile)
        try:
            job_id = java_runtime.start(profile_id, required, user["user_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job_id": job_id, "required_version": required, "minecraft_version": minecraft_version}

    @app.get("/api/servers/{profile_id}/java/auto/{job_id}")
    def auto_configure_java_status(profile_id: int, job_id: str, request: Request):
        require_server_permission(request, profile_id, "settings.view")
        job = java_runtime.get(job_id, profile_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到 Java 設定工作")
        return {"job": job}

    @app.get("/api/servers/{profile_id}/core-update")
    def latest_core_update(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "settings.view")
        return {"job": core_updates.latest(profile_id)}

    @app.get("/api/servers/{profile_id}/core-update/{job_id}")
    def core_update_status(profile_id: int, job_id: str, request: Request):
        user = require_user(request)
        if not profiles.get(profile_id):
            raise HTTPException(status_code=404, detail="找不到伺服器")
        if not (db.has_server_permission(user, profile_id, "settings.view")
                or db.has_server_permission(user, profile_id, "server.start")):
            raise HTTPException(status_code=403, detail="缺少檢視伺服器或啟動伺服器權限")
        job = core_updates.get(job_id, profile_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到核心更新工作")
        return {"job": job}

    @app.post("/api/servers/{profile_id}/core-update")
    async def start_core_update(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "settings.edit_startup")
        check_csrf(request, user)
        payload = await request.json()
        try:
            job_id = core_updates.start(profile_id, payload.get("core_type"), payload.get("version"), user["user_id"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "start_core_update", f"{profile_id}:{payload.get('core_type')}:{payload.get('version')}", server_id=profile_id)
        return {"job_id": job_id, "job": core_updates.get(job_id, profile_id)}

    @app.get("/server/{profile_id}/players", response_class=HTMLResponse)
    def players_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "players.view")
        profile = profiles.get(profile_id)
        capabilities={key:db.has_server_permission(user,profile_id,key) for key in ("players.kick","players.ban","players.unban","players.op","players.deop","players.whitelist")}
        return render(request, "players.html", profile=profile, inventory=app.state.players.inventory(profile_id), csrf=user["csrf_token"], capabilities=capabilities)

    @app.get("/api/servers/{profile_id}/players")
    def players_api(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "players.view")
        return app.state.players.inventory(profile_id)

    @app.get("/api/servers/{profile_id}/players/history")
    def players_history(profile_id: int, request: Request, q: str = "", limit: int = 100, offset: int = 0):
        require_server_permission(request, profile_id, "players.view")
        app.state.players.list(profile_id)
        return {**db.player_history(profile_id, q, limit, offset), "events": db.player_events(profile_id, limit=min(max(limit,1),500))}

    @app.get("/api/servers/{profile_id}/players/export.csv")
    def players_export(profile_id: int, request: Request, q: str = ""):
        require_server_permission(request, profile_id, "players.view")
        app.state.players.list(profile_id)
        rows=db.player_history(profile_id,q,500,0)["players"]
        output=io.StringIO(newline="")
        writer=csv.writer(output);writer.writerow(["玩家名稱","UUID","首次記錄","最後在線","最後登入","在線中","累計在線秒數"])
        def safe(value):
            value=str(value or "")
            return "'"+value if value.startswith(("=","+","-","@")) else value
        for item in rows: writer.writerow([safe(item["name"]),safe(item["uuid"]),item["first_seen"],item["last_seen"],item["last_login"],item["is_online"],round(item["total_online_seconds"])])
        return Response(output.getvalue(),media_type="text/csv; charset=utf-8",headers={"content-disposition":f'attachment; filename="players-{profile_id}.csv"'})

    @app.post("/api/servers/{profile_id}/players/{action}")
    async def player_action(profile_id: int, action: str, request: Request):
        user = require_server_permission(request, profile_id, "players.view")
        check_csrf(request, user); payload=await request.json(); player=str(payload.get("player", "")).strip()
        if action in {"kick","ban","pardon","op","deop","whitelist_add","whitelist_remove","ban_ip","pardon_ip"}:
            perm = "players.unban" if action in {"pardon","pardon_ip"} else "players.ban" if action=="ban_ip" else "players.whitelist" if action.startswith("whitelist") else f"players.{action}"
            require_server_permission(request, profile_id, perm)
        if action in {"ban_ip","pardon_ip"}: ok,message=app.state.players.ip_command(profile_id,action,player)
        else: ok,message=app.state.players.command(profile_id,action,player)
        if ok: db.audit(user["user_id"],"player_"+action,f"{profile_id}:{player}",server_id=profile_id)
        return {"ok":ok,"message":message}

    @app.get("/server/{profile_id}/schedules", response_class=HTMLResponse)
    def schedules_page(profile_id: int, request: Request):
        user=require_server_permission(request,profile_id,"schedule.view"); return render(request,"schedules.html",profile=profiles.get(profile_id),schedules=app.state.scheduler.list(profile_id),csrf=user["csrf_token"])

    @app.get("/api/servers/{profile_id}/schedules")
    def schedules_api(profile_id:int,request:Request): require_server_permission(request,profile_id,"schedule.view"); return {"schedules":app.state.scheduler.list(profile_id)}

    @app.get("/api/servers/{profile_id}/schedule-runs")
    def schedule_runs_api(profile_id:int,request:Request,limit:int=100):
        require_server_permission(request,profile_id,"schedule.view")
        with db._connect() as conn:
            rows=conn.execute("SELECT * FROM schedule_runs WHERE server_id=? ORDER BY id DESC LIMIT ?",(profile_id,min(max(limit,1),500))).fetchall()
        return {"runs":[dict(row) for row in rows]}

    @app.post("/api/servers/{profile_id}/schedules")
    async def create_schedule(profile_id:int,request:Request):
        user=require_server_permission(request,profile_id,"schedule.create");check_csrf(request,user);p=await request.json()
        try:
            item=app.state.scheduler.create(profile_id,str(p.get("name","排程")),str(p.get("action","backup")),int(p.get("interval_seconds",3600)),schedule_type=p.get("schedule_type","interval"),weekdays=p.get("weekdays",[]),run_time=p.get("run_time","00:00"),retry_count=p.get("retry_count",0),retry_delay_seconds=p.get("retry_delay_seconds",60),ready_timeout_seconds=p.get("ready_timeout_seconds",300),notify_webhook=p.get("notify_webhook",""))
        except (ValueError,TypeError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
        db.audit(user["user_id"],"create_schedule",f"{profile_id}:{item['name']}",server_id=profile_id)
        return {"ok":True,"schedule":item}

    @app.put("/api/schedules/{schedule_id}")
    async def update_schedule(schedule_id:int,request:Request):
        item=app.state.scheduler.get(schedule_id)
        if not item:raise HTTPException(status_code=404,detail="找不到排程")
        user=require_server_permission(request,item["server_id"],"schedule.edit");check_csrf(request,user);p=await request.json()
        try:updated=app.state.scheduler.update(schedule_id,p)
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
        db.audit(user["user_id"],"update_schedule",f"{schedule_id}:{updated['name']}",server_id=item["server_id"])
        return {"ok":True,"schedule":{k:v for k,v in updated.items() if k!="notify_webhook"}}

    @app.post("/api/schedules/{schedule_id}/run")
    def run_schedule_now(schedule_id:int,request:Request):
        item=app.state.scheduler.get(schedule_id)
        if not item:raise HTTPException(status_code=404,detail="找不到排程")
        user=require_server_permission(request,item["server_id"],"schedule.run");check_csrf(request,user)
        result=app.state.scheduler.run_now(schedule_id)
        db.audit(user["user_id"],"run_schedule",f"{schedule_id}:{result['result']}",server_id=item["server_id"])
        return result

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

    @app.get("/api/servers/{profile_id}/backups/settings-retention")
    def get_settings_backup_retention(profile_id: int, request: Request):
        require_server_permission(request, profile_id, "backups.view")
        settings = db.backup_retention(profile_id)
        return {"retain_count": settings["settings_retain_count"], "max_age_days": settings["settings_max_age_days"]}

    @app.put("/api/servers/{profile_id}/backups/settings-retention")
    async def put_settings_backup_retention(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.create")
        check_csrf(request, user)
        payload = await request.json()
        try:
            settings = db.set_settings_backup_retention(profile_id, payload.get("retain_count", 50), payload.get("max_age_days", 180))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"retain_count": settings["settings_retain_count"], "max_age_days": settings["settings_max_age_days"]}

    @app.post("/api/servers/{profile_id}/backups/settings-cleanup")
    def cleanup_settings_backups(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "backups.delete")
        check_csrf(request, user)
        settings = db.backup_retention(profile_id)
        profile = profiles.get(profile_id)
        removed = BackupManager(profile["directory"]).cleanup_settings_retention(settings["settings_retain_count"], settings["settings_max_age_days"])
        db.audit(user["user_id"], "cleanup_settings_backups", f"{profile_id}:removed={len(removed)}", server_id=profile_id)
        return {"removed": [Path(path).name for path in removed]}

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
        settings_removed = BackupManager(profile["directory"]).cleanup_settings_retention(settings["settings_retain_count"], settings["settings_max_age_days"])
        db.audit(user["user_id"], "cleanup_backups", f"{profile_id}:removed={len(removed)}", server_id=profile_id)
        return {"removed": [Path(path).name for path in removed], "settings_removed": [Path(path).name for path in settings_removed]}

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
        settings_dir=Path(manager.backup_directory)/"settings"
        settings_items=[{"name":p.name,"size":p.stat().st_size,"modified_at":p.stat().st_mtime} for p in sorted(settings_dir.glob("*.zip"),key=lambda p:p.stat().st_mtime,reverse=True)] if settings_dir.exists() else []
        return render(request,"backups_v2.html",profile=profile,backups=items,settings_backups=settings_items,csrf=user["csrf_token"])

    @app.get("/api/servers/{profile_id}/backups/{name}/download")
    def download_backup(profile_id:int,name:str,request:Request):
        require_server_permission(request,profile_id,"backups.download");profile=profiles.get(profile_id);root=(Path(profile["directory"])/"backups").resolve();target=(root/name).resolve()
        if target.parent!=root or not target.is_file(): raise HTTPException(status_code=404,detail="找不到備份")
        return FileResponse(target,filename=target.name)

    @app.delete("/api/servers/{profile_id}/backups/{name}")
    def delete_backup(profile_id:int,name:str,request:Request):
        user=require_server_permission(request,profile_id,"backups.delete");check_csrf(request,user);profile=profiles.get(profile_id);root=(Path(profile["directory"])/"backups").resolve();target=(root/name).resolve()
        if target.parent!=root or target.suffix.lower()!='.zip': raise HTTPException(status_code=400,detail="無效的備份")
        target.unlink(missing_ok=False);db.audit(user["user_id"],"delete_backup",f"{profile_id}:{name}",server_id=profile_id);return {"ok":True}

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
        try:
            task_id = app.state.backup_jobs.start(profile_id, profile["directory"], str(payload.get("label", "manual")), settings["retain_count"], settings["max_age_days"], str(payload.get("scope", "all")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "start_backup", f"{profile_id}:task={task_id}", server_id=profile_id)
        return {"ok": True, "task_id": task_id, "status": "queued"}

    @app.get("/api/servers/{profile_id}/backups/jobs")
    def list_backup_jobs(profile_id: int, request: Request, limit: int = 100):
        require_server_permission(request, profile_id, "backups.view")
        return {"jobs": db.backup_jobs(profile_id, limit)}

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

    @app.get("/server/{profile_id}/files", response_class=HTMLResponse)
    def files_page(profile_id: int, request: Request):
        user = require_server_permission(request, profile_id, "files.view")
        permissions = {key: db.has_server_permission(user, profile_id, key) for key in ("files.upload", "files.download", "files.create_folder", "files.edit", "files.rename", "files.delete", "files.copy", "files.move", "files.compress", "files.extract")}
        return render(request, "files.html", profile=profiles.get(profile_id), csrf=user["csrf_token"], permissions=permissions)

    @app.get("/api/servers/{profile_id}/files/search")
    def search_files(profile_id: int, request: Request, q: str, path: str = "", content: bool = False, limit: int = 500):
        require_server_permission(request, profile_id, "files.view")
        try:
            return {"results": files.search(profile_id, q, path, content, limit)}
        except (PermissionError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
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
    async def upload_file(profile_id: int, request: Request, file: UploadFile = File(...), path: str = Form(""), conflict: str = Form("error")):
        user = require_server_permission(request, profile_id, "files.upload")
        check_csrf(request, user)
        relative = str(Path(path) / (file.filename or "upload.bin"))
        try:
            target = files.write_upload(profile_id, relative, file, conflict=conflict)
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "upload_file", f"{profile_id}:{relative}", server_id=profile_id)
        return {"ok": True, "path": target.name}

    @app.post("/api/servers/{profile_id}/files/upload/chunk")
    async def upload_file_chunk(profile_id: int, request: Request, upload_id: str = Query(...), path: str = Query(...), offset: int = Query(0), final: bool = Query(False), conflict: str = Query("error")):
        user = require_server_permission(request, profile_id, "files.upload")
        check_csrf(request, user)
        try:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_UPLOAD_CHUNK_BYTES:
                raise HTTPException(status_code=413, detail="單次上傳分段不可超過 8 MiB")
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_UPLOAD_CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail="單次上傳分段不可超過 8 MiB")
                body.extend(chunk)
            result = files.upload_chunk(profile_id, upload_id, path, offset, body, final, conflict)
        except (PermissionError, ValueError, OSError, FileExistsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.get("complete"):
            db.audit(user["user_id"], "upload_file", f"{profile_id}:{result['path']}", server_id=profile_id)
        return result

    @app.get("/api/servers/{profile_id}/files/upload/{upload_id}")
    def upload_file_status(profile_id: int, upload_id: str, request: Request):
        require_server_permission(request, profile_id, "files.upload")
        return files.upload_status(profile_id, upload_id)

    @app.delete("/api/servers/{profile_id}/files/upload/{upload_id}")
    def cancel_file_upload(profile_id: int, upload_id: str, request: Request):
        user = require_server_permission(request, profile_id, "files.upload")
        check_csrf(request, user)
        files.cancel_upload(profile_id, upload_id)
        db.audit(user["user_id"], "cancel_upload_file", f"{profile_id}:{upload_id}", server_id=profile_id)
        return {"ok": True}

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
        path_value = str(payload.get("path", ""))
        normalized_parts = [part for part in path_value.replace("\\", "/").split("/") if part not in {"", "."}]
        is_root_properties = len(normalized_parts) == 1 and normalized_parts[0] == "server.properties"
        content = str(payload.get("content", ""))
        port = None
        if is_root_properties:
            parsed = {}
            for line in content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key, value = stripped.split("=", 1)
                    parsed[key] = value
            try:
                port = validated_server_port(parsed.get("server-port", 25565))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            files.write_text(profile_id, path_value, content)
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if is_root_properties:
            profiles.update(profile_id, {"server_port": port})
        db.audit(user["user_id"], "edit_file", f"{profile_id}:{payload.get('path', '')}", server_id=profile_id)
        return {"ok": True}

    @app.get("/api/servers/{profile_id}/files/versions")
    def file_versions(profile_id:int,request:Request,path:str):
        require_server_permission(request,profile_id,"files.view")
        try:return files.versions(profile_id,path)
        except (PermissionError,FileNotFoundError,ValueError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc

    @app.post("/api/servers/{profile_id}/files/versions/restore")
    async def restore_file_version(profile_id:int,request:Request):
        user=require_server_permission(request,profile_id,"files.edit");check_csrf(request,user);payload=await request.json()
        try:result=files.restore_version(profile_id,payload.get("path",""),payload.get("version_id",""))
        except (PermissionError,FileNotFoundError,ValueError,OSError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
        db.audit(user["user_id"],"restore_file_version",f"{profile_id}:{result['path']}:{result['restored_version']}",server_id=profile_id)
        return {"ok":True,**result}

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
                target = files.copy_or_move(profile_id, payload.get("source", ""), payload.get("destination", ""), operation == "move", payload.get("conflict", "error"))
            elif operation == "zip":
                target = files.zip_folder(profile_id, payload.get("source", ""), payload.get("destination", "archive.zip"), payload.get("conflict", "error"))
            elif operation == "unzip":
                target = files.unzip(profile_id, payload.get("source", ""), payload.get("destination", ""), payload.get("conflict", "error"))
            else:
                raise ValueError("未知的檔案操作")
        except (PermissionError, ValueError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], f"file_{operation}", f"{profile_id}:{payload}", server_id=profile_id)
        return {"ok": True, "path": str(target)}

    @app.post("/api/servers/{profile_id}/files/batch/{operation}")
    async def batch_file_operation(profile_id: int, operation: str, request: Request):
        permission = {"delete": "files.delete", "copy": "files.copy", "move": "files.move"}.get(operation)
        if not permission:
            raise HTTPException(status_code=404, detail="未知的批次檔案操作")
        user = require_server_permission(request, profile_id, permission)
        check_csrf(request, user)
        payload = await request.json()
        try:
            if operation == "delete":
                result = {"count": files.delete_many(profile_id, payload.get("paths", []))}
            else:
                paths = files.batch_copy_move(profile_id, payload.get("paths", []), payload.get("destination", ""), operation == "move", payload.get("conflict", "rename"))
                result = {"paths": paths, "count": len(paths)}
        except (PermissionError, ValueError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], f"files_batch_{operation}", f"{profile_id}:{payload}", server_id=profile_id)
        return {"ok": True, **result}

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
        return render(request, "admin_users.html", users=db.list_users(), csrf=user["csrf_token"], error=None, role_labels={"admin": "管理員", "operator": "操作者", "viewer": "檢視者"})

    @app.post("/admin/users")
    def create_managed_user(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form(...), csrf: str = Form(...)):
        user = require_user(request, {"admin"})
        if csrf != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        try:
            new_id = db.create_user(username, password, role)
            db.audit(user["user_id"], "create_user", username)
        except Exception as exc:
            return render(request, "admin_users.html", users=db.list_users(), csrf=user["csrf_token"], error=str(exc), role_labels={"admin": "管理員", "operator": "操作者", "viewer": "檢視者"})
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/edit")
    def edit_managed_user(user_id: int, request: Request, username: str = Form(...), password: str = Form(""), role: str = Form(...), csrf: str = Form(...)):
        admin = require_user(request, {"admin"})
        if csrf != admin["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        if role not in ROLES:
            raise HTTPException(status_code=400, detail="角色選擇無效")
        try:
            old = next((entry for entry in db.list_users() if entry["id"] == user_id), None)
            if not old:
                raise ValueError("找不到使用者")
            db.update_user(user_id, username, password, role)
            db.audit(admin["user_id"], "update_user", f"id={user_id},username={username},password_changed={bool(password)},role={role}")
            if password and user_id == admin["user_id"]:
                response = RedirectResponse("/login", status_code=303)
                response.delete_cookie("mcm_session")
                response.delete_cookie("mcm_csrf")
                return response
        except Exception as exc:
            return render(request, "admin_users.html", users=db.list_users(), csrf=admin["csrf_token"], error=str(exc), role_labels={"admin": "管理員", "operator": "操作者", "viewer": "檢視者"})
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/delete")
    def delete_managed_user(user_id: int, request: Request, csrf: str = Form(...)):
        admin = require_user(request, {"admin"})
        if csrf != admin["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")
        if user_id == admin["user_id"]:
            raise HTTPException(status_code=400, detail="不能刪除目前登入中的帳號")
        entry = next((item for item in db.list_users() if item["id"] == user_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="找不到使用者")
        try:
            db.delete_user(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(admin["user_id"], "delete_user", f"id={user_id},username={entry['username']}")
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/admin/users/{user_id}/permissions", response_class=HTMLResponse)
    def permissions_page(user_id: int, request: Request):
        admin = require_user(request, {"admin"})
        matches = [item for item in db.list_users() if item["id"] == user_id]
        if not matches:
            raise HTTPException(status_code=404, detail="找不到使用者")
        server_rows = [{"profile": profile, "selected": db.get_server_permissions(user_id, profile["id"])} for profile in profiles.list()]
        return render(request, "permissions.html", managed_user=matches[0], servers=server_rows, groups=PERMISSION_GROUPS, labels=PERMISSION_LABELS, role_presets={key: sorted(value) for key, value in ROLE_DEFAULTS.items()}, csrf=admin["csrf_token"])

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
        if not any(item["id"] == user_id for item in db.list_users()):
            raise HTTPException(status_code=404, detail="找不到使用者")
        if not any(item["id"] == server_id for item in profiles.list()):
            raise HTTPException(status_code=404, detail="找不到伺服器")
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
    async def server_action(profile_id: int, action: str, request: Request):
        permission = {"start": "server.start", "stop": "server.stop", "force-stop": "server.force_stop"}.get(action)
        if not permission:
            raise HTTPException(status_code=404, detail="未知的操作")
        user = require_server_permission(request, profile_id, permission)
        check_csrf(request, user)
        payload = {}
        if action == "start" and request.headers.get("content-type", "").startswith(
            "application/json"
        ):
            try:
                payload = await request.json()
            except (ValueError, TypeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        accept_eula = payload.get("accept_eula") is True
        actions = {
            "start": lambda value: multi.start(value, accept_eula=accept_eula),
            "stop": multi.stop,
            "force-stop": lambda value: multi.stop(value, True),
        }
        ok, message = actions[action](profile_id)
        if action == "start" and not ok and message == EULA_REQUIRED_MESSAGE:
            return {
                "ok": False,
                "eula_required": True,
                "message": "Minecraft EULA 尚未同意。請先閱讀官方條款；只有確認同意後才可繼續。",
            }
        if action == "start" and not ok and message == "找不到伺服器啟動檔，請先上傳或安裝核心":
            profile = profiles.get(profile_id)
            version = str((profile or {}).get("minecraft_version") or "").strip()
            if not version:
                message = "伺服器尚未設定 Minecraft 版本；請先在伺服器設定填入版本，再按啟動即可自動安裝核心。"
            elif not db.has_server_permission(user, profile_id, "settings.edit_startup"):
                message = "目前帳號沒有核心安裝權限；請管理員授予伺服器啟動設定權限，或由管理員執行啟動。"
            else:
                try:
                    job_id = core_updates.start(profile_id, profile["core_type"], version, user["user_id"])
                    db.audit(user["user_id"], "auto_install_core", f"{profile_id}:{profile['core_type']}:{version}", server_id=profile_id)
                    return {
                        "ok": False,
                        "core_install_job_id": job_id,
                        "message": f"找不到核心，已自動開始安裝 {profile['core_type']} {version}；安裝完成後會自動啟動伺服器。",
                    }
                except ValueError as exc:
                    message = str(exc)
        db.audit(user["user_id"], f"server_{action}", str(profile_id), server_id=profile_id)
        required_match = re.search(r"需要 Java (\d+)", message) if action == "start" and not ok else None
        if required_match and db.has_server_permission(user, profile_id, "settings.edit_java"):
            required = int(required_match.group(1))
            try:
                job_id = java_runtime.start(profile_id, required, user["user_id"], start_after=True)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": False, "java_setup_job_id": job_id,
                    "message": f"{message} 正在自動搜尋／下載相容 Java {required}，完成後重試啟動。"}
        return {"ok": ok, "message": message}

    @app.websocket("/ws/logs")
    async def logs_socket(websocket: WebSocket):
        expected_scheme = "https" if websocket.url.scheme == "wss" else "http"
        if not same_origin(websocket.headers, expected_scheme, websocket.url.netloc):
            await websocket.close(code=1008)
            return
        profile = current_legacy_profile()
        session = db.get_session(websocket.cookies.get("mcm_session"))
        if not profile or not session or not db.has_server_permission(session, profile["id"], "console.view"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            last = len(app.state.bridge.logs())
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                session = db.get_session(session["id"])
                profile = current_legacy_profile()
                if not profile or not session or not db.has_server_permission(session, profile["id"], "console.view"):
                    await websocket.close(code=1008)
                    return
                logs = app.state.bridge.logs()
                if len(logs) > last:
                    for line in logs[last:]:
                        await websocket.send_json({"line": line})
                    last = len(logs)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/servers")
    async def servers_socket(websocket: WebSocket):
        expected_scheme = "https" if websocket.url.scheme == "wss" else "http"
        if not same_origin(websocket.headers, expected_scheme, websocket.url.netloc):
            await websocket.close(code=1008)
            return
        session = db.get_session(websocket.cookies.get("mcm_session"))
        if not session:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                session = db.get_session(session["id"])
                if not session:
                    await websocket.close(code=1008)
                    return
                snapshot = visible_servers(session)
                message = {"type": "servers", "servers": snapshot, "timestamp": asyncio.get_running_loop().time()}
                if session["role"] == "admin":
                    message["node_resources"] = app.state.node_resources.snapshot(profiles.list())
                await websocket.send_json(message)
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

    return app
