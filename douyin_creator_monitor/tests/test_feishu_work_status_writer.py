import argparse
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
MODULE_PATH = SCRIPTS_DIR / "feishu_work_status_writer.py"
SPEC = importlib.util.spec_from_file_location("feishu_work_status_writer", MODULE_PATH)
WRITER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(WRITER)


class WorkStatusWriterTest(unittest.TestCase):
    def test_build_patch_writes_all_statuses_and_record_time(self):
        args = argparse.Namespace(
            ima_status="已上传", kuake_status="已上传", local_status="已写入",
            record_time="2026-07-18 12:34:56",
        )
        self.assertEqual(
            WRITER.build_patch(args),
            {
                "记录时间": "2026-07-18 12:34:56",
                "ima状态": "已上传",
                "夸克网盘状态": "已上传",
                "本地知识库状态": "已写入",
            },
        )


if __name__ == "__main__":
    unittest.main()
