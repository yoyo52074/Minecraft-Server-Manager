"""Safe probing of the Minecraft world's exclusive session.lock."""

import errno
import os
import stat
from pathlib import Path


class WorldLockProbeError(OSError):
    """The lock file could not be checked safely."""


def _world_path(server_directory):
    root = Path(server_directory).resolve()
    level_name = "world"
    properties = root / "server.properties"
    try:
        properties_info = os.lstat(properties)
    except FileNotFoundError:
        properties_info = None
    if properties_info is not None:
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(properties_info.st_mode) or getattr(properties_info, "st_file_attributes", 0) & reparse_flag:
            raise WorldLockProbeError("server.properties 是符號連結或 reparse point，無法安全檢查世界路徑")
        if not stat.S_ISREG(properties_info.st_mode):
            raise WorldLockProbeError("server.properties 不是一般檔案，無法安全檢查世界路徑")
        try:
            for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == "level-name":
                    level_name = value.strip() or "world"
                    break
        except OSError as exc:
            raise WorldLockProbeError(f"無法讀取 server.properties 以確認世界路徑：{exc}") from exc
    world = (root / level_name).resolve()
    try:
        world.relative_to(root)
    except ValueError as exc:
        raise WorldLockProbeError("level-name 指向伺服器資料夾外，無法安全檢查世界鎖") from exc
    return world / "session.lock"


def is_world_locked(server_directory):
    """Return True only when another process holds the world's session lock.

    Minecraft's DirectoryLock uses an exclusive file-region lock. The probe
    acquires and immediately releases the same region without changing the
    file contents. A stale session.lock file alone is not considered locked.
    """
    lock_path = _world_path(server_directory)
    try:
        info = os.lstat(lock_path)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & reparse_flag:
        raise WorldLockProbeError("世界 session.lock 是符號連結或 reparse point，已停止啟動")
    if not stat.S_ISREG(info.st_mode):
        raise WorldLockProbeError("世界 session.lock 不是一般檔案，已停止啟動")

    try:
        with open(lock_path, "r+b") as lock_file:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(exc, "winerror", None) in {33, 36}:
                        return True
                    raise
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        return True
                    raise
                fcntl.lockf(lock_file.fileno(), fcntl.LOCK_UN)
    except WorldLockProbeError:
        raise
    except OSError as exc:
        raise WorldLockProbeError(f"無法安全檢查世界鎖：{exc}") from exc
    return False
