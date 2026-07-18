#!/usr/bin/env python3
"""Write one backup platform's creator-directory mapping to Feishu Base."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
LOCAL_FEISHU_IDS = PROJECT_DIR / "local" / "feishu-ids.md"
DEFAULT_LARK_CLI = REPO_DIR / "tools" / "lark-cli" / "lark-cli.exe"


class MappingSyncError(RuntimeError):
    pass


def load_base_token(explicit: str | None) -> str:
    if explicit:
        return explicit
    token = os.environ.get("FEISHU_BASE_TOKEN", "").strip()
    if token:
        return token
    if LOCAL_FEISHU_IDS.exists():
        match = re.search(
            r"base_token:\s*([A-Za-z0-9]+)",
            LOCAL_FEISHU_IDS.read_text(encoding="utf-8-sig"),
        )
        if match:
            return match.group(1)
    raise MappingSyncError("缺少飞书 Base token。")


def run_lark(cli: str, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    result = subprocess.run(
        [cli, *args], cwd=REPO_DIR, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise MappingSyncError(f"lark-cli 未返回 JSON: {output[:500]}") from exc
    if result.returncode or not payload.get("ok", False):
        raise MappingSyncError(json.dumps(payload, ensure_ascii=False))
    return payload


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def find_record_id(payload: dict[str, Any], match_field: str, match_value: str) -> str:
    matches: list[str] = []
    for item in iter_dicts(payload):
        record_id = item.get("record_id") or item.get("id")
        if not isinstance(record_id, str) or not record_id.startswith("rec"):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        field_value = fields.get(match_field)
        if field_value is not None and str(field_value).strip() != match_value:
            continue
        matches.append(record_id)
    unique = list(dict.fromkeys(matches))
    if not unique:
        raise MappingSyncError(f"达人基础信息表中未找到 {match_field}={match_value}。")
    if len(unique) > 1:
        raise MappingSyncError(f"达人基础信息表中 {match_field}={match_value} 匹配到多条记录。")
    return unique[0]


def search_creator_record(
    cli: str, base_token: str, table_id: str, match_field: str,
    match_value: str, as_identity: str,
) -> str:
    payload = run_lark(
        cli,
        [
            "base", "+record-search", "--base-token", base_token,
            "--table-id", table_id, "--keyword", match_value,
            "--search-field", match_field, "--field-id", match_field,
            "--limit", "10", "--format", "json", "--as", as_identity,
        ],
    )
    return find_record_id(payload, match_field, match_value)


def get_record_fields(
    cli: str, base_token: str, table_id: str, record_id: str, as_identity: str,
) -> dict[str, Any]:
    payload = run_lark(
        cli,
        [
            "base", "+record-get", "--base-token", base_token,
            "--table-id", table_id, "--record-id", record_id,
            "--format", "json", "--as", as_identity,
        ],
    )
    for item in iter_dicts(payload):
        fields = item.get("fields")
        if isinstance(fields, dict):
            item_id = item.get("record_id") or item.get("id")
            if item_id in (None, record_id):
                return fields
    raise MappingSyncError(f"无法读取达人记录字段: {record_id}")


def comparable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and len(value) == 1:
        return comparable(value[0])
    if isinstance(value, dict):
        for key in ("text", "name", "value", "link"):
            if key in value:
                return comparable(value[key])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def changed_fields(desired: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in desired.items()
        if comparable(existing.get(key)) != comparable(value)
    }


def load_patch(metadata_file: Path) -> tuple[str, bool, dict[str, Any]]:
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise MappingSyncError(f"备份映射文件不存在: {metadata_file}") from exc
    except json.JSONDecodeError as exc:
        raise MappingSyncError(f"备份映射文件格式错误: {metadata_file}") from exc
    fields = data.get("feishu_fields")
    if not isinstance(fields, dict):
        raise MappingSyncError(f"映射文件缺少 feishu_fields: {metadata_file}")
    # Never erase an existing remote ID/path merely because one provider did not return it.
    patch = {str(key): value for key, value in fields.items() if value not in (None, "")}
    if not patch:
        raise MappingSyncError(f"映射文件没有可回写字段: {metadata_file}")
    directory = data.get("directory") if isinstance(data.get("directory"), dict) else {}
    return str(data.get("platform") or "unknown"), bool(directory.get("created")), patch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把达人备份目录映射回写到飞书达人基础信息表")
    parser.add_argument("--base-token")
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--metadata-file", type=Path, required=True)
    parser.add_argument("--match-field", required=True)
    parser.add_argument("--match-value", required=True)
    parser.add_argument("--record-id")
    parser.add_argument("--lark-cli", default=str(DEFAULT_LARK_CLI))
    parser.add_argument("--as", dest="as_identity", default="user", choices=["user", "bot"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        platform, created, patch = load_patch(args.metadata_file)
        token = load_base_token(args.base_token)
        record_id = args.record_id or search_creator_record(
            args.lark_cli, token, args.table_id, args.match_field,
            args.match_value, args.as_identity,
        )
        existing = get_record_fields(
            args.lark_cli, token, args.table_id, record_id, args.as_identity,
        )
        changes = changed_fields(patch, existing)
        if not changes:
            print(json.dumps({
                "platform": platform,
                "record_id": record_id,
                "status": "skipped",
                "directory_created": created,
                "reason": "飞书目录映射已经完整且一致",
            }, ensure_ascii=False, indent=2))
            return 0
        command = [
            "base", "+record-upsert", "--base-token", token,
            "--table-id", args.table_id, "--record-id", record_id,
            "--json", json.dumps(changes, ensure_ascii=False),
            "--format", "json", "--as", args.as_identity,
        ]
        if args.dry_run:
            print(json.dumps({"platform": platform, "record_id": record_id, "directory_created": created, "patch": changes, "dry_run": True}, ensure_ascii=False, indent=2))
            return 0
        run_lark(args.lark_cli, command)
        print(json.dumps({"platform": platform, "record_id": record_id, "directory_created": created, "status": "updated", "updated_fields": list(changes)}, ensure_ascii=False, indent=2))
        return 0
    except MappingSyncError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
