"""Sync normalized Douyin works into a Feishu Base works table.

The sync is intentionally non-destructive:
- match existing rows by "抖音作品ID";
- update only fields present in the current normalized input;
- create rows missing from Feishu;
- never delete rows that are absent from the current Douyin capture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


REPO_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKS_FILE = PROJECT_DIR / "runtime" / "zhiliao-works-from-mediacrawler.json"
DEFAULT_LARK_CLI = REPO_DIR / "tools" / "lark-cli" / "lark-cli.exe"
BEIJING_TZ = timezone(timedelta(hours=8))
HASHTAG_RE = re.compile(r"#([^#\s]+)")


def run_lark(cli: str, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    result = subprocess.run(
        [cli, *args],
        cwd=REPO_DIR,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"lark-cli did not return JSON: {output[:1000]}") from exc
    if result.returncode != 0 or not payload.get("ok", False):
        raise SystemExit(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def beijing_time(epoch_seconds: int | float | None) -> str | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).astimezone(BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def extract_hashtags(desc: str) -> str:
    return " ".join(f"#{tag}" for tag in HASHTAG_RE.findall(desc or ""))


def title_from_desc(desc: str) -> str:
    title = HASHTAG_RE.sub("", desc or "")
    title = re.sub(r"\s+", " ", title).strip()
    return title[:120] or (desc or "")[:120]


def build_patch(work: dict[str, Any], captured_at: str) -> dict[str, Any]:
    desc = work.get("desc") or ""
    patch: dict[str, Any] = {
        "抖音作品ID": str(work["aweme_id"]),
        "作品链接": work.get("url") or f"https://www.douyin.com/video/{work['aweme_id']}",
        "作品类型": "视频",
        "是否置顶": "是" if work.get("is_top") else "否",
        "作品标题": title_from_desc(desc),
        "原始文案": desc,
        "话题标签": extract_hashtags(desc),
        "页面可见数字": work.get("digg_count"),
        "页面可见数字含义": "点赞数（来自主页列表接口 statistics.digg_count）",
        "发布时间": beijing_time(work.get("create_time")),
        "点赞数": work.get("digg_count"),
        "评论数": work.get("comment_count"),
        "收藏数": work.get("collect_count"),
        "分享数": work.get("share_count"),
        "最近采集时间": captured_at,
        "记录时间": captured_at,
        "采集状态": "已完成",
        "原始作品数据": json.dumps(work, ensure_ascii=False, separators=(",", ":")),
    }
    if work.get("cover_url"):
        patch["封面图URL"] = work["cover_url"]
    return {key: value for key, value in patch.items() if value is not None}


def load_existing_records(cli: str, base_token: str, table_id: str) -> dict[str, str]:
    payload = run_lark(
        cli,
        [
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--field-id",
            "抖音作品ID",
            "--limit",
            "200",
            "--format",
            "json",
            "--as",
            "user",
        ],
    )
    data = payload["data"]
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    existing: dict[str, str] = {}
    for row, record_id in zip(rows, record_ids):
        if not row:
            continue
        work_id = str(row[0] or "").strip()
        if work_id:
            existing[work_id] = record_id
    return existing


def sync(args: argparse.Namespace) -> dict[str, Any]:
    base_token = args.base_token or os.environ.get("FEISHU_BASE_TOKEN")
    if not base_token:
        raise SystemExit("Missing --base-token or FEISHU_BASE_TOKEN.")
    works_payload = json.loads(Path(args.works_file).read_text(encoding="utf-8"))
    works = works_payload.get("works") or []
    if not works:
        raise SystemExit("No works found in normalized input.")
    missing = [
        work.get("aweme_id")
        for work in works
        if not work.get("aweme_id")
        or not work.get("create_time")
        or work.get("digg_count") is None
        or work.get("comment_count") is None
        or work.get("collect_count") is None
        or work.get("share_count") is None
    ]
    if missing:
        raise SystemExit(f"Refusing to sync; missing core fields for works: {missing[:10]}")

    captured_at = beijing_time(datetime.now(tz=timezone.utc).timestamp())
    existing = load_existing_records(args.lark_cli, base_token, args.table_id)
    existing_before = len(existing)
    updated = 0
    created = 0
    actions: list[dict[str, str]] = []

    for work in works:
        work_id = str(work["aweme_id"])
        patch = build_patch(work, captured_at or "")
        command = [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            args.table_id,
            "--json",
            json.dumps(patch, ensure_ascii=False),
            "--as",
            "user",
        ]
        record_id = existing.get(work_id)
        if record_id:
            command.extend(["--record-id", record_id])
        if args.dry_run:
            actions.append({"work_id": work_id, "action": "update" if record_id else "create"})
            if record_id:
                updated += 1
            else:
                created += 1
            continue
        response = run_lark(args.lark_cli, command)
        data = response.get("data") or {}
        new_record_id = data.get("record_id") or data.get("id") or record_id or ""
        if record_id:
            updated += 1
            action = "updated"
        else:
            created += 1
            action = "created"
            if new_record_id:
                existing[work_id] = new_record_id
        actions.append({"work_id": work_id, "action": action, "record_id": new_record_id})

    return {
        "input_count": len(works),
        "existing_before": existing_before,
        "updated": updated,
        "created": created,
        "dry_run": args.dry_run,
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--works-file", default=str(DEFAULT_WORKS_FILE))
    parser.add_argument("--base-token")
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--lark-cli", default=str(DEFAULT_LARK_CLI))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = sync(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
