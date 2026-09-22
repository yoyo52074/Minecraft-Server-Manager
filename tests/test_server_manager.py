import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from server_manager import ServerManager


class FakeResponse:
    def __init__(self, payload=None, chunks=(), headers=None, error=None):
        self.payload = payload
        self.chunks = chunks
        self.headers = headers or {}
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload

    def iter_content(self, chunk_size=8192):
        return iter(self.chunks)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class ServerManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = ServerManager(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_paper_versions_use_fill_v3_response(self):
        response = Mock()
        response.json.return_value = {"versions": {"1.21": ["1.21.10", "1.21.9"], "1.20": ["1.20.6"]}}
        response.raise_for_status.return_value = None
        with patch("server_manager.requests.get", return_value=response) as get:
            versions = self.manager.get_core_versions("Paper")
        self.assertEqual(versions, ["1.21.10", "1.21.9", "1.20.6"])
        self.assertIn("fill.papermc.io/v3/projects/paper", get.call_args.args[0])
        self.assertIn("User-Agent", get.call_args.kwargs["headers"])

    def test_interrupted_download_removes_part_file(self):
        response = FakeResponse(chunks=[b"partial"], headers={"content-length": "100"}, error=RuntimeError("network"))
        target = os.path.join(self.temp_dir.name, "paper.jar")
        with patch("server_manager.requests.get", return_value=response):
            with self.assertRaises(RuntimeError):
                self.manager._download_file("https://example.invalid/paper.jar", "paper.jar", lambda _: None)
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(target + ".part"))

    def test_properties_parser_skips_malformed_lines(self):
        with open(self.manager.properties_path, "w", encoding="utf-8") as file:
            file.write("motd=hello=world\nmalformed line\nmax-players=10\n")
        self.assertEqual(self.manager.get_server_properties(), {"motd": "hello=world", "max-players": "10"})


if __name__ == "__main__":
    unittest.main()
