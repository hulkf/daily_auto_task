import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_write_selected_works_file_limits_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source = directory_path / "works.json"
            output = directory_path / "selected" / "works.json"
            source.write_text(
                json.dumps({"count": 3, "works": [{"aweme_id": "1"}, {"aweme_id": "2"}, {"aweme_id": "3"}]}),
                encoding="utf-8",
            )

            PIPELINE.write_selected_works_file(source, [{"aweme_id": "2"}], output)

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["works"], [{"aweme_id": "2"}])
            self.assertEqual(payload["source_file"], str(source))
            self.assertIn("selected_at", payload)

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

    def test_sync_record_ids_are_reused_by_writeback_commands(self):
        output = json.dumps({"record_ids": {"123": "rec123"}})
        self.assertEqual(PIPELINE.parse_sync_record_ids(output), {"123": "rec123"})
        config = {"python": "python", "feishu": {}}
        creator = {"key": "demo", "works_table_id": "tbl-demo"}
        work = {"aweme_id": "123"}
        paths = {"final": Path("final.txt")}
        transcript = PIPELINE.writeback_command(config, creator, work, paths, "rec123")
        self.assertEqual(transcript[transcript.index("--record-id") + 1], "rec123")
        statuses = PIPELINE.status_writeback_command(config, creator, work, {}, "rec123")
        self.assertEqual(statuses[statuses.index("--record-id") + 1], "rec123")
        combined = PIPELINE.status_writeback_command(config, creator, work, {}, "rec123", Path("final.txt"))
        self.assertEqual(combined[combined.index("--transcript-file") + 1], "final.txt")

    def test_mapping_cache_requires_valid_identity_and_success_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "ima.json", root / "kuake.json", root / "obsidian.json"]
            creator_name = "示例达人"
            for path_value in paths:
                payload = {
                    "platform": path_value.stem,
                    "directory": {"name": creator_name, "path": f"/root/{creator_name}"},
                    "feishu_fields": {"同步状态": "已映射"},
                }
                path_value.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            marker = root / "feishu-sync.json"
            marker.write_text(json.dumps({"signature": PIPELINE.mapping_cache_signature(paths)}), encoding="utf-8")
            now_epoch = paths[0].stat().st_mtime + 60
            self.assertTrue(PIPELINE.mapping_cache_is_fresh(
                paths, ttl_hours=24, now_epoch=now_epoch,
                marker_path=marker, expected_creator_name=creator_name,
            ))
            payload = json.loads(paths[-1].read_text(encoding="utf-8"))
            payload["directory"]["name"] = "另一个达人"
            paths[-1].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(PIPELINE.mapping_cache_is_fresh(
                paths, ttl_hours=24, now_epoch=now_epoch,
                marker_path=marker, expected_creator_name=creator_name,
            ))

    def test_logger_replaces_characters_unsupported_by_console_encoding(self):
        class GbkStream(io.StringIO):
            encoding = "gbk"

        stream = GbkStream()
        logger = PIPELINE.Logger(Path("unused.log"), persist=False)
        with patch.object(PIPELINE.sys, "stdout", stream):
            logger.write("unsupported: ʹ")
        self.assertIn("unsupported: ?", stream.getvalue())

    def test_runner_records_precise_success_timing(self):
        logger = PIPELINE.Logger(Path("unused.log"), persist=False)
        runner = PIPELINE.Runner(logger, dry_run=False)
        completed = Mock(returncode=0, stdout="ok", stderr="")
        with patch.object(PIPELINE.subprocess, "run", return_value=completed), patch.object(
            PIPELINE.time, "perf_counter", side_effect=[10.0, 12.25]
        ):
            runner.run("测试阶段", ["tool"], {})
        self.assertEqual(runner.metrics[0]["label"], "测试阶段")
        self.assertEqual(runner.metrics[0]["status"], "success")
        self.assertEqual(runner.metrics[0]["seconds"], 2.25)

    def test_runner_records_timing_when_subprocess_raises(self):
        logger = PIPELINE.Logger(Path("unused.log"), persist=False)
        runner = PIPELINE.Runner(logger, dry_run=False)
        with patch.object(PIPELINE.subprocess, "run", side_effect=OSError("cannot start")), patch.object(
            PIPELINE.time, "perf_counter", side_effect=[10.0, 10.5]
        ):
            with self.assertRaises(OSError):
                runner.run("启动失败", ["tool"], {})
        self.assertEqual(runner.metrics[0], {"label": "启动失败", "seconds": 0.5, "status": "failed"})

    def test_combined_finalizer_duration_is_not_double_counted(self):
        state = {}
        PIPELINE.set_combined_finalizer_status(
            state, transcript_included=True, status="success", duration_seconds=2.5,
        )
        self.assertEqual(state["stages"]["feishu_written_back"]["duration_seconds"], 2.5)
        self.assertEqual(state["stages"]["backup_statuses_written_back"]["duration_seconds"], 0.0)
        self.assertEqual(
            state["stages"]["feishu_written_back"]["operation_id"],
            state["stages"]["backup_statuses_written_back"]["operation_id"],
        )

    def test_stage_state_and_summary_keep_duration_metrics(self):
        state = {}
        PIPELINE.set_status(state, "ima_backed_up", "success", duration_seconds=3.125)
        self.assertEqual(state["stages"]["ima_backed_up"]["duration_seconds"], 3.125)
        summary = PIPELINE.summarize_metrics(
            [
                {"label": "飞书同步", "seconds": 2.0, "status": "success"},
                {"label": "夸克备份", "seconds": 5.0, "status": "failed"},
            ]
        )
        self.assertEqual(summary["total_calls"], 2)
        self.assertEqual(summary["total_seconds"], 7.0)
        self.assertEqual(summary["failed_calls"], 1)

    def test_early_configuration_failure_still_emits_run_timing_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid.json"
            config_path.write_text('{"creators": []}', encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), patch.object(
                PIPELINE.time, "perf_counter", side_effect=[10.0, 10.25]
            ):
                exit_code = PIPELINE.main(["--config", str(config_path), "--dry-run"])
        self.assertEqual(exit_code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["wall_seconds"], 0.25)
        self.assertIn("error", payload)

    def test_independent_backup_actions_isolate_failures(self):
        actions = [
            ("ima_backed_up", lambda: None),
            ("kuake_backed_up", lambda: (_ for _ in ()).throw(RuntimeError("network"))),
            ("obsidian_exported", lambda: None),
        ]
        outcomes = PIPELINE.run_independent_actions(actions, max_workers=3, dry_run=False)
        self.assertEqual(outcomes["ima_backed_up"]["status"], "success")
        self.assertEqual(outcomes["kuake_backed_up"]["status"], "failed")
        self.assertIn("network", outcomes["kuake_backed_up"]["error"])
        self.assertEqual(outcomes["obsidian_exported"]["status"], "success")

    def test_backup_worker_count_is_bounded_by_runnable_stages(self):
        args = argparse.Namespace(backup_workers=None)
        self.assertEqual(PIPELINE.backup_worker_count({"backups": {"max_workers": 3}}, args, 2), 2)
        args.backup_workers = 1
        self.assertEqual(PIPELINE.backup_worker_count({"backups": {"max_workers": 3}}, args, 3), 1)

    def test_mapping_sync_command_combines_provider_metadata(self):
        config = {"python": "python", "feishu": {"creator_table_id": "tbl-creators"}}
        creator = {"key": "demo", "creator_name": "示例达人"}
        command = PIPELINE.mapping_sync_command(
            config, creator, [Path("ima.json"), Path("kuake.json"), Path("obsidian.json")]
        )
        self.assertEqual(command.count("--metadata-file"), 3)

    def test_resume_skips_finalizer_when_nothing_changed(self):
        stages = {
            "ima_backed_up": "skipped",
            "kuake_backed_up": "skipped",
            "obsidian_exported": "skipped",
        }
        self.assertFalse(PIPELINE.finalizer_needed(stages, None, "success", resume=True))
        self.assertTrue(PIPELINE.finalizer_needed({**stages, "kuake_backed_up": "failed"}, None, "success", resume=True))
        self.assertTrue(PIPELINE.finalizer_needed(stages, Path("final.txt"), "success", resume=True))


    def test_pending_collection_ids_prefers_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "collection.json"
            works_file = root / "works.json"
            state_file.write_text(json.dumps({"pending_aweme_ids": ["3", "2", "2"]}), encoding="utf-8")
            works_file.write_text(json.dumps({"pending_aweme_ids": ["1"]}), encoding="utf-8")
            self.assertEqual(PIPELINE.pending_collection_ids(state_file, works_file), ["3", "2"])

    def test_complete_collection_pending_only_removes_processed_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "collection.json"
            works_file = root / "works.json"
            payload = {"pending_aweme_ids": ["new-3", "new-2", "new-1"], "pending_count": 3}
            state_file.write_text(json.dumps(payload), encoding="utf-8")
            works_file.write_text(json.dumps(payload), encoding="utf-8")

            PIPELINE.complete_collection_pending(state_file, works_file, ["new-3"])

            for path in (state_file, works_file):
                updated = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(updated["pending_aweme_ids"], ["new-2", "new-1"])
                self.assertEqual(updated["pending_count"], 2)
                self.assertIn("last_pipeline_completed_at", updated)

    def test_max_works_limits_pending_selection_without_discarding_remainder(self):
        works = [
            {"aweme_id": "new-3", "create_time": 3},
            {"aweme_id": "new-2", "create_time": 2},
            {"aweme_id": "new-1", "create_time": 1},
        ]
        selected = PIPELINE.select_works(works, {"new-3", "new-2", "new-1"}, 1)
        self.assertEqual([item["aweme_id"] for item in selected], ["new-3"])

    def test_collect_command_passes_incremental_state_and_probe(self):
        config = {
            "python": "python",
            "collection": {"incremental_probe_count": 3, "incremental_enabled": True},
        }
        creator = {"key": "demo", "creator_url": "creator-id"}
        command = PIPELINE.collect_command(
            config,
            creator,
            Path("works.json"),
            Path("media-output"),
            Path("collection-state.json"),
            False,
        )
        self.assertIn("--collection-state-file", command)
        self.assertIn("collection-state.json", command)
        self.assertIn("--incremental-probe-count", command)
        self.assertNotIn("--force-full-collect", command)

        forced = PIPELINE.collect_command(
            config,
            creator,
            Path("works.json"),
            Path("media-output"),
            Path("collection-state.json"),
            False,
            True,
        )
        self.assertIn("--force-full-collect", forced)


if __name__ == "__main__":
    unittest.main()
