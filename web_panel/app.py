from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .database import WebDatabase


ROLES = {"admin", "operator", "viewer"}


def create_app(controller, database_path):
    app = FastAPI(title="Minecraft Server Manager Web Panel")
    db = WebDatabase(database_path)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.state.db = db
    app.state.bridge = controller.web_bridge

    def current_user(request: Request):
        session = db.get_session(request.cookies.get("mcm_session"))
        return session

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

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return render(request, "dashboard.html", status=app.state.bridge.status(), logs=app.state.bridge.logs())

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

    def check_csrf(request: Request, user):
        token = request.headers.get("x-csrf-token") or request.cookies.get("mcm_csrf")
        if not token or token != user["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF 驗證失敗")

    @app.get("/api/status")
    def api_status(request: Request):
        require_user(request)
        return app.state.bridge.status()

    @app.get("/api/logs")
    def api_logs(request: Request, limit: int = 100):
        require_user(request)
        return {"logs": app.state.bridge.logs(limit)}

    @app.post("/api/server/{action}")
    def api_server_action(action: str, request: Request):
        user = require_user(request, {"admin", "operator"})
        check_csrf(request, user)
        actions = {"start": app.state.bridge.start, "stop": app.state.bridge.stop, "restart": app.state.bridge.restart}
        if action not in actions:
            raise HTTPException(status_code=404, detail="未知的操作")
        ok, message = actions[action]()
        db.audit(user["user_id"], action, message)
        return {"ok": ok, "message": message}

    @app.post("/api/console")
    async def api_console(request: Request):
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
        except (ValueError, Exception) as exc:
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

    return app
