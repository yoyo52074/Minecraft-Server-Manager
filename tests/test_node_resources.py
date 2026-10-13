import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from web_panel.node_resources import NodeResourceService


class NodeResourceServiceTests(unittest.TestCase):
    def test_aggregate_sums_online_capacity_and_weights_cpu_usage(self):
        gib = NodeResourceService.GIB
        nodes = [
            {
                "node_id": "a", "status": "online",
                "cpu_physical_cores": 4, "cpu_logical_cores": 8, "cpu_percent": 50,
                "memory_total_bytes": 8 * gib, "memory_used_bytes": 4 * gib,
                "memory_available_bytes": 4 * gib,
                "disk_total_bytes": 100 * gib, "disk_used_bytes": 40 * gib,
                "disk_free_bytes": 60 * gib,
            },
            {
                "node_id": "b", "status": "online",
                "cpu_physical_cores": 2, "cpu_logical_cores": 4, "cpu_percent": 25,
                "memory_total_bytes": 4 * gib, "memory_used_bytes": gib,
                "memory_available_bytes": 3 * gib,
                "disk_total_bytes": 50 * gib, "disk_used_bytes": 10 * gib,
                "disk_free_bytes": 40 * gib,
            },
            {
                "node_id": "offline", "status": "offline",
                "cpu_physical_cores": 16, "cpu_logical_cores": 32, "cpu_percent": 100,
                "memory_total_bytes": 64 * gib, "memory_used_bytes": 64 * gib,
                "memory_available_bytes": 0,
                "disk_total_bytes": 500 * gib, "disk_used_bytes": 500 * gib,
                "disk_free_bytes": 0,
            },
        ]

        totals = NodeResourceService.aggregate(nodes)

        self.assertEqual(totals["node_count"], 3)
        self.assertEqual(totals["online_node_count"], 2)
        self.assertEqual(totals["cpu_physical_cores"], 6)
        self.assertEqual(totals["cpu_logical_cores"], 12)
        self.assertEqual(totals["cpu_percent"], 41.7)
        self.assertEqual(totals["memory_total_bytes"], 12 * gib)
        self.assertEqual(totals["memory_used_bytes"], 5 * gib)
        self.assertEqual(totals["disk_total_bytes"], 150 * gib)
        self.assertEqual(totals["disk_used_bytes"], 50 * gib)

    def test_snapshot_reports_local_node_and_deduplicates_shared_volume(self):
        gib = NodeResourceService.GIB
        memory = SimpleNamespace(total=16 * gib, used=6 * gib, available=10 * gib, percent=37.5)
        disk = SimpleNamespace(total=200 * gib, used=80 * gib, free=120 * gib)
        fake_psutil = SimpleNamespace(
            cpu_count=lambda logical=True: 8 if logical else 4,
            cpu_percent=Mock(return_value=17.5),
            virtual_memory=Mock(return_value=memory),
            disk_usage=Mock(return_value=disk),
        )
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "server-a"
            second = Path(tmp) / "server-b"
            first.mkdir()
            second.mkdir()
            with patch("web_panel.node_resources.psutil", fake_psutil), patch(
                "web_panel.node_resources.platform.node", return_value="node-one"
            ):
                snapshot = NodeResourceService().snapshot([
                    {"directory": str(first)}, {"directory": str(second)},
                ])

        self.assertEqual(snapshot["mode"], "single-node")
        self.assertFalse(snapshot["remote_nodes_enabled"])
        self.assertEqual(snapshot["node_count"], 1)
        self.assertEqual(snapshot["online_node_count"], 1)
        self.assertEqual(snapshot["nodes"][0]["node_id"], "local")
        self.assertEqual(snapshot["nodes"][0]["name"], "node-one")
        self.assertEqual(snapshot["totals"]["cpu_logical_cores"], 8)
        self.assertEqual(snapshot["totals"]["cpu_percent"], 17.5)
        self.assertEqual(snapshot["totals"]["memory_total_bytes"], 16 * gib)
        self.assertEqual(snapshot["totals"]["disk_total_bytes"], 200 * gib)
        fake_psutil.disk_usage.assert_called_once()

    def test_empty_node_list_returns_zero_totals(self):
        totals = NodeResourceService.aggregate([])
        self.assertEqual(totals["node_count"], 0)
        self.assertEqual(totals["online_node_count"], 0)
        self.assertEqual(totals["cpu_percent"], 0.0)
        self.assertEqual(totals["memory_percent"], 0.0)
        self.assertEqual(totals["disk_percent"], 0.0)


if __name__ == "__main__":
    unittest.main()
