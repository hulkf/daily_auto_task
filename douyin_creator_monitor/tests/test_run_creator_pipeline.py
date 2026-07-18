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


if __name__ == "__main__":
    unittest.main()
