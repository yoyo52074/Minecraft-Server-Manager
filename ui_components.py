import tkinter as tk
import ttkbootstrap as ttk
from tkinter import messagebox, scrolledtext
import os
import sys
import tkinter as std_tk

class MainAppWindow(ttk.Window):
    def __init__(self, themename="darkly"):
        super().__init__(themename=themename)
        self.title("Minecraft 伺服器架設工具 v1.7 -Produced by yoyo")

        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.abspath(".")
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.geometry("900x750")
        self.minsize(850, 650)

        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        main_frame.rowconfigure(2, weight=1)
        main_frame.columnconfigure(0, weight=1)

        top_frame = ttk.Frame(main_frame)
        top_frame.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        top_frame.columnconfigure(0, weight=1)

        setup_frame = ttk.LabelFrame(top_frame, text=" 伺服器安裝與路徑 ", padding="15")
        setup_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        setup_frame.columnconfigure(1, weight=1)

        ttk.Label(setup_frame, text="當前路徑:").grid(row=0, column=0, padx=5, pady=8, sticky="w")
        self.path_label = ttk.Label(setup_frame, text="...", anchor="w", bootstyle="info")
        self.path_label.grid(row=0, column=1, columnspan=2, padx=5, pady=8, sticky="ew")

        ttk.Label(setup_frame, text="伺服器核心:").grid(row=1, column=0, padx=5, pady=8, sticky="w")
        self.core_combo = ttk.Combobox(setup_frame, state="readonly", font=("Segoe UI", 10))
        self.core_combo.grid(row=1, column=1, padx=5, pady=8, sticky="ew")

        ttk.Label(setup_frame, text="Minecraft 版本:").grid(row=2, column=0, padx=5, pady=8, sticky="w")
        self.version_combo = ttk.Combobox(setup_frame, state="readonly", font=("Segoe UI", 10))
        self.version_combo.grid(row=2, column=1, padx=5, pady=8, sticky="ew")

        action_frame = ttk.Frame(top_frame)
        action_frame.grid(row=0, column=1, sticky="ns")

        self.change_dir_button = ttk.Button(action_frame, text="📁 更改伺服器路徑", bootstyle="secondary")
        self.change_dir_button.pack(padx=5, pady=5, fill=tk.X)
        self.download_button = ttk.Button(action_frame, text="✓ 下載/安裝伺服器", bootstyle="success")
        self.download_button.pack(padx=5, pady=5, fill=tk.X)
        self.settings_button = ttk.Button(action_frame, text="⚙ 伺服器設定", state="disabled", bootstyle="info")
        self.settings_button.pack(padx=5, pady=5, fill=tk.X)
        self.about_button = ttk.Button(action_frame, text="ℹ 關於", bootstyle="secondary-outline")
        self.about_button.pack(padx=5, pady=5, fill=tk.X)

        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=1, column=0, sticky="ew", pady=(0, 15))

        self.status_label = ttk.Label(status_frame, text="狀態：請選擇核心與版本", font=("Segoe UI", 10, "bold"), bootstyle="warning")
        self.status_label.pack(fill=tk.X, pady=(0, 5))

        self.progress_bar = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate", bootstyle="success-striped")
        self.progress_bar.pack(fill=tk.X)

        console_area = ttk.Frame(main_frame)
        console_area.grid(row=2, column=0, sticky="nsew")
        console_area.rowconfigure(1, weight=1)
        console_area.columnconfigure(0, weight=1)

        control_frame = ttk.LabelFrame(console_area, text=" 伺服器控制 ", padding="15")
        control_frame.grid(row=0, column=0, sticky="ew", pady=(0, 15))

        server_buttons_frame = ttk.Frame(control_frame)
        server_buttons_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(server_buttons_frame, text="記憶體 (MB):").pack(side=tk.LEFT, padx=(0, 5))
        self.ram_spinbox = ttk.Spinbox(server_buttons_frame, from_=1024, to=16384, increment=1024, width=8, font=("Segoe UI", 10))
        self.ram_spinbox.set("2048")
        self.ram_spinbox.pack(side=tk.LEFT, padx=5)

        self.start_button = ttk.Button(server_buttons_frame, text="▶ 啟動伺服器", state="disabled", bootstyle="primary")
        self.start_button.pack(side=tk.LEFT, padx=10)
        self.stop_button = ttk.Button(server_buttons_frame, text="■ 停止伺服器", state="disabled", bootstyle="danger")
        self.stop_button.pack(side=tk.LEFT, padx=5)

        playit_frame = ttk.Frame(control_frame)
        playit_frame.pack(side=tk.RIGHT, padx=5)
        ttk.Label(playit_frame, text="公開IP (Playit.gg):").pack(side=tk.LEFT, padx=5)

        self.playit_enabled = tk.BooleanVar()
        self.playit_checkbox = ttk.Checkbutton(playit_frame, text="", variable=self.playit_enabled, bootstyle="success-round-toggle")
        self.playit_checkbox.pack(side=tk.LEFT, padx=5)

        self.playit_address_label = ttk.Label(playit_frame, text="等待啟動...", bootstyle="secondary")
        self.playit_address_label.pack(side=tk.LEFT, padx=5)

        console_frame = ttk.LabelFrame(console_area, text=" 伺服器控制台 ", padding="10")
        console_frame.grid(row=1, column=0, sticky="nsew")
        console_frame.rowconfigure(0, weight=1)
        console_frame.columnconfigure(0, weight=1)

        self.console_output = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, state="disabled", font=("Consolas", 10), bg="#1e1e1e", fg="#ffffff")
        self.console_output.grid(row=0, column=0, sticky="nsew")

        self.console_output.tag_config("info", foreground="#5bc0de")
        self.console_output.tag_config("warn", foreground="#f0ad4e")
        self.console_output.tag_config("error", foreground="#d9534f")
        self.console_output.tag_config("success", foreground="#5cb85c")
        self.console_output.tag_config("normal", foreground="#ffffff")

        command_frame = ttk.Frame(console_area)
        command_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        command_frame.columnconfigure(0, weight=1)

        self.command_input = ttk.Entry(command_frame, state="disabled", font=("Consolas", 11))
        self.command_input.grid(row=0, column=0, sticky="ew", ipady=3)
        self.send_command_button = ttk.Button(command_frame, text="✉ 發送", state="disabled", bootstyle="primary")
        self.send_command_button.grid(row=0, column=1, padx=(10, 0))

class ServerSettingsWindow(ttk.Toplevel):
    def __init__(self, parent, properties_dict, save_callback):
        super().__init__(parent)
        self.title("⚙ 伺服器設定 (server.properties)")
        self.geometry("650x700")
        self.transient(parent)
        self.grab_set()

        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.abspath(".")
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(default=icon_path)
        except Exception:
            pass

        self.save_callback = save_callback
        self.properties = properties_dict
        self.entries = {}

        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas, padding="10")
        self.scrollable_frame.columnconfigure(1, weight=1)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.bind_mouse_wheel()

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.known_settings = {
            "motd": ("伺服器名稱 (motd)", "entry"),
            "gamemode": ("遊戲模式 (gamemode)", "combobox", ["survival", "creative", "adventure", "spectator"]),
            "difficulty": ("難度 (difficulty)", "combobox", ["peaceful", "easy", "normal", "hard"]),
            "online-mode": ("正版驗證 (online-mode)", "boolean"),
            "max-players": ("最大玩家數 (max-players)", "entry"),
            "pvp": ("玩家傷害 (pvp)", "boolean"),
            "allow-flight": ("允許飛行 (allow-flight)", "boolean"),
            "server-port": ("連接埠 (server-port)", "entry"),
            "level-seed": ("地圖種子碼 (level-seed)", "entry"),
            "view-distance": ("視距 (view-distance)", "entry"),
            "server-ip": ("伺服器 IP (server-ip)", "entry"),
            "white-list": ("白名單 (white-list)", "boolean"),
            "hardcore": ("極限模式 (hardcore)", "boolean"),
            "spawn-monsters": ("生成怪物 (spawn-monsters)", "boolean"),
            "spawn-animals": ("生成動物 (spawn-animals)", "boolean"),
            "spawn-npcs": ("生成村民 (spawn-npcs)", "boolean"),
            "enable-command-block": ("啟用指令方塊 (enable-command-block)", "boolean")
        }

        row = 0
        for key in self.properties.keys():
            if key in self.known_settings:
                info = self.known_settings[key]
                label_text = info[0]
                widget_type = info[1]

                if widget_type == "boolean":
                    self.create_boolean_entry(row, key, label_text)
                elif widget_type == "combobox":
                    self.create_combobox(row, key, label_text, info[2])
                else:
                    self.create_entry(row, key, label_text)
            else:
                self.create_entry(row, key, f"{key} (進階)")
            row += 1

        button_frame = ttk.Frame(self, padding="10")
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        save_button = ttk.Button(button_frame, text="💾 儲存並關閉", command=self.save_and_close, bootstyle="success")
        save_button.pack(pady=5)

    def bind_mouse_wheel(self):
        self.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.bind_all("<Button-4>", self._on_mouse_wheel)
        self.bind_all("<Button-5>", self._on_mouse_wheel)

    def _on_mouse_wheel(self, event):
        if isinstance(event.widget, ttk.Combobox):
            return
        if not self.winfo_exists():
            return
        if event.delta:
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        elif event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")

    def destroy(self):
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")
        super().destroy()

    def create_entry(self, row, key, label_text):
        label = ttk.Label(self.scrollable_frame, text=label_text, font=("Segoe UI", 10))
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        entry = ttk.Entry(self.scrollable_frame, font=("Segoe UI", 10))
        entry.insert(0, self.properties.get(key, ""))
        entry.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = entry

    def create_boolean_entry(self, row, key, label_text):
        label = ttk.Label(self.scrollable_frame, text=label_text, font=("Segoe UI", 10))
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(self.scrollable_frame, values=["true", "false"], state="readonly", font=("Segoe UI", 10))
        combo.set(self.properties.get(key, "true"))
        combo.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = combo

    def create_combobox(self, row, key, label_text, values):
        label = ttk.Label(self.scrollable_frame, text=label_text, font=("Segoe UI", 10))
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(self.scrollable_frame, values=values, state="readonly", font=("Segoe UI", 10))
        combo.set(self.properties.get(key, values[0]))
        combo.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = combo

    def save_and_close(self):
        updated_properties = {key: widget.get() for key, widget in self.entries.items()}
        self.save_callback(updated_properties)
        self.destroy()

class AboutWindow(std_tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("關於")
        self.geometry("400x200")
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
        except Exception:
            pass

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
        std_tk.Label(ver_frame, text=" v1.7", font=("Segoe UI", 9, "bold")).pack(side="left")

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=15)

        std_tk.Frame(main_frame).pack(expand=True, fill='y')

        std_tk.Label(main_frame, text="©廢人伺服器 版權所有", fg="grey").pack(side="bottom")
