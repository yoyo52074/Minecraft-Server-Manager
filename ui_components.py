import ttkbootstrap as tk
from ttkbootstrap import ttk
from tkinter import messagebox, scrolledtext
import os
import sys
# 為了 AboutWindow，額外匯入標準的 tkinter
import tkinter as std_tk

class MainAppWindow(tk.Window):
    def __init__(self, themename="darkly"):
        super().__init__(themename=themename)
        self.title("Minecraft 伺服器架設工具 -Produced by yoyo")

        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.abspath(".")
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception as e:
            print(f"設定圖示時發生錯誤: {e}")

        self.geometry("850x700")
        self.minsize(800, 600)

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        main_frame.rowconfigure(2, weight=1)
        main_frame.columnconfigure(0, weight=1)

        top_frame = ttk.Frame(main_frame)
        top_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        top_frame.columnconfigure(0, weight=1)

        setup_frame = ttk.LabelFrame(top_frame, text="伺服器安裝與路徑", padding="10")
        setup_frame.grid(row=0, column=0, sticky="ew")
        setup_frame.columnconfigure(1, weight=1)

        ttk.Label(setup_frame, text="當前路徑:").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.path_label = ttk.Label(setup_frame, text="...", anchor="w", style="info.TLabel")
        self.path_label.grid(row=0, column=1, columnspan=2, padx=5, pady=2, sticky="ew")
        ttk.Label(setup_frame, text="伺服器核心:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.core_combo = ttk.Combobox(setup_frame, state="readonly")
        self.core_combo.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(setup_frame, text="Minecraft 版本:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.version_combo = ttk.Combobox(setup_frame, state="readonly")
        self.version_combo.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

        action_frame = ttk.Frame(top_frame)
        action_frame.grid(row=0, column=1, sticky="ns", padx=(5, 0))

        self.change_dir_button = ttk.Button(action_frame, text="更改伺服器路徑")
        self.change_dir_button.pack(padx=5, pady=5, fill=tk.X)
        self.download_button = ttk.Button(action_frame, text="下載/安裝伺服器", style="success.TButton")
        self.download_button.pack(padx=5, pady=5, fill=tk.X)
        self.settings_button = ttk.Button(action_frame, text="伺服器設定", state="disabled")
        self.settings_button.pack(padx=5, pady=5, fill=tk.X)
        self.about_button = ttk.Button(action_frame, text="關於", style="info.Outline.TButton")
        self.about_button.pack(padx=5, pady=5, fill=tk.X)

        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=1, column=0, sticky="ew", pady=5)
        self.status_label = ttk.Label(status_frame, text="狀態：請選擇核心與版本")
        self.status_label.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=5)

        console_area = ttk.Frame(main_frame)
        console_area.grid(row=2, column=0, sticky="nsew")
        console_area.rowconfigure(1, weight=1)
        console_area.columnconfigure(0, weight=1)

        control_frame = ttk.LabelFrame(console_area, text="伺服器控制", padding="10")
        control_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        server_buttons_frame = ttk.Frame(control_frame)
        server_buttons_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(server_buttons_frame, text="記憶體 (MB):").pack(side=tk.LEFT, padx=5)
        self.ram_spinbox = ttk.Spinbox(server_buttons_frame, from_=1024, to=16384, increment=1024, width=10)
        self.ram_spinbox.set("2048")
        self.ram_spinbox.pack(side=tk.LEFT, padx=5)
        self.start_button = ttk.Button(server_buttons_frame, text="▶ 啟動伺服器", state="disabled", style="primary.TButton")
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(server_buttons_frame, text="■ 停止伺服器", state="disabled", style="danger.TButton")
        self.stop_button.pack(side=tk.LEFT, padx=5)

        playit_frame = ttk.LabelFrame(control_frame, text="公開IP (Playit.gg)", padding=(10, 5))
        playit_frame.pack(side=tk.RIGHT, padx=(10, 0))
        self.playit_enabled = tk.BooleanVar()
        self.playit_checkbox = ttk.Checkbutton(playit_frame, text="啟用", variable=self.playit_enabled)
        self.playit_checkbox.pack(side=tk.LEFT)
        self.playit_address_label = ttk.Label(playit_frame, text="伺服器啟動後顯示", style="info.TLabel")
        self.playit_address_label.pack(side=tk.LEFT, padx=5)

        console_frame = ttk.LabelFrame(console_area, text="伺服器控制台", padding="10")
        console_frame.grid(row=1, column=0, sticky="nsew")
        console_frame.rowconfigure(0, weight=1)
        console_frame.columnconfigure(0, weight=1)
        self.console_output = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, state="disabled")
        self.console_output.grid(row=0, column=0, sticky="nsew")

        command_frame = ttk.Frame(console_area)
        command_frame.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        command_frame.columnconfigure(0, weight=1)
        self.command_input = ttk.Entry(command_frame, state="disabled")
        self.command_input.grid(row=0, column=0, sticky="ew")
        self.send_command_button = ttk.Button(command_frame, text="發送指令", state="disabled")
        self.send_command_button.grid(row=0, column=1, padx=(5, 0))

class ServerSettingsWindow(tk.Toplevel):
    def __init__(self, parent, properties_dict, save_callback):
        super().__init__(parent)
        self.title("伺服器設定 (server.properties)"); self.geometry("600x700"); self.transient(parent); self.grab_set()
        self.save_callback = save_callback; self.properties = properties_dict; self.entries = {}
        canvas = tk.Canvas(self); scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas, padding=10); self.scrollable_frame.columnconfigure(1, weight=1)
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw"); canvas.configure(yscrollcommand=scrollbar.set)
        self.bind_mouse_wheel(canvas); canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        self.create_entry(0, "motd", "伺服器名稱")
        self.create_combobox(1, "gamemode", "遊戲模式", ["survival", "creative", "adventure", "spectator"])
        self.create_combobox(2, "difficulty", "難度", ["peaceful", "easy", "normal", "hard"])
        self.create_boolean_entry(3, "online-mode", "正版驗證")
        self.create_entry(4, "max-players", "最大玩家數")
        self.create_boolean_entry(5, "pvp", "玩家傷害 (PVP)")
        self.create_boolean_entry(6, "allow-flight", "允許飛行")
        self.create_entry(7, "server-port", "連接埠")
        self.create_entry(8, "level-seed", "地圖種子碼")
        button_frame = ttk.Frame(self); button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=10)
        save_button = ttk.Button(button_frame, text="儲存並關閉", command=self.save_and_close, style="success.TButton"); save_button.pack()
    def bind_mouse_wheel(self, widget):
        widget.bind_all("<MouseWheel>", self._on_mouse_wheel); widget.bind_all("<Button-4>", self._on_mouse_wheel); widget.bind_all("<Button-5>", self._on_mouse_wheel)
    def _on_mouse_wheel(self, event):
        canvas = event.widget
        if not isinstance(canvas, tk.Canvas): canvas = event.widget.winfo_toplevel().nametowidget("!serversettingswindow.!canvas")
        if event.num == 5 or event.delta < 0: canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0: canvas.yview_scroll(-1, "units")
    def create_entry(self, row, key, label_text):
        label = ttk.Label(self.scrollable_frame, text=label_text); label.grid(row=row, column=0, sticky="w", padx=5, pady=5)
        entry = ttk.Entry(self.scrollable_frame); entry.insert(0, self.properties.get(key, "")); entry.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        self.entries = entry
    def create_boolean_entry(self, row, key, label_text):
        label = ttk.Label(self.scrollable_frame, text=label_text); label.grid(row=row, column=0, sticky="w", padx=5, pady=5)
        combo = ttk.Combobox(self.scrollable_frame, values=["true", "false"], state="readonly"); combo.set(self.properties.get(key, "true")); combo.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        self.entries["key"] = combo
    def create_combobox(self, row, key, label_text, values):
        label = ttk.Label(self.scrollable_frame, text=label_text); label.grid(row=row, column=0, sticky="w", padx=5, pady=5)
        combo = ttk.Combobox(self.scrollable_frame, values=values, state="readonly"); combo.set(self.properties.get(key, values [0])); combo.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        self.entries["key"] = combo
    def save_and_close(self):
        updated_properties = {key: widget.get() for key, widget in self.entries.items()}; self.save_callback(updated_properties); self.destroy()

class AboutWindow(std_tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("關於")
        self.geometry("400x260") # 調整高度
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.abspath(".")
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(default=icon_path)
        except Exception as e:
            print(f"設定關於視窗圖示時發生錯誤: {e}")

        main_frame = std_tk.Frame(self, padx=20, pady=20)
        main_frame.pack(fill=std_tk.BOTH, expand=True)

        std_tk.Label(main_frame, text="Minecraft 伺服器架設工具", font=("Segoe UI", 14, "bold")).pack(anchor="w")

        info_frame = std_tk.Frame(main_frame)
        info_frame.pack(fill="x", pady=10, anchor="w")
        std_tk.Label(info_frame, text="開發人員:").pack(side="left")
        std_tk.Label(info_frame, text=" yoyo", font=("Segoe UI", 9, "bold")).pack(side="left")

        ver_frame = std_tk.Frame(main_frame)
        ver_frame.pack(fill="x", anchor="w")
        std_tk.Label(ver_frame, text="版本:").pack(side="left")
        std_tk.Label(ver_frame, text=" v1.5", font=("Segoe UI", 9, "bold")).pack(side="left")

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=15)

        std_tk.Label(main_frame, text="我們的伺服器", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        std_tk.Label(main_frame, text="IP: yoyotv.xyz").pack(anchor="w", pady=(5,0))

        # 佔據剩餘空間的空白 Frame，將版權標籤推到底部
        std_tk.Frame(main_frame).pack(expand=True, fill='y')

        std_tk.Label(main_frame, text="©廢人伺服器 版權所有", fg="grey").pack(side="bottom")