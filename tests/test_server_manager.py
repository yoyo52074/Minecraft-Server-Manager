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
        self.is_redirect = False

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

    def close(self):
        pass


class ServerManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = ServerManager(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_paper_versions_use_fill_v3_response(self):
        response = Mock()
        response.is_redirect = False
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
                self.manager._download_file("https://fill.papermc.io/paper.jar", "paper.jar", lambda _: None)
        self.assertFalse(os.path.exists(target))
        self.assertFalse(any(name.endswith(".part") for name in os.listdir(self.temp_dir.name)))

    def test_core_download_rejects_untrusted_url_and_remote_filename(self):
        with self.assertRaisesRegex(ValueError,"不受信任"):
            self.manager._download_file("http://127.0.0.1/private","paper.jar",lambda _:None)
        with self.assertRaisesRegex(ValueError,"檔名不安全"):
            self.manager._download_file("https://fill.papermc.io/paper.jar","../../outside.jar",lambda _:None)

    def test_core_download_stops_when_stream_exceeds_limit(self):
        response=FakeResponse(chunks=[b"123"],headers={})
        with patch.object(ServerManager,"MAX_CORE_DOWNLOAD_BYTES",2),patch("server_manager.requests.get",return_value=response):
            with self.assertRaisesRegex(ValueError,"超過 1 GiB"):
                self.manager._download_file("https://fill.papermc.io/paper.jar","paper.jar",lambda _:None)
        self.assertFalse(any(name.endswith(".part") for name in os.listdir(self.temp_dir.name)))

    def test_properties_parser_skips_malformed_lines(self):
        with open(self.manager.properties_path, "w", encoding="utf-8") as file:
            file.write("motd=hello=world\nmalformed line\nmax-players=10\n")
        self.assertEqual(self.manager.get_server_properties(), {"motd": "hello=world", "max-players": "10"})

    def test_properties_symlink_is_rejected_for_read_and_write(self):
        external_dir=tempfile.TemporaryDirectory()
        self.addCleanup(external_dir.cleanup)
        external=os.path.join(external_dir.name,"outside.properties")
        with open(external,"w",encoding="utf-8") as file:file.write("motd=untouched\n")
        try:
            os.symlink(external,self.manager.properties_path)
        except (OSError,NotImplementedError):
            self.skipTest("目前平台不允許建立符號連結")
        with self.assertRaisesRegex(ValueError,"符號連結"):
            self.manager.get_server_properties()
        with self.assertRaisesRegex(ValueError,"符號連結"):
            self.manager.save_server_properties({"motd":"overwrite"})
        with open(external,encoding="utf-8") as file:
            self.assertEqual(file.read(),"motd=untouched\n")


if __name__ == "__main__":
    unittest.main()
