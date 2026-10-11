PERMISSION_GROUPS = {
    "伺服器控制": ["server.view", "server.start", "server.stop", "server.restart", "server.force_stop"],
    "主控台": ["console.view", "console.send_command", "console.download_log"],
    "檔案": ["files.view", "files.upload", "files.download", "files.create_folder", "files.edit", "files.rename", "files.copy", "files.move", "files.delete", "files.compress", "files.extract"],
    "設定": ["settings.view", "settings.edit", "settings.edit_java", "settings.edit_startup"],
    "備份": ["backups.view", "backups.create", "backups.download", "backups.delete", "backups.restore"],
    "插件與模組": ["plugins.view", "plugins.upload", "plugins.enable", "plugins.disable", "plugins.delete", "mods.view", "mods.upload", "mods.enable", "mods.disable", "mods.delete"],
    "玩家": ["players.view", "players.kick", "players.ban", "players.unban", "players.op", "players.deop", "players.whitelist"],
    "排程": ["schedule.view", "schedule.create", "schedule.edit", "schedule.delete", "schedule.run"],
    "監控": ["resources.view", "resources.view_cpu", "resources.view_memory", "resources.view_disk", "resources.view_network"],
}

ALL_PERMISSIONS = [permission for group in PERMISSION_GROUPS.values() for permission in group]
PERMISSION_LABELS = {
    "server.view": "查看伺服器狀態", "server.start": "啟動伺服器", "server.stop": "正常停止伺服器",
    "server.restart": "重新啟動伺服器", "server.force_stop": "強制停止伺服器",
    "console.view": "查看主控台與日誌", "console.send_command": "傳送主控台指令", "console.download_log": "下載主控台日誌",
    "files.view": "瀏覽檔案", "files.upload": "上傳檔案", "files.download": "下載檔案",
    "files.create_folder": "建立資料夾", "files.edit": "編輯文字檔", "files.rename": "重新命名檔案",
    "files.copy": "複製檔案", "files.move": "移動檔案", "files.delete": "刪除檔案",
    "files.compress": "壓縮檔案", "files.extract": "解壓縮檔案",
    "settings.view": "查看伺服器設定", "settings.edit": "修改伺服器設定",
    "settings.edit_java": "修改 Java 設定", "settings.edit_startup": "修改啟動參數",
    "backups.view": "查看備份", "backups.create": "建立備份", "backups.download": "下載備份",
    "backups.delete": "刪除備份", "backups.restore": "還原備份",
    "plugins.view": "查看插件", "plugins.upload": "上傳插件", "plugins.enable": "啟用插件",
    "plugins.disable": "停用插件", "plugins.delete": "刪除插件", "mods.view": "查看模組",
    "mods.upload": "上傳模組", "mods.enable": "啟用模組", "mods.disable": "停用模組", "mods.delete": "刪除模組",
    "players.view": "查看玩家", "players.kick": "踢出玩家", "players.ban": "封鎖玩家",
    "players.unban": "解除封鎖", "players.op": "授予管理員（OP）", "players.deop": "移除管理員（OP）",
    "players.whitelist": "管理白名單", "schedule.view": "查看排程", "schedule.create": "建立排程",
    "schedule.edit": "修改排程", "schedule.delete": "刪除排程", "schedule.run": "手動執行排程",
    "resources.view": "查看監控總覽", "resources.view_cpu": "查看 CPU 使用率",
    "resources.view_memory": "查看記憶體使用率", "resources.view_disk": "查看磁碟使用率",
    "resources.view_network": "查看網路流量",
}

ROLE_DEFAULTS = {
    "viewer": {"server.view", "console.view", "files.view", "files.download", "settings.view", "backups.view", "plugins.view", "mods.view", "players.view", "resources.view"},
    "operator": {"server.view", "server.start", "server.stop", "server.restart", "console.view", "console.send_command", "files.view", "files.upload", "files.download", "files.edit", "files.rename", "files.copy", "files.move", "files.delete", "files.compress", "files.extract", "settings.view", "backups.view", "backups.create", "plugins.view", "plugins.upload", "plugins.enable", "plugins.disable", "plugins.delete", "mods.view", "mods.upload", "mods.enable", "mods.disable", "mods.delete", "players.view", "players.kick", "players.whitelist", "resources.view", "resources.view_cpu", "resources.view_memory", "resources.view_disk", "resources.view_network"},
}
