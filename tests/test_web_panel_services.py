import json
import os
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from web_panel.database import WebDatabase
from web_panel.file_service import SafeFileService
from web_panel.multi_server import CoreAdapter, MultiServerManager, ServerProfileStore
from web_panel.core_update import CoreUpdateJobs
from core_metadata import minimum_java_version


class WebPanelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = WebDatabase(Path(self.temp.name) / "panel.db")
        self.store = ServerProfileStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_fabric_profile_and_launcher_detection(self):
        directory = Path(self.temp.name) / "fabric"
        profile = self.store.create("Fabric", directory, "Fabric", minecraft_version="1.21.1")
        (directory / "fabric-server-launch.jar").write_bytes(b"jar")
        launcher = CoreAdapter("Fabric").find_launcher(directory)
        self.assertEqual(launcher.name, "fabric-server-launch.jar")
        self.assertEqual(profile["core_type"], "Fabric")

    def test_minecraft_java_requirements(self):
        self.assertEqual(minimum_java_version("1.20.4"), 17)
        self.assertEqual(minimum_java_version("1.20.5"), 21)
        self.assertEqual(minimum_java_version("1.21.4"), 21)
        self.assertEqual(minimum_java_version("26.1"), 25)
        self.assertEqual(minimum_java_version("27.0"), 25)

    def test_server_start_rejects_java_below_minecraft_requirement(self):
        directory = Path(self.temp.name) / "minecraft-26-1"
        profile = self.store.create("Minecraft 26.1", directory, "Paper", minecraft_version="26.1", java_path="java")
        (directory / "server.jar").write_bytes(b"placeholder")
        manager = MultiServerManager(self.store)
        with patch.object(CoreAdapter, "java_major_version", return_value=21), patch("web_panel.multi_server.subprocess.Popen") as popen:
            ok, message = manager.start(profile["id"])
        self.assertFalse(ok)
        self.assertIn("Minecraft 26.1", message)
        self.assertIn("Java 25", message)
        self.assertIn("Java 21", message)
        popen.assert_not_called()

    def test_core_update_creates_backup_archives_old_jar_and_updates_profile(self):
        directory=Path(self.temp.name)/"core-update"
        profile=self.store.create("Old Paper",directory,"Paper",minecraft_version="1.21.4",launcher="old-paper.jar")
        with zipfile.ZipFile(directory/"old-paper.jar","w") as archive:
            archive.writestr("version.txt","old")
        (directory/"world").mkdir()
        (directory/"world"/"level.dat").write_text("world",encoding="utf-8")
        manager=MultiServerManager(self.store)
        updates=CoreUpdateJobs(self.store,manager)

        def fake_download(core,version,progress):
            path=directory/f"paper-{version}.jar"
            with zipfile.ZipFile(path,"w") as archive:
                archive.writestr("version.txt",version)
            progress(100)
            return str(path),"downloaded"

        with patch("web_panel.core_update.ServerManager.download_server",side_effect=fake_download):
            job_id=updates.start(profile["id"],"Paper","26.1")
            for _ in range(200):
                job=updates.get(job_id,profile["id"])
                if job["status"] in {"completed","failed"}:
                    break
                time.sleep(0.01)
        self.assertEqual(job["status"],"completed",job)
        updated=self.store.get(profile["id"])
        self.assertEqual(updated["minecraft_version"],"26.1")
        self.assertEqual(updated["launcher"],"paper-26.1.jar")
        self.assertFalse((directory/"old-paper.jar").exists())
        self.assertTrue(any((directory/"backups"/"cores").glob("*-old-paper.jar")))
        self.assertTrue(any((directory/"backups").glob("backup-*.zip")))
        self.assertEqual(json.loads((directory/"core-info.json").read_text(encoding="utf-8"))["java_required"],25)

    def test_file_service_rejects_path_traversal(self):
        profile = self.store.create("Paper", Path(self.temp.name) / "paper", "Paper")
        service = SafeFileService(self.store)
        with self.assertRaises(PermissionError):
            service.resolve(profile["id"], "../../outside.txt")

    def test_file_service_blocks_windows_case_aliases_of_internal_directories(self):
        profile=self.store.create("Internal aliases",Path(self.temp.name)/"internal-aliases","Paper")
        service=SafeFileService(self.store)
        for path in (".MCM_VERSIONS./abc/version.bin","plugins/.MCM_UPLOADS. /abc.part"):
            with self.assertRaises(PermissionError):
                service.resolve(profile["id"],path)

    def test_launcher_must_be_a_regular_file_inside_server_root(self):
        directory = Path(self.temp.name) / "safe-server"
        directory.mkdir()
        profile = self.store.create("Safe", directory, "Forge", launcher="../outside.bat")
        outside = Path(self.temp.name) / "outside.bat"
        outside.write_text("echo unsafe", encoding="utf-8")
        adapter = CoreAdapter("Forge")
        self.assertIsNone(adapter.find_launcher(directory, profile["launcher"]))
        (directory / "safe.jar").write_bytes(b"jar")
        self.assertEqual(adapter.find_launcher(directory, "../outside.bat"), (directory / "safe.jar").resolve())

    def test_forge_posix_launcher_runs_as_shell_script_not_jar(self):
        directory=Path(self.temp.name)/"forge-shell"
        directory.mkdir()
        (directory/"run.sh").write_text("#!/bin/sh\nexec java -jar forge.jar nogui\n",encoding="utf-8")
        profile=self.store.create("Forge shell",directory,"Forge",launcher="run.sh")
        adapter=CoreAdapter("Forge")
        if os.name=="nt":
            with self.assertRaisesRegex(ValueError,"POSIX shell"):
                adapter.command(profile)
        else:
            self.assertEqual(adapter.command(profile),["/bin/sh","run.sh"])

    def test_file_rename_cannot_bypass_blocked_executable_suffixes(self):
        profile = self.store.create("Rename", Path(self.temp.name) / "rename", "Paper")
        service = SafeFileService(self.store)
        (Path(profile["directory"]) / "payload.txt").write_text("not executable", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "系統執行檔"):
            service.rename(profile["id"], "payload.txt", "run.bat")

    def test_chunk_upload_rejects_oversized_single_chunk(self):
        profile = self.store.create("ChunkLimit", Path(self.temp.name) / "chunk-limit", "Paper")
        service = SafeFileService(self.store)
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            service.upload_chunk(profile["id"], uuid.uuid4().hex, "plugins/test.jar", 0,
                                 b"x" * (8 * 1024 * 1024 + 1), False, "error")

    def test_file_search_batch_and_conflict_policies(self):
        profile = self.store.create("Files", Path(self.temp.name) / "files", "Paper")
        service = SafeFileService(self.store)
        root = Path(profile["directory"])
        (root / "config.yml").write_text("motd: welcome\n", encoding="utf-8")
        (root / "other.txt").write_text("nothing here\n", encoding="utf-8")
        self.assertEqual([item["name"] for item in service.search(profile["id"], "config")], ["config.yml"])
        content_matches = service.search(profile["id"], "welcome", content=True)
        self.assertEqual(content_matches[0]["content_matches"][0]["line"], 1)
        service.mkdir(profile["id"], "target")
        copied = service.batch_copy_move(profile["id"], ["config.yml"], "target", conflict="rename")
        self.assertEqual(copied, ["target/config.yml"])
        (root / "target/config.yml").write_text("existing", encoding="utf-8")
        renamed = service.copy_or_move(profile["id"], "other.txt", "target/config.yml", conflict="rename")
        self.assertEqual(renamed.name, "config (1).yml")
        with self.assertRaises(FileExistsError):
            service.copy_or_move(profile["id"], "other.txt", "target/config.yml", conflict="error")
        self.assertEqual(service.delete_many(profile["id"], ["other.txt"]), 1)

    def test_file_versions_snapshot_before_edit_delete_and_restore(self):
        profile=self.store.create("Versions",Path(self.temp.name)/"versions","Paper");service=SafeFileService(self.store)
        root=Path(profile["directory"]);target=root/"server.properties";target.write_text("motd=old\n",encoding="utf-8")
        service.write_text(profile["id"],"server.properties","motd=new\n")
        versions=service.versions(profile["id"],"server.properties")["versions"]
        self.assertEqual(len(versions),1);self.assertEqual((root/"server.properties").read_text(encoding="utf-8"),"motd=new\n")
        service.restore_version(profile["id"],"server.properties",versions[0]["version_id"])
        self.assertEqual(target.read_text(encoding="utf-8"),"motd=old\n")
        self.assertEqual(len(service.versions(profile["id"],"server.properties")["versions"]),2)
        service.delete(profile["id"],"server.properties")
        self.assertFalse(target.exists());self.assertEqual(len(service.versions(profile["id"],"server.properties")["versions"]),3)
        with self.assertRaises(PermissionError):service.resolve(profile["id"],".mcm_versions")
        self.assertNotIn(".mcm_versions",[entry["name"] for entry in service.list(profile["id"])])

    def test_chunk_upload_resume_and_cancel(self):
        profile = self.store.create("Upload", Path(self.temp.name) / "upload", "Paper")
        service = SafeFileService(self.store)
        upload_id = uuid.uuid4().hex
        first = service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 0, b"abc", False, "error")
        self.assertEqual(first["received"], 3)
        with self.assertRaisesRegex(ValueError, "位置錯誤"):
            service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 0, b"x", False, "error")
        status = service.upload_status(profile["id"], upload_id)
        self.assertEqual(status["received"], 3)
        final = service.upload_chunk(profile["id"], upload_id, "plugins/test.jar", 3, b"def", True, "error")
        self.assertTrue(final["complete"])
        self.assertEqual((Path(profile["directory"]) / "plugins/test.jar").read_bytes(), b"abcdef")
        service.upload_chunk(profile["id"], upload_id, "cancel.bin", 0, b"x", False, "error")
        service.cancel_upload(profile["id"], upload_id)
        self.assertFalse(service.upload_status(profile["id"], upload_id)["exists"])

    def test_chunk_upload_failed_final_cleans_staging_and_stale_chunks_expire(self):
        profile=self.store.create("Upload cleanup",Path(self.temp.name)/"upload-cleanup","Paper")
        service=SafeFileService(self.store)
        root=Path(profile["directory"])
        (root/"exists.txt").write_text("old",encoding="utf-8")
        upload_id=uuid.uuid4().hex
        service.upload_chunk(profile["id"],upload_id,"exists.txt",0,b"new",False,"error")
        with self.assertRaisesRegex(ValueError,"衝突策略"):
            service.upload_chunk(profile["id"],upload_id,"exists.txt",3,b"!",True,"typo")
        staging=root/".mcm_uploads"
        self.assertFalse((staging/f"{upload_id}.part").exists())
        self.assertFalse((staging/f"{upload_id}.json").exists())
        stale_id=uuid.uuid4().hex
        service.upload_chunk(profile["id"],stale_id,"new.txt",0,b"partial",False,"error")
        old=time.time()-25*60*60
        os.utime(staging/f"{stale_id}.part",(old,old))
        os.utime(staging/f"{stale_id}.json",(old,old))
        self.assertFalse(service.upload_status(profile["id"],stale_id)["exists"])
        self.assertFalse((staging/f"{stale_id}.part").exists())

    def test_zip_extraction_rejects_traversal(self):
        profile = self.store.create("Zip", Path(self.temp.name) / "zip", "Paper")
        service = SafeFileService(self.store)
        root = Path(profile["directory"])
        with zipfile.ZipFile(root / "bad.zip", "w") as archive:
            archive.writestr("../../outside.txt", "bad")
        with self.assertRaisesRegex(ValueError, "不安全"):
            service.unzip(profile["id"], "bad.zip", "extract")
        self.assertFalse((Path(self.temp.name) / "outside.txt").exists())

    def test_zip_extraction_rejects_invalid_conflict_without_overwrite(self):
        profile=self.store.create("ZIP conflict",Path(self.temp.name)/"zip-conflict","Paper")
        service=SafeFileService(self.store)
        root=Path(profile["directory"]);(root/"out").mkdir();(root/"out"/"old.txt").write_text("old",encoding="utf-8")
        with zipfile.ZipFile(root/"source.zip","w") as archive:archive.writestr("old.txt","new")
        with self.assertRaisesRegex(ValueError,"衝突策略"):
            service.unzip(profile["id"],"source.zip","out",conflict="typo")
        self.assertEqual((root/"out"/"old.txt").read_text(encoding="utf-8"),"old")

    def test_zip_extraction_stages_all_entries_before_commit(self):
        profile=self.store.create("ZIP staging",Path(self.temp.name)/"zip-staging","Paper")
        service=SafeFileService(self.store)
        root=Path(profile["directory"]);(root/"out").mkdir();(root/"out"/"old.txt").write_text("old",encoding="utf-8")
        with zipfile.ZipFile(root/"source.zip","w") as archive:
            archive.writestr("old.txt","new")
            archive.writestr("broken.txt","bad")
        original_open=zipfile.ZipFile.open
        def fail_broken(archive,name,*args,**kwargs):
            filename=name.filename if isinstance(name,zipfile.ZipInfo) else name
            if filename=="broken.txt":raise zipfile.BadZipFile("simulated CRC failure")
            return original_open(archive,name,*args,**kwargs)
        with patch("web_panel.file_service.zipfile.ZipFile.open",new=fail_broken):
            with self.assertRaisesRegex(zipfile.BadZipFile,"simulated CRC failure"):
                service.unzip(profile["id"],"source.zip","out",conflict="overwrite")
        self.assertEqual((root/"out"/"old.txt").read_text(encoding="utf-8"),"old")
        self.assertFalse((root/"out"/"broken.txt").exists())

    def test_profile_update_and_delete(self):
        profile = self.store.create("Old", Path(self.temp.name) / "old", "Vanilla")
        updated = self.store.update(profile["id"], {"name": "New", "core_type": "Forge"})
        self.assertEqual(updated["name"], "New")
        self.assertEqual(updated["core_type"], "Forge")
        self.store.delete(profile["id"])
        self.assertIsNone(self.store.get(profile["id"]))

    def test_server_permissions_default_deny_and_assign(self):
        profile = self.store.create("Protected", Path(self.temp.name) / "protected", "Paper")
        user_id = self.db.create_user("limited", "l" * 12, "viewer")
        user = {"user_id": user_id, "role": "viewer"}
        self.assertFalse(self.db.has_server_permission(user, profile["id"], "server.view"))
        self.db.set_server_permissions(user_id, profile["id"], {"server.view", "files.view"})
        self.assertTrue(self.db.has_server_permission(user, profile["id"], "server.view"))
        self.assertFalse(self.db.has_server_permission(user, profile["id"], "server.stop"))


if __name__ == "__main__":
    unittest.main()
