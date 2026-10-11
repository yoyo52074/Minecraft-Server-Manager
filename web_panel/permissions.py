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

ROLE_DEFAULTS = {
    "viewer": {"server.view", "console.view", "files.view", "files.download", "settings.view", "backups.view", "plugins.view", "mods.view", "players.view", "resources.view"},
    "operator": {"server.view", "server.start", "server.stop", "server.restart", "console.view", "console.send_command", "files.view", "files.upload", "files.download", "files.edit", "files.rename", "files.copy", "files.move", "files.delete", "files.compress", "files.extract", "settings.view", "backups.view", "backups.create", "plugins.view", "plugins.upload", "plugins.enable", "plugins.disable", "plugins.delete", "mods.view", "mods.upload", "mods.enable", "mods.disable", "mods.delete", "players.view", "players.kick", "players.whitelist", "resources.view", "resources.view_cpu", "resources.view_memory", "resources.view_disk", "resources.view_network"},
}
