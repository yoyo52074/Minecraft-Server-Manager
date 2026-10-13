"""Host/node resource snapshots for the dashboard.

The current manager runs on one local node. The response shape deliberately
uses a node list plus aggregated totals so a future authenticated remote-node
agent can contribute snapshots without changing the dashboard contract.
"""

import os
import platform
import shutil
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a project dependency
    psutil = None


class NodeResourceService:
    GIB = 1024 ** 3

    def snapshot(self, profiles):
        """Return one local node and its aggregate, using the future cluster shape."""
        nodes = [self._local_node(profiles)]
        totals = self.aggregate(nodes)
        return {
            "mode": "single-node",
            "remote_nodes_enabled": False,
            "node_count": totals["node_count"],
            "online_node_count": totals["online_node_count"],
            "nodes": nodes,
            "totals": totals,
        }

    @classmethod
    def aggregate(cls, nodes):
        """Aggregate online node snapshots; CPU load is weighted by thread count."""
        nodes = list(nodes or [])
        online = [node for node in nodes if node.get("status") == "online"]

        def number(node, key):
            try:
                return max(0.0, float(node.get(key, 0) or 0))
            except (TypeError, ValueError):
                return 0.0

        logical = sum(number(node, "cpu_logical_cores") for node in online)
        physical = sum(number(node, "cpu_physical_cores") for node in online)
        busy_cores = 0.0
        for node in online:
            logical_count = number(node, "cpu_logical_cores")
            cpu_percent = min(100.0, number(node, "cpu_percent"))
            busy_cores += logical_count * cpu_percent / 100.0
        memory_total = sum(number(node, "memory_total_bytes") for node in online)
        memory_used = sum(number(node, "memory_used_bytes") for node in online)
        memory_available = sum(number(node, "memory_available_bytes") for node in online)
        disk_total = sum(number(node, "disk_total_bytes") for node in online)
        disk_used = sum(number(node, "disk_used_bytes") for node in online)
        disk_free = sum(number(node, "disk_free_bytes") for node in online)

        return {
            "node_count": len(nodes),
            "online_node_count": len(online),
            "cpu_physical_cores": int(physical),
            "cpu_logical_cores": int(logical),
            "cpu_busy_cores": round(busy_cores, 2),
            "cpu_percent": round(busy_cores / logical * 100.0, 1) if logical else 0.0,
            "memory_total_bytes": int(memory_total),
            "memory_used_bytes": int(memory_used),
            "memory_available_bytes": int(memory_available),
            "memory_percent": round(memory_used / memory_total * 100.0, 1) if memory_total else 0.0,
            "disk_total_bytes": int(disk_total),
            "disk_used_bytes": int(disk_used),
            "disk_free_bytes": int(disk_free),
            "disk_percent": round(disk_used / disk_total * 100.0, 1) if disk_total else 0.0,
        }

    def _local_node(self, profiles):
        logical = os.cpu_count() or 0
        physical = logical
        cpu_percent = 0.0
        memory_total = memory_used = memory_available = 0
        memory_percent = 0.0

        if psutil is not None:
            try:
                logical = psutil.cpu_count(logical=True) or logical
                physical = psutil.cpu_count(logical=False) or logical
                cpu_percent = float(psutil.cpu_percent(interval=None) or 0.0)
            except (AttributeError, OSError, ValueError):
                pass
            try:
                memory = psutil.virtual_memory()
                memory_total = int(memory.total)
                memory_used = int(memory.used)
                memory_available = int(memory.available)
                memory_percent = float(memory.percent)
            except (AttributeError, OSError, ValueError):
                pass

        disk_total = disk_used = disk_free = 0
        seen_volumes = set()
        directories = [profile.get("directory") for profile in profiles or [] if profile.get("directory")]
        if not directories:
            directories = [str(Path.cwd())]
        for directory in directories:
            path = Path(directory)
            try:
                stat_result = os.stat(path)
                volume_key = (stat_result.st_dev, path.anchor.casefold())
                if volume_key in seen_volumes:
                    continue
                usage = psutil.disk_usage(str(path)) if psutil is not None else shutil.disk_usage(path)
            except Exception:
                continue
            seen_volumes.add(volume_key)
            disk_total += int(usage.total)
            disk_used += int(usage.used)
            disk_free += int(usage.free)

        return {
            "node_id": "local",
            "name": platform.node() or "本機節點",
            "status": "online",
            "source": "local-agent",
            "cpu_physical_cores": int(physical or 0),
            "cpu_logical_cores": int(logical or 0),
            "cpu_percent": min(100.0, max(0.0, cpu_percent)),
            "memory_total_bytes": memory_total,
            "memory_used_bytes": memory_used,
            "memory_available_bytes": memory_available,
            "memory_percent": memory_percent,
            "disk_total_bytes": disk_total,
            "disk_used_bytes": disk_used,
            "disk_free_bytes": disk_free,
        }
