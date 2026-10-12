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

    def test_fabric_versions_come_from_official_meta_api(self):
        response = FakeResponse(payload=[
            {"version": "26.2-snapshot-1", "stable": False},
            {"version": "26.2", "stable": True},
            {"version": "1.21.8", "stable": True},
        ])
        with patch.object(self.manager, "_safe_get", return_value=response) as get:
            versions = self.manager.get_core_versions("Fabric")
        self.assertEqual(versions, ["26.2", "1.21.8"])
        self.assertEqual(get.call_args.args[0], "https://meta.fabricmc.net/v2/versions/game")

    def test_fabric_download_uses_latest_stable_loader_and_installer(self):
        loader_response = FakeResponse(payload=[
            {"loader": {"version": "0.20.0", "stable": False}},
            {"loader": {"version": "0.19.5", "stable": True}},
        ])
        installer_response = FakeResponse(payload=[{"version": "1.1.2", "stable": True}])
        with patch.object(self.manager, "_safe_get", side_effect=[loader_response, installer_response]) as get, \
             patch.object(self.manager, "_download_file", return_value=("fabric-server.jar", "downloaded")) as download:
            result = self.manager._download_fabric("1.21.8", lambda _: None)
        self.assertEqual(result, ("fabric-server.jar", "downloaded"))
        self.assertEqual(get.call_args_list[0].args[0], "https://meta.fabricmc.net/v2/versions/loader/1.21.8")
        self.assertEqual(get.call_args_list[1].args[0], "https://meta.fabricmc.net/v2/versions/installer")
        self.assertEqual(download.call_args.args[0], "https://meta.fabricmc.net/v2/versions/loader/1.21.8/0.19.5/1.1.2/server/jar")
        self.assertEqual(download.call_args.args[1], "fabric-server-1.21.8-0.19.5-1.1.2.jar")

    def test_forge_versions_are_discovered_from_official_metadata(self):
        metadata = b"""<metadata><versioning><versions>
        <version>1.20.1-47.2.0</version><version>1.20.1-47.4.0</version>
        <version>1.21.1-52.0.1</version><version>26.3-66.0.10</version>
        <version>not-a-forge-coordinate</version>
        </versions></versioning></metadata>"""
        response = Mock(content=metadata)
        response.raise_for_status.return_value = None
        with patch.object(self.manager, "_safe_get", return_value=response) as get:
            versions = self.manager.get_core_versions("Forge")
        self.assertEqual(versions, ["26.3", "1.21.1", "1.20.1"])
        self.assertEqual(get.call_args.args[0], ServerManager.FORGE_METADATA_URL)

    def test_forge_download_uses_latest_build_for_requested_game_version(self):
        metadata = b"""<metadata><versioning><versions>
        <version>1.20.1-47.2.0</version><version>1.20.1-47.4.0</version>
        </versions></versioning></metadata>"""
        response = Mock(content=metadata)
        response.raise_for_status.return_value = None
        with patch.object(self.manager, "_safe_get", return_value=response), \
             patch.object(self.manager, "_download_file", return_value=("forge.jar", "downloaded")) as download:
            result = self.manager._download_forge("1.20.1", lambda _: None)
        self.assertEqual(result, ("forge.jar", "downloaded"))
        self.assertEqual(download.call_args.args[0], "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.4.0/forge-1.20.1-47.4.0-installer.jar")
        self.assertEqual(download.call_args.args[1], "forge-1.20.1-47.4.0-installer.jar")

    def test_core_download_rejects_nonstandard_https_port(self):
        with self.assertRaisesRegex(ValueError, "不受信任"):
            self.manager._safe_get("https://fill.papermc.io:8443/v3/projects/paper")

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
