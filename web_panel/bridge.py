import threading
import time


class ServerBridge:
    """Thread-safe bridge around the existing desktop controller.

    The desktop controller remains the owner of Tk widgets. Web requests only
    call these methods; UI-affecting callbacks are scheduled through Tk's after().
    """

    def __init__(self, controller):
        self.controller = controller
        self._lock = threading.RLock()
        self._logs = []
        self._started_at = None

    def attach(self):
        original_log = self.controller.log

        def log_proxy(message, tag_override=None):
            with self._lock:
                self._logs.append(str(message))
                self._logs = self._logs[-500:]
            return original_log(message, tag_override)

        self.controller.log = log_proxy

    def status(self):
        process = self.controller.server_process
        running = bool(process and process.poll() is None)
        if running and self._started_at is None:
            self._started_at = time.time()
        if not running:
            self._started_at = None
        return {
            "running": running,
            "uptime_seconds": int(time.time() - self._started_at) if self._started_at else 0,
            "server_directory": self.controller.server_directory,
            "web_port": self.controller.web_panel_port,
        }

    def logs(self, limit=100):
        with self._lock:
            return self._logs[-max(1, min(limit, 500)):]

    def start(self):
        with self._lock:
            if self.controller.server_process and self.controller.server_process.poll() is None:
                return False, "伺服器已經在運行中"
            self.controller.root.after(0, self.controller.start_server)
            return True, "已送出啟動要求"

    def stop(self):
        with self._lock:
            if not self.controller.server_process or self.controller.server_process.poll() is not None:
                return False, "伺服器目前沒有運行"
            self.controller.root.after(0, self.controller.stop_server)
            return True, "已送出停止要求"

    def restart(self):
        ok, message = self.stop()
        if not ok:
            return self.start()
        self.controller.root.after(1500, self.controller.start_server)
        return True, "已送出重啟要求"

    def send_command(self, command):
        command = command.strip()
        if not command or len(command) > 512:
            return False, "指令不可為空或超過 512 個字元"
        process = self.controller.server_process
        if not process or process.poll() is not None:
            return False, "伺服器目前沒有運行"
        with self._lock:
            if not process.stdin:
                return False, "伺服器主控台輸入串流不可用"
            try:
                process.stdin.write(command + "\n")
                process.stdin.flush()
            except (OSError, BrokenPipeError) as exc:
                return False, f"指令發送失敗：{exc}"
        return True, "指令已送出"
