import os
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext

import ttkbootstrap as ttk
from ttkbootstrap.constants import *


# Aternos-inspired color scheme
COLORS = {
    "bg_dark": "#1a1a1a",
    "bg_card": "#252525",
    "bg_hover": "#2d2d2d",
    "accent": "#2196F3",
    "accent_hover": "#1976D2",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0a0",
    "border": "#333333",
    "success": "#4CAF50",
    "danger": "#f44336",
    "warning": "#ff9800",
    "info": "#2196F3",
}


class MainAppWindow(ttk.Window):
    def __init__(self, themename="darkly"):
        super().__init__(themename=themename)
        self.title("Minecraft 伺服器管理器 v2.2 - Produced by yoyo")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self._pages = {}
        self._nav_buttons = {}

        # Apply custom dark theme colors
        self.configure(background=COLORS["bg_dark"])

        try:
            base_path = (
                sys._MEIPASS if getattr(sys, "frozen", False) else os.path.abspath(".")
            )
            icon_path = os.path.join(base_path, "my_logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self._build_shell()
        self._build_overview_page()
        self._build_console_page()
        self._build_settings_page()
        self._show_page("overview")

    def _build_shell(self):
        # Sidebar with Aternos-style dark theme
        self.sidebar = tk.Frame(self, bg=COLORS["bg_card"], width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        # Brand header
        brand = tk.Frame(self.sidebar, bg=COLORS["bg_card"])
        brand.pack(fill=tk.X, padx=20, pady=(24, 20))
        tk.Label(
            brand,
            text="MINECRAFT",
            font=("Segoe UI", 16, "bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="SERVER MANAGER",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).pack(anchor="w")

        # Separator
        tk.Frame(self.sidebar, height=1, bg=COLORS["border"]).pack(
            fill=tk.X, padx=16, pady=(0, 16)
        )

        # Navigation buttons
        self._add_nav("overview", "▣ 伺服器總覽")
        self._add_nav("console", "▤ 控制台")
        self._add_nav("settings", "⚙ 伺服器設定")

        # Separator
        tk.Frame(self.sidebar, height=1, bg=COLORS["border"]).pack(
            fill=tk.X, padx=16, pady=16
        )

        # Action buttons
        self.backup_button = self._create_sidebar_button("💾 建立備份")
        self.backup_button.pack(fill=tk.X, padx=16, pady=3)
        self.restore_button = self._create_sidebar_button("↩ 還原備份")
        self.restore_button.pack(fill=tk.X, padx=16, pady=3)
        self.change_dir_button = self._create_sidebar_button("📁 更改路徑")
        self.change_dir_button.pack(fill=tk.X, padx=16, pady=3)
        self.about_button = self._create_sidebar_button("ℹ 關於")
        self.about_button.pack(fill=tk.X, padx=16, pady=3)

        # Server path at bottom
        path_frame = tk.Frame(self.sidebar, bg=COLORS["bg_card"])
        path_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))
        tk.Label(
            path_frame,
            text="伺服器路徑",
            font=("Segoe UI", 8),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).pack(anchor="w")
        self.path_label = tk.Label(
            path_frame,
            text="",
            wraplength=180,
            justify=tk.LEFT,
            font=("Segoe UI", 8),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        )
        self.path_label.pack(anchor="w", pady=(4, 0))

        # Main content area
        self.content = tk.Frame(self, bg=COLORS["bg_dark"])
        self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=24, pady=20)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def _create_sidebar_button(self, text):
        """Create a styled sidebar button with hover effects."""
        btn = tk.Button(
            self.sidebar,
            text=text,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            activeforeground=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"],
            bd=0,
            padx=12,
            pady=8,
            anchor="w",
            cursor="hand2",
        )
        btn.bind("<Enter>", lambda e: btn.configure(bg=COLORS["bg_hover"]))
        btn.bind("<Leave>", lambda e: btn.configure(bg=COLORS["bg_card"]))
        return btn

    def _add_nav(self, name, text):
        button = self._create_sidebar_button(text)
        button.configure(command=lambda: self._show_page(name))
        button.pack(fill=tk.X, padx=16, pady=3)
        self._nav_buttons[name] = button

    def _new_page(self, name):
        page = tk.Frame(self.content, bg=COLORS["bg_dark"])
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
            if key == name:
                button.configure(fg=COLORS["accent"], bg=COLORS["bg_hover"])
            else:
                button.configure(fg=COLORS["text_secondary"], bg=COLORS["bg_card"])

    def _page_header(self, page, title, subtitle):
        header = tk.Frame(page, bg=COLORS["bg_dark"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        tk.Label(
            header,
            text=title,
            font=("Segoe UI", 24, "bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_dark"],
        ).pack(anchor="w")
        tk.Label(
            header,
            text=subtitle,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        ).pack(anchor="w", pady=(4, 0))

    def _create_card(self, parent, title=None):
        """Create a styled card frame with optional title."""
        card = tk.Frame(
            parent,
            bg=COLORS["bg_card"],
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        if title:
            title_frame = tk.Frame(card, bg=COLORS["bg_card"])
            title_frame.pack(fill=tk.X, padx=16, pady=(12, 0))
            tk.Label(
                title_frame,
                text=title,
                font=("Segoe UI", 11, "bold"),
                fg=COLORS["text_primary"],
                bg=COLORS["bg_card"],
            ).pack(anchor="w")
        inner = tk.Frame(card, bg=COLORS["bg_card"])
        inner.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        return card, inner

    def _build_overview_page(self):
        page = self._new_page("overview")
        self._page_header(
            page, "伺服器總覽", "選擇核心版本，一鍵啟動你的 Minecraft 伺服器"
        )
        body = tk.Frame(page, bg=COLORS["bg_dark"])
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(2, weight=1)

        # ── Status card ──
        status_card, status_inner = self._create_card(body, "伺服器狀態")
        status_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        status_inner.columnconfigure(0, weight=1)
        status_inner.columnconfigure(1, weight=1)
        status_inner.columnconfigure(2, weight=1)

        self.status_label = tk.Label(
            status_inner,
            text="● 尚未準備",
            font=("Segoe UI", 17, "bold"),
            fg=COLORS["warning"],
            bg=COLORS["bg_card"],
        )
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # Progress row: bar + percentage
        progress_frame = tk.Frame(status_inner, bg=COLORS["bg_card"])
        progress_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        progress_frame.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(
            progress_frame, orient="horizontal", mode="determinate", bootstyle="info"
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress_label = tk.Label(
            progress_frame,
            text="0%",
            font=("Segoe UI", 11, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["bg_card"],
            width=5,
            anchor="e",
        )
        self.progress_label.grid(row=0, column=1, sticky="e")

        self.status_detail_label = tk.Label(
            status_inner,
            text="請選擇核心與版本後啟動伺服器",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            justify=tk.LEFT,
        )
        self.status_detail_label.grid(row=2, column=0, columnspan=3, sticky="w")

        # ── Server setup card (core + version + RAM) ──
        setup_card, setup_inner = self._create_card(body, "伺服器設定")
        setup_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(0, 14))
        setup_inner.columnconfigure(1, weight=1)

        tk.Label(
            setup_inner,
            text="伺服器核心",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=7)
        self.core_combo = ttk.Combobox(
            setup_inner, state="readonly", font=("Segoe UI", 10)
        )
        self.core_combo.grid(row=0, column=1, sticky="ew", pady=7)

        tk.Label(
            setup_inner,
            text="Minecraft 版本",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=7)
        self.version_combo = ttk.Combobox(
            setup_inner, state="readonly", font=("Segoe UI", 10)
        )
        self.version_combo.grid(row=1, column=1, sticky="ew", pady=7)

        tk.Label(
            setup_inner,
            text="記憶體 (MB)",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=7)
        self.ram_spinbox = ttk.Spinbox(
            setup_inner,
            from_=1024,
            to=16384,
            increment=1024,
            width=12,
            font=("Segoe UI", 11),
        )
        self.ram_spinbox.set("2048")
        self.ram_spinbox.grid(row=2, column=1, sticky="w", pady=7)

        # ── Control buttons ──
        control_card, control_inner = self._create_card(body, "快速操作")
        control_card.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        control_inner.columnconfigure(0, weight=1)
        control_inner.columnconfigure(1, weight=1)

        self.start_button = self._create_button(
            control_inner, "▶  啟動伺服器", COLORS["success"], state="disabled"
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = self._create_button(
            control_inner, "■  停止伺服器", COLORS["danger"], state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        # Info row inside control card
        info_frame = tk.Frame(control_inner, bg=COLORS["bg_card"])
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        info_frame.columnconfigure(0, weight=1)
        info_frame.columnconfigure(1, weight=1)
        info_frame.columnconfigure(2, weight=1)

        self.server_info_label = tk.Label(
            info_frame,
            text="核心：尚未安裝\n版本：--",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            justify=tk.LEFT,
        )
        self.server_info_label.grid(row=0, column=0, sticky="w")
        self.java_info_label = tk.Label(
            info_frame,
            text="Java：檢查中...",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            justify=tk.LEFT,
        )
        self.java_info_label.grid(row=0, column=1, sticky="w")
        self.uptime_label = tk.Label(
            info_frame,
            text="運行時間：--",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        )
        self.uptime_label.grid(row=0, column=2, sticky="e")

        # ── Playit card ──
        playit_card, playit_inner = self._create_card(body, "Playit.gg 公開連線")
        playit_card.grid(row=2, column=1, sticky="nsew", pady=(0, 14))

        self.playit_enabled = tk.BooleanVar()
        self.playit_checkbox = ttk.Checkbutton(
            playit_inner,
            text="啟用公開連線",
            variable=self.playit_enabled,
            bootstyle="info-round-toggle",
        )
        self.playit_checkbox.pack(anchor="w", pady=(0, 14))
        self.playit_address_label = tk.Label(
            playit_inner,
            text="○ 尚未啟動\n請在啟動伺服器時開啟",
            justify=tk.LEFT,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        )
        self.playit_address_label.pack(anchor="w")

    def _create_button(self, parent, text, color, state="normal"):
        """Create a styled button with the given color."""
        btn = tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 10, "bold"),
            fg="#ffffff",
            bg=color,
            activebackground=color,
            activeforeground="#ffffff",
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            state=state,
        )
        # Hover effect
        hover_color = self._darken_color(color)
        btn.bind(
            "<Enter>",
            lambda e: (
                btn.configure(bg=hover_color)
                if str(btn["state"]) != "disabled"
                else None
            ),
        )
        btn.bind(
            "<Leave>",
            lambda e: (
                btn.configure(bg=color) if str(btn["state"]) != "disabled" else None
            ),
        )
        return btn

    def _darken_color(self, color):
        """Darken a hex color by 20%."""
        color = color.lstrip("#")
        r, g, b = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
        r, g, b = int(r * 0.8), int(g * 0.8), int(b * 0.8)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _build_console_page(self):
        page = self._new_page("console")
        self._page_header(page, "伺服器控制台", "查看即時輸出並傳送伺服器指令")
        page.rowconfigure(1, weight=1)

        console_card, console_inner = self._create_card(page, "即時輸出")
        console_card.grid(row=1, column=0, sticky="nsew")
        console_inner.rowconfigure(0, weight=1)
        console_inner.columnconfigure(0, weight=1)

        self.console_output = scrolledtext.ScrolledText(
            console_inner,
            wrap=tk.WORD,
            state="disabled",
            font=("Consolas", 10),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            bd=0,
            padx=12,
            pady=12,
        )
        self.console_output.grid(row=0, column=0, sticky="nsew")
        for tag, color in {
            "info": COLORS["info"],
            "warn": COLORS["warning"],
            "error": COLORS["danger"],
            "success": COLORS["success"],
            "normal": COLORS["text_primary"],
        }.items():
            self.console_output.tag_config(tag, foreground=color)

        command_frame = tk.Frame(page, bg=COLORS["bg_dark"])
        command_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        command_frame.columnconfigure(0, weight=1)

        self.command_input = tk.Entry(
            command_frame,
            state="disabled",
            font=("Consolas", 11),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            bd=1,
            relief=tk.SOLID,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.command_input.grid(row=0, column=0, sticky="ew", ipady=8, padx=(0, 10))
        self.send_command_button = self._create_button(
            command_frame, "✉ 發送", COLORS["accent"], state="disabled"
        )
        self.send_command_button.grid(row=0, column=1)

    def _build_settings_page(self):
        page = self._new_page("settings")
        self._page_header(
            page, "伺服器設定", "編輯 server.properties；伺服器首次啟動後可用"
        )
        self.settings_button = self._create_button(
            page, "⚙ 開啟設定編輯器", COLORS["accent"], state="disabled"
        )
        self.settings_button.grid(row=1, column=0, sticky="w")
        tk.Label(
            page,
            text="設定視窗內提供搜尋功能，可快速找到 MOTD、模式、連接埠與白名單等項目。",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        ).grid(row=2, column=0, sticky="w", pady=(14, 0))


class ServerSettingsWindow(ttk.Toplevel):
    def __init__(self, parent, properties_dict, save_callback):
        super().__init__(parent)
        self.title("伺服器設定")
        self.geometry("700x760")
        self.configure(bg=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()
        self.save_callback = save_callback
        self.properties = properties_dict
        self.entries = {}
        self.setting_rows = {}

        main_frame = tk.Frame(self, bg=COLORS["bg_dark"], padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        search_frame = tk.Frame(main_frame, bg=COLORS["bg_dark"])
        search_frame.pack(fill="x", pady=(0, 8))
        tk.Label(
            search_frame,
            text="搜尋設定：",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        ).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_frame,
            textvariable=self.search_var,
            font=("Segoe UI", 10),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            bd=1,
            relief=tk.SOLID,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        search_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.search_var.trace_add("write", lambda *_: self.filter_settings())

        canvas = tk.Canvas(main_frame, bg=COLORS["bg_dark"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = tk.Frame(canvas, bg=COLORS["bg_dark"], padx=10, pady=10)
        self.scrollable_frame.columnconfigure(1, weight=1)
        self.scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas = canvas
        self.bind_mouse_wheel()

        self.known_settings = {
            "motd": ("伺服器名稱 (motd)", "entry"),
            "gamemode": (
                "遊戲模式 (gamemode)",
                "combobox",
                ["survival", "creative", "adventure", "spectator"],
            ),
            "difficulty": (
                "難度 (difficulty)",
                "combobox",
                ["peaceful", "easy", "normal", "hard"],
            ),
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

        save_btn = tk.Button(
            self,
            text="💾 儲存並關閉",
            command=self.save_and_close,
            font=("Segoe UI", 10, "bold"),
            fg="#ffffff",
            bg=COLORS["accent"],
            activebackground=COLORS["accent_hover"],
            activeforeground="#ffffff",
            bd=0,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        save_btn.pack(fill=tk.X, padx=15, pady=10)

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
        label = tk.Label(
            self.scrollable_frame,
            text=label_text,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        )
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        entry = tk.Entry(
            self.scrollable_frame,
            font=("Segoe UI", 10),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            bd=1,
            relief=tk.SOLID,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        entry.insert(0, self.properties.get(key, ""))
        entry.grid(row=row, column=1, sticky="ew", padx=10, pady=8, ipady=4)
        self.entries[key] = entry
        self.setting_rows[key] = (label, entry, label_text.lower(), key.lower())

    def create_boolean_entry(self, row, key, label_text):
        label = tk.Label(
            self.scrollable_frame,
            text=label_text,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        )
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(
            self.scrollable_frame,
            values=["true", "false"],
            state="readonly",
            font=("Segoe UI", 10),
        )
        combo.set(self.properties.get(key, "true"))
        combo.grid(row=row, column=1, sticky="ew", padx=10, pady=8)
        self.entries[key] = combo
        self.setting_rows[key] = (label, combo, label_text.lower(), key.lower())

    def create_combobox(self, row, key, label_text, values):
        label = tk.Label(
            self.scrollable_frame,
            text=label_text,
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        )
        label.grid(row=row, column=0, sticky="w", padx=10, pady=8)
        combo = ttk.Combobox(
            self.scrollable_frame,
            values=values,
            state="readonly",
            font=("Segoe UI", 10),
        )
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
        self.configure(bg=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()
        frame = tk.Frame(self, bg=COLORS["bg_dark"], padx=24, pady=24)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frame,
            text="Minecraft 伺服器管理器",
            font=("Segoe UI", 16, "bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_dark"],
        ).pack(anchor="w")
        tk.Label(
            frame,
            text="版本：v2.2",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        ).pack(anchor="w", pady=(10, 0))
        tk.Frame(frame, height=1, bg=COLORS["border"]).pack(fill=tk.X, pady=15)
        tk.Label(
            frame,
            text="Produced by yoyo",
            font=("Segoe UI", 10),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
        ).pack(anchor="w")
