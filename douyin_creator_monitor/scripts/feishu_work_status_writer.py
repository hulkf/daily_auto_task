#!/usr/bin/env python3
"""Write final backup statuses and last-update time to a Feishu work row."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from feishu_transcript_writer import (
    find_record_by_work_id,
    load_base_token,
    redact_value,
    resolve_lark_cli,
    write_transcript,
)


BEIJING_TZ = timezone(timedelta(hours=8))


def build_patch(args: argparse.Namespace) -> dict[str, Any]:
    patch: dict[str, Any] = {"记录时间": args.record_time or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")}
    if args.ima_status:
        patch["ima状态"] = args.ima_status
    if args.kuake_status:
        patch["夸克网盘状态"] = args.kuake_status
    if args.local_status:
        patch["本地知识库状态"] = args.local_status
    transcript_file = getattr(args, "transcript_file", None)
    if transcript_file:
        transcript = Path(transcript_file).read_text(encoding="utf-8-sig").strip()
        if not transcript:
            raise SystemExit(f"Transcript file is empty: {transcript_file}")
        patch[getattr(args, "transcript_field", "语音转写全文")] = transcript
    return patch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-token")
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--work-id-field", default="抖音作品ID")
    parser.add_argument("--record-id")
    parser.add_argument("--lark-cli")
    parser.add_argument("--as", dest="as_identity", default="user", choices=["user", "bot"])
    parser.add_argument("--ima-status", choices=["待上传", "已上传", "失败", "跳过"])
    parser.add_argument("--kuake-status", choices=["待上传", "已上传", "失败", "跳过"])
    parser.add_argument("--local-status", choices=["待写入", "已写入", "失败", "跳过"])
    parser.add_argument("--record-time")
    parser.add_argument("--transcript-file")
    parser.add_argument("--transcript-field", default="语音转写全文")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cli = resolve_lark_cli(args.lark_cli)
    base_token = load_base_token(args.base_token)
    record_id = args.record_id or find_record_by_work_id(
        cli, base_token, args.table_id, args.work_id, args.work_id_field, args.as_identity,
    )
    response = write_transcript(
        cli, base_token, args.table_id, record_id, build_patch(args), args.as_identity, args.dry_run,
    )
    print(json.dumps({"record_id": record_id, "response": redact_value(response, base_token)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
