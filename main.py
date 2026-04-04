import threading
import subprocess
import os
import sys
import json
import zipfile
import shutil
import re
import requests
from tkinter import messagebox, filedialog

import ttkbootstrap as tk
from ttkbootstrap import ttk

from server_manager import ServerManager
from ui_components import MainAppWindow, ServerSettingsWindow, AboutWindow

class ApplicationController:
    def __init__(self, root):
        self.root = root
        self.app_directory = self.get_application_path()
        self.config_path = os.path.join(self.app_directory, "config.json")
        config = self.load_config()
        self.server_directory = config.get("server_path", os.path.join(self.app_directory, "server"))
        self.server_manager = ServerManager(self.server_directory)
        self.root.core_combo.bind("<<ComboboxSelected>>", self.on_core_selected)
        self.root.download_button.config(command=self.on_download_button_click)
        self.root.settings_button.config(command=self.open_settings_window)
        self.root.start_button.config(command=self.start_server)
        self.root.stop_button.config(command=self.stop_server)
        self.root.change_dir_button.config(command=self.change_server_directory)
        self.root.send_command_button.config(command=self.send_command)
        self.root.command_input.bind("<Return>", self.send_command)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.about_button.config(command=self.open_about_window)
        self.java_executable_path = "java"
        self.embedded_java_path = os.path.join(self.app_directory, "jdk-17", "bin", "java.exe")
        self.server_process = None
        self.playit_process = None
        if getattr(sys, 'frozen', False):
            self.playit_executable_path = os.path.join(sys._MEIPASS, "playit.exe")
        else:
            self.playit_executable_path = os.path.join(self.app_directory, "playit.exe")
        self.root.after(100, self.initialize)
    
    def start_indeterminate_progress(self, speed=10):
        self.root.progress_bar.config(mode="indeterminate")
        self.root.progress_bar.start(speed)

    def stop_and_reset_progress(self, value=0):
        self.root.progress_bar.stop()
        self.root.progress_bar.config(mode="determinate")
        self.root.progress_bar["value"] = value

    def update_progress(self, value): 
        if self.root.progress_bar.cget("mode") == "indeterminate":
            self.root.progress_bar.stop()
            self.root.progress_bar.config(mode="determinate")
        self.root.progress_bar["value"] = value

    def open_about_window(self):
        AboutWindow(self.root)

    def start_playit_tunnel(self):
        if not os.path.exists(self.playit_executable_path):
            self.log("錯誤：找不到捆綁的 playit.exe 檔案！", "error")
            messagebox.showerror("內部錯誤", "找不到 playit.exe，請確認程式打包是否正確，\n且已包含 playit.exe 檔案。")
            self.root.playit_enabled.set(False)
            return

        self.log("正在新視窗中啟動 Playit.gg...", "info")
        self.root.playit_address_label.config(text="請查看彈出的視窗")
        command = f'start "Playit.gg" "{self.playit_executable_path}"'
        self.playit_process = subprocess.Popen(command, shell=True)

    def stop_playit_tunnel(self):
        self.log("正在關閉 Playit.gg 通道...", "warn")
        subprocess.run("taskkill /F /IM playit.exe /T", capture_output=True, text=True)
        self.playit_process = None
        self.root.playit_address_label.config(text="已中斷連線")
        
    def get_application_path(self):
        if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
        else: return os.path.dirname(os.path.abspath(__file__))

    def initialize(self):
        self.log("管理器啟動...", "info")
        self.root.path_label.config(text=self.server_directory)
        self.check_and_prepare_java()
        self.populate_core_selector()
        self.check_existing_server()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
            except json.JSONDecodeError: return {}
        return {}

    def save_config(self, config):
        with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(config, f, indent=4)

    def change_server_directory(self):
        new_path = filedialog.askdirectory(title="請選擇新的伺服器檔案位置")
        if new_path:
            self.log(f"伺服器路徑已更改為: {new_path}", "success")
            self.server_directory = new_path
            config = self.load_config()
            config["server_path"] = new_path
            self.save_config(config)
            self.root.path_label.config(text=new_path)
            self.server_manager = ServerManager(new_path)
            self.check_existing_server()
            messagebox.showinfo("成功", f"伺服器路徑已更新！")

    def send_command(self, event=None):
        command = self.root.command_input.get()
        if command and self.server_process and self.server_process.poll() is None:
            self.log(f"> {command}", "normal")
            try: 
                self.server_process.stdin.write(command + "\n")
                self.server_process.stdin.flush()
                self.root.command_input.delete(0, "end")
            except Exception as e: 
                self.log(f"發送指令失敗: {e}", "error")

    def log(self, message, tag_override=None):
        self.root.console_output.config(state="normal")
        
        tag = "normal"
        if tag_override:
            tag = tag_override
        else:
            if "INFO" in message: tag = "info"
            elif "WARN" in message: tag = "warn"
            elif "ERROR" in message or "Exception" in message: tag = "error"
            elif "Done" in message or "✅" in message: tag = "success"

        self.root.console_output.insert("end", message + "\n", tag)
        self.root.console_output.see("end")
        self.root.console_output.config(state="disabled")

    def set_status(self, message): 
        self.root.status_label.config(text=f"狀態：{message}")

    def populate_core_selector(self):
        cores = self.server_manager.get_available_cores()
        self.root.core_combo["values"] = cores
        if cores: 
            self.root.core_combo.set(cores[0])
            self.on_core_selected(None)

    def on_core_selected(self, event):
        selected_core = self.root.core_combo.get()
        self.set_status(f"正在獲取 {selected_core} 的版本列表...")
        self.root.version_combo.set("載入中...")
        
        self.start_indeterminate_progress()
        
        def _fetch_versions():
            versions = self.server_manager.get_core_versions(selected_core)
            def _update_ui():
                self.stop_and_reset_progress()
                self.root.version_combo["values"] = versions
                if versions:
                    if selected_core == "Paper": self.root.version_combo.set(versions[-1])
                    else: self.root.version_combo.set(versions[0])
                    self.set_status("請選擇版本並下載")
                else: 
                    self.root.version_combo.set("無可用版本")
                    self.set_status("獲取版本列表失敗")
            self.root.after(0, _update_ui)
        threading.Thread(target=_fetch_versions, daemon=True).start()

    def on_download_button_click(self):
        core = self.root.core_combo.get()
        version = self.root.version_combo.get()
        if not core or not version or "載入中" in version: 
            messagebox.showerror("錯誤", "請先選擇有效的伺服器核心與版本。")
            return
            
        existing_jars = [f for f in os.listdir(self.server_manager.server_directory) if f.endswith('.jar') and 'installer' not in f]
        if existing_jars:
            if not messagebox.askyesno("替換核心", f"偵測到資料夾內已有伺服器核心。\n如果你想升級或更換版本，程式將會自動刪除舊核心 ({existing_jars[0]}) 並下載新核心。\n\n(放心，你的地圖與設定檔不會受到影響)\n\n確定要繼續替換嗎？"):
                return
            for old_jar in existing_jars:
                try:
                    os.remove(os.path.join(self.server_manager.server_directory, old_jar))
                except Exception as e:
                    messagebox.showerror("錯誤", f"無法刪除舊核心: {e}\n請確認伺服器已經完全關閉！")
                    return
            self.log("已成功清理舊版本伺服器核心。", "warn")

        self.set_status(f"準備下載 {core} {version}...")
        self.root.download_button.config(state="disabled")
        
        self.start_indeterminate_progress()
        
        def _download_worker():
            filepath, message = self.server_manager.download_server(core, version, self._progress_callback_from_thread)
            def _update_ui():
                self.stop_and_reset_progress(100 if filepath else 0)
                self.log(message, "info")
                self.set_status(message)
                self.root.download_button.config(state="normal")
                if filepath:
                    if "forge" in core.lower() and "installer" in filepath: 
                        self.handle_forge_installation(filepath)
                    else: 
                        self.setup_eula_and_finish()
                else: 
                    messagebox.showerror("下載失敗", message)
            self.root.after(0, _update_ui)
        threading.Thread(target=_download_worker, daemon=True).start()

    def _progress_callback_from_thread(self, progress): 
        self.root.after(0, self.update_progress, progress)

    def handle_forge_installation(self, installer_path):
        self.set_status("Forge 需要安裝...")
        self.log("請在彈出的 Forge 安裝程式中，點擊 'Install Server'。", "warn")
        install_command = [self.java_executable_path, "-jar", installer_path]
        def _run_installer():
            try: 
                subprocess.run(install_command, cwd=self.server_manager.server_directory, check=True)
                self.root.after(0, self.setup_eula_and_finish)
            except Exception as e: 
                self.root.after(0, lambda: messagebox.showerror("錯誤", f"Forge 安裝失敗: {e}"))
                self.root.after(0, lambda: self.set_status("Forge 安裝失敗"))
        threading.Thread(target=_run_installer, daemon=True).start()

    def setup_eula_and_finish(self):
        self.set_status("正在設定 EULA...")
        eula_path = os.path.join(self.server_manager.server_directory, "eula.txt")
        try:
            with open(eula_path, "w") as f: f.write("eula=true\n")
            self.log("EULA 同意完成！", "success")
            self.set_status("伺服器準備就緒！")
            self.check_existing_server()
        except Exception as e: 
            self.log(f"寫入 EULA 失敗: {e}", "error")

    def check_existing_server(self):
        jar_files = [f for f in os.listdir(self.server_manager.server_directory) if f.endswith('.jar') and 'installer' not in f]
        eula_exists = os.path.exists(os.path.join(self.server_manager.server_directory, "eula.txt"))
        properties_exist = os.path.exists(self.server_manager.properties_path)
        if jar_files and eula_exists:
            self.log(f"偵測到伺服器核心: {jar_files[0]}", "info")
            self.set_status("伺服器已就緒")
            self.root.start_button.config(state="normal")
            if properties_exist: 
                self.root.settings_button.config(state="normal")
        else:
            self.root.start_button.config(state="disabled")
            self.root.settings_button.config(state="disabled")
            self.set_status("請先下載並安裝一個伺服器")

    def open_settings_window(self):
        properties = self.server_manager.get_server_properties()
        if not properties: 
            messagebox.showinfo("提示", "找不到 server.properties 檔案。\n請先成功啟動一次伺服器以自動生成。")
            return
        ServerSettingsWindow(self.root, properties, self.save_settings)

    def save_settings(self, new_properties):
        try: 
            self.server_manager.save_server_properties(new_properties)
            self.log("伺服器設定已儲存！", "success")
            messagebox.showinfo("成功", "伺服器設定已儲存！")
        except Exception as e: 
            messagebox.showerror("錯誤", f"儲存設定失敗: {e}")

    def start_server(self):
        if self.root.playit_enabled.get(): 
            self.start_playit_tunnel()
        jar_files = [f for f in os.listdir(self.server_manager.server_directory) if f.endswith('.jar') and 'installer' not in f]
        if not jar_files: 
            messagebox.showerror("錯誤", "找不到伺服器核心 .jar 檔案。")
            return
        server_jar_path = os.path.join(self.server_manager.server_directory, jar_files[0])
        ram = self.root.ram_spinbox.get()
        java_command = [self.java_executable_path, f"-Xmx{ram}M", f"-Xms{ram}M", "-jar", server_jar_path, "nogui"]
        self.log("---------- 伺服器正在啟動 ----------", "info")
        
        self.start_indeterminate_progress(15)
        
        self.server_process = subprocess.Popen(
            java_command, cwd=self.server_manager.server_directory, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace', creationflags=self.get_creation_flags())
        self.root.start_button.config(state="disabled")
        self.root.stop_button.config(state="normal")
        threading.Thread(target=self.read_server_output, daemon=True).start()

    def read_server_output(self):
        for line in iter(self.server_process.stdout.readline, ''):
            clean_line = line.strip()
            self.log(clean_line)
            if "Done" in clean_line: 
                self.root.after(0, self.server_started_ui_update)
                self.root.after(0, lambda: self.log("\n========================================", "success"))
                self.root.after(0, lambda: self.log("        ✅ 伺服器已成功啟動並準備就緒！", "success"))
                self.root.after(0, lambda: self.log("========================================\n", "success"))
        self.root.after(0, self.server_stopped_ui_update)

    def server_started_ui_update(self):
        self.stop_and_reset_progress(100)
        self.set_status("伺服器運行中！")
        self.root.settings_button.config(state="normal")
        self.root.command_input.config(state="normal")
        self.root.send_command_button.config(state="normal")

    def server_stopped_ui_update(self):
        self.stop_and_reset_progress(0)
        self.set_status("伺服器已停止")
        self.root.start_button.config(state="normal")
        self.root.stop_button.config(state="disabled")
        self.root.command_input.delete(0, "end")
        self.root.command_input.config(state="disabled")
        self.root.send_command_button.config(state="disabled")
        self.server_process = None

    def stop_server(self):
        if self.root.playit_enabled.get(): 
            self.stop_playit_tunnel()
        if self.server_process and self.server_process.poll() is None:
            self.log("正在向伺服器發送 'stop' 指令...", "warn")
            self.server_process.stdin.write("stop\n")
            self.server_process.stdin.flush()
        else: 
            messagebox.showinfo("提示", "伺服器未在運行中。")

    def on_closing(self):
        if self.playit_process:
            self.stop_playit_tunnel()
        if self.server_process and self.server_process.poll() is None:
            if messagebox.askyesno("確認", "伺服器仍在運行中，確定要關閉嗎？"): 
                self.stop_server()
                self.root.after(3000, self.root.destroy)
        else:
            self.root.destroy()

    def get_creation_flags(self): 
        return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def check_and_prepare_java(self):
        if os.path.exists(self.embedded_java_path): 
            self.java_executable_path = self.embedded_java_path
            self.log(f"偵測到內嵌 Java", "success")
            return
        try: 
            subprocess.run(["java", "-version"], check=True, capture_output=True, text=True, creationflags=self.get_creation_flags())
            self.log("偵測到系統 Java。", "success")
        except (FileNotFoundError, subprocess.CalledProcessError):
            if messagebox.askyesno("缺少 Java", "是否要自動下載 Java 17？"): 
                self.download_java()

    def download_java(self):
        def _worker():
            try:
                java_url = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk"
                zip_path = os.path.join(self.app_directory, "jdk-17-portable.zip")
                
                self.root.after(0, lambda: self.start_indeterminate_progress())
                self.log(f"正在下載 Java 17...", "info")
                self.root.after(0, lambda: self.set_status("正在下載 Java 17..."))
                
                with requests.get(java_url, stream=True, allow_redirects=True, timeout=30) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get('content-length', 0))
                    with open(zip_path, 'wb') as f:
                        bytes_downloaded = 0
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            if total_size > 0: 
                                self.root.after(0, self.update_progress, (bytes_downloaded / total_size) * 100)
                
                self.log("Java 下載完成...", "success")
                self.root.after(0, lambda: self.set_status("正在解壓縮 Java..."))
                
                self.root.after(0, lambda: self.start_indeterminate_progress())
                
                temp_extract_path = os.path.join(self.app_directory, "jdk_temp")
                with zipfile.ZipFile(zip_path, 'r') as zip_ref: 
                    zip_ref.extractall(temp_extract_path)
                extracted_folder = os.path.join(temp_extract_path, os.listdir(temp_extract_path)[0])
                final_jdk_path = os.path.join(self.app_directory, "jdk-17")
                if os.path.exists(final_jdk_path): 
                    shutil.rmtree(final_jdk_path)
                shutil.move(extracted_folder, final_jdk_path)
                os.remove(zip_path)
                shutil.rmtree(temp_extract_path)
                self.log("Java 環境已準備就緒！", "success")
                self.root.after(0, lambda: self.set_status("Java 環境已準備就緒！"))
                self.java_executable_path = self.embedded_java_path
            except Exception as e: 
                self.log(f"下載 Java 失敗: {e}", "error")
                self.root.after(0, lambda: messagebox.showerror("錯誤", f"下載 Java 失敗: {e}"))
            finally: 
                self.root.after(0, lambda: self.stop_and_reset_progress(0))
        threading.Thread(target=_worker, daemon=True).start()

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes
            myappid = 'yoyo.mcserver.manager.v152' 
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    root = MainAppWindow()
    app = ApplicationController(root)
    root.mainloop()