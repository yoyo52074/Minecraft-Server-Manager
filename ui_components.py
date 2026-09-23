import os
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext

import ttkbootstrap as ttk


class MainAppWindow(ttk.Window):
    def __init__(self, themename="darkly"):
        super().__init__(themename=themename)
        self.title("Minecraft 伺服器管理器 v2.1 - Produced by yoyo")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self._pages = {}
        self._nav_buttons = {}

        try:
            base_path = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.abspath(".")
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self._build_shell()
        self._build_overview_page()
        self._build_install_page()
        self._build_console_page()
        self._build_settings_page()
        self._show_page("overview")

    def _build_shell(self):
        self.sidebar = ttk.Frame(self, bootstyle="dark", padding=(16, 20))
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.configure(width=220)
        self.sidebar.pack_propagate(False)

        brand = ttk.Frame(self.sidebar, bootstyle="dark")
        brand.pack(fill=tk.X, pady=(0, 28))
        ttk.Label(brand, text="MINECRAFT", font=("Segoe UI", 15, "bold"), bootstyle="success-inverse").pack(anchor="w")
        ttk.Label(brand, text="SERVER MANAGER", font=("Segoe UI", 9), bootstyle="secondary-inverse").pack(anchor="w")
        ttk.Separator(self.sidebar).pack(fill=tk.X, pady=(0, 18))

        self._add_nav("overview", "▣ 伺服器總覽")
        self._add_nav("install", "＋ 安裝伺服器")
        self._add_nav("console", "▤ 控制台")
        self._add_nav("settings", "⚙ 伺服器設定")

        ttk.Separator(self.sidebar).pack(fill=tk.X, pady=18)
        self.backup_button = ttk.Button(self.sidebar, text="💾 建立備份", bootstyle="secondary-outline")
        self.backup_button.pack(fill=tk.X, pady=4)
        self.restore_button = ttk.Button(self.sidebar, text="↩ 還原備份", bootstyle="secondary-outline")
        self.restore_button.pack(fill=tk.X, pady=4)
        self.about_button = ttk.Button(self.sidebar, text="ℹ 關於", bootstyle="secondary-outline")
        self.about_button.pack(fill=tk.X, pady=4)

        self.path_label = ttk.Label(self.sidebar, text="", wraplength=185, justify=tk.LEFT, bootstyle="secondary-inverse")
        self.path_label.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))
        ttk.Label(self.sidebar, text="伺服器路徑", bootstyle="secondary-inverse", font=("Segoe UI", 8)).pack(side=tk.BOTTOM, anchor="w")

        self.content = ttk.Frame(self, padding=(24, 20))
        self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def _add_nav(self, name, text):
        button = ttk.Button(self.sidebar, text=text, command=lambda: self._show_page(name), bootstyle="dark-outline")
        button.pack(fill=tk.X, pady=3)
        self._nav_buttons[name] = button

    def _new_page(self, name):
        page = ttk.Frame(self.content)
        page.grid(row=0, column=0, sticky="nsew")
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        self._pages[name] = page
        return page

    def _show_page(self, name):
        page = self._pages.get(name)
        if page:
            page.tkraise()
        for key, button in self._nav_buttons.items():
            button.configure(bootstyle="success" if key == name else "dark-outline")

    def _page_header(self, page, title, subtitle):
        header = ttk.Frame(page)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        ttk.Label(header, text=title, font=("Segoe UI", 22, "bold"), bootstyle="light").pack(anchor="w")
        ttk.Label(header, text=subtitle, font=("Segoe UI", 10), bootstyle="secondary").pack(anchor="w", pady=(3, 0))

    def _build_overview_page(self):
        page = self._new_page("overview")
        self._page_header(page, "伺服器總覽", "管理狀態、啟動伺服器與查看最近活動")
        body = ttk.Frame(page)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(1, weight=1)

        status_card = ttk.LabelFrame(body, text=" 目前狀態 ", padding=18, bootstyle="success")
        status_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        status_card.columnconfigure(0, weight=1)
        status_card.columnconfigure(1, weight=1)
        status_card.columnconfigure(2, weight=1)
        self.status_label = ttk.Label(status_card, text="● 尚未準備", font=("Segoe UI", 17, "bold"), bootstyle="warning")
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        self.server_info_label = ttk.Label(status_card, text="核心：尚未安裝\n版本：--", font=("Segoe UI", 10), bootstyle="secondary")
        self.server_info_label.grid(row=1, column=0, sticky="w")
        self.java_info_label = ttk.Label(status_card, text="Java：檢查中...\n需求：--", font=("Segoe UI", 10), bootstyle="secondary")
        self.java_info_label.grid(row=1, column=1, sticky="w")
        self.uptime_label = ttk.Label(status_card, text="運行時間：--", font=("Segoe UI", 10), bootstyle="secondary")
        self.uptime_label.grid(row=1, column=2, sticky="e")
        self.progress_bar = ttk.Progressbar(status_card, orient="horizontal", mode="determinate", bootstyle="success-striped")
        self.progress_bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(16, 0))

        control_card = ttk.LabelFrame(body, text=" 快速操作 ", padding=16)
        control_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        control_card.columnconfigure(0, weight=1)
        control_card.columnconfigure(1, weight=1)
        ttk.Label(control_card, text="記憶體配置 (MB)").grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.ram_spinbox = ttk.Spinbox(control_card, from_=1024, to=16384, increment=1024, width=12, font=("Segoe UI", 11))
        self.ram_spinbox.set("2048")
        self.ram_spinbox.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 18))
        self.start_button = ttk.Button(control_card, text="▶  啟動伺服器", state="disabled", bootstyle="success")
        self.start_button.grid(row=1, column=1, sticky="ew", pady=(0, 18))
        self.stop_button = ttk.Button(control_card, text="■  停止伺服器", state="disabled", bootstyle="danger")
        self.stop_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)

        playit_card = ttk.LabelFrame(body, text=" Playit.gg 公開連線 ", padding=16)
        playit_card.grid(row=1, column=1, sticky="nsew")
        self.playit_enabled = tk.BooleanVar()
        self.playit_checkbox = ttk.Checkbutton(playit_card, text="啟用公開連線", variable=self.playit_enabled, bootstyle="success-round-toggle")
        self.playit_checkbox.pack(anchor="w", pady=(0, 14))
        self.playit_address_label = ttk.Label(playit_card, text="○ 尚未啟動\n請在啟動伺服器時開啟", justify=tk.LEFT, bootstyle="secondary")
        self.playit_address_label.pack(anchor="w")

    def _build_install_page(self):
        page = self._new_page("install")
        self._page_header(page, "安裝伺服器", "選擇核心與版本，系統會自動準備 Java、下載核心並設定 EULA")
        card = ttk.LabelFrame(page, text=" 安裝設定 ", padding=20)
        card.grid(row=1, column=0, sticky="new")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="步驟 1  伺服器核心", font=("Segoe UI", 10, "bold"), bootstyle="success").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(card, text="核心類型").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=7)
        self.core_combo = ttk.Combobox(card, state="readonly", font=("Segoe UI", 10))
        self.core_combo.grid(row=1, column=1, sticky="ew", pady=7)
        ttk.Label(card, text="Minecraft 版本").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=7)
        self.version_combo = ttk.Combobox(card, state="readonly", font=("Segoe UI", 10))
        self.version_combo.grid(row=2, column=1, sticky="ew", pady=7)
        ttk.Separator(card).grid(row=3, column=0, columnspan=2, sticky="ew", pady=16)
        ttk.Label(card, text="步驟 2  安裝位置", font=("Segoe UI", 10, "bold"), bootstyle="success").grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.change_dir_button = ttk.Button(card, text="📁 更改伺服器路徑", bootstyle="secondary")
        self.change_dir_button.grid(row=5, column=0, sticky="ew", pady=7)
        self.download_button = ttk.Button(card, text="✓ 下載 / 安裝伺服器", bootstyle="success")
        self.download_button.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=7)
        self.wizard_button = ttk.Button(card, text="🧭 開啟安裝精靈", bootstyle="primary-outline")
        self.wizard_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(card, text="Java 版本會在按下安裝或啟動時依 Minecraft 版本自動判斷。", bootstyle="secondary").grid(row=7, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def _build_console_page(self):
        page = self._new_page("console")
        self._page_header(page, "伺服器控制台", "查看即時輸出並傳送伺服器指令")
        page.rowconfigure(1, weight=1)
        console_card = ttk.LabelFrame(page, text=" 即時輸出 ", padding=10)
        console_card.grid(row=1, column=0, sticky="nsew")
        console_card.rowconfigure(0, weight=1)
        console_card.columnconfigure(0, weight=1)
        self.console_output = scrolledtext.ScrolledText(console_card, wrap=tk.WORD, state="disabled", font=("Consolas", 10), bg="#15191e", fg="#e8edf2", insertbackground="white")
        self.console_output.grid(row=0, column=0, sticky="nsew")
        for tag, color in {"info": "#5bc0de", "warn": "#f0ad4e", "error": "#d9534f", "success": "#5cb85c", "normal": "#e8edf2"}.items():
            self.console_output.tag_config(tag, foreground=color)
        command_frame = ttk.Frame(page)
        command_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        command_frame.columnconfigure(0, weight=1)
        self.command_input = ttk.Entry(command_frame, state="disabled", font=("Consolas", 11))
        self.command_input.grid(row=0, column=0, sticky="ew", ipady=5)
        self.send_command_button = ttk.Button(command_frame, text="✉ 發送", state="disabled", bootstyle="primary")
        self.send_command_button.grid(row=0, column=1, padx=(10, 0))

    def _build_settings_page(self):
        page = self._new_page("settings")
        self._page_header(page, "伺服器設定", "編輯 server.properties；伺服器首次啟動後可用")
        self.settings_button = ttk.Button(page, text="⚙ 開啟設定編輯器", state="disabled", bootstyle="info")
        self.settings_button.grid(row=1, column=0, sticky="w")
        ttk.Label(page, text="設定視窗內提供搜尋功能，可快速找到 MOTD、模式、連接埠與白名單等項目。", bootstyle="secondary").grid(row=2, column=0, sticky="w", pady=(14, 0))


class ServerSettingsWindow(ttk.Toplevel):
    def __init__(self, parent, properties_dict, save_callback):
        super().__init__(parent)
        self.title("伺服器設定")
        self.geometry("700x760")
        self.transient(parent)
        self.grab_set()
        self.save_callback = save_callback
        self.properties = properties_dict
        self.entries = {}
        self.setting_rows = {}

        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(search_frame, text="搜尋設定：").pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_: self.filter_settings())

        canvas = tk.Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas, padding="10")
        self.scrollable_frame.columnconfigure(1, weight=1)
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas = canvas
        self.bind_mouse_wheel()

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
            "enable-command-block": ("啟用指令方塊 (enable-command-block)", "boolean"),
        }
        for row, key in enumerate(self.properties.keys()):
            info = self.known_settings.get(key)
            if info and info[1] == "boolean":
                self.create_boolean_entry(row, key, info[0])
            elif info and info[1] == "combobox":
                self.create_combobox(row, key, info[0], info[2])
            else:
                self.create_entry(row, key, info[0] if info else f"{key} (進階)")

        ttk.Button(self, text="💾 儲存並關閉", command=self.save_and_close, bootstyle="success").pack(fill=tk.X, padx=15, pady=10)

    def bind_mouse_wheel(self):
        self.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.bind_all("<Button-4>", self._on_mouse_wheel)
        self.bind_all("<Button-5>", self._on_mouse_wheel)

    def _on_mouse_wheel(self, event):
        if not self.winfo_exists():
            return
        if event.delta:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
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
        self.setting_rows[key] = (label, entry, label_text.lower(), key.lower())

    def create_boolean_entry(self, row, key, label_text):
        label = ttk.Label(self.scrollable_frame, text=label_text, font=("Segoe UI", 10))
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(self.scrollable_frame, values=["true", "false"], state="readonly", font=("Segoe UI", 10))
        combo.set(self.properties.get(key, "true"))
        combo.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = combo
        self.setting_rows[key] = (label, combo, label_text.lower(), key.lower())

    def create_combobox(self, row, key, label_text, values):
        label = ttk.Label(self.scrollable_frame, text=label_text, font=("Segoe UI", 10))
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(self.scrollable_frame, values=values, state="readonly", font=("Segoe UI", 10))
        combo.set(self.properties.get(key, values[0]))
        combo.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = combo
        self.setting_rows[key] = (label, combo, label_text.lower(), key.lower())

    def filter_settings(self):
        query = self.search_var.get().strip().lower()
        for label, widget, label_text, key in self.setting_rows.values():
            visible = not query or query in label_text or query in key
            if visible:
                label.grid()
                widget.grid()
            else:
                label.grid_remove()
                widget.grid_remove()

    def save_and_close(self):
        updated_properties = {key: widget.get() for key, widget in self.entries.items()}
        self.save_callback(updated_properties)
        self.destroy()


class AboutWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("關於")
        self.geometry("400x220")
        self.transient(parent)
        self.grab_set()
        ttk.Frame(self, padding=24).pack(fill=tk.BOTH, expand=True)
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Minecraft 伺服器管理器", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="版本：v2.1", bootstyle="secondary").pack(anchor="w", pady=(10, 0))
        ttk.Separator(frame).pack(fill=tk.X, pady=15)
        ttk.Label(frame, text="Produced by yoyo", bootstyle="secondary").pack(anchor="w")


class InstallWizard(tk.Toplevel):
    def __init__(self, parent, start_callback):
        super().__init__(parent)
        self.title("Minecraft 伺服器安裝精靈")
        self.geometry("500x390")
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=28)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="建立你的 Minecraft 伺服器", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(frame, text="完成以下步驟後即可開始與朋友遊玩", bootstyle="secondary").pack(anchor="w", pady=(4, 20))
        for index, text in enumerate(("選擇伺服器核心與 Minecraft 版本", "準備相容的 Java 環境", "下載核心並設定 EULA", "啟動伺服器或建立第一份備份"), 1):
            ttk.Label(frame, text=f"{index}   {text}").pack(anchor="w", pady=6)
        ttk.Button(frame, text="開始下載與安裝", bootstyle="success", command=lambda: (self.destroy(), start_callback())).pack(side="bottom", fill=tk.X, pady=(20, 0))
