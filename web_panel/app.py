import asyncio
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backup_manager import BackupManager
from server_manager import ServerManager
from .database import WebDatabase
from .file_service import SafeFileService
from .multi_server import CORE_TYPES, MultiServerManager, ServerProfileStore

ROLES = {"admin", "operator", "viewer"}


def create_app(controller, database_path):
    app = FastAPI(title="Minecraft Server Manager Web Panel")
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

    def current_user(request: Request):
        return db.get_session(request.cookies.get("mcm_session"))

    def require_user(request: Request, roles=None):
        user = current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="請先登入")
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="權限不足")
        return user

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
        return render(request, "dashboard.html", status=app.state.bridge.status(), logs=app.state.bridge.logs(), servers=multi.statuses())

    @app.get("/server/{profile_id}", response_class=HTMLResponse)
    def server_page(profile_id: int, request: Request):
        require_user(request)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return render(request, "server.html", profile=profile, status=multi.status(profile_id), csrf=current_user(request)["csrf_token"])

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
        require_user(request)
        return {"servers": multi.statuses(), "core_types": sorted(CORE_TYPES)}

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
        db.audit(user["user_id"], "create_server", str(profile["id"]))
        return profile

    @app.get("/api/servers/{profile_id}")
    def get_server(profile_id: int, request: Request):
        require_user(request)
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
        db.audit(user["user_id"], "update_server", str(profile_id))
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

    @app.post("/api/servers/{profile_id}/{action}")
    def server_action(profile_id: int, action: str, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        actions = {"start": multi.start, "stop": multi.stop, "force-stop": lambda value: multi.stop(value, True)}
        if action not in actions:
            raise HTTPException(status_code=404, detail="未知的操作")
        ok, message = actions[action](profile_id)
        db.audit(user["user_id"], f"server_{action}", str(profile_id))
        return {"ok": ok, "message": message}

    @app.get("/api/servers/{profile_id}/logs")
    def server_logs(profile_id: int, request: Request, limit: int = 200):
        require_user(request)
        if not profiles.get(profile_id):
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return {"logs": multi.logs(profile_id, limit)}

    @app.post("/api/servers/{profile_id}/console")
    async def server_console(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        payload = await request.json()
        ok, message = multi.send_command(profile_id, str(payload.get("command", "")))
        db.audit(user["user_id"], "console_command", f"{profile_id}: {payload.get('command', '')}")
        return {"ok": ok, "message": message}

    @app.get("/api/servers/{profile_id}/settings")
    def server_settings(profile_id: int, request: Request):
        require_user(request)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        return {"profile": profile, "properties": ServerManager(profile["directory"]).get_server_properties()}

    @app.put("/api/servers/{profile_id}/settings")
    async def update_server_settings(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        payload = await request.json()
        properties = payload.get("properties", payload)
        if not isinstance(properties, dict):
            raise HTTPException(status_code=400, detail="設定格式錯誤")
        if "server-port" in properties:
            try:
                port = int(properties["server-port"])
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="伺服器連接埠必須介於 1 到 65535") from exc
        try:
            ServerManager(profile["directory"]).save_server_properties({str(k): str(v) for k, v in properties.items()})
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "update_settings", str(profile_id))
        return {"ok": True, "properties": ServerManager(profile["directory"]).get_server_properties()}

    @app.get("/api/servers/{profile_id}/backups")
    def list_backups(profile_id: int, request: Request):
        require_user(request)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        manager = BackupManager(profile["directory"])
        return {"backups": [{"name": Path(path).name, "size": Path(path).stat().st_size, "modified_at": Path(path).stat().st_mtime} for path in manager.list_backups()]}

    @app.post("/api/servers/{profile_id}/backups")
    async def create_backup(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        profile = profiles.get(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到伺服器")
        status = multi.status(profile_id)
        if status and status["running"]:
            raise HTTPException(status_code=409, detail="建立備份前建議先停止伺服器")
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        path = BackupManager(profile["directory"]).create_backup(str(payload.get("label", "manual")))
        db.audit(user["user_id"], "create_backup", f"{profile_id}:{Path(path).name}")
        return {"ok": True, "name": Path(path).name}

    @app.post("/api/servers/{profile_id}/backups/restore")
    async def restore_backup(profile_id: int, request: Request):
        user = require_user(request, {"admin"})
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
        try:
            path = BackupManager(profile["directory"]).restore_backup(str(backup))
        except (OSError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "restore_backup", f"{profile_id}:{Path(path).name}")
        return {"ok": True}

    @app.get("/api/servers/{profile_id}/files")
    def list_files(profile_id: int, request: Request, path: str = ""):
        require_user(request)
        try:
            return {"path": path, "items": files.list(profile_id, path)}
        except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/servers/{profile_id}/files/download")
    def download_file(profile_id: int, request: Request, path: str):
        require_user(request)
        try:
            target = files.resolve(profile_id, path)
        except (PermissionError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=400, detail="不是檔案")
        return FileResponse(target, filename=target.name)

    @app.post("/api/servers/{profile_id}/files/upload")
    async def upload_file(profile_id: int, request: Request, file: UploadFile = File(...), path: str = Form("")):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        relative = str(Path(path) / (file.filename or "upload.bin"))
        try:
            target = files.write_upload(profile_id, relative, file)
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "upload_file", f"{profile_id}:{relative}")
        return {"ok": True, "path": target.name}

    @app.post("/api/servers/{profile_id}/files/mkdir")
    async def make_directory(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.mkdir(profile_id, payload.get("path", ""))
        except (PermissionError, FileExistsError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/servers/{profile_id}/files/rename")
    async def rename_file(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        payload = await request.json()
        try:
            target = files.rename(profile_id, payload.get("path", ""), payload.get("new_name", ""))
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "path": target.name}

    @app.get("/api/servers/{profile_id}/files/text")
    def read_text_file(profile_id: int, request: Request, path: str):
        require_user(request)
        try:
            return {"path": path, "content": files.read_text(profile_id, path)}
        except (PermissionError, FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/servers/{profile_id}/files/text")
    async def write_text_file(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.write_text(profile_id, payload.get("path", ""), str(payload.get("content", "")))
        except (PermissionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "edit_file", f"{profile_id}:{payload.get('path', '')}")
        return {"ok": True}

    @app.post("/api/servers/{profile_id}/files/{operation}")
    async def file_operation(profile_id: int, operation: str, request: Request):
        user = require_user(request, {"admin", "operator"})
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
        db.audit(user["user_id"], f"file_{operation}", f"{profile_id}:{payload}")
        return {"ok": True, "path": str(target)}

    @app.delete("/api/servers/{profile_id}/files")
    async def delete_file(profile_id: int, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        payload = await request.json()
        try:
            files.delete(profile_id, payload.get("path", ""))
        except (PermissionError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(user["user_id"], "delete_file", f"{profile_id}:{payload.get('path', '')}")
        return {"ok": True}

    @app.post("/api/server/{action}")
    def legacy_server_action(action: str, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        actions = {"start": app.state.bridge.start, "stop": app.state.bridge.stop, "restart": app.state.bridge.restart}
        if action not in actions:
            raise HTTPException(status_code=404, detail="未知的操作")
        ok, message = actions[action]()
        db.audit(user["user_id"], action, message)
        return {"ok": ok, "message": message}

    @app.post("/api/console")
    async def legacy_console(request: Request):
        user = require_user(request, {"admin", "operator"})
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
                snapshot = multi.statuses()
                await websocket.send_json({"type": "servers", "servers": snapshot, "timestamp": asyncio.get_running_loop().time()})
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

    return app
