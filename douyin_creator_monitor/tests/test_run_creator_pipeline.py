import argparse
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_creator_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_creator_pipeline", MODULE_PATH)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PIPELINE)


class PipelineHelpersTest(unittest.TestCase):
    def test_select_works_filters_sorts_and_limits(self):
        works = [
            {"aweme_id": "old", "create_time": 1},
            {"aweme_id": "new", "create_time": 3},
            {"aweme_id": "middle", "create_time": 2},
        ]
        selected = PIPELINE.select_works(works, set(), 2)
        self.assertEqual([item["aweme_id"] for item in selected], ["new", "middle"])
        selected = PIPELINE.select_works(works, {"middle"}, 0)
        self.assertEqual([item["aweme_id"] for item in selected], ["middle"])

    def test_audio_url_prefers_normalized_then_raw(self):
        self.assertEqual(PIPELINE.audio_url({"music_download_url": "direct", "raw": {"music_download_url": "raw"}}), "direct")
        self.assertEqual(PIPELINE.audio_url({"raw": {"music_download_url": ["raw-list"]}}), "raw-list")

    def test_sensitive_command_values_are_masked(self):
        command = ["python", "script.py", "--audio-url", "signed-url", "--table-id", "tbl-secret"]
        shown = PIPELINE.Runner.display(command, ("--audio-url", "--table-id"))
        self.assertNotIn("signed-url", shown)
        self.assertNotIn("tbl-secret", shown)
        self.assertEqual(shown.count("<redacted>"), 2)

    def test_artifact_paths_keep_existing_naming_convention(self):
        paths = PIPELINE.artifact_paths(Path("runtime/media"), "123", "volcengine")
        self.assertEqual(paths["raw_text"].name, "123.volc-url.txt")
        self.assertEqual(paths["final"].name, "123.final.txt")

    def test_asr_worker_count_uses_cli_then_config_and_caps_to_work_count(self):
        args = argparse.Namespace(asr_workers=None)
        self.assertEqual(PIPELINE.asr_worker_count({"asr": {"max_workers": 6}}, args, 3), 3)
        args.asr_workers = 2
        self.assertEqual(PIPELINE.asr_worker_count({"asr": {"max_workers": 6}}, args, 9), 2)

    def test_asr_worker_count_rejects_zero(self):
        args = argparse.Namespace(asr_workers=0)
        with self.assertRaises(PIPELINE.PipelineError):
            PIPELINE.asr_worker_count({}, args, 10)

    def test_final_backup_status_maps_pipeline_outcomes(self):
        self.assertEqual(PIPELINE.final_backup_status("success", "已上传"), "已上传")
        self.assertEqual(PIPELINE.final_backup_status("skipped", "已写入"), "已写入")
        self.assertEqual(PIPELINE.final_backup_status("disabled", "已上传"), "跳过")
        self.assertEqual(PIPELINE.final_backup_status("blocked", "已上传"), "失败")
        self.assertEqual(PIPELINE.final_backup_status("failed", "已写入"), "失败")

    def test_status_writeback_command_contains_all_final_statuses(self):
        config = {"python": "python", "feishu": {"work_id_field": "抖音作品ID", "as_identity": "user"}}
        creator = {"key": "demo", "works_table_id": "tbl-demo"}
        work = {"aweme_id": "123"}
        stages = {"ima_backed_up": "success", "kuake_backed_up": "disabled", "obsidian_exported": "failed"}
        command = PIPELINE.status_writeback_command(config, creator, work, stages)
        self.assertIn("feishu_work_status_writer.py", command[1])
        self.assertEqual(command[command.index("--ima-status") + 1], "已上传")
        self.assertEqual(command[command.index("--kuake-status") + 1], "跳过")
        self.assertEqual(command[command.index("--local-status") + 1], "失败")


if __name__ == "__main__":
    unittest.main()
