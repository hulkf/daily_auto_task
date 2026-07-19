import argparse
import importlib.util
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backup_transcripts_to_kuake.py"
SPEC = importlib.util.spec_from_file_location("backup_transcripts_to_kuake_batch", MODULE_PATH)
KUAKE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = KUAKE
SPEC.loader.exec_module(KUAKE)


class KuakeBatchTest(unittest.TestCase):
    def test_manifest_reuses_credentials_and_directory_and_reports_each_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "1.txt"
            second = root / "2.txt"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "creator_name": "达人 A",
                "files": [
                    {"aweme_id": "1", "path": str(first), "video_date": "2026-07-19", "title": "一"},
                    {"aweme_id": "2", "path": str(second), "video_date": "2026-07-19", "title": "二"},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            args = argparse.Namespace(
                manifest=manifest, local_env=root / "env.json", base_dir="/备份",
                creator_name="", remote_dir="", kuake_exe=root / "kuake.exe",
            )
            outputs = []
            with patch.object(KUAKE, "load_credentials", return_value=(KUAKE.KuakeCredentials(cookie="x"), "/备份")) as credentials, patch.object(
                KUAKE, "ensure_dir_details", return_value=KUAKE.RemoteDirectory("达人 A", "/备份/达人 A"),
            ) as ensure, patch.object(
                KUAKE, "upload_file", side_effect=["/备份/达人 A/1.txt", KUAKE.KuakeBackupError("upload failed")],
            ), patch("builtins.print", side_effect=lambda value: outputs.append(str(value))):
                exit_code = KUAKE.cmd_upload_manifest(args)

        payload = json.loads(outputs[-1])
        self.assertEqual(exit_code, 0)
        self.assertEqual(credentials.call_count, 1)
        self.assertEqual(ensure.call_count, 1)
        self.assertEqual(payload["results"]["1"]["status"], "success")
        self.assertEqual(payload["results"]["2"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
